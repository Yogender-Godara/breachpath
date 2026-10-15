"""The worker (docs/03_LLD.md section 8): a plain sync loop. Run: python -m worker.main

It takes one agent-mode incident at a time from the `jobs` stream, holds the incident lock (with its heartbeat), runs the ONE pipeline
(core/pipeline.py, mode "agent"), and cleans up. A janitor deletes expired sandboxes. XAUTOCLAIM (other workers' jobs) stays cut: one worker.
"""
import logging
import os
import signal
import socket
import threading
import time

from core import config
from core.graph import client
from core.graph.schema import is_sandbox
from core.pipeline import run_incident
from orchestrator import state as state_store
from orchestrator.jobs import GROUP, STREAM, ensure_group
from orchestrator.lock import LockBusy, hold_lock
from orchestrator.state_machine import TERMINAL

log = logging.getLogger("breachpath.worker")
JANITOR_EVERY_S = 600


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def finish(redis, state, msg_id):
    """The incident is over for this worker: remove it from the active sets and ACK the job. The agent sandbox is KEPT (the UI may show it)."""
    for key in (f"active:{state['tenant']}", "active:agent"):
        redis.srem(key, state["id"])
    redis.xack(STREAM, GROUP, msg_id)


def handle(redis, msg_id, incident_id):
    state = state_store.load(incident_id)
    if state is None:
        log.warning("job %s: incident not found, dropped", incident_id)
        redis.xack(STREAM, GROUP, msg_id)
        return
    if state["status"] in TERMINAL:  # a duplicate job, or a job read again after the incident finished
        log.info("job %s: already %s, acked", incident_id, state["status"])
        finish(redis, state, msg_id)
        return
    started = time.perf_counter()
    try:
        with hold_lock(incident_id) as lock:
            try:
                run_incident(state, "agent", lock_token=lock.token, lock=lock)  # loops on state["status"]: a saved status resumes where it stopped
            finally:
                finish(redis, state, msg_id)
        log.info("job %s: %s in %d ms, degraded=%s", incident_id, state["status"], round((time.perf_counter() - started) * 1000), state["degraded"])
    except LockBusy:
        log.warning("job %s: locked by another holder, left un-acked", incident_id)


def process_one(redis, consumer, block_ms=5000, pending=False):
    """Read one job (new ones, or this consumer's own un-acked ones when pending=True) and handle it. Returns the incident id or None."""
    rows = redis.xreadgroup(GROUP, consumer, {STREAM: "0" if pending else ">"}, count=1, block=None if pending else block_ms)
    entries = rows[0][1] if rows else []
    if not entries:
        return None
    msg_id, fields = entries[0]
    incident_id = _text(fields.get("incident_id", fields.get(b"incident_id")))
    handle(redis, _text(msg_id), incident_id)
    return incident_id


def janitor(redis):
    """Delete the sandboxes in sbx:expiry whose time has passed (and their reach:* keys). Never anything that is not a sandbox name."""
    for name in redis.zrangebyscore("sbx:expiry", "-inf", time.time()):
        name = _text(name)
        if is_sandbox(name):
            client.delete_graph(name)
            for key in redis.scan_iter(match=f"reach:{name}:*"):
                redis.delete(key)
            log.info("janitor: deleted %s", name)
        else:
            log.warning("janitor: %s is not a sandbox name, not deleted", name)
        redis.zrem("sbx:expiry", name)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redis = client.get_db().connection
    ensure_group(redis)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())  # finish the current job, then exit
    log.info("worker %s started (providers: %s)", consumer, ",".join(config.LLM_PROVIDERS))
    seen = set()  # a job left un-acked (this consumer crashed, or the lock was busy) is read first; once each, so a busy one cannot loop
    while (incident_id := process_one(redis, consumer, pending=True)) and incident_id not in seen:
        seen.add(incident_id)
    last_janitor = 0.0
    while not stop.is_set():
        if time.time() - last_janitor >= JANITOR_EVERY_S:
            janitor(redis)
            last_janitor = time.time()
        process_one(redis, consumer)
    log.info("worker %s stopped", consumer)


if __name__ == "__main__":
    main()

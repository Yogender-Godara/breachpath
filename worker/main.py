"""The worker (docs/03_LLD.md section 8): a plain sync loop. Run: python -m worker.main

It takes one agent-mode incident at a time from the `jobs` stream, holds the incident lock (with its heartbeat), runs the ONE pipeline
(core/pipeline.py, mode "agent"), and cleans up. A janitor deletes expired sandboxes. XAUTOCLAIM (other workers' jobs) stays cut: one worker.
Liveness (P9.2): the worker SETs `worker:alive:<consumer>` (60 s TTL) on every loop turn, and every 15 s while a job runs; the compose
healthcheck only asks whether such a key exists. A crashed worker restarts under the same consumer name (same container, PID 1), so its
un-acked job is read again at startup (`resume_pending`).
"""
import logging
import os
import signal
import socket
import threading
import time

from redis.exceptions import ResponseError

from core import config
from core.graph import client
from core.graph.schema import is_sandbox
from core.pipeline import run_incident
from orchestrator import events, state as state_store
from orchestrator.jobs import GROUP, STREAM, ensure_group
from orchestrator.lock import LockBusy, hold_lock
from orchestrator.state_machine import TERMINAL

log = logging.getLogger("breachpath.worker")
JANITOR_EVERY_S = 600
ALIVE_PREFIX, ALIVE_TTL_S, ALIVE_EVERY_S = "worker:alive:", 60, 15


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def beat(redis, consumer):
    """The liveness key: it disappears 60 s after the last turn, so a stuck or dead worker is visible to the healthcheck."""
    redis.set(f"{ALIVE_PREFIX}{consumer}", int(time.time()), ex=ALIVE_TTL_S)


def _keep_alive(redis, consumer, done):
    while not done.wait(ALIVE_EVERY_S):
        beat(redis, consumer)


def finish(redis, state, msg_id):
    """The incident is over for this worker: remove it from the active sets and ACK the job. The agent sandbox is KEPT (the UI may show it)."""
    for key in (f"active:{state['tenant']}", "active:agent"):
        redis.srem(key, state["id"])
    redis.xack(STREAM, GROUP, msg_id)


def handle(redis, msg_id, incident_id, consumer=None):
    """-> False when the incident is locked by another holder (the job stays un-acked), else True. consumer: keeps its liveness key fresh during the job."""
    state = state_store.load(incident_id)
    if state is None:
        log.warning("job %s: incident not found, dropped", incident_id)
        redis.xack(STREAM, GROUP, msg_id)
        return True
    if state["status"] in TERMINAL:  # a duplicate job, or a job read again after the incident finished
        log.info("job %s: already %s, acked", incident_id, state["status"])
        finish(redis, state, msg_id)
        return True
    trim_sandboxes(redis)  # room in the graph quota for this incident's sandbox
    started = time.perf_counter()
    done = threading.Event()
    if consumer:  # a job can take longer than the 60 s TTL of the liveness key
        threading.Thread(target=_keep_alive, args=(redis, consumer, done), daemon=True).start()
    try:
        with hold_lock(incident_id) as lock:
            try:
                log.info("job %s: running from status %s", incident_id, state["status"])
                # the first thing the UI timeline shows (NFR-L3: the first agent event waits for an MCP subprocess start, about a second)
                events.emit(incident_id, "orchestrator", "step", f"worker picked up the incident (status {state['status']})", {"status": state["status"]})
                run_incident(state, "agent", lock_token=lock.token, lock=lock)  # loops on state["status"]: a saved status resumes where it stopped
            finally:
                finish(redis, state, msg_id)
        log.info("job %s: %s in %d ms, degraded=%s", incident_id, state["status"], round((time.perf_counter() - started) * 1000), state["degraded"])
        return True
    except LockBusy:
        log.warning("job %s: locked by another holder, left un-acked", incident_id)
        return False
    finally:
        done.set()


def process_one(redis, consumer, block_ms=5000, pending=False):
    """Read one job (new ones, or this consumer's own un-acked ones when pending=True) and handle it. Returns the incident id or None."""
    rows = redis.xreadgroup(GROUP, consumer, {STREAM: "0" if pending else ">"}, count=1, block=None if pending else block_ms)
    entries = rows[0][1] if rows else []
    if not entries:
        return None
    msg_id, fields = entries[0]
    incident_id = _text(fields.get("incident_id", fields.get(b"incident_id")))
    handle(redis, _text(msg_id), incident_id, consumer)
    return incident_id


def resume_pending(redis, consumer, wait_s=config.LOCK_TTL_MS / 1000 + 5, pause_s=2.0):
    """After a restart: run this consumer's un-acked jobs (the incident resumes from its saved status). A crashed worker's lock is still held until
    its TTL (LOCK_TTL_MS) runs out, so a locked job is retried for up to `wait_s` instead of being forgotten until the next restart.
    -> the incident ids that ran."""
    ran, deadline = [], time.time() + wait_s
    while True:
        rows = redis.xreadgroup(GROUP, consumer, {STREAM: "0"}, count=1)  # "0" = this consumer's own un-acked entries
        entries = rows[0][1] if rows else []
        if not entries:
            return ran
        msg_id, fields = entries[0]
        incident_id = _text(fields.get("incident_id", fields.get(b"incident_id")))
        beat(redis, consumer)
        if handle(redis, _text(msg_id), incident_id, consumer):
            ran.append(incident_id)
        elif time.time() >= deadline:
            log.warning("job %s: still locked after %.0f s, left un-acked", incident_id, wait_s)
            return ran
        else:
            time.sleep(pause_s)


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


def _id_of(name):
    return name.split(":sbx:", 1)[1].split("-")[0]  # t:s2:sbx:<incident id>[-split]


def trim_sandboxes(redis, keep=None):
    """Agent sandboxes are KEPT (the UI shows them) for SANDBOX_TTL_HOURS, but every kept graph counts toward MAX_GRAPHS: with only the janitor,
    about 10 agent incidents in a day made every new one FAIL with LIMIT_EXCEEDED (found by the P9.3 latency run). Before each job the worker keeps the
    newest `keep` sandboxes and deletes the older ones. Never a baseline, never a sandbox of an incident that is still active. -> the names deleted."""
    keep = config.MAX_KEPT_SANDBOXES if keep is None else keep
    active = {_text(i) for key in redis.scan_iter(match="active:*") for i in redis.smembers(key)}
    names = [_text(n) for n in redis.zrange("sbx:expiry", 0, -1)]  # oldest first: all sandboxes share one TTL
    gone = []
    for name in names[:max(0, len(names) - keep)]:
        if is_sandbox(name) and _id_of(name) not in active:
            client.delete_graph(name)
            redis.zrem("sbx:expiry", name)
            for key in redis.scan_iter(match=f"reach:{name}:*"):
                redis.delete(key)
            gone.append(name)
    if gone:
        log.info("trimmed %d old sandbox(es), keeping the newest %d: %s", len(gone), keep, ", ".join(gone))
    return gone


def loop(redis, consumer, stop):
    """Beat, janitor, one job: until `stop` is set."""
    last_janitor = 0.0
    while not stop.is_set():
        beat(redis, consumer)
        if time.time() - last_janitor >= JANITOR_EVERY_S:
            janitor(redis)
            last_janitor = time.time()
        try:
            process_one(redis, consumer)
        except ResponseError as e:
            if "NOGROUP" not in str(e):
                raise
            log.warning("the jobs stream or its group is gone (%s): creating it again", e)  # e.g. the stream was deleted: do not crash-loop
            ensure_group(redis)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    redis = client.get_db().connection
    ensure_group(redis)
    consumer = f"{socket.gethostname()}-{os.getpid()}"
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())  # finish the current job, then exit
    log.info("worker %s started (providers: %s)", consumer, ",".join(config.LLM_PROVIDERS))
    beat(redis, consumer)
    resumed = resume_pending(redis, consumer)  # a job left un-acked (this consumer crashed) is read first
    if resumed:
        log.info("worker %s resumed %s", consumer, ", ".join(resumed))
    loop(redis, consumer, stop)
    log.info("worker %s stopped", consumer)


if __name__ == "__main__":
    main()

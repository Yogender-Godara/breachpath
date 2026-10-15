"""Incident lock with a heartbeat (docs/03_LLD.md section 8).

The heartbeat is a plain background thread, not an asyncio task, so the same lock works in the sync fast-mode API
and in the async worker.
"""
import secrets
import threading
from contextlib import contextmanager

from core import config
from core.graph.client import get_db

# Atomic: only the owner may extend or delete.
EXTEND = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('PEXPIRE', KEYS[1], ARGV[2]) else return 0 end"
RELEASE = "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end"


class LockBusy(Exception):
    """Another holder owns the lock of this incident."""


class Handle:
    def __init__(self, token):
        self.token = token
        self.lost = threading.Event()  # set when the heartbeat finds the lock is no longer ours


def _beat(redis, key, handle, ttl_ms, heartbeat_s, stop):
    while not stop.wait(heartbeat_s):
        try:
            ours = redis.eval(EXTEND, 1, key, handle.token, ttl_ms)
        except Exception:  # cannot confirm the lock: treat it as lost rather than keep working on a guess
            ours = 0
        if not ours:
            handle.lost.set()
            return


@contextmanager
def hold_lock(incident_id, ttl_ms=config.LOCK_TTL_MS, heartbeat_s=config.LOCK_HEARTBEAT_S):
    redis = get_db().connection
    key = f"lock:inc:{incident_id}"
    handle = Handle(secrets.token_hex(8))
    if not redis.set(key, handle.token, nx=True, px=ttl_ms):
        raise LockBusy(f"incident {incident_id} is locked by another holder")
    stop = threading.Event()
    thread = threading.Thread(target=_beat, args=(redis, key, handle, ttl_ms, heartbeat_s, stop), daemon=True)
    thread.start()
    try:
        # Writes stay safe even if the lock is lost: guards.check_write compares the token on every write call.
        yield handle
    finally:
        stop.set()
        thread.join()
        redis.eval(RELEASE, 1, key, handle.token)

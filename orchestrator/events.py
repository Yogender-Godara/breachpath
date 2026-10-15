"""Incident events (docs/03_LLD.md section 8). Agent mode: a Redis stream inc:{id}:events. Fast mode: a list in memory."""
import json
from datetime import datetime, timezone

from core.graph.client import get_db

EVENT_TYPES = ("tool_call", "tool_result", "step", "finding", "plan", "action", "verdict", "report", "error")
STREAM_MAXLEN = 1000
STREAM_TTL_S = 7 * 24 * 3600


def _key(incident_id):
    return f"inc:{incident_id}:events"


def emit(incident_id, agent, type, msg, data=None, latency_ms=None, tokens_in=None, tokens_out=None, sink=None):
    if type not in EVENT_TYPES:
        raise ValueError(f"unknown event type {type!r}; expected one of {EVENT_TYPES}")
    event = {"agent": agent, "type": type, "msg": msg, "data": data, "latency_ms": latency_ms,
             "tokens_in": tokens_in, "tokens_out": tokens_out, "ts": datetime.now(timezone.utc).isoformat()}
    if sink is not None:  # fast mode: no Redis
        sink.append(event)
        return event
    redis = get_db().connection
    redis.xadd(_key(incident_id), {"e": json.dumps(event)}, maxlen=STREAM_MAXLEN, approximate=True)
    redis.expire(_key(incident_id), STREAM_TTL_S)
    return event


def read(incident_id, after="0-0", count=100, block_ms=None):
    """Events with a stream id greater than `after`, oldest first: [(stream_id, event)].
    block_ms: wait up to that long for a new event (long-poll); None = return at once."""
    rows = get_db().connection.xread({_key(incident_id): after}, count=count, block=block_ms) or []
    out = []
    for _, entries in rows:
        for stream_id, fields in entries:
            raw = fields.get("e", fields.get(b"e"))
            out.append((stream_id.decode() if isinstance(stream_id, bytes) else stream_id, json.loads(raw)))
    return out

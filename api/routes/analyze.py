import time

from fastapi import APIRouter, Request
from pydantic import BaseModel

from api.checks import check_target, rate_limit
from api.graph_view import build_view
from api.routes.scenarios import ACTIVE_TTL_S
from core.graph.client import get_db
from core.graph.schema import sandbox_name
from core.pipeline import run_incident
from core.tools import write
from orchestrator.lock import hold_lock
from orchestrator.state import new_state

router = APIRouter(prefix="/v1")


class AnalyzeBody(BaseModel):
    tenant: str
    src: str


def timings(events, total_ms):
    """Milliseconds per agent (the pipeline times every step), plus the request total."""
    out = {}
    for e in events:
        out[e["agent"]] = out.get(e["agent"], 0) + (e["latency_ms"] or 0)
    return {**out, "total": total_ms}


@router.post("/analyze")
def analyze(body: AnalyzeBody, request: Request):
    """Fast mode, synchronous: the whole pipeline runs inside this request, in memory. Open (no token) but rate-limited per IP."""
    rate_limit(get_db().connection, request, "analyze")
    check_target(body.tenant, body.src)
    state, sink = new_state(body.tenant, body.src, "fast"), []
    redis, active = get_db().connection, f"active:{body.tenant}"
    started = time.perf_counter()
    redis.sadd(active, state["id"])
    redis.expire(active, ACTIVE_TTL_S)
    try:
        with hold_lock(state["id"]) as lock:
            auth = {"incident_id": state["id"], "lock_token": lock.token}
            try:
                run_incident(state, "fast", lock_token=lock.token, sink=sink)
            finally:  # the sandbox and the -split sandbox never outlive the request
                for suffix in ("", "-split"):
                    write.delete_sandbox(sandbox_name(body.tenant, state["id"], suffix), **auth)
    finally:
        redis.srem(active, state["id"])
    total = round((time.perf_counter() - started) * 1000)
    return {"incident_id": state["id"], "status": state["status"], "failure_detail": state.get("failure_detail"),
            **{k: state[k] for k in ("findings", "plan", "action", "verdict", "report", "attempt", "degraded", "degraded_steps")},
            "graph": build_view(state) if state["findings"] else None,  # fast-mode state is never stored, so the UI cannot ask /graph later
            "events": sink, "timings_ms": timings(sink, total)}

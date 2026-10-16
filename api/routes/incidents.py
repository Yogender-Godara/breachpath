import hashlib
import json
import secrets

from typing import Literal

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.checks import check_target, rate_limit
from api.errors import ApiError
from api.graph_view import build_view
from api.routes.scenarios import ACTIVE_TTL_S
from core import config
from core.graph.client import get_db
from orchestrator import events, state as state_store
from orchestrator.jobs import ensure_group
from orchestrator.state import new_state

router = APIRouter(prefix="/v1")
IDEM_TTL_S = 24 * 3600
JOBS_MAXLEN = 10000
MAX_WAIT_S = 10


class IncidentBody(BaseModel):
    tenant: str
    src: str
    mode: Literal["fast", "agent"]
    instructions: str | None = Field(None, max_length=500)  # agent mode: an operator instruction in free text (/analyze ignores it)


@router.post("/incidents", status_code=202)
def create_incident(body: IncidentBody, request: Request, x_demo_token: str | None = Header(None),
                    idempotency_key: str | None = Header(None)):
    redis = get_db().connection
    if not (config.DEMO_TOKEN and x_demo_token and secrets.compare_digest(x_demo_token, config.DEMO_TOKEN)):
        raise ApiError(403, "FORBIDDEN", "agent mode needs a valid X-Demo-Token header")
    rate_limit(redis, request, "incidents")
    if body.mode == "fast":
        raise ApiError(400, "BAD_INPUT", "fast mode runs synchronously: use POST /v1/analyze")
    check_target(body.tenant, body.src)

    body_hash = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True).encode()).hexdigest()
    if idempotency_key:
        seen = redis.get(f"idem:{idempotency_key}")
        if seen:
            seen = json.loads(seen)
            if seen["hash"] != body_hash:
                raise ApiError(409, "IDEMPOTENCY_CONFLICT", "this Idempotency-Key was used with a different request body")
            existing = state_store.load(seen["incident_id"])
            if existing:
                return JSONResponse(_created(existing), status_code=200)  # a replay: 200 and the same incident

    if redis.scard("active:agent") >= config.MAX_ACTIVE_AGENT:
        raise ApiError(429, "BUSY", f"{config.MAX_ACTIVE_AGENT} agent incidents are already running: try again later")

    state = new_state(body.tenant, body.src, "agent", (body.instructions or "").strip())
    state_store.save(state)
    for key in (f"active:{body.tenant}", "active:agent"):  # the worker removes the id when the incident ends (P6.6)
        redis.sadd(key, state["id"])
        redis.expire(key, ACTIVE_TTL_S)
    ensure_group(redis)  # the worker consumes this stream; the group must exist before the job is added
    redis.xadd("jobs", {"incident_id": state["id"]}, maxlen=JOBS_MAXLEN, approximate=True)
    if idempotency_key:
        redis.set(f"idem:{idempotency_key}", json.dumps({"incident_id": state["id"], "hash": body_hash}), ex=IDEM_TTL_S, nx=True)
    return _created(state)


def _created(state):
    return {"incident_id": state["id"], "status": state["status"]}


def _load(incident_id):
    state = state_store.load(incident_id)
    if state is None:
        raise ApiError(404, "NOT_FOUND", f"incident {incident_id} not found")
    return state


@router.get("/incidents/{incident_id}")
def get_incident(incident_id: str):
    return _load(incident_id)


@router.get("/incidents/{incident_id}/events")
def get_events(incident_id: str, after: str = "0-0", wait: int = Query(5, ge=0)):
    _load(incident_id)
    rows = events.read(incident_id, after=after, block_ms=min(wait, MAX_WAIT_S) * 1000 or None)
    return {"events": [e for _, e in rows], "last_id": rows[-1][0] if rows else after}


@router.get("/incidents/{incident_id}/graph")
def get_graph(incident_id: str):
    state = _load(incident_id)
    if state["findings"] is None:
        raise ApiError(404, "NOT_FOUND", f"incident {incident_id} has no findings yet")
    return build_view(state)

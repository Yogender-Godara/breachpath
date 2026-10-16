"""Incident state (docs/03_LLD.md section 7.2): one JSON string per incident, inc:{id}, kept 7 days."""
import json
import secrets
from datetime import datetime, timezone

from core.graph.client import get_db
from core.graph.schema import baseline_name

STATE_TTL_S = 7 * 24 * 3600


def _now():
    return datetime.now(timezone.utc).isoformat()


def new_state(tenant, src, mode, instructions=""):
    """instructions: the operator's free-text instruction (agent mode only; the Planner turns it into graph constraints)."""
    now = _now()
    return {"id": secrets.token_hex(4), "tenant": tenant, "src": src, "mode": mode,
            "status": "QUEUED", "attempt": 0, "degraded": False, "degraded_steps": [], "instructions": instructions,
            "baseline_graph": baseline_name(tenant), "sandbox_graph": None,
            "created_at": now, "updated_at": now,
            "findings": None, "plan": None, "action": None, "verdict": None, "report": None,
            "history": [], "tried_changes": [], "retry_change": None}


# Fast mode keeps the state dict in memory for the whole run and never calls save().
# Agent mode saves after every step: that checkpoint is what lets a restarted worker resume.
def save(state):
    state["updated_at"] = _now()
    get_db().connection.set(f"inc:{state['id']}", json.dumps(state), ex=STATE_TTL_S)


def load(incident_id):
    raw = get_db().connection.get(f"inc:{incident_id}")
    return None if raw is None else json.loads(raw)

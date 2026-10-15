"""Write guards (docs/03_LLD.md section 5). They raise GuardRefused / LimitExceeded; the @tool decorator maps them to codes."""
import re

from core import config
from core.graph.client import get_db
from core.graph.schema import is_sandbox
from core.tools.common import BadInput, GuardRefused, LimitExceeded

SIMS_TTL_S = 7 * 24 * 3600  # same as inc:{id}


def _sims_key(incident_id):
    return f"quota:inc:{incident_id}:sims"


def _suffix(graph):
    return graph.split(":sbx:", 1)[1]


def check_write(graph, incident_id, lock_token):
    """Run before every write tool. Order: name, scope, owner."""
    if not is_sandbox(graph):  # this is what stops every write to a baseline (AC-5)
        raise GuardRefused(f"{graph} is not a sandbox graph")
    suffix = _suffix(graph)
    if not (suffix in (incident_id, f"{incident_id}-split") or re.fullmatch(re.escape(incident_id) + r"-sim-\d+", suffix)):
        raise GuardRefused(f"{graph} does not belong to incident {incident_id}")  # exact match, not a substring
    owner = get_db().connection.get(f"lock:inc:{incident_id}")
    if isinstance(owner, bytes):
        owner = owner.decode()
    if owner is None or owner != lock_token:
        raise GuardRefused(f"lock for incident {incident_id} is not held by this caller")


def check_quota(incident_id, kind):
    """Run before creating a NEW graph. kind: "sandbox" or "simulation" (returns the simulation number n)."""
    if kind not in ("sandbox", "simulation"):
        raise BadInput(f"quota kind must be 'sandbox' or 'simulation', got {kind!r}")
    graphs = get_db().list_graphs()
    if len(graphs) >= config.MAX_GRAPHS:
        raise LimitExceeded(f"{len(graphs)} graphs exist (limit {config.MAX_GRAPHS})")
    if kind == "sandbox":
        own = [g for g in graphs if is_sandbox(g) and _suffix(g) in (incident_id, f"{incident_id}-split")]
        if len(own) >= config.MAX_SANDBOXES:
            raise LimitExceeded(f"incident {incident_id} already has {len(own)} sandboxes (limit {config.MAX_SANDBOXES})")
    else:
        # A counter: simulation copies are deleted after each run, so counting graphs could not enforce this.
        redis = get_db().connection
        count = redis.incr(_sims_key(incident_id))
        redis.expire(_sims_key(incident_id), SIMS_TTL_S)
        if count > config.MAX_SIMULATIONS:
            raise LimitExceeded(f"incident {incident_id} used {count - 1} simulations (limit {config.MAX_SIMULATIONS})")
        return count  # the n of this simulation, used for the -sim-<n> graph name

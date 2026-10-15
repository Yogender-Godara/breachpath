import re

from fastapi import APIRouter

from api.errors import ApiError
from core.graph import loader
from core.graph.client import get_db
from core.graph.schema import baseline_name
from data.scenarios import SCENARIO_DIR, build, load_scenario

router = APIRouter(prefix="/v1")
ACTIVE_TTL_S = 3600  # safety net: a crashed run must not block reloads forever


def _order(path):
    return int(re.sub(r"\D", "", path.stem) or 0)


@router.get("/scenarios")
def list_scenarios():
    out = []
    for path in sorted(SCENARIO_DIR.glob("*.yaml"), key=_order):
        sc = load_scenario(path)
        out.append({"id": sc["id"], "title": sc["title"], "src": sc["src"], "description": sc["title"]})
    return out


@router.post("/scenarios/{sid}/load")
def load_scenario_route(sid: str):
    if not (SCENARIO_DIR / f"{sid}.yaml").is_file() or not re.fullmatch(r"[a-z0-9-]+", sid):
        raise ApiError(404, "NOT_FOUND", f"unknown scenario {sid}")
    if get_db().connection.scard(f"active:{sid}") > 0:
        raise ApiError(409, "SCENARIO_BUSY", f"an incident is running on {sid}: wait for it to finish")
    # Built in memory (same seed = the same data as data/generated/*.json): the API image has no generated files.
    return loader.load_graph(baseline_name(sid), build(sid))

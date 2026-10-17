from fastapi import APIRouter

from api.errors import ApiError
from core.graph.client import get_db

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.get("/readyz")
def readyz():
    # Readiness = FalkorDB answers PING. There is no MCP check: each agent step opens its own stdio session, so there is nothing to ping.
    try:
        get_db().connection.ping()
    except Exception as e:
        raise ApiError(503, "DB_UNAVAILABLE", f"FalkorDB is not reachable: {e}")
    return {"ok": True}

from fastapi import APIRouter

from api.errors import ApiError
from core.graph.client import get_db

router = APIRouter()


@router.get("/healthz")
def healthz():
    return {"ok": True}


@router.get("/readyz")
def readyz():
    # P6 adds the MCP check here (the worker's stdio subprocess); for now readiness = FalkorDB answers PING.
    try:
        get_db().connection.ping()
    except Exception as e:
        raise ApiError(503, "DB_UNAVAILABLE", f"FalkorDB is not reachable: {e}")
    return {"ok": True}

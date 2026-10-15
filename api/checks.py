"""Checks shared by /analyze and /incidents: the tenant's baseline is loaded, and src is a User in it."""
from api.errors import ApiError
from core.graph import queries
from core.graph.client import get_db, ro_query
from core.graph.schema import baseline_name


def check_target(tenant, src):
    try:
        graph = baseline_name(tenant)
    except ValueError as e:
        raise ApiError(400, "BAD_INPUT", e)
    if graph not in get_db().list_graphs():
        raise ApiError(404, "NOT_FOUND", f"scenario {tenant} is not loaded: POST /v1/scenarios/{tenant}/load")
    rows = ro_query(graph, queries.NODE_INFO, {"id": src})
    if not rows or "User" not in rows[0][0]:
        raise ApiError(400, "UNKNOWN_SRC", f"{src} is not a user in {tenant}")
    return graph

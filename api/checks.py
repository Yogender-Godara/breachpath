"""Checks shared by /analyze and /incidents: the tenant's baseline is loaded, src is a User in it, and the per-IP rate limit."""
import time

from api.errors import ApiError
from core import config
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


def rate_limit(redis, request, scope):
    """Per client IP and minute, counted per scope: "incidents" (agent mode) allows RATE_LIMIT_PER_MIN, "analyze" (fast mode: open, no LLM, HLD 11)
    allows ANALYZE_RATE_LIMIT_PER_MIN. Each scope has its own counter. Behind the UI every user has the UI container's IP, so these are shared limits."""
    ip = request.client.host if request.client else "unknown"
    key = f"ratelimit:{scope}:{ip}:{int(time.time() // 60)}"
    count = redis.incr(key)
    redis.expire(key, 60)
    limit = config.ANALYZE_RATE_LIMIT_PER_MIN if scope == "analyze" else config.RATE_LIMIT_PER_MIN
    if count > limit:
        raise ApiError(429, "RATE_LIMITED", f"more than {limit} requests per minute")

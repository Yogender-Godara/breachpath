"""Shared by the tools: the error model (docs/03_LLD.md section 5) as one decorator."""
from functools import wraps

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError
from redis.exceptions import TimeoutError as RedisTimeoutError


class BadInput(Exception):
    pass


class NotFound(Exception):
    pass


class GuardRefused(Exception):
    pass


class LimitExceeded(Exception):
    pass


class ToolFailed(Exception):
    """A tool returned ok = False inside the pipeline. The pipeline must stop, never continue on a failed tool."""


def unwrap(result):
    """The pipeline's one rule for tool results: return the data, or raise."""
    if not result["ok"]:
        raise ToolFailed(f"{result['error']['code']}: {result['error']['message']}")
    return result["data"]


def _error(code, message):
    return {"ok": False, "error": {"code": code, "message": str(message)}}


def tool(fn):
    """Return {"ok": True, "data": ...} or {"ok": False, "error": {"code", "message"}}.

    MinCutError is deliberately NOT caught: it means an invariant broke (the cut does not add up to the
    max flow), so it must reach the pipeline, which turns it into NEEDS_HUMAN. It is never a tool error.
    """
    @wraps(fn)
    def run(*args, **kwargs):
        try:
            return {"ok": True, "data": fn(*args, **kwargs)}
        except BadInput as e:
            return _error("BAD_INPUT", e)
        except NotFound as e:
            return _error("NOT_FOUND", e)
        except GuardRefused as e:
            return _error("GUARD_REFUSED", e)
        except LimitExceeded as e:
            return _error("LIMIT_EXCEEDED", e)
        except (RedisConnectionError, RedisTimeoutError) as e:
            return _error("DB_UNAVAILABLE", e)
        except ResponseError as e:
            if "timed out" in str(e).lower():
                return _error("TIMEOUT", e)
            return _error("BAD_INPUT", e)  # e.g. a write sent to run_readonly_cypher, or bad Cypher
    return run

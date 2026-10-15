"""One error format for the whole API (docs/03_LLD.md section 9): {"error": {"code", "message"}}."""
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.exceptions import HTTPException as StarletteHTTPException


class ApiError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


def _body(status, code, message):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": str(message)}})


def install(app: FastAPI):
    @app.exception_handler(ApiError)
    async def api_error(request: Request, e: ApiError):
        return _body(e.status, e.code, e.message)

    @app.exception_handler(RequestValidationError)
    async def validation(request: Request, e: RequestValidationError):
        first = e.errors()[0]
        return _body(400, "BAD_INPUT", f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}")

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, e: StarletteHTTPException):
        codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return _body(e.status_code, codes.get(e.status_code, "HTTP_ERROR"), e.detail)

    @app.exception_handler(RedisConnectionError)
    async def db_down(request: Request, e: RedisConnectionError):
        return _body(503, "DB_UNAVAILABLE", e)

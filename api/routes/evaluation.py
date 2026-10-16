import json
from pathlib import Path

from fastapi import APIRouter
from redis.exceptions import RedisError

from api.errors import ApiError
from core.graph.client import get_db

router = APIRouter(prefix="/v1")
RESULTS_FILE = Path(__file__).resolve().parent.parent.parent / "eval" / "results" / "latest.json"


@router.get("/eval/latest")
def eval_latest():
    """The last evaluation (eval/run_eval.py): `eval:latest` in Redis, else the shipped file, else 404."""
    try:
        raw = get_db().connection.get("eval:latest")
    except RedisError:  # a Redis problem is not a reason to hide the shipped results
        raw = None
    if raw:
        return json.loads(raw)
    if RESULTS_FILE.is_file():
        return json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    raise ApiError(404, "NOT_FOUND", "no evaluation results yet: run python -m eval.run_eval")

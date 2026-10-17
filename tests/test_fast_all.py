"""Fast mode on S1-S8 vs the answer keys, through the real API (the same check as scripts/check_fast_mode.py)."""
import pytest
from fastapi.testclient import TestClient

from api.main import app
from core import config
from scripts.check_fast_mode import check
from tests.helpers import SCENARIOS


@pytest.mark.parametrize("sid", SCENARIOS)
def test_fast_mode_matches_answer_key(falkordb, sid, monkeypatch):
    monkeypatch.setattr(config, "ANALYZE_RATE_LIMIT_PER_MIN", 10**6)  # a checker, not a load test: 8 scenarios in one minute are fine
    row, failures = check(TestClient(app), sid)
    assert failures == [], f"{sid}: {failures}"

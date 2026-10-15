
from core.graph import client
from orchestrator import state

KEYS = ["id", "tenant", "src", "mode", "status", "attempt", "degraded", "baseline_graph", "sandbox_graph",
        "created_at", "updated_at", "findings", "plan", "action", "verdict", "report", "history", "tried_changes", "retry_change"]


def test_new_state_has_exactly_the_lld_fields():
    s = state.new_state("s2", "user:ravi", "fast")
    assert sorted(s) == sorted(KEYS)
    assert (s["status"], s["attempt"], s["degraded"], s["history"]) == ("QUEUED", 0, False, [])
    assert s["baseline_graph"] == "t:s2:baseline" and s["sandbox_graph"] is None
    assert all(s[k] is None for k in ("findings", "plan", "action", "verdict", "report"))
    assert s["created_at"] == s["updated_at"] and s["created_at"].endswith("+00:00")
    assert state.new_state("s2", "user:ravi", "fast")["id"] != s["id"]


def test_save_load_round_trip_and_ttl(falkordb):
    redis = client.get_db().connection
    s = state.new_state("s2", "user:ravi", "agent")
    try:
        before = s["updated_at"]
        state.save(s)
        assert s["updated_at"] >= before
        assert state.load(s["id"]) == s
        assert 6.9 * 24 * 3600 < redis.ttl(f"inc:{s['id']}") <= 7 * 24 * 3600
        assert state.load("nope0000") is None
    finally:
        redis.delete(f"inc:{s['id']}")

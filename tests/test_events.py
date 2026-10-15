import pytest

from core.graph import client
from orchestrator import events

INC = "p51events"


@pytest.fixture
def redis(falkordb):
    r = client.get_db().connection
    r.delete(f"inc:{INC}:events")
    yield r
    r.delete(f"inc:{INC}:events")


def test_list_sink_needs_no_redis():
    sink = []
    events.emit("x", "investigator", "step", "hello", data={"a": 1}, latency_ms=5, sink=sink)
    assert len(sink) == 1
    assert {k: sink[0][k] for k in ("agent", "type", "msg", "data", "latency_ms", "tokens_in", "tokens_out")} == {
        "agent": "investigator", "type": "step", "msg": "hello", "data": {"a": 1}, "latency_ms": 5,
        "tokens_in": None, "tokens_out": None}
    assert sink[0]["ts"].endswith("+00:00")


def test_bad_type_raises_and_nothing_is_written():
    sink = []
    with pytest.raises(ValueError, match="unknown event type"):
        events.emit("x", "planner", "chatter", "hi", sink=sink)
    assert sink == []


def test_emit_then_read_in_order(redis):
    for i in range(3):
        events.emit(INC, "planner", "plan", f"m{i}", data={"i": i}, tokens_in=10 + i)
    rows = events.read(INC)
    assert [e["msg"] for _, e in rows] == ["m0", "m1", "m2"]
    assert rows[2][1]["tokens_in"] == 12 and rows[2][1]["data"] == {"i": 2}


def test_read_after_returns_only_later_events(redis):
    for i in range(3):
        events.emit(INC, "planner", "plan", f"m{i}")
    first_id = events.read(INC)[0][0]
    assert [e["msg"] for _, e in events.read(INC, after=first_id)] == ["m1", "m2"]
    assert events.read(INC, after=events.read(INC)[-1][0]) == []
    assert len(events.read(INC, count=2)) == 2


def test_stream_has_a_seven_day_ttl(redis):
    events.emit(INC, "executor", "action", "x")
    assert 6.9 * 24 * 3600 < redis.ttl(f"inc:{INC}:events") <= 7 * 24 * 3600

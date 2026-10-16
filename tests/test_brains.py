"""The agent brains (agents/brains.py): the pipeline in agent mode with the Investigator/Planner agents faked (no LLM), real MCP sessions + FalkorDB."""
import pytest

from agents import brains, investigator, planner, reporter
from agents.base import ToolCrashed
from core import fast_brains
from core.graph import client, loader
from core.pipeline import run_incident
from orchestrator import events
from orchestrator.lock import hold_lock
from orchestrator.state import new_state


def run_pipeline(monkeypatch, investigate_degraded=False, plan_error=None, plan_degraded=False):
    """run_incident(mode agent) with fake agents: the investigator returns the fast findings, the planner returns the fast plan or raises."""
    async def fake_investigate(session, state, emit=None):
        return fast_brains.investigate(state), investigate_degraded

    async def fake_plan(session, state, *, lock_token, emit=None):
        if plan_error:
            raise plan_error
        return fast_brains.plan(state, lock_token=lock_token), plan_degraded
    monkeypatch.setattr(investigator, "investigate", fake_investigate)
    monkeypatch.setattr(planner, "plan", fake_plan)
    monkeypatch.setattr(reporter, "write", lambda state, emit=None: (fast_brains.report(state)["markdown"], False))  # no LLM here
    loader.load_scenario("s2")
    state = new_state("s2", "user:ravi", "agent")
    redis = client.get_db().connection
    try:
        with hold_lock(state["id"]) as lock:
            run_incident(state, "agent", lock_token=lock.token, lock=lock)
    finally:
        if state["sandbox_graph"]:
            client.delete_graph(state["sandbox_graph"])
            redis.zrem("sbx:expiry", state["sandbox_graph"])
        redis.delete(f"inc:{state['id']}", f"inc:{state['id']}:events", f"quota:inc:{state['id']}:sims", *redis.keys(f"reach:*:sbx:{state['id']}*"))
    return state


def test_a_min_cut_error_in_an_mcp_tool_makes_the_incident_needs_human_not_a_retry(falkordb, monkeypatch):
    crash = ToolCrashed("compute_min_cut: Error executing tool compute_min_cut: MinCutError: the cut does not add up")
    state = run_pipeline(monkeypatch, plan_error=crash)
    assert state["status"] == "NEEDS_HUMAN" and state["failure_detail"].startswith("invariant violated") and state["attempt"] == 0
    assert state["degraded"] is False


def test_any_other_tool_crash_fails_the_incident(falkordb, monkeypatch):
    state = run_pipeline(monkeypatch, plan_error=ToolCrashed("apply_fix: Error executing tool apply_fix: KeyError: boom"))
    assert state["status"] == "FAILED" and "ToolCrashed" in state["failure_detail"] and "KeyError" in state["failure_detail"]


def test_a_degraded_agent_marks_the_incident_degraded_and_the_report_says_so(falkordb, monkeypatch):
    state = run_pipeline(monkeypatch, investigate_degraded=True)
    assert state["status"] == "DONE" and state["degraded"] is True
    assert "Generated in degraded mode" in state["report"]["markdown"]
    assert run_pipeline(monkeypatch, plan_degraded=True)["degraded"] is True
    assert run_pipeline(monkeypatch)["degraded"] is False


def test_the_agents_events_go_to_the_incidents_redis_stream_with_the_agents_name(falkordb, monkeypatch):
    state = new_state("s2", "user:ravi", "agent")

    async def fake_investigate(session, state, emit=None):
        emit("finding", "found it", {"valid_paths": 2}, latency_ms=5, tokens_in=11, tokens_out=3)
        return {"reachable_jewels": []}, False
    monkeypatch.setattr(investigator, "investigate", fake_investigate)
    redis = client.get_db().connection
    try:
        brains.investigate(state)
        (_, event), = events.read(state["id"])
    finally:
        redis.delete(f"inc:{state['id']}:events")
    assert (event["agent"], event["type"], event["msg"], event["data"]) == ("investigator", "finding", "found it", {"valid_paths": 2})
    assert (event["latency_ms"], event["tokens_in"], event["tokens_out"]) == (5, 11, 3)


def test_the_report_comes_from_the_reporter_and_a_fallback_marks_the_incident_degraded(monkeypatch):
    state = new_state("s2", "user:ravi", "agent")
    seen = {}

    def fake_write(state, emit=None):
        seen["emit"] = emit
        return "the report", seen["degraded"]
    monkeypatch.setattr(reporter, "write", fake_write)
    seen["degraded"] = False
    assert brains.report(state) == {"markdown": "the report"} and state["degraded"] is False and callable(seen["emit"])
    seen["degraded"] = True  # the Reporter fell back to the template
    brains.report(state)
    assert state["degraded"] is True

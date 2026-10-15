"""One pipeline, fast brains, on the real FalkorDB. Each test owns a fresh incident; its graphs and keys are removed afterwards."""
import re
from contextlib import contextmanager

import pytest

from core import fast_brains, pipeline
from core.graph import client, loader, queries
from core.graph.mincut import MinCutError
from core.graph.schema import baseline_name, sandbox_name
from core.pipeline import run_incident
from core.tools import verify, write
from orchestrator.lock import hold_lock
from orchestrator.state import new_state

HEADINGS = ["## Summary", "## What was at risk", "## Attack paths", "## Fix", "## Proof", "## Business impact", "## Next steps"]


def baseline_fingerprint(sid):
    return client.ro_query(baseline_name(sid), "MATCH ()-[r]->() RETURN count(r), sum(r.cap), count(r.protected)")[0]


@contextmanager
def incident(sid, src, run=True):
    """A fresh state under a held lock. Yields (state, sink, auth). The baseline must come out unchanged."""
    loader.load_scenario(sid)
    before = baseline_fingerprint(sid)
    state, sink = new_state(sid, src, "fast"), []
    redis = client.get_db().connection
    with hold_lock(state["id"]) as lock:
        auth = {"incident_id": state["id"], "lock_token": lock.token}
        try:
            if run:
                run_incident(state, "fast", lock_token=lock.token, sink=sink)
            yield state, sink, auth
        finally:
            for suffix in ("", "-split"):
                write.delete_sandbox(sandbox_name(sid, state["id"], suffix), **auth)
            redis.delete(f"quota:inc:{state['id']}:sims")
    assert baseline_fingerprint(sid) == before


def sims_used(state):
    raw = client.get_db().connection.get(f"quota:inc:{state['id']}:sims")
    return int(raw) if raw else 0


def test_s2_done_with_the_right_cut_and_report(falkordb):
    with incident("s2", "user:ravi") as (state, sink, _):
        plan, action, verdict = state["plan"], state["action"], state["verdict"]
        assert state["status"] == "DONE" and state["attempt"] == 1
        assert (plan["cut_eids"], plan["chosen"], plan["source"], plan["safe"]) == (["e:s2:05", "e:s2:10"], "source_side", "code", True)
        assert action["applied_eids"] == ["e:s2:05", "e:s2:10"] and action["refused_protected"] == []
        assert verdict["pass"] is True and verdict["attack_max_flow"] == 0 and verdict["bfs_reached_jewels"] == []
        text = state["report"]["markdown"]
        assert [m for m in re.findall(r"^## .+$", text, re.M)] == HEADINGS
        assert "No safe automatic fix" not in text and "Proof passed" in text
        assert "Detach the policy" in text and "Delete the file and rotate the secret" in text
        print("\n--- S2 state ---")
        for key in ("status", "attempt", "plan", "action", "verdict"):
            print(key, "=", state[key])
        print("--- S2 report ---\n" + text)


def test_events_hold_the_steps_in_order(falkordb):
    with incident("s2", "user:ravi") as (state, sink, _):
        assert [(e["agent"], e["type"]) for e in sink] == [
            ("investigator", "finding"), ("executor", "step"), ("planner", "plan"), ("executor", "action"),
            ("verifier", "verdict"), ("reporter", "report")]
        assert client.get_db().connection.get(f"inc:{state['id']}") is None  # fast mode never saves the state


def test_s8_prefers_the_cut_with_no_collateral(falkordb):
    with incident("s8", "user:max") as (state, sink, _):
        assert state["status"] == "DONE" and state["plan"]["cut_eids"] == ["e:s8:05"]
        assert state["plan"]["simulation"]["collateral"]["count"] == 0
        assert sims_used(state) == 2  # both candidates were simulated


def test_s5_needs_human_with_a_proven_split(falkordb):
    with incident("s5", "user:zoe") as (state, sink, _):
        plan = state["plan"]
        assert state["status"] == "NEEDS_HUMAN" and plan["safe"] is False and plan["cut_eids"] == []
        assert plan["suggestion"]["suggestion_type"] == "split_node" and plan["suggestion"]["proven"] is True
        assert state["failure_detail"].startswith("no safe fix")
        text = state["report"]["markdown"]
        assert "Verified suggestion" in text and "No safe automatic fix" in text
        assert state["verdict"] is None and sink[-1]["type"] == "report"


def test_s7_eight_hop_path_is_found_and_cut(falkordb):
    with incident("s7", "user:nia") as (state, sink, _):
        assert state["findings"]["paths"] == [] and state["findings"]["reachable_jewels"] == ["db:s7-billing"]
        assert state["findings"]["beyond_hops_jewels"] == ["db:s7-billing"]
        assert state["status"] == "DONE" and state["plan"]["cut_eids"] == ["e:s7:08"]  # same size, so the stalest edge wins
        assert "No path within 6 hops" in state["report"]["markdown"]


def test_s3_identical_candidates_use_one_simulation(falkordb):
    with incident("s3", "user:mia") as (state, sink, _):
        assert state["status"] == "DONE" and state["plan"]["cut_eids"] == ["e:s3:02"]
        assert sims_used(state) == 1


def test_no_risk_user_gets_a_short_report_and_no_sandbox(falkordb):
    loader.load_scenario("s2")
    src = next(u for (u,) in client.ro_query(baseline_name("s2"), "MATCH (u:User) RETURN u.id ORDER BY u.id")
               if not verify.reached_jewels(baseline_name("s2"), u))
    with incident("s2", src) as (state, sink, _):
        assert state["status"] == "DONE_NO_RISK" and state["sandbox_graph"] is None and state["plan"] is None
        assert not [g for g in client.get_db().list_graphs() if f":sbx:{state['id']}" in g]
        assert "no exposure" in state["report"]["markdown"] and [e["type"] for e in sink] == ["finding", "report"]


# --- failures ---

def test_min_cut_error_is_needs_human_never_a_retry(falkordb, monkeypatch):
    def boom(state, *, lock_token):
        raise MinCutError("cut capacity 3 != max_flow 2")
    monkeypatch.setattr(fast_brains, "plan", boom)
    with incident("s2", "user:ravi") as (state, sink, _):
        assert state["status"] == "NEEDS_HUMAN" and state["failure_detail"].startswith("invariant violated")
        assert state["attempt"] == 0 and [e["type"] for e in sink].count("error") == 1
        assert state["report"] is not None  # NEEDS_HUMAN still gets its report


def test_any_other_exception_is_failed_with_an_error_event(falkordb, monkeypatch):
    def boom(state):
        raise RuntimeError("db went away")
    monkeypatch.setattr(fast_brains, "investigate", boom)
    with incident("s2", "user:ravi") as (state, sink, _):
        assert state["status"] == "FAILED" and "db went away" in state["failure_detail"]
        assert [e["type"] for e in sink] == ["error"] and state["report"] is None


def test_agent_mode_swaps_only_the_brains():
    from agents import brains
    assert pipeline._brain("agent") is brains and pipeline._brain("fast") is fast_brains
    assert [hasattr(brains, f) for f in ("investigate", "plan", "report")] == [True] * 3
    with pytest.raises(ValueError):
        pipeline._brain("nope")


def test_a_failed_tool_stops_the_pipeline(falkordb):
    with incident("s2", "user:ravi", run=False) as (state, sink, auth):
        run_incident(state, "fast", lock_token="wrong-token", sink=sink)  # every write is refused
        assert state["status"] == "FAILED" and "GUARD_REFUSED" in state["failure_detail"]


def test_invalid_plan_goes_back_to_planning(falkordb, monkeypatch):
    real = fast_brains.plan
    calls = []

    def bad_then_good(state, *, lock_token):
        calls.append(1)
        out = real(state, lock_token=lock_token)
        if len(calls) == 1:
            out = {**out, "cut_eids": ["e:nope:99"]}
        return out
    monkeypatch.setattr(fast_brains, "plan", bad_then_good)
    with incident("s2", "user:ravi") as (state, sink, _):
        assert state["status"] == "DONE" and len(calls) == 2 and state["attempt"] == 2  # invalid plan 1, verified plan 2
        assert state["plan"]["cut_eids"] == ["e:s2:05", "e:s2:10"]


# --- prepare with the retry change, and delete_sandbox ---

def test_prepare_protects_the_second_path_of_a_retried_need(falkordb):
    with incident("s2", "user:ravi", run=False) as (state, sink, auth):
        base = baseline_name("s2")
        sbx = sandbox_name("s2", state["id"])
        state["status"] = "PREPARING"
        pipeline._prepare(state, auth)
        protected = {r[0] for r in client.ro_query(sbx, "MATCH ()-[r]->() WHERE r.protected = true RETURN r.eid")}
        pick = None
        for (need_id, principal, resource) in client.ro_query(base, queries.VALID_NEEDS_FULL):
            rows = client.ro_query(base, queries.SHORTEST_PATHS, {"src": principal, "target": resource, "k": 2})
            if len(rows) == 2 and not set(rows[1][0]) <= protected:
                pick = (need_id, set(rows[1][0]))
                break
        assert pick, "no need with an unprotected second path in S2"
        state["status"] = "PREPARING"
        state["tried_changes"] = [{"type": "protect_second_path", "needs": [pick[0]]}]
        pipeline._prepare(state, auth)
        after = {r[0] for r in client.ro_query(sbx, "MATCH ()-[r]->() WHERE r.protected = true RETURN r.eid")}
        assert pick[1] <= after and len(after) > len(protected)
        assert state["status"] == "PLANNING"


def test_delete_sandbox_is_idempotent_and_guarded(falkordb):
    with incident("s2", "user:ravi", run=False) as (state, sink, auth):
        sbx = sandbox_name("s2", state["id"])
        assert write.create_sandbox("s2", **auth)["ok"]
        assert write.delete_sandbox(sbx, **auth) == {"ok": True, "data": {"deleted": True}}
        assert write.delete_sandbox(sbx, **auth) == {"ok": True, "data": {"deleted": False}}
        assert sbx not in client.get_db().list_graphs()
        assert client.get_db().connection.zscore("sbx:expiry", sbx) is None
        refused = write.delete_sandbox(sbx, incident_id=state["id"], lock_token="wrong")
        assert refused["ok"] is False and refused["error"]["code"] == "GUARD_REFUSED"
        assert write.delete_sandbox(baseline_name("s2"), **auth)["error"]["code"] == "GUARD_REFUSED"

import itertools

import pytest

from core import config
from orchestrator.state import new_state
from orchestrator.state_machine import (STATUSES, TERMINAL, TRANSITIONS, InvalidTransition, advance, after_invalid_plan,
                                        after_verify, retry_change)

ALLOWED = [("QUEUED", "INVESTIGATING"), ("QUEUED", "PREPARING"), ("QUEUED", "DONE_NO_RISK"),
           ("INVESTIGATING", "PREPARING"), ("INVESTIGATING", "DONE_NO_RISK"),
           ("PREPARING", "PLANNING"), ("PLANNING", "EXECUTING"), ("PLANNING", "NEEDS_HUMAN"),
           ("EXECUTING", "VERIFYING"), ("EXECUTING", "PLANNING"), ("EXECUTING", "NEEDS_HUMAN"),
           ("VERIFYING", "REPORTING"), ("VERIFYING", "PREPARING"), ("VERIFYING", "NEEDS_HUMAN"),
           ("REPORTING", "DONE")]


def at(status, plan_source="code", cut=("e:1",)):
    state = new_state("s2", "user:ravi", "fast")
    state["status"] = status
    state["plan"] = {"source": plan_source, "cut_eids": list(cut)}
    return state


def verdict(max_flow=0, reached=(), broken=()):
    ok = max_flow == 0 and not reached and not broken
    return {"pass": ok, "attack_max_flow": max_flow, "bfs_reached_jewels": list(reached),
            "business": [{"need_id": n, "ok": False} for n in broken] + [{"need_id": "need:fine", "ok": True}],
            "failure_detail": None if ok else "text for humans"}


def broken(*needs):
    return verdict(broken=needs)


# --- transitions ---

@pytest.mark.parametrize("a,b", ALLOWED)
def test_allowed_transitions_work(a, b):
    assert advance({"status": a}, b)["status"] == b


def test_exactly_the_allowed_arrows_exist():
    arrows = {(a, b) for a, nexts in TRANSITIONS.items() for b in nexts if b != "FAILED"}
    assert arrows == set(ALLOWED)


@pytest.mark.parametrize("a,b", [("QUEUED", "EXECUTING"), ("DONE", "PLANNING"), ("PREPARING", "VERIFYING"),
                                 ("PLANNING", "DONE"), ("REPORTING", "PREPARING")])
def test_forbidden_transitions_raise_and_keep_the_status(a, b):
    state = {"status": a}
    with pytest.raises(InvalidTransition):
        advance(state, b)
    assert state["status"] == a


@pytest.mark.parametrize("status", [s for s in STATUSES if s not in TERMINAL])
def test_any_non_terminal_status_can_fail(status):
    assert advance({"status": status}, "FAILED")["status"] == "FAILED"


@pytest.mark.parametrize("a,b", list(itertools.product(sorted(TERMINAL), STATUSES)))
def test_nothing_leaves_a_terminal_status(a, b):
    with pytest.raises(InvalidTransition):
        advance({"status": a}, b)


# --- retry_change (pure) ---

def test_retry_change_decides_from_the_fields():
    plan = {"source": "planner", "cut_eids": ["e:2", "e:1"]}
    assert retry_change(verdict(), plan, []) == (None, "passed")
    assert retry_change(verdict(max_flow=0, reached=["db:x"]), plan, []) == (None, "invariant violated: proof mismatch")
    assert retry_change(verdict(max_flow=2, reached=["db:x"]), plan, [])[0] == {"type": "use_code_candidate", "exclude": ["e:1", "e:2"]}
    assert retry_change(verdict(max_flow=1, reached=["db:x"]), {"source": "code", "cut_eids": []}, []) == (
        None, "invariant violated: code min-cut left an attack path")
    assert retry_change(broken("need:b", "need:a"), plan, [])[0] == {"type": "protect_second_path", "needs": ["need:a", "need:b"]}


# --- after_verify ---

def test_pass_goes_to_reporting():
    state = at("VERIFYING")
    out = after_verify(state, verdict())
    assert (out["status"], out["attempt"]) == ("REPORTING", 1)
    assert out["history"] == [{"attempt": 1, "plan": state["plan"], "verdict": verdict()}]
    assert state["status"] == "VERIFYING" and state["attempt"] == 0 and state["history"] == []  # input untouched


def test_broken_need_retries_with_protect_second_path():
    out = after_verify(at("VERIFYING"), broken("need:b", "need:a"))
    change = {"type": "protect_second_path", "needs": ["need:a", "need:b"]}
    assert (out["status"], out["attempt"]) == ("PREPARING", 1)
    assert out["retry_change"] == change and out["tried_changes"] == [change]


def test_failed_planner_cut_retries_with_the_code_candidate():
    out = after_verify(at("VERIFYING", "planner", ("e:9", "e:3")), verdict(max_flow=1, reached=["db:j"]))
    assert out["status"] == "PREPARING"
    assert out["retry_change"] == {"type": "use_code_candidate", "exclude": ["e:3", "e:9"]}


def test_failed_code_cut_is_a_bug_not_a_retry():
    out = after_verify(at("VERIFYING", "code"), verdict(max_flow=1, reached=["db:j"]))
    assert out["status"] == "NEEDS_HUMAN" and out["failure_detail"].startswith("invariant violated")
    assert out["tried_changes"] == [] and out["retry_change"] is None


def test_proof_mismatch_is_a_bug_not_a_retry():
    out = after_verify(at("VERIFYING", "planner"), verdict(max_flow=0, reached=["db:j"]))
    assert (out["status"], out["failure_detail"]) == ("NEEDS_HUMAN", "invariant violated: proof mismatch")


def test_same_change_twice_ends_in_needs_human():
    state = after_verify(at("VERIFYING"), broken("need:a"))
    assert state["status"] == "PREPARING"
    state["status"] = "VERIFYING"  # the pipeline ran PREPARING..VERIFYING again
    out = after_verify(state, broken("need:a"))
    assert (out["status"], out["failure_detail"]) == ("NEEDS_HUMAN", "same change already tried")
    assert out["attempt"] == 2


def test_max_attempts_three_failures_with_new_changes_end_in_needs_human():
    assert config.MAX_ATTEMPTS == 3
    state = at("VERIFYING")
    for n, need in enumerate(["need:a", "need:b", "need:c"], start=1):
        state["status"] = "VERIFYING"
        state = after_verify(state, broken(need))
        assert state["attempt"] == n
        assert state["status"] == ("PREPARING" if n < 3 else "NEEDS_HUMAN")
    assert state["failure_detail"] == "text for humans"  # the verdict's own explanation: the attempts ran out
    assert len(state["history"]) == 3 and len(state["tried_changes"]) == 2


# --- after_invalid_plan ---

def test_invalid_plan_goes_back_to_planning_twice_then_needs_human():
    state = at("EXECUTING")
    for n in (1, 2):
        state["status"] = "EXECUTING"
        state = after_invalid_plan(state, "e:x does not exist")
        assert (state["status"], state["attempt"]) == ("PLANNING", n)
    state["status"] = "EXECUTING"
    state = after_invalid_plan(state, "e:x does not exist")
    assert (state["status"], state["attempt"]) == ("NEEDS_HUMAN", 3)
    assert "e:x does not exist" in state["failure_detail"]


def test_new_state_has_the_retry_fields():
    state = new_state("s2", "user:ravi", "fast")
    assert state["tried_changes"] == [] and state["retry_change"] is None

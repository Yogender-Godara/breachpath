"""Incident statuses, allowed transitions and the retry policy (docs/03_LLD.md section 7). Pure: no Redis, no graph."""
import copy

from core import config

STATUSES = ("QUEUED", "INVESTIGATING", "PREPARING", "PLANNING", "EXECUTING", "VERIFYING", "REPORTING",
            "DONE", "DONE_NO_RISK", "NEEDS_HUMAN", "FAILED")
TERMINAL = {"DONE", "DONE_NO_RISK", "NEEDS_HUMAN", "FAILED"}

TRANSITIONS = {
    "QUEUED": {"INVESTIGATING", "PREPARING", "DONE_NO_RISK"},
    "INVESTIGATING": {"PREPARING", "DONE_NO_RISK"},
    "PREPARING": {"PLANNING"},
    "PLANNING": {"EXECUTING", "NEEDS_HUMAN"},
    "EXECUTING": {"VERIFYING", "PLANNING", "NEEDS_HUMAN"},  # PLANNING = invalid plan; NEEDS_HUMAN = the 3rd invalid plan
    "VERIFYING": {"REPORTING", "PREPARING", "NEEDS_HUMAN"},  # PREPARING = retry with a changed input
    "REPORTING": {"DONE"},
}
for _status in STATUSES:
    TRANSITIONS.setdefault(_status, set())
    if _status not in TERMINAL:
        TRANSITIONS[_status].add("FAILED")  # any non-terminal status can fail


class InvalidTransition(Exception):
    pass


def advance(state, to):
    """Set state["status"] to `to` if the transition is allowed (mutates and returns the state)."""
    if to not in TRANSITIONS[state["status"]]:
        raise InvalidTransition(f"{state['status']} -> {to} is not allowed")
    state["status"] = to
    return state


def retry_change(verdict, plan, tried):
    """Return (change, reason). change is None when nothing can be retried. Decided from the verdict fields only:
    failure_detail is text for humans."""
    if verdict["pass"]:
        return None, "passed"
    if (verdict["attack_max_flow"] == 0) != (not verdict["bfs_reached_jewels"]):
        return None, "invariant violated: proof mismatch"
    if verdict["attack_max_flow"] > 0:  # attack left (the two proofs agree, so the reached list is non-empty too)
        if plan["source"] != "planner":  # a code min-cut must always block the attack: this is a bug
            return None, "invariant violated: code min-cut left an attack path"
        change = {"type": "use_code_candidate", "exclude": sorted(plan["cut_eids"])}
    else:
        broken = sorted(n["need_id"] for n in verdict["business"] if not n["ok"])
        change = {"type": "protect_second_path", "needs": broken}
    if change in tried:
        return None, "same change already tried"
    return change, "retry with a changed input"


def after_verify(state, verdict):
    """Return the state after the Verifier ran (the input is not modified)."""
    state = copy.deepcopy(state)
    state["attempt"] += 1
    state["history"].append({"attempt": state["attempt"], "plan": state["plan"], "verdict": verdict})
    if verdict["pass"]:
        return advance(state, "REPORTING")
    change, reason = retry_change(verdict, state["plan"], state["tried_changes"])
    if change and state["attempt"] < config.MAX_ATTEMPTS:
        state["tried_changes"].append(change)
        state["retry_change"] = change
        return advance(state, "PREPARING")
    # No change, or attempts used up: give the reason, or the verdict's own text when only the attempts ran out
    state["failure_detail"] = (reason if change is None else verdict["failure_detail"]) or "no change left"
    return advance(state, "NEEDS_HUMAN")


def after_invalid_plan(state, error):
    """An invalid plan counts as an attempt (LLD 7.3). `error` is kept as failure_detail if we give up."""
    state = copy.deepcopy(state)
    state["attempt"] += 1
    if state["attempt"] < config.MAX_ATTEMPTS:
        return advance(state, "PLANNING")
    state["failure_detail"] = f"invalid plan: {error}"
    return advance(state, "NEEDS_HUMAN")

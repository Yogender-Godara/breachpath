"""The ONE pipeline (docs/03_LLD.md section 7.3). Fast and agent mode share every step except the three "brains".

run_incident loops on state["status"]. Each step is a small function that does its work, moves the state with
state_machine, and returns the event to emit. Prepare, Execute and Verify are code in both modes.
"""
import time

from core import fast_brains
from core.graph import queries
from core.graph.client import ro_query
from core.graph.mincut import MinCutError
from core.graph.schema import sandbox_name
from core.tools import verify, write
from core.tools.common import unwrap
from orchestrator import events, state as state_store
from orchestrator.state_machine import InvalidTransition, TERMINAL, advance, after_invalid_plan, after_verify


def _brain(mode):
    if mode == "fast":
        return fast_brains
    if mode == "agent":
        from agents import brains  # imported here: fast mode (and the API) never imports the agents
        return brains  # the seam: same investigate/plan/report
    raise ValueError(f"unknown mode {mode!r}")


# --- shared steps: (state, auth) -> (agent, event type, message, data) ---

def _prepare(state, auth):
    """Reset the sandbox, copy the baseline into it, protect the business paths (LLD 6.5 steps 1-3)."""
    sandbox = sandbox_name(state["tenant"], state["id"])
    # every protect_second_path change so far, not only the last: a later retry must keep the earlier protections
    second = sorted({n for c in state["tried_changes"] if c["type"] == "protect_second_path" for n in c["needs"]})
    unwrap(write.delete_sandbox(sandbox, **auth))
    unwrap(write.create_sandbox(state["tenant"], **auth))
    protected = unwrap(write.protect_business_paths(sandbox, second or None, **auth))
    state["sandbox_graph"] = sandbox
    state["action"] = {"sandbox": sandbox, "protected_eids": protected["protected_eids"], "applied_eids": [],
                       "missing": [], "refused_protected": []}
    advance(state, "PLANNING")
    return "executor", "step", f"sandbox {sandbox} ready, {len(protected['protected_eids'])} business edges protected", {
        "second_path_needs": second}


def _execute(state, auth):
    """Validate the plan (every eid exists and is not protected), then apply it (LLD 6.5 steps 4-5)."""
    sandbox, eids = state["sandbox_graph"], state["plan"]["cut_eids"]
    problems = []
    for eid, found, protected, _ in ro_query(sandbox, queries.EDGE_STATE, {"eids": sorted(set(eids))}):
        if not found:
            problems.append(f"{eid} does not exist")
        elif protected:
            problems.append(f"{eid} is protected")
    if not eids or problems:
        error = "; ".join(problems) or "the plan has no edges"
        after = after_invalid_plan(state, error)
        state.update(after)
        return "executor", "step", f"invalid plan: {error}", {"status": state["status"]}
    done = unwrap(write.apply_fix(sandbox, eids, state["plan"]["notes"] or "min-cut", **auth))
    state["action"].update(applied_eids=done["deleted"], missing=done["missing"], refused_protected=done["refused_protected"])
    advance(state, "VERIFYING")
    return "executor", "action", f"removed {len(done['deleted'])} edge(s)", state["action"]


def _verify(state, auth):
    verdict = unwrap(verify.verify_fix(state["sandbox_graph"], state["src"]))
    state["verdict"] = verdict
    state.update(after_verify(state, verdict))
    return "verifier", "verdict", f"{'pass' if verdict['pass'] else 'fail'}: {verdict['failure_detail'] or 'all proofs hold'}", {
        "status": state["status"], "attempt": state["attempt"]}


# --- brain steps ---

def _investigate(state, brain, auth):
    if state["status"] == "QUEUED":
        advance(state, "INVESTIGATING")
    state["findings"] = brain.investigate(state)
    jewels = state["findings"]["reachable_jewels"]
    advance(state, "PREPARING" if jewels else "DONE_NO_RISK")
    return "investigator", "finding", f"{len(jewels)} crown jewel(s) reachable, {len(state['findings']['paths'])} path(s)", {
        "reachable_jewels": jewels}


def _plan(state, brain, auth):
    state["plan"] = brain.plan(state, lock_token=auth["lock_token"])
    plan = state["plan"]
    if not plan["safe"]:  # no safe cut: the suggestion is in the plan, a person decides
        state["failure_detail"] = f"no safe fix: {plan['suggestion']['text']}"
        advance(state, "NEEDS_HUMAN")
    else:
        advance(state, "EXECUTING")
    return "planner", "plan", f"{plan['chosen'] or 'no safe cut'}: {plan['cut_eids']}", {"safe": plan["safe"]}


def _report(state, brain):
    state["report"] = brain.report(state)
    if state["status"] == "REPORTING":
        advance(state, "DONE")  # DONE_NO_RISK and NEEDS_HUMAN keep their status
    return "reporter", "report", f"report written ({len(state['report']['markdown'])} chars)", {"status": state["status"]}


def run_incident(state, mode, *, lock_token, sink=None, lock=None):
    """Run the incident to a final status and return the state. Never raises for a failed step: the status says what happened.
    lock: the incident's lock handle (agent mode). If its heartbeat lost it between two steps, the run stops with FAILED (LLD section 8)."""
    brain = _brain(mode)
    if mode == "fast" and sink is None:
        sink = []  # fast mode keeps events in memory
    auth = {"incident_id": state["id"], "lock_token": lock_token}
    steps = {"QUEUED": lambda: _investigate(state, brain, auth), "INVESTIGATING": lambda: _investigate(state, brain, auth),
             "PREPARING": lambda: _prepare(state, auth),
             "PLANNING": lambda: _plan(state, brain, auth), "EXECUTING": lambda: _execute(state, auth),
             "VERIFYING": lambda: _verify(state, auth), "REPORTING": lambda: _report(state, brain)}

    def checkpoint(agent, type_, msg, data, started):
        events.emit(state["id"], agent, type_, msg, data, latency_ms=round((time.perf_counter() - started) * 1000), sink=sink)
        if mode == "agent":  # fast mode never touches Redis for the state
            state_store.save(state)

    while True:
        status = state["status"]
        try:
            if status == "FAILED":
                break
            if status in TERMINAL:  # DONE_NO_RISK and NEEDS_HUMAN get a report too; DONE already has one
                if state["report"] is None:
                    started = time.perf_counter()
                    checkpoint(*_report(state, brain), started)
                break
            if lock is not None and lock.lost.is_set():  # another holder may own the incident now: stop (writes were guarded anyway)
                state["failure_detail"] = "lock lost"
                advance(state, "FAILED")
                checkpoint("orchestrator", "error", "lock lost", {"status": "FAILED"}, time.perf_counter())
                break
            started = time.perf_counter()
            checkpoint(*steps[status](), started)
        except MinCutError as e:  # an invariant broke: never a blind retry (CLAUDE.md)
            state["failure_detail"] = f"invariant violated: {e}"
            try:
                advance(state, "NEEDS_HUMAN")
            except InvalidTransition:
                advance(state, "FAILED")
            checkpoint("orchestrator", "error", state["failure_detail"], {"status": state["status"]}, time.perf_counter())
        except Exception as e:  # any other failure: FAILED plus an error event; the caller reads the state
            state["failure_detail"] = f"{type(e).__name__}: {e}"
            if state["status"] not in TERMINAL:
                advance(state, "FAILED")
            checkpoint("orchestrator", "error", state["failure_detail"], {"status": state["status"]}, time.perf_counter())
            break
    return state

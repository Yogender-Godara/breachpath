"""The Planner agent (docs/06 section 3, docs/03_LLD.md 4.2 and 16): the LLM chooses among SIMULATED cuts, the CODE verifies the choice.

- The code computes the min-cut. Not safe -> no LLM call at all: the split suggestion is proven in code, the LLM adds nothing.
- Safe -> the LLM chooses. Its cut is accepted ONLY if a simulate_cut result for exactly those eids says blocks_attack and needs_ok
  (and the cut has no protected or missing edge). Otherwise the code ranking decides and a planner_choice_rejected event says why.
- simulate_cut is limited to 6 per incident: no set is ever simulated twice (sims = every result seen; a repeated call is answered
  from it, also after a provider restart).
The plan has EXACTLY the keys of core/fast_brains.plan.
"""
import json
from pathlib import Path
from types import SimpleNamespace

from agents.base import AgentSpec, run_agent
from agents.schemas import SUBMIT_TOOLS, FixPlanOut
from core import fast_brains
from core.graph import queries
from core.graph.client import ro_query
from core.tools import read, write
from core.tools.common import ToolFailed, unwrap

SPEC = AgentSpec(
    name="planner", system=(Path(__file__).parent / "prompts" / "planner.md").read_text(encoding="utf-8").strip(),
    allow_list=["compute_min_cut", "simulate_cut", "edge_path_frequency", "choke_points", "get_edge"],
    submit=SUBMIT_TOOLS["submit_fix_plan"], output_model=FixPlanOut, max_turns=8)


def sim_key(eids):
    return tuple(sorted(set(eids)))


class SeenSims:
    """The MCP session as the loop sees it, except that a simulate_cut for a set already simulated is answered from `sims`.
    (Sandbox and src are always the injected ones, so the eids are the whole key.)"""

    def __init__(self, session, sims):
        self.session, self.sims = session, sims

    async def list_tools(self):
        return await self.session.list_tools()

    async def call_tool(self, name, args):
        if name == "simulate_cut" and sim_key(args["eids"]) in self.sims:
            text = json.dumps({"ok": True, "data": self.sims[sim_key(args["eids"])]})  # the loop makes it compact and sorted
            return SimpleNamespace(is_error=False, content=[SimpleNamespace(text=text)])
        return await self.session.call_tool(name, args)


def _emit(emit, type_, msg, data):
    if emit:
        emit(type_, msg, data, latency_ms=None, tokens_in=None, tokens_out=None)


def _task(state, max_flow):
    task = f"The compromised identity is {state['src']}.\nmax_flow (the minimum cut size): {max_flow:g}"  # no sandbox name: it is injected
    if state["tried_changes"]:  # a retry: what failed and what was already tried (docs/06 section 3)
        failure = (state.get("verdict") or {}).get("failure_detail") or state.get("failure_detail")
        task += f"\nThis is a retry. failure_detail: {failure}\nchanges already tried: {json.dumps(state['tried_changes'], sort_keys=True)}"
    return task


def _verdict_on(sandbox, src, key, sims, auth):
    """None if the cut may be accepted, else the reason it is rejected. Simulates it ONCE if the LLM never did."""
    if not key:
        return "the cut is empty although a safe cut exists"
    if key not in sims:
        try:
            sims[key] = unwrap(write.simulate_cut(sandbox, src, list(key), **auth))
        except ToolFailed as e:
            return f"the cut could not be simulated: {e}"
    sim = sims[key]
    if sim["refused_protected"] or sim["missing"]:
        return f"the cut has protected or missing edges: {sim['refused_protected'] + sim['missing']}"
    if not (sim["blocks_attack"] and sim["needs_ok"]):
        return f"simulation says blocks_attack={sim['blocks_attack']}, needs_ok={sim['needs_ok']} (broken needs: {sim['broken_needs']})"
    return None


def _plan_event(emit, plan_, accepted, llm, result, sims):
    sim = plan_["simulation"]
    _emit(emit, "plan", f"{plan_['source']} plan: {plan_['cut_eids']}", {
        "chosen": plan_["chosen"], "cut_eids": plan_["cut_eids"], "source": plan_["source"], "llm_choice": list(llm.cut_eids) if llm else None,
        "llm_chosen": llm.chosen if llm else None,
        "accepted": accepted, "collateral": sim["collateral"]["count"] if sim else None, "sims_used": len(sims),
        "provider": result.provider, "turns": result.turns})


async def plan(session, state, *, lock_token, emit=None):
    """-> (plan dict like fast_brains.plan, degraded). degraded=True means the LLM run failed and the code ranking was used."""
    sandbox, src = state["sandbox_graph"], state["src"]
    auth = {"incident_id": state["id"], "lock_token": lock_token}
    cut = unwrap(read.compute_min_cut(sandbox, src))  # MinCutError is not caught: the pipeline turns it into NEEDS_HUMAN
    if not cut["safe"] or any(c["type"] == "use_code_candidate" for c in state["tried_changes"]):
        return fast_brains.plan(state, lock_token=lock_token), False  # no LLM call: unsafe, or a Planner cut already failed once

    sims = {}

    def capture(name, args, result):
        if name == "simulate_cut":
            sims[sim_key(args["eids"])] = result["data"]

    inject = {"graph": sandbox, "sandbox": sandbox, "src": src, **auth}  # what the code knows: the LLM never sees or writes it
    result = await run_agent(SeenSims(session, sims), SPEC, _task(state, cut["max_flow"]), inject=inject, emit=emit, on_tool_result=capture)
    if result.degraded:
        _emit(emit, "step", f"Planner LLM unavailable ({result.reason}): the code ranking decides", {"reason": result.reason})
        plan_ = fast_brains.plan(state, lock_token=lock_token, known_sims=sims)
        _plan_event(emit, plan_, False, None, result, sims)
        return plan_, True

    llm = result.output
    key = sim_key(llm.cut_eids)
    reason = _verdict_on(sandbox, src, key, sims, auth)
    if reason:
        _emit(emit, "step", f"planner_choice_rejected: {reason}", {"event": "planner_choice_rejected", "cut_eids": list(llm.cut_eids), "reason": reason})
        plan_ = fast_brains.plan(state, lock_token=lock_token, known_sims=sims)
        _plan_event(emit, plan_, False, llm, result, sims)
        return plan_, False

    max_flow = int(cut["max_flow"])
    info = {r[0]: (r[1], r[2], r[4]) for r in ro_query(sandbox, queries.ALL_EDGES)}
    witness = {e: w for c in cut["candidates"] for e, w in c["witness"].items() if e in key}  # a custom edge may have none
    why = {r.eid: r.why for r in llm.reasons}
    reasons = []
    for e in key:
        code_why = fast_brains._reason(info, e, witness)["why"]
        reasons.append({"eid": e, "why": code_why + (f" Planner: {why[e]}" if why.get(e) else "")})
    names = [c["name"] for c in cut["candidates"] if sim_key(c["eids"]) == key]
    chosen = names[0] if names else "custom"  # the code sets the label: the cut equals a min-cut candidate, or it is the Planner's own
    plan_ = {"cut_eids": list(key), "chosen": chosen, "source": "planner", "reasons": reasons, "safe": True, "max_flow": max_flow,
             "witness": {e: witness.get(e, []) for e in key}, "simulation": sims[key], "suggestion": None,
             "notes": f"{llm.notes.strip()} size {len(key)} vs minimum {max_flow}.".strip()}
    _plan_event(emit, plan_, True, llm, result, sims)
    return plan_, False

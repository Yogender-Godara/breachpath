"""The Planner agent (docs/06 section 3, docs/03_LLD.md 4.2 and 16): the LLM chooses among SIMULATED cuts, the CODE verifies the choice.

- The code computes the min-cut. Not safe -> no LLM call at all: the split suggestion is proven in code, the LLM adds nothing.
- Safe -> the LLM chooses. Its cut is accepted ONLY if a simulate_cut result for exactly those eids says blocks_attack and needs_ok
  (and the cut has no protected or missing edge). Otherwise the code ranking decides and a planner_choice_rejected event says why.
- No operator instruction and only ONE verified option (the two candidates are the same set, or only one passes simulate_cut): the LLM
  cannot choose anything, so it is not called ("planner skipped": the code plan, 0 LLM calls).
- simulate_cut is limited to 6 per incident: no set is ever simulated twice (sims = every result seen; a repeated call is answered
  from it, also after a provider restart). The LLM may use 4 of the 6: 2 are reserved for the code (core/tools/guards.check_quota).
- An instruction that the constrained compute_min_cut proved impossible (safe=false for the avoided edges) is not retried: the code cut, with the note.
The plan has EXACTLY the keys of core/fast_brains.plan.
"""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from agents.base import AgentSpec, note_degraded, run_agent
from agents.schemas import SUBMIT_TOOLS, FixPlanOut
from core import fast_brains
from core.graph import queries
from core.graph.client import ro_query
from core.tools import read, write
from core.tools.common import ToolFailed, unwrap

SPEC = AgentSpec(
    name="planner", system=(Path(__file__).parent / "prompts" / "planner.md").read_text(encoding="utf-8").strip(),
    allow_list=["compute_min_cut", "simulate_cut", "edge_path_frequency", "choke_points", "get_edge"],
    submit=SUBMIT_TOOLS["submit_fix_plan"], output_model=FixPlanOut, max_turns=6)


INSTRUCTION_PROMPT = (Path(__file__).parent / "prompts" / "planner_instructions.md").read_text(encoding="utf-8").strip()


INSTRUCTION_MAX_TURNS = 8  # mapping a free-text instruction to edges takes extra turns (get_node / get_edge): 6 left no room to submit (P9.5)


def _spec_for(state):
    """The operator-instruction block is added to the system prompt ONLY when there is an instruction, so a normal run keeps its LLM cache keys.
    max_turns is not part of the messages, so the longer limit does not change any cache key either."""
    if not state.get("instructions"):
        return SPEC
    return replace(SPEC, system=f"{SPEC.system}\n\n{INSTRUCTION_PROMPT}", max_turns=INSTRUCTION_MAX_TURNS)


def sim_key(eids):
    return tuple(sorted(set(eids)))


class SeenSims:
    """The MCP session as the loop sees it, except that a simulate_cut for a set already simulated is answered from `sims`.
    (Sandbox and src are always the injected ones, so the eids are the whole key.)"""

    def __init__(self, session, sims, constrained=None):
        self.session, self.sims = session, sims
        self.constrained = constrained if constrained is not None else {}  # {sorted avoid eids: compute_min_cut result}: one guarded copy each

    async def list_tools(self):
        return await self.session.list_tools()

    async def call_tool(self, name, args):
        if name == "simulate_cut" and sim_key(args["eids"]) in self.sims:
            text = json.dumps({"ok": True, "data": self.sims[sim_key(args["eids"])]})  # the loop makes it compact and sorted
            return SimpleNamespace(is_error=False, content=[SimpleNamespace(text=text)])
        if name == "compute_min_cut" and args.get("avoid_eids") and tuple(sorted(set(args["avoid_eids"]))) in self.constrained:
            text = json.dumps({"ok": True, "data": self.constrained[tuple(sorted(set(args["avoid_eids"])))]})  # a repeat (a retry) costs no 2nd copy
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
    if state.get("instructions"):
        task += f"\nOperator instructions (free text from the person in charge): {json.dumps(state['instructions'])}"
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
    """result: the AgentResult, or None when the LLM was never called (planner skipped)."""
    sim = plan_["simulation"]
    _emit(emit, "plan", f"{plan_['source']} plan: {plan_['cut_eids']}", {
        "chosen": plan_["chosen"], "cut_eids": plan_["cut_eids"], "source": plan_["source"], "llm_choice": list(llm.cut_eids) if llm else None,
        "llm_chosen": llm.chosen if llm else None,
        "accepted": accepted, "collateral": sim["collateral"]["count"] if sim else None, "sims_used": len(sims),
        "provider": result.provider if result else None, "turns": result.turns if result else 0})


def _one_verified_option(sandbox, src, cut, sims, auth):
    """True when the LLM could choose nothing: the min-cut candidates are ONE set, or only one of the sets passes simulate_cut
    (blocks_attack and needs_ok). Simulates each distinct set once, into `sims` (the code plan and the LLM reuse them). A tool failure
    (e.g. the quota) is not 'one option': the LLM path runs as before."""
    for key in sorted({sim_key(c["eids"]) for c in cut["candidates"]}):
        try:
            sims[key] = unwrap(write.simulate_cut(sandbox, src, list(key), **auth))
        except ToolFailed:
            return False
    return sum(bool(s["blocks_attack"] and s["needs_ok"]) for s in sims.values()) == 1


def _proved_impossible(llm, constrained, instructions, info):
    """True when the constrained compute_min_cut for exactly the edges the LLM says it avoided came back safe=false: no cut keeps them
    untouched (proven by the tool, not by the model). Only counts when the avoid list itself holds up (real eids, real quotes)."""
    if not llm.avoid or _instruction_problem(llm, instructions, info, ()):
        return False
    result = constrained.get(tuple(sorted({a.eid for a in llm.avoid})))
    return bool(result) and not result["safe"]


def _instruction_problem(llm, instructions, known, key):
    """None if the Planner's `avoid` list holds up, else why not: every avoid eid exists, every quote is in the instruction, and the cut
    contains none of the avoided edges."""
    problems = []
    for a in llm.avoid:
        if a.eid not in known:
            problems.append(f"avoid eid {a.eid} does not exist")
        elif not a.quote or a.quote not in instructions:
            problems.append(f"the quote {a.quote!r} for {a.eid} is not a substring of the operator instruction")
    touched = sorted({a.eid for a in llm.avoid} & set(key))
    if touched:
        problems.append(f"the cut contains the avoided edge(s) {touched}")
    return "; ".join(problems) or None


def _with_instruction(plan_, instructions, honoured, why):
    """Say what happened to the operator instruction, in the plan itself (never silently ignored). No instruction: the plan is unchanged."""
    if not instructions:
        return plan_
    plan_["instruction"] = {"honoured": honoured, "note": why}
    if not honoured:
        plan_["notes"] = f"Instruction could not be honoured: {why}. {plan_['notes']}".strip()
    return plan_


def _impossible_hint(constrained):
    """When the constraint made a safe cut impossible, the tool said which avoided edges: keep that for the note."""
    for result in constrained.values():
        if not result["safe"] and result.get("note"):
            return f" ({result['note']})"
    return ""


def _default_fix(state, lock_token, sims):
    """What the code would have chosen WITHOUT the instruction (fast_brains.plan: unconstrained candidates, collateral first). None if it cannot be computed."""
    try:
        default = fast_brains.plan(state, lock_token=lock_token, known_sims=sims)
    except ToolFailed:  # e.g. the 6 simulations of this incident are used up
        return None, {}
    if not default["safe"] or not default["simulation"]:
        return None, {}
    sim = default["simulation"]
    known = {sim_key(default["cut_eids"]): sim}
    known.update({sim_key(a["cut_eids"]): a for a in default["alternatives"]})  # an alternative record has the fields alternative() reads
    return {"cut_eids": default["cut_eids"], "collateral": {"count": sim["collateral"]["count"], "weighted": sim["collateral"]["weighted"]},
            "size": sim["size"]}, known


async def plan(session, state, *, lock_token, emit=None):
    """-> (plan dict like fast_brains.plan, degraded). degraded=True means the LLM run failed and the code ranking was used."""
    sandbox, src = state["sandbox_graph"], state["src"]
    auth = {"incident_id": state["id"], "lock_token": lock_token}
    instructions = state.get("instructions") or ""
    cut = unwrap(read.compute_min_cut(sandbox, src))  # MinCutError is not caught: the pipeline turns it into NEEDS_HUMAN
    if not cut["safe"]:
        return _with_instruction(fast_brains.plan(state, lock_token=lock_token), instructions, False, "no safe cut exists even without the instruction"), False
    if any(c["type"] == "use_code_candidate" for c in state["tried_changes"]):  # a Planner cut already failed verification: the code decides
        return _with_instruction(fast_brains.plan(state, lock_token=lock_token), instructions, False, "a cut chosen under the instruction failed verification"), False

    sims, constrained = {}, {}
    if not instructions and _one_verified_option(sandbox, src, cut, sims, auth):  # nothing to choose: the LLM would add nothing
        plan_ = fast_brains.plan(state, lock_token=lock_token, known_sims=sims)
        _emit(emit, "step", "planner skipped: one verified option", {"event": "planner_skipped", "cut_eids": plan_["cut_eids"], "sims_used": len(sims)})
        _plan_event(emit, plan_, None, None, None, sims)
        return plan_, False

    def capture(name, args, result):
        if name == "simulate_cut":
            sims[sim_key(args["eids"])] = result["data"]
        elif name == "compute_min_cut" and args.get("avoid_eids"):
            constrained[tuple(sorted(set(args["avoid_eids"])))] = result["data"]

    inject = {"graph": sandbox, "sandbox": sandbox, "src": src, **auth}  # what the code knows: the LLM never sees or writes it
    info = {r[0]: (r[1], r[2], r[4]) for r in ro_query(sandbox, queries.ALL_EDGES)}
    problem = ""
    for attempt in (1, 2) if instructions else (1,):  # with an instruction, ONE retry with the problem; without, the code ranking decides at once
        task = _task(state, cut["max_flow"]) + (f"\nYour previous answer was rejected: {problem}. Fix exactly that and submit again." if problem else "")
        result = await run_agent(SeenSims(session, sims, constrained), _spec_for(state), task, inject=inject, emit=emit, on_tool_result=capture, state=state)
        if result.degraded:
            _emit(emit, "step", f"Planner LLM unavailable ({result.reason}): the code ranking decides", {"reason": result.reason})
            note_degraded(state, "planner", result.reason)
            plan_ = _with_instruction(fast_brains.plan(state, lock_token=lock_token, known_sims=sims), instructions, False, "the LLM was unavailable")
            _plan_event(emit, plan_, False, None, result, sims)
            return plan_, True
        llm = result.output
        key = sim_key(llm.cut_eids)
        if instructions and _proved_impossible(llm, constrained, instructions, info):  # the tool proved it: a retry could only repeat it
            problem = "the instruction rules out every safe cut"
            _emit(emit, "step", f"planner_choice_rejected: {problem}", {"event": "planner_choice_rejected", "cut_eids": list(llm.cut_eids), "reason": problem})
            break
        problem = (_instruction_problem(llm, instructions, info, key) if instructions else None) or _verdict_on(sandbox, src, key, sims, auth)
        if not problem:
            break
        _emit(emit, "step", f"planner_choice_rejected: {problem}", {"event": "planner_choice_rejected", "cut_eids": list(llm.cut_eids), "reason": problem})
    if problem:
        plan_ = fast_brains.plan(state, lock_token=lock_token, known_sims=sims)
        _with_instruction(plan_, instructions, False, problem + _impossible_hint(constrained))
        _plan_event(emit, plan_, False, llm, result, sims)
        return plan_, False

    max_flow = int(cut["max_flow"])
    candidates = [*cut["candidates"], *(c for r in constrained.values() for c in r["candidates"])]
    witness = {e: w for c in candidates for e, w in c["witness"].items() if e in key}  # a custom edge may have none
    why = {r.eid: r.why for r in llm.reasons}
    reasons = []
    for e in key:
        code_why = fast_brains._reason(info, e, witness)["why"]
        reasons.append({"eid": e, "why": code_why + (f" Planner: {why[e]}" if why.get(e) else "")})
    names = {sim_key(c["eids"]): c["name"] for c in candidates}
    chosen = names.get(key, "custom")  # the code sets the label: the cut equals a min-cut candidate, or it is the Planner's own
    avoid = [{"eid": a.eid, "quote": a.quote} for a in llm.avoid] if instructions else []
    default, default_sims = _default_fix(state, lock_token, sims) if avoid else (None, {})
    alternatives = fast_brains.alternatives_ranked({**default_sims, **sims}, key, names)
    for alt in alternatives if avoid else []:
        alt["touches_avoided"] = sorted(set(alt["cut_eids"]) & {a["eid"] for a in avoid})  # why an alternative was ruled out by the instruction
    plan_ = {"cut_eids": list(key), "chosen": chosen, "source": "planner", "reasons": reasons, "safe": True, "max_flow": max_flow,
             "witness": {e: witness.get(e, []) for e in key}, "simulation": sims[key], "suggestion": None, "alternatives": alternatives,
             "avoid": avoid, "instruction": None, "default_fix": default if default and default["cut_eids"] != list(key) else None,
             "notes": f"{llm.notes.strip()} size {len(key)} vs minimum {max_flow}.".strip()}
    _with_instruction(plan_, instructions, True, f"avoided {', '.join(a['eid'] for a in avoid)}" if avoid else "the instruction rules out no edge")
    _plan_event(emit, plan_, True, llm, result, sims)
    return plan_, False

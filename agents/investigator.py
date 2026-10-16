"""The Investigator agent (docs/06 section 2, docs/03_LLD.md 6.2): the LLM finds the attack paths, the CODE decides.

Graph guarantees, LLM chooses: reachable_jewels comes from the code BFS (the pipeline picks DONE_NO_RISK from it); the LLM's own
list is only compared with it. Every path the LLM submits is checked against the baseline and enriched in code. The result has
EXACTLY the keys of core/fast_brains.investigate, so the pipeline, the report and the API do not change.
"""
from pathlib import Path

from agents.base import AgentSpec, note_degraded, run_agent
from agents.schemas import SUBMIT_TOOLS, FindingsOut
from core import fast_brains
from core.graph import queries
from core.graph.client import ro_query

SPEC = AgentSpec(
    name="investigator", system=(Path(__file__).parent / "prompts" / "investigator.md").read_text(encoding="utf-8").strip(),
    allow_list=["get_blast_radius", "find_attack_paths", "shortest_paths", "edge_path_frequency", "get_node", "get_edge", "run_readonly_cypher"],
    submit=SUBMIT_TOOLS["submit_findings"], output_model=FindingsOut, max_turns=8)


def check_paths(paths, src, edges, jewels):
    """Pure. paths: the LLM's [{target, eids}]. edges: {eid: (u, v)}. -> (valid paths in fast-mode format, [drop reasons])."""
    valid, dropped, seen = [], [], set()
    for p in paths:
        eids = list(p.eids)
        if not eids:
            reason = "no edges"
        elif unknown := [e for e in eids if e not in edges]:
            reason = f"unknown eid {unknown[0]}"
        elif edges[eids[0]][0] != src:
            reason = f"does not start at {src}"
        elif any(edges[a][1] != edges[b][0] for a, b in zip(eids, eids[1:])):
            reason = "edges do not form a connected chain"
        elif edges[eids[-1]][1] != p.target:
            reason = f"does not end at its target {p.target}"
        elif p.target not in jewels:
            reason = f"{p.target} is not a crown jewel"
        elif tuple(eids) in seen:
            reason = "duplicate path"
        else:
            seen.add(tuple(eids))
            valid.append({"target": p.target, "node_ids": [src] + [edges[e][1] for e in eids], "eids": eids, "hops": len(eids)})
            continue
        dropped.append(f"{p.target} via {eids}: {reason}")
    return valid, dropped


def _notes(llm, code_jewels):
    notes = llm.notes.strip()
    missed, extra = sorted(set(code_jewels) - set(llm.reachable_jewels)), sorted(set(llm.reachable_jewels) - set(code_jewels))
    if missed:
        notes += f" LLM missed: {', '.join(missed)}."
    if extra:
        notes += f" LLM listed jewels the BFS does not reach: {', '.join(extra)}."
    return notes.strip()


def _note(emit, msg, data):
    if emit:
        emit("finding", msg, data, latency_ms=None, tokens_in=None, tokens_out=None)


async def investigate(session, state, emit=None):
    """-> (findings dict like fast_brains.investigate, degraded). degraded=True means the fast brain's findings were used."""
    graph, src = state["baseline_graph"], state["src"]
    facts = fast_brains.code_facts(graph, src)  # decided by code
    edges = {r[0]: (r[1], r[2]) for r in ro_query(graph, queries.ALL_EDGES)}
    jewels = {r[0] for r in ro_query(graph, queries.JEWELS)}
    within_hops = set(facts["reachable_jewels"]) - set(facts["beyond_hops_jewels"])  # none (e.g. S7): zero paths is CORRECT
    task = f"The compromised identity is {src}.\nfind_attack_paths k is 1-50 (default 20)."  # no graph name: it is injected, so the messages never depend on the incident

    problems = ""
    for attempt in (1, 2):  # the second one only after a failed first one
        result = await run_agent(session, SPEC, task + problems, inject={"graph": graph, "src": src}, emit=emit, state=state)
        valid, dropped = ([], []) if result.degraded else check_paths(result.output.paths, src, edges, jewels)
        for reason in dropped:
            _note(emit, f"dropped path: {reason}", {"dropped": reason, "attempt": attempt})
        if not result.degraded and (valid or not within_hops):
            break
        reasons = result.reason if result.degraded else "; ".join(dropped[:3]) or "no paths were submitted"
        problems = f"\nYour previous paths were invalid: {reasons[:300]}. Copy paths exactly from tool results."
    else:  # nothing usable twice: the fast brain answers, and the incident is marked degraded
        _note(emit, "falling back to the fast investigation", {"reason": reasons, "provider": result.provider})
        note_degraded(state, "investigator", result.reason if result.degraded else "no valid path after a retry")
        return fast_brains.investigate(state), True

    llm = result.output
    counts = {}
    for p in valid:
        counts[p["target"]] = counts.get(p["target"], 0) + 1
    _note(emit, f"{len(facts['reachable_jewels'])} crown jewel(s) by BFS, {len(valid)} valid path(s)", {
        "llm_jewels": sorted(llm.reachable_jewels), "code_jewels": facts["reachable_jewels"], "valid_paths": len(valid),
        "dropped_paths": len(dropped), "provider": result.provider, "turns": result.turns, "llm_calls": result.llm_calls})
    return {"reachable_jewels": facts["reachable_jewels"], "paths": valid, "truncated": llm.truncated,
            "notes": _notes(llm, facts["reachable_jewels"]), "path_count_by_target": counts, "top_edges": facts["top_edges"],
            "beyond_hops_jewels": facts["beyond_hops_jewels"]}, False

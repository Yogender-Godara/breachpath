"""Fast brains (docs/03_LLD.md section 7.3): Investigate, Plan and Report in plain code, no LLM.

The agent brains (P6) have the same three entry points, so core/pipeline.py never needs to know which one it calls.
Facts in the report come only from the incident state.
"""
from core import config
from core.graph import queries
from core.graph.client import ro_query
from core.graph.schema import EDGE_TYPES
from core.tools import read, split, verify, write
from core.tools.common import unwrap


# --- Investigate (reads the baseline) ---

def code_facts(graph, src):
    """What the code decides, shared by the fast and the agent Investigator: the BFS jewels (unbounded: finds 8-hop paths too),
    the jewels only reachable beyond MAX_HOPS, and the edges most paths share."""
    jewels = verify.reached_jewels(graph, src)
    if not jewels:
        return {"reachable_jewels": [], "beyond_hops_jewels": [], "top_edges": []}
    within_hops = {j["id"] for j in unwrap(read.get_blast_radius(graph, src))}
    return {"reachable_jewels": jewels, "beyond_hops_jewels": sorted(set(jewels) - within_hops),
            "top_edges": unwrap(read.edge_path_frequency(graph, src, top=10))}


def investigate(state):
    graph, src = state["baseline_graph"], state["src"]
    facts = code_facts(graph, src)
    jewels, beyond = facts["reachable_jewels"], facts["beyond_hops_jewels"]
    if not jewels:
        return {"reachable_jewels": [], "paths": [], "truncated": False, "notes": f"{src} reaches no crown jewel.",
                "path_count_by_target": {}, "top_edges": [], "beyond_hops_jewels": []}
    found = unwrap(read.find_attack_paths(graph, src, k=config.TOP_K_PATHS))
    counts = {}
    for p in found["paths"]:
        counts[p["target"]] = counts.get(p["target"], 0) + 1
    notes = f"{len(found['paths'])} attack path(s) shown" + (" (truncated)" if found["truncated"] else "") + "."
    if beyond:
        notes += f" {', '.join(beyond)} only reachable by a path longer than {config.MAX_HOPS} hops: the min-cut still covers it."
    return {"reachable_jewels": jewels, "paths": found["paths"], "truncated": found["truncated"], "notes": notes,
            "path_count_by_target": counts, "top_edges": facts["top_edges"], "beyond_hops_jewels": beyond}


# --- Plan (reads and simulates on the prepared sandbox) ---

def _reason(info, eid, witness):
    u, v, kind = info[eid]
    host = " [host action]" if EDGE_TYPES[kind].get("host_action") else ""
    on_paths = f"; on every attack path, e.g. {', '.join(witness[eid])}" if witness.get(eid) else ""  # a custom cut edge has no witness
    return {"eid": eid, "why": f"{EDGE_TYPES[kind]['remediation']}: {u} → {v}{host}{on_paths}"}


def alternative(eids, sim, name=None):
    """A simulated cut that was NOT chosen, as the plan records it. Filled in code from the simulate_cut result: the LLM never writes it."""
    return {"cut_eids": list(eids), "name": name, "blocks_attack": sim["blocks_attack"], "needs_ok": sim["needs_ok"],
            "collateral": {"count": sim["collateral"]["count"], "weighted": sim["collateral"]["weighted"]},
            "size": sim["size"], "stale_days_total": sim["stale_days_total"]}


def alternatives_ranked(sims, chosen, names=None):
    """Every simulated cut except `chosen` (a sorted eid tuple), best first by the same ranking as the choice."""
    names = names or {}
    rows = [alternative(k, s, names.get(k, "custom")) for k, s in sims.items() if k != chosen]
    return sorted(rows, key=lambda a: (not (a["blocks_attack"] and a["needs_ok"]), a["collateral"]["weighted"], a["size"], -a["stale_days_total"], a["cut_eids"]))


def plan(state, *, lock_token, known_sims=None):
    """known_sims = {tuple(sorted(eids)): simulate_cut data} already seen in this incident: never simulated twice (6 per incident)."""
    sandbox, src = state["sandbox_graph"], state["src"]
    auth = {"incident_id": state["id"], "lock_token": lock_token}
    cut = unwrap(read.compute_min_cut(sandbox, src))  # MinCutError is not caught here: the pipeline turns it into NEEDS_HUMAN
    max_flow = int(cut["max_flow"])
    base = {"cut_eids": [], "chosen": None, "source": "code", "reasons": [], "notes": "", "safe": cut["safe"],
            "max_flow": max_flow, "witness": {}, "simulation": None, "suggestion": None, "alternatives": [],
            "avoid": [], "instruction": None, "default_fix": None}  # the last three: operator instructions (agent mode only, agents/planner.py)
    if not cut["safe"]:
        base["suggestion"] = unwrap(split.split_suggestion(sandbox, src, **auth))
        base["notes"] = f"No safe cut: max flow {max_flow} means an attack path made only of business-critical edges."
        return base

    info = {r[0]: (r[1], r[2], r[4]) for r in ro_query(sandbox, queries.ALL_EDGES)}
    known = known_sims or {}
    excluded = {tuple(sorted(ch["exclude"])) for ch in state["tried_changes"] if ch["type"] == "use_code_candidate"}  # failed Planner cuts
    options = [c for c in cut["candidates"] if tuple(sorted(c["eids"])) not in excluded] or cut["candidates"]
    candidates, seen = [], set()
    for c in options:
        key_ = tuple(sorted(c["eids"]))
        if key_ in seen:  # e.g. S3: both sides are the same edge, so one simulation is enough
            continue
        seen.add(key_)
        sim = known[key_] if key_ in known else unwrap(write.simulate_cut(sandbox, src, c["eids"], **auth))
        hosts = sum(bool(EDGE_TYPES[info[e][2]].get("host_action")) for e in c["eids"])
        # collateral first, then size, then staleness (older is better), then IAM changes over host actions (LLD 4.2)
        key = (not (sim["blocks_attack"] and sim["needs_ok"]), sim["collateral"]["weighted"], sim["size"],
               -sim["stale_days_total"], hosts)
        candidates.append((key, c, sim))
    candidates.sort(key=lambda t: t[0])
    _, best, sim = candidates[0]
    summary = "; ".join(f"{c['name']}: collateral {s['collateral']['weighted']}, size {s['size']}, stale {s['stale_days_total']} days"
                        for _, c, s in candidates)
    return {**base, "cut_eids": best["eids"], "chosen": best["name"], "witness": best["witness"], "simulation": sim,
            "reasons": [_reason(info, e, best["witness"]) for e in best["eids"]],
            "alternatives": [alternative(c["eids"], s, c["name"]) for _, c, s in candidates[1:]],
            "notes": f"Chose {best['name']} (collateral, then size, then staleness). {summary}."}


# --- Report (pure: the state in, Markdown out) ---

def _path_line(path):
    return f"{' → '.join(path['node_ids'])} ({', '.join(path['eids'])})"


def _summary(state):
    findings, plan_, verdict = state["findings"], state["plan"], state["verdict"]
    status, src = state["status"], state["src"]
    jewels = findings["reachable_jewels"]
    head = f"{src} was treated as compromised."
    if status == "DONE_NO_RISK":
        return f"{head} It reaches no crown jewel, so there is no exposure and no fix was needed."
    risk = f"It can reach {len(jewels)} crown jewel(s) ({', '.join(jewels)}) over {len(findings['paths'])} attack path(s)."
    if status in ("DONE", "REPORTING"):  # the report is written while the status is still REPORTING
        return (f"{head} {risk} {len(plan_['cut_eids'])} edge(s) were removed in sandbox {state['sandbox_graph']}. "
                f"Proof passed: max flow {verdict['attack_max_flow']:g}, no crown jewel reachable, all business needs still work.")
    return (f"{head} {risk} No safe automatic fix was found: {state.get('failure_detail') or (plan_ and plan_['notes']) or 'see below.'} "
            f"A person must decide.")


def _fix(state):
    status, plan_ = state["status"], state["plan"]
    if status == "DONE_NO_RISK":
        return "Nothing to remove."
    if not plan_ or not plan_["cut_eids"]:
        return "No automatic fix was applied." + _instruction_text(state)
    lines = [f"- `{r['eid']}`: {r['why']}" for r in plan_["reasons"]]
    text = "\n".join(lines) + f"\n\n{plan_['notes']}"
    if plan_.get("alternatives"):
        text += "\n\nAlternatives considered:\n" + "\n".join(f"- {alternative_line(a)}" for a in plan_["alternatives"])
    return text + _instruction_text(state)


def _instruction_text(state):
    """The operator instruction, the edges it ruled out (with the quotes), and what it changed versus the default fix. '' without an instruction."""
    plan_ = state["plan"] or {}
    if not state.get("instructions"):
        return ""
    out = f'\n\nOperator instruction: "{state["instructions"]}"'
    info = plan_.get("instruction") or {}
    if not info.get("honoured"):
        return out + f"\nThe instruction could not be honoured: {info.get('note', 'no plan was made')}."
    out += "".join(f'\n- avoided {a["eid"]}, from the words: "{a["quote"]}"' for a in plan_.get("avoid", [])) or "\n- the instruction rules out no edge"
    default = plan_.get("default_fix")
    if default:
        c = default["collateral"]
        out += (f"\nWithout the instruction the fix would have been {', '.join(default['cut_eids'])} (collateral {c['count']}, weighted {c['weighted']}); "
                f"because of the instruction it is {', '.join(plan_['cut_eids'])}.")
    else:
        out += "\nThe instruction did not change the fix."
    return out


def alternative_line(a):
    """One alternative in words, from its numbers only: 'e:s8:07 · blocks the attack · 12 (user, resource) pair(s) would lose access (weighted 12) · rejected'."""
    blocks = "blocks the attack" if a["blocks_attack"] and a["needs_ok"] else "does NOT block the attack safely"
    lost = a["collateral"]
    return (f"{', '.join(a['cut_eids'])} · {blocks} · {lost['count']} (user, resource) pair(s) would lose access (weighted {lost['weighted']}) · "
            f"size {a['size']}, stale {a['stale_days_total']} days · rejected")


def _proof(state):
    v, plan_ = state["verdict"], state["plan"]
    if state["status"] == "DONE_NO_RISK":
        return "- BFS from the compromised identity reaches no crown jewel."
    if v is None:
        return (f"- No fix was applied, so there is nothing to verify.\n- Min-cut max flow {plan_['max_flow'] if plan_ else '?'}: "
                f"every cut would remove an edge that business needs depend on.")
    bad = [n["need_id"] for n in v["business"] if not n["ok"]]
    needs = f"✅ all {len(v['business'])} ok" if not bad else f"❌ {len(bad)} of {len(v['business'])} broken: {', '.join(bad)}"
    reached = ", ".join(v["bfs_reached_jewels"]) or "no crown jewel"
    return f"- Max flow after the fix: {v['attack_max_flow']:g}\n- BFS reaches: {reached}\n- Business needs checked: {needs}"


def _impact(state):
    v, plan_ = state["verdict"], state["plan"]
    if v is None:
        return "No change was made. Collateral: not assessed (no collateral analysis was run for this incident)."
    col = v["collateral"]
    who = "nobody" if not col["count"] else "; ".join(f"{p} loses {r}" for p, r in col["pairs"])
    out = f"- Collateral: {who} ({col['count']} pair(s), weighted {col['weighted']})."
    if plan_ and plan_["cut_eids"]:
        out += f"\n- Fix size {len(plan_['cut_eids'])} vs minimum {plan_['max_flow']} (extra {len(plan_['cut_eids']) - plan_['max_flow']})."
    if v["already_broken"]:
        out += f"\n- Already broken before the fix: {', '.join(v['already_broken'])}."
    return out


def _next(state):
    plan_ = state["plan"]
    suggestion = plan_ and plan_["suggestion"]
    if suggestion:
        label = "Verified suggestion" if suggestion["proven"] else "Unproven suggestion (manual review needed)"
        return f"- {label}: {suggestion['text']}\n- Then rotate the credentials of {state['src']}."
    if state["status"] == "DONE_NO_RISK":
        return "- No action needed. Re-run the check after the next access change."
    if state["status"] == "NEEDS_HUMAN":
        return f"- Manual review: {state.get('failure_detail') or 'no automatic fix could be proven'}.\n- Rotate the credentials of {state['src']}."
    return f"- Rotate the credentials of {state['src']}.\n- Review the removed access with its owners."


DEGRADED_LINE = "Generated in degraded mode (LLM step unavailable)."


def degraded_line(steps):
    """The report's degraded line; it names WHICH steps fell back when the state knows ('Generated in degraded mode (reporter: rate limit).')."""
    return DEGRADED_LINE if not steps else f"Generated in degraded mode ({'; '.join(steps)})."


def report(state):
    findings = state["findings"]
    paths = findings["paths"][:5]
    if paths:
        attack = "\n".join(f"{i}. {_path_line(p)}" for i, p in enumerate(paths, 1))
    elif findings["reachable_jewels"] and state["plan"] and state["plan"]["witness"]:
        witness = next(iter(state["plan"]["witness"].values()))
        attack = f"No path within {config.MAX_HOPS} hops. A longer path found by the max-flow: {' → '.join(witness)}"
    else:
        attack = "None."
    risk = ", ".join(findings["reachable_jewels"]) or "none"
    body = [("Summary", _summary(state)),
            ("What was at risk", f"Crown jewels reachable: {risk}. Attack paths: {len(findings['paths'])}"
                                 f"{' (truncated)' if findings['truncated'] else ''}."),
            ("Attack paths", attack), ("Fix", _fix(state)), ("Proof", _proof(state)),
            ("Business impact", _impact(state)), ("Next steps", _next(state))]
    text = "\n\n".join(f"## {title}\n{content}" for title, content in body)
    if state["degraded"]:
        text += "\n\n" + degraded_line(state.get("degraded_steps"))
    return {"markdown": text + "\n"}

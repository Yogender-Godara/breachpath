"""Pure helpers for the UI (no Streamlit, no HTTP): phases, the graph as DOT, event rows, the proof checklist. Unit-tested."""
from datetime import datetime

from orchestrator.state_machine import TERMINAL

# The graph is redrawn only when the phase changes (docs/03_LLD.md section 15). Several statuses make one phase.
PHASES = ["INVESTIGATING", "PLANNING", "VERIFYING", "DONE"]
PHASE_OF = {"QUEUED": "INVESTIGATING", "INVESTIGATING": "INVESTIGATING", "PREPARING": "PLANNING", "PLANNING": "PLANNING",
            "EXECUTING": "PLANNING", "VERIFYING": "VERIFYING", "REPORTING": "VERIFYING"}
# Readable on a dark and a light theme: nodes are light boxes with black text; edges are bright colours.
COLORS = {"src": "#d62728", "jewel": "#f2b600", "node": "#e8e8e8", "cut": "#ff4d4d", "protected": "#3fb950", "path": "#ffb000", "other": "#9aa0a6"}
LEGEND = ("🔴 compromised identity · 🟡 crown jewel · red dashed = removed (cut) edge · amber bold = attack path · "
          "green = protected business edge · grey = other")
SHAPES = {"User": "ellipse", "Group": "ellipse", "Role": "hexagon", "Policy": "note", "Server": "box3d", "Database": "cylinder", "Secret": "diamond"}


def phase_of(status):
    """The status of an incident -> the phase the graph is drawn for (any final status is DONE)."""
    return "DONE" if status in TERMINAL else PHASE_OF[status]


def status_line(status):
    """'🟡 PLANNING (2/3)' while running, '🟢 DONE', '🟠 NEEDS_HUMAN', '🔴 FAILED'."""
    if status in TERMINAL:
        return f"{'🟢' if status.startswith('DONE') else '🟠' if status == 'NEEDS_HUMAN' else '🔴'} {status}"
    phase = phase_of(status)
    return f"🟡 {phase} ({PHASES.index(phase) + 1}/3)"


def _esc(text):
    return str(text).replace("\\", "\\\\").replace('"', '\\"')


def _quote(text):
    return '"' + _esc(text) + '"'


def src_options(default_src, users):
    """The Src dropdown: only User ids, the scenario's own src first."""
    return [default_src] + [u for u in users if u != default_src and u.startswith("user:")]


def graph_dot(view):
    """The sub-graph (api/graph_view.py: {nodes, edges}) as Graphviz DOT, left to right with the src first. src = red, crown jewel = gold,
    cut edge = red dashed, protected edge = green, attack path = amber, other = grey. Colour: cut > protected > path > other.
    Being on a path (or cut) makes the line bold."""
    lines = ["digraph G {", '  rankdir=LR; bgcolor="transparent";', '  node [fontsize=11, style=filled, fontname="Helvetica", color="#555555"];',
             '  edge [fontsize=9, fontname="Helvetica"];']
    src = next((n["id"] for n in view["nodes"] if n["is_src"]), None)
    if src:
        lines.append(f"  {{ rank=source; {_quote(src)}; }}")
    for n in view["nodes"]:
        fill = COLORS["src"] if n["is_src"] else COLORS["jewel"] if n["crown_jewel"] else COLORS["node"]
        font = "white" if n["is_src"] else "black"
        lines.append(f'  {_quote(n["id"])} [label="{_esc(n["name"])}\\n{_esc(n["label"])}", shape={SHAPES.get(n["label"], "ellipse")}, '
                     f'fillcolor="{fill}", fontcolor="{font}"];')
    for e in view["edges"]:
        color = COLORS["cut"] if e["cut"] else COLORS["protected"] if e["protected"] else COLORS["path"] if e["on_path"] else COLORS["other"]
        style = "dashed" if e["cut"] else "solid"
        width = 2.8 if e["on_path"] or e["cut"] else 1.0
        lines.append(f'  {_quote(e["src"])} -> {_quote(e["dst"])} [label={_quote(e["eid"])}, tooltip={_quote(e["kind"])}, '
                     f'color="{color}", fontcolor="{color}", style={style}, penwidth={width}];')
    return "\n".join(lines + ["}"])


def event_rows(events):
    """Rows for the timeline table: time | agent | step | result | latency ms | tokens."""
    rows = []
    for e in events:
        data = e.get("data") or {}
        rows.append({"time": datetime.fromisoformat(e["ts"]).strftime("%H:%M:%S"), "agent": e["agent"], "step": data.get("tool") or e["type"],
                     "result": e["msg"], "latency ms": "" if e.get("latency_ms") is None else str(e["latency_ms"]),
                     "tokens": f"{e['tokens_in']}/{e['tokens_out']}" if e.get("tokens_in") is not None else ""})
    return rows


def split_reason(why):
    """A plan reason ("Detach the policy: a → b [host action]; on every attack path, e.g. e:1, e:2. Planner: <why>") ->
    (action, host_action, planner reason or ""). The "on every attack path" clause is dropped: the witness column shows the path."""
    code, _, planner = why.partition(" Planner: ")
    code = code.split("; on every attack path")[0]
    host = code.endswith(" [host action]")
    return code.removesuffix(" [host action]"), host, planner.strip()


def cut_rows(plan):
    """One row per removed edge: edge | action (verb + src → dst, with a host-action tag) | witness path."""
    witness = plan.get("witness") or {}
    rows = []
    for r in plan.get("reasons") or []:
        action, host, _ = split_reason(r["why"])
        rows.append({"edge": r["eid"], "action": action + (" · host action" if host else ""), "witness path": " → ".join(witness.get(r["eid"], []))})
    return rows


def reason_lines(plan):
    """[(eid, full reason)] shown as wrapped text under the table: the Planner's reason when it gave one, else the action."""
    out = []
    for r in plan.get("reasons") or []:
        action, host, planner = split_reason(r["why"])
        out.append((r["eid"], planner or action + (" (host action)" if host else "")))
    return out


def alternative_rows(plan):
    """The 'Alternatives considered' table: every simulated cut that was NOT chosen, with the numbers that made it lose."""
    return [{"cut": ", ".join(a["cut_eids"]), "label": a.get("name") or "", "blocks the attack": "yes" if a["blocks_attack"] and a["needs_ok"] else "NO",
             "pairs that lose access": a["collateral"]["count"], "weighted": a["collateral"]["weighted"], "size": a["size"],
             "stale days": a["stale_days_total"], "decision": "rejected"} for a in plan.get("alternatives") or []]


def tradeoff(plan):
    """The trade-off in one sentence per cut, from the simulation numbers only ('' when nothing else was simulated)."""
    sim, alts = plan.get("simulation"), plan.get("alternatives") or []
    if not sim or not alts:
        return ""
    col = sim["collateral"]
    chosen = f"Chosen {', '.join(plan['cut_eids'])}: {col['count']} (user, resource) pair(s) lose access (weighted {col['weighted']}), size {sim['size']}."
    rejected = [f"Rejected {', '.join(a['cut_eids'])}: {'blocks the attack' if a['blocks_attack'] and a['needs_ok'] else 'does not block the attack safely'}, "
                f"but {a['collateral']['count']} (user, resource) pair(s) would lose access (weighted {a['collateral']['weighted']}), size {a['size']}."
                for a in alts]
    return " ".join([chosen, *rejected])


def checks(verdict):
    """The proof checklist: [(icon, text)]. Empty when no verdict (nothing was applied). Collateral is a warning, never a failure."""
    if not verdict:
        return []
    needs = verdict["business"]
    broken = [n["need_id"] for n in needs if not n["ok"]]
    col = verdict["collateral"]
    reached = ", ".join(verdict["bfs_reached_jewels"]) or "no crown jewel"
    icon = lambda ok: "✅" if ok else "❌"
    return [(icon(verdict["attack_max_flow"] == 0), f"Max flow after the fix = {verdict['attack_max_flow']:g}"),
            (icon(not verdict["bfs_reached_jewels"]), f"BFS reaches: {reached}"),
            (icon(not broken), f"Business needs: {len(needs) - len(broken)}/{len(needs)} ok" + (f" (broken: {', '.join(broken)})" if broken else "")),
            ("✅" if not col["count"] else "⚠️", f"Collateral: {col['count']} pair(s), weighted {col['weighted']}"
                                               + "".join(f"; {p} loses {r}" for p, r in col["pairs"][:5]))]

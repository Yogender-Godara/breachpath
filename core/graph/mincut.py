"""Min-cut from a max flow (docs/03_LLD.md section 4, steps 1-9).

min_cut() is a pure function (no database, no networkx), so it can be unit-tested with toy data.
compute_min_cut() is the thin runner that fetches its inputs from FalkorDB.
"""
from collections import deque

from core.graph import queries
from core.graph.client import ro_query

EPS = 1e-9


class MinCutError(RuntimeError):
    """The cut does not add up to the max flow: one of our assumptions is wrong."""


def _has_room(cap, flow):
    return cap - flow > EPS


def _bfs(starts, neighbours):
    seen, queue = set(starts), deque(starts)
    while queue:
        for nxt in neighbours.get(queue.popleft(), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def _witness(cut_edge, src, jewels, flow_out):
    """src -> u, the cut edge, v -> a jewel, each part found by BFS over edges that carry flow."""
    eid, u, v = cut_edge

    def path(start, goals):
        prev, queue = {start: None}, deque([start])
        while queue:
            node = queue.popleft()
            if node in goals:
                out = []
                while prev[node]:
                    node_before, step = prev[node]
                    out.append(step)
                    node = node_before
                return out[::-1]
            for step, nxt in flow_out.get(node, []):
                if nxt not in prev:
                    prev[nxt] = (node, step)
                    queue.append(nxt)
        raise MinCutError(f"no flow path for cut edge {eid} ({start} -> {sorted(goals)}): flow is not conserved")

    return path(src, {u}) + [eid] + path(v, set(jewels))


def min_cut(src, jewels, edges, flow_eids, edge_flows, max_flow, protect_cap):
    """edges: rows (eid, u, v, cap, ...) from ALL_EDGES. Returns the two candidate cuts and the verdict."""
    result = {"max_flow": max_flow, "protect_cap": protect_cap, "safe": max_flow < protect_cap,
              "candidates": [{"name": "source_side", "eids": [], "witness": {}},
                             {"name": "sink_side", "eids": [], "witness": {}}],
              "protected_in_way": []}
    if max_flow < EPS:  # src reaches no jewel: nothing to cut
        return result

    edges = [(e[0], e[1], e[2], e[3]) for e in edges]
    cap = {eid: c for eid, _, _, c in edges}
    flow = dict(zip(flow_eids, edge_flows))  # a missing eid means flow 0

    # Residual arcs. A protected edge (cap = protect_cap) carrying flow 1 still has residual > 0, so the BFS
    # CROSSES it. That is correct: cut edges must be saturated and a protected edge never is. Do NOT block
    # protected edges here: it would break the max-flow = min-cut equality.
    forward, backward, flow_out = {}, {}, {}
    for eid, u, v, c in sorted(edges):
        f = flow.get(eid, 0)
        if _has_room(c, f):
            forward.setdefault(u, []).append(v)
            backward.setdefault(v, []).append(u)
        if f > EPS:  # backward arc v -> u
            forward.setdefault(v, []).append(u)
            backward.setdefault(u, []).append(v)
            flow_out.setdefault(u, []).append((eid, v))

    S = _bfs([src], forward)  # reachable from src
    T = _bfs(list(jewels), backward)  # can still reach a jewel
    cuts = {"source_side": [e for e in edges if e[1] in S and e[2] not in S],
            "sink_side": [e for e in edges if e[1] not in T and e[2] in T]}

    for side, cut in cuts.items():
        total = sum(e[3] for e in cut)
        if abs(total - max_flow) >= EPS:
            raise MinCutError(f"{side} cut capacity {total} != max_flow {max_flow}")

    for candidate in result["candidates"]:
        cut = cuts[candidate["name"]]
        candidate["eids"] = sorted(e[0] for e in cut)
        candidate["witness"] = {e[0]: _witness((e[0], e[1], e[2]), src, jewels, flow_out) for e in cut}
    result["protected_in_way"] = sorted({eid for c in result["candidates"] for eid in c["eids"] if cap[eid] >= protect_cap})
    return result


def compute_min_cut(graph_name, src):
    protect_cap = ro_query(graph_name, queries.PROTECT_CAP)[0][0]
    edges = ro_query(graph_name, queries.ALL_EDGES)
    jewels = [row[0] for row in ro_query(graph_name, queries.JEWELS)]
    if not jewels:
        return min_cut(src, jewels, edges, [], [], 0, protect_cap)
    ((flow_eids, edge_flows, max_flow),) = ro_query(graph_name, queries.MAX_FLOW, {"src": src})
    return min_cut(src, jewels, edges, flow_eids, edge_flows, max_flow, protect_cap)

"""Brute-force optimum (docs/03_LLD.md section 11): an independent check of the min-cut and of the protection heuristic.

candidates = the non-protected edges on any simple src -> jewel path (NO hop bound, so S7's 8-hop path counts). "Protected" = the answer
key's protected_eids. Try subsets of size 1..MAX_SIZE: the first size with a subset that disconnects src from every jewel AND keeps every
valid need is the optimum. More than MAX_CANDIDATES candidates -> not computed.
"""
from itertools import combinations

import networkx as nx

from eval import metrics

MAX_CANDIDATES = 25
MAX_SIZE = 4


def candidates(G, src, protected):
    """Sorted eids of the non-protected edges that lie on some simple src -> jewel path."""
    found = set()
    for j in metrics.reached_jewels(G, src):
        for path in nx.all_simple_edge_paths(G, src, j):
            found.update(k for _, _, k in path)
    return sorted(found - set(protected))


def _adjacency(G):
    adj = {}
    for u, v, k in G.edges(keys=True):
        adj.setdefault(u, []).append((v, k))
    return adj


def _reach(adj, start, removed):
    seen, stack = {start}, [start]
    while stack:
        for nxt, eid in adj.get(stack.pop(), ()):
            if eid not in removed and nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def optimum(graph, key, max_candidates=MAX_CANDIDATES, max_size=MAX_SIZE):
    """-> {"optimum": int | None, "cut": [eid] | None, "candidates": int, "note": str}. None = not computed / no safe fix, with the reason in `note`."""
    G = metrics.nx_graph(graph)
    src, jewels = graph["src"], set(metrics.jewels_of(G))
    cands = candidates(G, src, key["protected_eids"])
    if len(cands) > max_candidates:
        return {"optimum": None, "cut": None, "candidates": len(cands), "note": f"not computed: {len(cands)} candidates (more than {max_candidates})"}
    adj = _adjacency(G)
    needs = [n for n in graph["needs"] if n["valid"]]

    def good(removed):
        if jewels & _reach(adj, src, removed):
            return False
        reach = {}
        return all(n["resource_id"] in reach.setdefault(n["principal_id"], _reach(adj, n["principal_id"], removed)) for n in needs)

    for size in range(1, min(max_size, len(cands)) + 1):
        for subset in combinations(cands, size):
            if good(set(subset)):
                return {"optimum": size, "cut": list(subset), "candidates": len(cands), "note": ""}
    if not good(set(cands)):  # even removing every candidate is not safe
        return {"optimum": None, "cut": None, "candidates": len(cands), "note": "no safe fix"}
    return {"optimum": None, "cut": None, "candidates": len(cands), "note": f"not computed: no safe subset of size <= {max_size}"}

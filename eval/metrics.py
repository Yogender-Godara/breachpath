"""Evaluation metrics (docs/03_LLD.md section 11). Pure and independent of the product: networkx only (the test oracle, HLD 14).

Every method (BreachPath fast/agent, the baselines) is reduced to the same small result and scored on the SAME networkx copy of the graph:
    result = {found_jewels: [id] | None, found_paths: [[eid]] | None, cut_eids: [eid], verdict_safe: bool | None}
None means "this method does not report that" (a baseline that only proposes a cut has no found paths): the metric is None, not 0.
A metric with nothing to measure (no true paths, as in S7) is also None: never NaN, so the results are plain JSON.
"""
import networkx as nx

from core.config import COLLATERAL_RECENT_DAYS, TOP_K_PATHS

RESOURCES = ("Server", "Database")
RECENT_WEIGHT, STALE_WEIGHT = 3, 1


def nx_graph(graph):
    """MultiDiGraph from a scenario graph ({nodes, edges, needs}): the eid is the edge key, last_used_days is kept as `stale`."""
    G = nx.MultiDiGraph()
    for n in graph["nodes"]:
        G.add_node(n["id"], label=n["label"], crown_jewel=bool(n["props"].get("crown_jewel")))
    for e in graph["edges"]:
        G.add_edge(e["src"], e["dst"], key=e["eid"], stale=e["last_used_days"])
    return G


def jewels_of(G):
    return sorted(n for n, d in G.nodes(data=True) if d["crown_jewel"])


def without(G, eids):
    """A copy of G without the edges whose eid is in eids (unknown eids are ignored)."""
    drop = set(eids)
    H = G.copy()
    H.remove_edges_from([(u, v, k) for u, v, k in G.edges(keys=True) if k in drop])
    return H


def reached_jewels(G, src):
    return sorted(set(jewels_of(G)) & nx.descendants(G, src))


def residual_flow(G, src):
    """Max flow from src to any crown jewel when every edge has capacity 1 (the number of edge-disjoint attack paths). 0 = no attack left."""
    jewels = reached_jewels(G, src)
    if not jewels:
        return 0
    H = nx.DiGraph()
    for u, v, eid in G.edges(keys=True):
        H.add_edge(u, f"n:{eid}", capacity=1)
        H.add_edge(f"n:{eid}", v)  # no capacity = infinite: parallel edges stay distinct
    for j in jewels:
        H.add_edge(j, "T")
    return nx.maximum_flow_value(H, src, "T")


def _ratio(num, den):
    return None if den == 0 else num / den


def broken_needs(G, needs, cut):
    """Ids of the valid needs that had a path before and have none after the cut."""
    H = without(G, cut)
    reach = {}
    broken = []
    for n in needs:
        if not n["valid"]:
            continue
        p = n["principal_id"]
        if p not in reach:
            reach[p] = nx.descendants(H, p)
        if n["resource_id"] not in reach[p]:
            broken.append(n["id"])
    return sorted(broken)


def collateral(G, src, cut, days=COLLATERAL_RECENT_DAYS):
    """(other user, Server/Database) pairs that lose access. Weight 3 if the pair was reachable BEFORE through edges all used within `days`,
    else 1 (the same rule as the product, computed independently here)."""
    H = without(G, cut)
    recent = nx.MultiDiGraph()
    recent.add_edges_from((u, v, k) for u, v, k, s in G.edges(keys=True, data="stale") if s <= days)
    users = sorted(n for n, d in G.nodes(data=True) if d["label"] == "User" and n != src)
    resources = {n for n, d in G.nodes(data=True) if d["label"] in RESOURCES}
    count = weighted = 0
    for u in users:
        before = nx.descendants(G, u) & resources
        lost = before - nx.descendants(H, u)
        fresh = nx.descendants(recent, u) if u in recent else set()
        count += len(lost)
        weighted += sum(RECENT_WEIGHT if r in fresh else STALE_WEIGHT for r in lost)
    return {"count": count, "weighted": weighted}


def score(graph, key, result, optimum=None, k=TOP_K_PATHS):
    """All metrics of one method on one scenario. `optimum` = the brute-force size (eval/bruteforce.py) or None (not computed)."""
    G = nx_graph(graph)
    src = graph["src"]
    cut = list(result["cut_eids"])
    known = {eid for _, _, eid in G.edges(keys=True)}
    H = without(G, cut)
    jewels, paths = result.get("found_jewels"), result.get("found_paths")
    true_paths = {tuple(p) for p in key["true_paths"]}
    found = [tuple(p) for p in paths[:k]] if paths is not None else None
    hits = len(set(found) & true_paths) if found is not None else None
    reachable = set(key["reachable_jewels"])
    residual, left = residual_flow(H, src), reached_jewels(H, src)
    broken = broken_needs(G, graph["needs"], cut)
    safe = result.get("verdict_safe")
    return {
        "blast_recall": None if jewels is None else _ratio(len(set(jewels) & reachable), len(reachable)),
        "path_recall": None if found is None else _ratio(hits, min(len(true_paths), k)),
        "path_precision": None if found is None else _ratio(hits, len(found)),
        "cut_size": len(cut),
        "fix_gap": None if optimum is None else len(cut) - optimum,
        "residual_flow": residual,
        "residual_jewels": left,
        "broken_needs": broken,
        "collateral": collateral(G, src, cut),
        "unknown_eids": sorted(set(cut) - known),
        "verdict_correct": None if safe is None else safe == key["safe_fix_exists"],
        "fixes_attack": residual == 0 and not left,
        "safe_fix": residual == 0 and not left and not broken,
    }

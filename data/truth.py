"""Ground truth (docs/03_LLD.md section 10.4): an independent networkx answer key. Generator/eval only, never the product."""
import networkx as nx

from core.config import MAX_HOPS


def build_graph(graph):
    G = nx.MultiDiGraph()
    G.add_nodes_from(n["id"] for n in graph["nodes"])
    for e in graph["edges"]:
        G.add_edge(e["src"], e["dst"], key=e["eid"])
    return G


def shortest_edge_path(G, u, v):
    """Shortest path as a tuple of eids. Ties: lowest eid per hop (parallel edges), then the smallest tuple."""
    paths = [tuple(min(G[a][b]) for a, b in zip(p, p[1:])) for p in nx.all_shortest_paths(G, u, v)]
    return min(paths)


def min_cut_size(G, src, jewels, protected, protect_cap):
    """Min-cut on a split graph: each edge u->v becomes u -> n:<eid> (capacity) -> v (infinite)."""
    if not jewels:
        return 0
    H = nx.DiGraph()
    for u, v, eid in G.edges(keys=True):
        H.add_edge(u, f"n:{eid}", capacity=protect_cap if eid in protected else 1)
        H.add_edge(f"n:{eid}", v)  # no capacity attribute = infinite
    for j in jewels:
        H.add_edge(j, "T")
    return nx.minimum_cut_value(H, src, "T")


def compute_truth(graph):
    G = build_graph(graph)
    src = graph["src"]
    crown_jewels = sorted(n["id"] for n in graph["nodes"] if n["props"].get("crown_jewel"))
    reachable = sorted(set(crown_jewels) & nx.descendants(G, src))

    true_paths, long_path_jewels = [], []
    for j in reachable:
        found = [[k for _, _, k in p] for p in nx.all_simple_edge_paths(G, src, j, cutoff=MAX_HOPS)]
        true_paths += found
        if not found:
            long_path_jewels.append(j)

    valid_needs = [n for n in graph["needs"] if n["valid"]]
    protected = sorted({eid for n in valid_needs for eid in shortest_edge_path(G, n["principal_id"], n["resource_id"])})
    protect_cap = G.number_of_edges() + 1
    cut = min_cut_size(G, src, reachable, set(protected), protect_cap)

    # No "optimum" key: it comes from brute force in eval/bruteforce.py (P8).
    return {
        "scenario": graph["scenario"],
        "src": src,
        "crown_jewels": crown_jewels,
        "reachable_jewels": reachable,
        "true_paths": sorted(true_paths),
        "long_path_jewels": long_path_jewels,
        "protected_eids": protected,
        "protect_cap": protect_cap,
        "min_cut_size": cut,
        "safe_fix_exists": cut < protect_cap,
        "business_needs": [{k: n[k] for k in ("id", "principal_id", "resource_id", "valid")} for n in graph["needs"]],
    }


def check_expect(truth, expect):
    got = {"reachable_jewels": set(truth["reachable_jewels"]), "paths": len(truth["true_paths"]),
           "min_cut": truth["min_cut_size"], "safe": truth["safe_fix_exists"],
           "long_path_jewels": set(truth["long_path_jewels"])}
    want = {**expect, "reachable_jewels": set(expect["reachable_jewels"])}
    if "long_path_jewels" in want:  # optional: jewels reachable only beyond MAX_HOPS
        want["long_path_jewels"] = set(want["long_path_jewels"])
    bad = [f"{key}: expected {want[key]}, got {got[key]}" for key in got if key in want and want[key] != got[key]]
    if bad:
        raise ValueError(f"{truth['scenario']}: expect mismatch: " + "; ".join(bad))

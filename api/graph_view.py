"""The sub-graph the UI draws (docs/03_LLD.md section 15), read from the BASELINE: the cut edges are gone from the sandbox."""
from core import config
from core.graph import queries
from core.graph.client import ro_query

MAX_ELEMENTS = 150  # nodes + edges


def build_view(state):
    """Union of the listed attack paths, the protected business paths, the cut edges and 1 hop around the cut edges,
    added in that order until the cap. Returns {nodes, edges, more} where `more` counts the edges left out."""
    graph = state["baseline_graph"]
    edges = {r[0]: {"eid": r[0], "src": r[1], "dst": r[2], "kind": r[4]} for r in ro_query(graph, queries.ALL_EDGES)}
    nodes = {r[0]: {"id": r[0], "label": next(l for l in r[1] if l != "Asset"), "name": r[2], "crown_jewel": r[3],
                    "is_src": r[0] == state["src"]} for r in ro_query(graph, queries.GRAPH_NODES)}
    path_eids = [e for p in state["findings"]["paths"] for e in p["eids"]]
    protected = set((state["action"] or {}).get("protected_eids", []))
    cut = list((state["plan"] or {}).get("cut_eids", []))
    cut_ends = {edges[e][side] for e in cut if e in edges for side in ("src", "dst")}
    around = [e for e, v in edges.items() if v["src"] in cut_ends or v["dst"] in cut_ends]

    wanted = list(dict.fromkeys(e for e in path_eids + cut + sorted(protected) + around if e in edges))
    kept, ids = [], {state["src"]}
    for eid in wanted:
        new = {edges[eid]["src"], edges[eid]["dst"]} - ids
        if len(kept) + 1 + len(ids | new) > MAX_ELEMENTS:
            continue
        kept.append(eid)
        ids |= new
    on_path, cut_set = set(path_eids), set(cut)
    return {"nodes": [nodes[i] for i in sorted(ids) if i in nodes],
            "edges": [{**edges[e], "on_path": e in on_path, "cut": e in cut_set, "protected": e in protected} for e in kept],
            "more": len(wanted) - len(kept)}

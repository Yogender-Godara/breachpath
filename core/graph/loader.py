"""Load a generated scenario into FalkorDB (docs/03_LLD.md Q0). Batched UNWIND, one query per label, one for all edges."""
import json
import sys
from collections import Counter
from pathlib import Path

from core.graph.client import create_indexes, delete_graph, query, ro_query
from core.graph.schema import EDGE_TYPE, NEED_LABEL, NODE_LABELS, SHARED_LABEL, baseline_name

BATCH_SIZE = 500
GENERATED_DIR = Path(__file__).resolve().parents[2] / "data" / "generated"

# Whole-map params can't be inline properties (P1.3), so nodes use SET n += row and edges list their fields.
NODE_QUERY = "UNWIND $rows AS row CREATE (n:{shared}:{label}) SET n += row"
EDGE_QUERY = (
    "UNWIND $rows AS row "
    "MATCH (a:{shared} {{id: row.src}}), (b:{shared} {{id: row.dst}}) "
    "CREATE (a)-[r:{type} {{eid: row.eid, kind: row.type, cap: 1, w: 1, last_used_days: row.last_used_days}}]->(b) "
    "SET r.technique = row.technique"  # missing technique = null = no property
)
NEED_QUERY = "UNWIND $rows AS row CREATE (n:{label}) SET n += row"  # BusinessNeed only: no Asset label, no edges (D10)


def check_no_parallel_edges(edges):
    """At most one edge per (src, dst): algo.maxFlow rejects multi-edges under one relationship type."""
    seen = {}
    for e in edges:
        pair = (e["src"], e["dst"])
        if pair in seen:
            raise ValueError(f"duplicate edge pair {pair[0]} -> {pair[1]}: {seen[pair]} and {e['eid']}")
        seen[pair] = e["eid"]


def run_batches(graph_name, cypher, rows):
    for i in range(0, len(rows), BATCH_SIZE):
        query(graph_name, cypher, {"rows": rows[i:i + BATCH_SIZE]})


def count(graph_name, cypher):
    return ro_query(graph_name, cypher)[0][0]


def verify(graph_name, data):
    """Read the counts back from FalkorDB and compare with what we meant to load."""
    checks = [
        ("nodes", count(graph_name, f"MATCH (n:{SHARED_LABEL}) RETURN count(n)"), len(data["nodes"])),
        ("edges", count(graph_name, f"MATCH ()-[r:{EDGE_TYPE}]->() RETURN count(r)"), len(data["edges"])),
        ("needs", count(graph_name, f"MATCH (n:{NEED_LABEL}) RETURN count(n)"), len(data["needs"])),
    ]
    by_kind = dict(ro_query(graph_name, f"MATCH ()-[r:{EDGE_TYPE}]->() RETURN r.kind, count(r)"))
    want = Counter(e["type"] for e in data["edges"])
    for kind in sorted(set(by_kind) | set(want)):
        checks.append((f"{kind} edges", by_kind.get(kind, 0), want[kind]))
    for name, got, expected in checks:
        if got != expected:
            raise ValueError(f"{graph_name}: {name}: loaded {got}, expected {expected}")
    return {"graph": graph_name, "nodes": checks[0][1], "edges": checks[1][1], "needs": checks[2][1]}


def load_graph(graph_name, data):
    check_no_parallel_edges(data["edges"])  # before any write
    delete_graph(graph_name)
    for label in NODE_LABELS:
        rows = [n["props"] for n in data["nodes"] if n["label"] == label]
        run_batches(graph_name, NODE_QUERY.format(shared=SHARED_LABEL, label=label), rows)
    create_indexes(graph_name)  # the graph must exist first (P2.1); still before the edges, so MATCH by id uses the index
    rows = [{**e, "technique": e.get("technique")} for e in data["edges"]]
    run_batches(graph_name, EDGE_QUERY.format(shared=SHARED_LABEL, type=EDGE_TYPE), rows)
    run_batches(graph_name, NEED_QUERY.format(label=NEED_LABEL), data["needs"])
    return verify(graph_name, data)


def load_scenario(sid):
    with open(GENERATED_DIR / f"{sid}.json", encoding="utf-8") as f:
        data = json.load(f)
    return load_graph(baseline_name(sid), data)


if __name__ == "__main__":
    for sid in sys.argv[1:]:
        r = load_scenario(sid)
        print(f"{sid}: loaded {r['graph']}: {r['nodes']} nodes, {r['edges']} edges, {r['needs']} needs")

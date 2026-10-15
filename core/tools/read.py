"""Read tools (docs/03_LLD.md section 5). Plain functions returning {"ok", "data"} dicts; MCP wiring comes in P6.1."""
from core import config
from core.graph import mincut, queries
from core.graph.client import get_db, get_graph, ro_query
from core.graph.schema import is_baseline, is_sandbox
from core.tools import verify
from core.tools.common import BadInput, NotFound, tool

ROW_CAP = 200
MAX_PATHS = 500  # paths Q4 looks at


# --- shared checks ---

def _graph(name):
    if not (isinstance(name, str) and (is_baseline(name) or is_sandbox(name))):
        raise BadInput(f"graph name {name!r} is not a baseline or sandbox name")
    if name not in get_db().list_graphs():
        raise NotFound(f"graph {name} does not exist")
    return name


def _node(graph, node_id):
    rows = ro_query(_graph(graph), queries.NODE_INFO, {"id": node_id})
    if not rows:
        raise NotFound(f"node {node_id} not found in {graph}")
    return rows[0]


def _limit(name, value, high):
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= high:
        raise BadInput(f"{name} must be an integer from 1 to {high}, got {value!r}")


def _plain(value):
    """FalkorDB Node / Edge / Path -> plain dicts and lists, so agents get JSON."""
    if hasattr(value, "labels") and hasattr(value, "properties"):  # Node
        return {"labels": list(value.labels), "properties": dict(value.properties)}
    if hasattr(value, "relation"):  # Edge
        return {"relation": value.relation, "properties": dict(value.properties)}
    if hasattr(value, "nodes") and hasattr(value, "edges"):  # Path
        return {"nodes": [_plain(n) for n in value.nodes()], "edges": [_plain(e) for e in value.edges()]}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


# --- tools ---

@tool
def get_blast_radius(graph, src):
    _node(graph, src)
    return [{"id": r[0], "name": r[1]} for r in ro_query(graph, queries.BLAST_RADIUS, {"src": src})]


@tool
def find_attack_paths(graph, src, target=None, k=20):
    _limit("k", k, 50)
    _node(graph, src)
    if target is not None:
        _node(graph, target)
    rows = ro_query(graph, queries.ATTACK_PATHS, {"src": src, "target": target, "k": k + 1})  # one extra row tells us if we cut
    paths = [{"target": r[0], "node_ids": r[1], "eids": r[2], "hops": r[3]} for r in rows[:k]]
    return {"paths": paths, "truncated": len(rows) > k}


@tool
def shortest_paths(graph, src, target, k=3):
    _limit("k", k, 10)
    _node(graph, src)
    _node(graph, target)
    rows = ro_query(graph, queries.SHORTEST_PATHS, {"src": src, "target": target, "k": k})
    return [{"eids": r[0], "hops": int(r[1])} for r in rows]


def _edge_frequency(graph, src, top):
    rows = ro_query(graph, queries.EDGE_FREQUENCY, {"src": src, "max_paths": MAX_PATHS, "top": top})
    return [{"eid": r[0], "kind": r[1], "paths_through": r[2]} for r in rows]


@tool
def edge_path_frequency(graph, src, top=10):
    _limit("top", top, 20)
    _node(graph, src)
    return _edge_frequency(graph, src, top)


@tool
def choke_points(graph, src, top=10):
    _limit("top", top, 20)
    _node(graph, src)
    n = ro_query(graph, queries.NODE_COUNT)[0][0]  # samplingSize = node count, so betweenness is exact
    rows = ro_query(graph, queries.BETWEENNESS, {"n": n, "top": top})
    return {"betweenness": [{"id": r[0], "labels": r[1], "score": r[2]} for r in rows],
            "edge_frequency": _edge_frequency(graph, src, top)}


@tool
def get_node(graph, id):
    labels, props = _node(graph, id)
    out = [{"eid": r[0], "kind": r[1], "to": r[2]} for r in ro_query(graph, queries.NODE_OUT, {"id": id})]
    into = [{"eid": r[0], "kind": r[1], "from": r[2]} for r in ro_query(graph, queries.NODE_IN, {"id": id})]
    return {"id": id, "labels": labels, "props": dict(props), "out": out, "in": into}


@tool
def get_edge(graph, eid):
    rows = ro_query(_graph(graph), queries.EDGE_INFO, {"eid": eid})
    if not rows:
        raise NotFound(f"edge {eid} not found in {graph}")
    eid, kind, src, dst, props = rows[0]
    return {"eid": eid, "kind": kind, "src": src, "dst": dst, "props": dict(props)}


@tool
def run_readonly_cypher(graph, query, params=None):
    if not isinstance(query, str) or not query.strip():
        raise BadInput("query must be a non-empty string")
    result = get_graph(_graph(graph)).ro_query(query, params, timeout=config.QUERY_TIMEOUT_MS)  # RO: the server rejects writes
    names = [h[1] for h in result.header]
    rows = [dict(zip(names, _plain(row))) for row in result.result_set[:ROW_CAP]]
    return {"rows": rows, "truncated": len(result.result_set) > ROW_CAP}


@tool
def check_business_access(graph):
    return verify.needs_status(_graph(graph))


@tool
def compute_min_cut(graph, src):
    _node(graph, src)
    return mincut.compute_min_cut(graph, src)  # when safe is false the pipeline calls split_suggestion (core/tools/split.py)

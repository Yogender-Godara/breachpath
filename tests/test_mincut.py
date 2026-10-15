"""Min-cut tests: S1-S3 on real data, pure unit tests of min_cut() (no database), then toy graphs in FalkorDB."""
import json
from contextlib import contextmanager

import pytest
from redis.exceptions import ResponseError

from core.graph import client, loader, mincut, queries
from core.graph.mincut import MinCutError, compute_min_cut, min_cut
from core.graph.schema import baseline_name, sandbox_name


@contextmanager
def protected_copy(sid):
    """Baseline copy with every valid need protected (cap = protect_cap), deleted afterwards."""
    loader.load_scenario(sid)
    sbx = sandbox_name(sid, "p33test")
    client.delete_graph(sbx)
    client.get_graph(baseline_name(sid)).copy(sbx)
    try:
        protect_cap = client.ro_query(sbx, queries.PROTECT_CAP)[0][0]
        client.query(sbx, queries.PROTECT_NEEDS, {"protect_cap": protect_cap})
        yield sbx
    finally:
        client.delete_graph(sbx)


def test_s2_cut(falkordb):
    with protected_copy("s2") as sbx:
        result = compute_min_cut(sbx, "user:ravi")
    print(json.dumps(result, indent=1))
    source, sink = result["candidates"]
    assert (result["max_flow"], result["safe"], result["protected_in_way"]) == (2, True, [])
    assert (source["name"], source["eids"]) == ("source_side", ["e:s2:05", "e:s2:10"])
    assert (sink["name"], sink["eids"]) == ("sink_side", ["e:s2:08", "e:s2:11"])
    assert source["witness"]["e:s2:05"] == ["e:s2:01", "e:s2:02", "e:s2:05", "e:s2:06", "e:s2:07", "e:s2:08"]
    assert source["witness"]["e:s2:10"] == ["e:s2:09", "e:s2:10", "e:s2:11", "e:s2:12", "e:s2:13"]


@pytest.mark.parametrize("sid", ["s1", "s3"])
def test_cut_size_matches_the_answer_key(falkordb, sid):
    with open(loader.GENERATED_DIR.parent / "answer_keys" / f"{sid}.json", encoding="utf-8") as f:
        key = json.load(f)
    with protected_copy(sid) as sbx:
        result = compute_min_cut(sbx, key["src"])
    source, sink = result["candidates"]
    assert len(source["eids"]) == key["min_cut_size"] and result["safe"] is True
    assert len(sink["eids"]) == key["min_cut_size"]


# --- A. Unit tests of the pure min_cut(): flows written by hand, no database ---

# s --a(cap 5 = protect_cap, protected)--> x --b(cap 1)--> j. Flow 1 on both is maximal: b is the only way into j
# and holds 1, so max flow <= 1, and 1 is reached. Edge a still has room (5 - 1 > 0), b is full.
PROTECTED_CHAIN = dict(src="s", jewels=["j"], edges=[("a", "s", "x", 5), ("b", "x", "j", 1)],
                       flow_eids=["a", "b"], edge_flows=[1.0, 1.0], max_flow=1.0, protect_cap=5)


def test_r23_bfs_crosses_a_protected_edge():
    result = min_cut(**PROTECTED_CHAIN)
    source, sink = result["candidates"]
    assert source["eids"] == ["b"] and sink["eids"] == ["b"]  # the BFS walked across protected edge a
    assert result["safe"] is True and result["protected_in_way"] == []
    assert source["witness"]["b"] == ["a", "b"]


def test_r23_blocking_protected_edges_breaks_the_math(monkeypatch):
    # "Block protected edges in the BFS" would stop at a, make the cut {a} (cap 5), and 5 != max flow 1.
    protect_cap = PROTECTED_CHAIN["protect_cap"]
    monkeypatch.setattr(mincut, "_has_room", lambda cap, flow: cap < protect_cap and cap - flow > mincut.EPS)
    with pytest.raises(MinCutError, match="source_side"):
        min_cut(**PROTECTED_CHAIN)


def test_zero_flow_gives_empty_candidates():
    # s -> x, but nothing leads to j: the only possible flow is 0, so there is nothing to cut.
    result = min_cut("s", ["j"], [("a", "s", "x", 1), ("b", "y", "j", 1)], [], [], 0.0, 5)
    assert result["safe"] is True and result["protected_in_way"] == []
    assert [(c["name"], c["eids"], c["witness"]) for c in result["candidates"]] == [
        ("source_side", [], {}), ("sink_side", [], {})]


def test_wrong_max_flow_is_caught():
    # The flow is right (1) but we claim 2: the cut adds up to 1, not 2.
    with pytest.raises(MinCutError, match="max_flow"):
        min_cut(**{**PROTECTED_CHAIN, "max_flow": 2.0})


def test_parallel_edges_in_the_pure_min_cut():
    # s => x (e1, e2) => j (e3, e4), flow 1 on each: every edge is full and max flow 2 is the most the pairs can carry.
    edges = [("e1", "s", "x", 1), ("e2", "s", "x", 1), ("e3", "x", "j", 1), ("e4", "x", "j", 1)]
    ends = {eid: (u, v) for eid, u, v, _ in edges}
    result = min_cut("s", ["j"], edges, ["e1", "e2", "e3", "e4"], [1.0] * 4, 2.0, 5)
    source, sink = result["candidates"]
    assert source["eids"] == ["e1", "e2"] and sink["eids"] == ["e3", "e4"]
    for candidate in result["candidates"]:
        for chain in candidate["witness"].values():
            assert ends[chain[0]][0] == "s" and ends[chain[-1]][1] == "j"
            assert all(ends[a][1] == ends[b][0] for a, b in zip(chain, chain[1:]))


def test_loader_rejects_a_duplicate_src_dst_pair():
    edges = [{"eid": "e1", "src": "a", "dst": "b"}, {"eid": "e2", "src": "b", "dst": "c"}, {"eid": "e3", "src": "a", "dst": "b"}]
    with pytest.raises(ValueError, match="a -> b: e1 and e3"):
        loader.check_no_parallel_edges(edges)
    loader.check_no_parallel_edges(edges[:2])  # distinct pairs are fine


# --- B. Toy graphs in FalkorDB (real algo.maxFlow) ---

PROTECT_EDGES = "MATCH ()-[r:ATTACK]->() WHERE r.eid IN $eids SET r.cap = $cap, r.protected = true"  # test only


def toy_node(label, nid, jewel=False):
    props = {"id": nid, "name": nid}
    if label == "Database":
        props["crown_jewel"] = jewel
    return {"id": nid, "label": label, "props": props}


def toy_edge(eid, kind, src, dst):
    return {"eid": eid, "type": kind, "src": src, "dst": dst, "last_used_days": 1}


@contextmanager
def toy_graph(case, nodes, edges, protect=()):
    """Load a toy graph (no needs), protect the given eids, yield its name, delete it afterwards."""
    name = sandbox_name("toy", f"p34-{case}")
    loader.load_graph(name, {"nodes": nodes, "edges": edges, "needs": []})
    try:
        if protect:
            cap = client.ro_query(name, queries.PROTECT_CAP)[0][0]
            client.query(name, PROTECT_EDGES, {"eids": list(protect), "cap": cap})
        yield name
    finally:
        client.delete_graph(name)


def test_textbook_graph_source_and_sink_cuts_differ(falkordb):
    # s has 3 exits (e01-e03) but the jewel has only 2 entrances (e10, e11), so max flow <= 2.
    # Flow 2 exists: e01,e04,e07,e10 and e03,e06,e09,e11, so max flow = 2.
    # The two sides differ because p1 has a single entrance, e07, so e07 and e10 are in series:
    #  - source side: S = {s,g1,g2,g3,r1,r2,p2} (r1 -> p2 -> ... still has room). Leaving S: e07 and e11.
    #  - sink side:   T = {j}. Entering T: e10 and e11.
    # Both are minimum cuts of size 2; going nearer the source than e07 / e11 needs 3 edges.
    nodes = [toy_node("User", "user:s"), toy_node("Group", "group:g1"), toy_node("Group", "group:g2"),
             toy_node("Group", "group:g3"), toy_node("Role", "role:r1"), toy_node("Role", "role:r2"),
             toy_node("Policy", "policy:p1"), toy_node("Policy", "policy:p2"), toy_node("Database", "db:j", jewel=True)]
    edges = [toy_edge("e01", "MEMBER_OF", "user:s", "group:g1"), toy_edge("e02", "MEMBER_OF", "user:s", "group:g2"),
             toy_edge("e03", "MEMBER_OF", "user:s", "group:g3"),
             toy_edge("e04", "CAN_ASSUME", "group:g1", "role:r1"), toy_edge("e05", "CAN_ASSUME", "group:g2", "role:r1"),
             toy_edge("e06", "CAN_ASSUME", "group:g3", "role:r2"),
             toy_edge("e07", "HAS_POLICY", "role:r1", "policy:p1"), toy_edge("e08", "HAS_POLICY", "role:r1", "policy:p2"),
             toy_edge("e09", "HAS_POLICY", "role:r2", "policy:p2"),
             toy_edge("e10", "CAN_READ", "policy:p1", "db:j"), toy_edge("e11", "CAN_READ", "policy:p2", "db:j")]
    with toy_graph("textbook", nodes, edges) as name:
        result = compute_min_cut(name, "user:s")
    source, sink = result["candidates"]
    assert result["max_flow"] == 2 and result["safe"] is True
    assert source["eids"] == ["e07", "e11"] and sink["eids"] == ["e10", "e11"]


def test_falkordb_limit_maxflow_rejects_multi_edges(falkordb):
    # Documents a FalkorDB 6.0.2 limit (P3.4): two edges between one pair of nodes break algo.maxFlow.
    # This is why the loader rejects duplicate (src, dst) pairs. Built with raw Cypher to bypass the loader.
    name = sandbox_name("toy", "p34-multiedge")
    client.delete_graph(name)
    try:
        client.query(name, "CREATE (a:Asset:User {id: 'user:s'}), (j:Asset:Database {id: 'db:j', crown_jewel: true}), "
                           "(a)-[:ATTACK {eid: 'e1', cap: 1, w: 1}]->(j), (a)-[:ATTACK {eid: 'e2', cap: 1, w: 1}]->(j)")
        with pytest.raises(ResponseError, match="multi-edges"):
            client.ro_query(name, queries.MAX_FLOW, {"src": "user:s"})
    finally:
        client.delete_graph(name)


def test_no_safe_fix_when_every_edge_on_the_only_path_is_protected(falkordb):
    nodes = [toy_node("User", "user:s"), toy_node("Policy", "policy:x"), toy_node("Database", "db:j", jewel=True)]
    edges = [toy_edge("a", "HAS_POLICY", "user:s", "policy:x"), toy_edge("b", "CAN_READ", "policy:x", "db:j")]
    with toy_graph("nosafe", nodes, edges, protect=["a", "b"]) as name:
        result = compute_min_cut(name, "user:s")
    assert result["safe"] is False and result["max_flow"] >= result["protect_cap"]
    assert result["protected_in_way"] == ["a", "b"]


def test_many_paths_are_not_a_false_alarm(falkordb):
    # 40 disjoint paths s -> m_i -> j with the first edges protected: max flow 40 is still below protect_cap 81.
    nodes = [toy_node("User", "user:s"), toy_node("Database", "db:j", jewel=True)]
    edges, first = [], []
    for i in range(40):
        nodes.append(toy_node("Policy", f"policy:m{i:02d}"))
        edges += [toy_edge(f"f{i:02d}", "HAS_POLICY", "user:s", f"policy:m{i:02d}"),
                  toy_edge(f"g{i:02d}", "CAN_READ", f"policy:m{i:02d}", "db:j")]
        first.append(f"f{i:02d}")
    with toy_graph("many", nodes, edges, protect=first) as name:
        result = compute_min_cut(name, "user:s")
    assert result["max_flow"] == 40 and result["protect_cap"] == 81 and result["safe"] is True
    assert result["candidates"][0]["eids"] == [f"g{i:02d}" for i in range(40)]
    assert result["protected_in_way"] == []

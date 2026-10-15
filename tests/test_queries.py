"""Integration test: every query once on S2. Reads run on the baseline, writes only on a sandbox copy."""
import json

import pytest

from core.graph import client, loader, queries
from core.graph.schema import baseline_name, sandbox_name
from tests.helpers import SCENARIOS

BASE = baseline_name("s2")
SBX = sandbox_name("s2", "p31test")
SRC = "user:ravi"
JEWELS = {"db:s2-customers", "db:s2-payments"}


@pytest.fixture(scope="module")
def s2(falkordb):
    loader.load_scenario("s2")
    client.delete_graph(SBX)
    client.get_graph(BASE).copy(SBX)
    with open(loader.GENERATED_DIR / "s2.json", encoding="utf-8") as f:
        data = json.load(f)
    yield data
    client.delete_graph(SBX)


def read(cypher, **params):
    return client.ro_query(BASE, cypher, params)


def test_blast_radius(s2):
    assert {r[0] for r in read(queries.BLAST_RADIUS, src=SRC)} == JEWELS


def test_attack_paths(s2):
    rows = read(queries.ATTACK_PATHS, src=SRC, k=20, target=None)
    assert len(rows) == 2
    assert {r[0] for r in rows} == JEWELS
    assert [r[3] for r in rows] == sorted(r[3] for r in rows)


def test_shortest_paths(s2):
    rows = read(queries.SHORTEST_PATHS, src=SRC, target="db:s2-payments", k=2)
    assert rows[0][0] == ["e:s2:09", "e:s2:10", "e:s2:11", "e:s2:12", "e:s2:13"]
    assert read(queries.SHORTEST_PATHS, src="db:s2-payments", target=SRC, k=1) == []  # no path, no rows


def test_edge_frequency(s2):
    rows = read(queries.EDGE_FREQUENCY, src=SRC, max_paths=100, top=50)
    assert len(rows) == 11 and {r[2] for r in rows} == {1}
    assert {r[0]: r[1] for r in rows}["e:s2:06"] == "CAN_PASS_ROLE"  # two disjoint paths: 6 + 5 edges, each used once


def test_betweenness(s2):
    rows = read(queries.BETWEENNESS, n=200, top=5)
    assert len(rows) == 5 and rows[0][2] >= rows[-1][2]


def test_max_flow(s2):
    ((flow_eids, edge_flows, max_flow),) = read(queries.MAX_FLOW, src=SRC)
    with open(loader.GENERATED_DIR.parent / "answer_keys" / "s2.json", encoding="utf-8") as f:
        path_eids = {eid for path in json.load(f)["true_paths"] for eid in path}
    assert max_flow == 2.0 and len(flow_eids) == len(edge_flows)
    assert {eid for eid, flow in zip(flow_eids, edge_flows) if flow > 0} == path_eids and len(path_eids) == 11


def test_all_edges(s2):
    rows = read(queries.ALL_EDGES)
    assert len(rows) == len(s2["edges"]) == 179
    assert all(r[3] == 1 for r in rows)  # cap = 1 on the baseline
    assert {r[0]: r[4] for r in rows}["e:s2:06"] == "CAN_PASS_ROLE"


def test_protect_cap(s2):
    assert read(queries.PROTECT_CAP)[0][0] == 180


def test_reach_bfs(s2):
    assert set(read(queries.REACH_BFS, src=SRC)[0][0]) == JEWELS


def test_needs_check_all_ok_on_baseline(s2):
    valid = {n["id"] for n in s2["needs"] if n["valid"]}
    rows = read(queries.NEEDS_CHECK)
    assert {r[0] for r in rows} == valid and all(r[1] for r in rows)


def test_reach_sets(s2):
    users = [n["id"] for n in s2["nodes"] if n["label"] == "User" and n["id"] != SRC]
    rows = read(queries.REACH_SETS, src=SRC)
    assert sorted(r[0] for r in rows) == sorted(users)


def test_writes_on_the_sandbox_copy(s2):
    protect_cap = client.ro_query(SBX, queries.PROTECT_CAP)[0][0]
    rows = client.query(SBX, queries.PROTECT_NEEDS, {"protect_cap": protect_cap})
    assert {r[0] for r in rows} <= {n["id"] for n in s2["needs"]} and rows

    def prop(eid):
        return client.ro_query(SBX, "MATCH ()-[r {eid: $eid}]->() RETURN r.protected, r.cap", {"eid": eid})[0]

    assert prop("e:s2:01") == [True, 180]
    assert prop("e:s2:05") == [None, 1]

    def fix(eid):
        return client.query(SBX, queries.APPLY_FIX, {"eids": [eid]})

    assert fix("e:s2:05") == [[1]]
    assert fix("e:s2:05") == [[0]]  # idempotent: nothing left to delete
    assert fix("e:s2:01") == [[0]]  # protected: refused
    assert prop("e:s2:01")[0] is True


def test_baseline_is_unchanged(s2):
    assert read("MATCH ()-[r]->() RETURN count(r)")[0][0] == 179
    assert read("MATCH ()-[r]->() WHERE r.protected IS NOT NULL RETURN count(r)")[0][0] == 0
    assert read("MATCH ()-[r {eid: 'e:s2:05'}]->() RETURN count(r)")[0][0] == 1


# --- Query results vs the networkx answer keys (S1-S8) ---

@pytest.fixture(scope="module", params=SCENARIOS)
def case(request, falkordb):
    sid = request.param
    loader.load_scenario(sid)
    with open(loader.GENERATED_DIR.parent / "answer_keys" / f"{sid}.json", encoding="utf-8") as f:
        return sid, json.load(f)


def test_q1_blast_radius_matches_key(case):
    sid, key = case
    rows = client.ro_query(baseline_name(sid), queries.BLAST_RADIUS, {"src": key["src"]})
    # Q1 is bounded by MAX_HOPS, so jewels that are only reachable by a longer path are left out on purpose
    assert {r[0] for r in rows} == set(key["reachable_jewels"]) - set(key["long_path_jewels"])


def test_q2_attack_paths_match_true_paths(case):
    sid, key = case
    rows = client.ro_query(baseline_name(sid), queries.ATTACK_PATHS,
                         {"src": key["src"], "k": len(key["true_paths"]) + 10, "target": None})
    assert {tuple(r[2]) for r in rows} == {tuple(p) for p in key["true_paths"]}  # recall and precision 100%
    assert len(rows) == len(key["true_paths"])  # no duplicates, nothing extra


def test_q9_bfs_reaches_every_reachable_jewel(case):
    sid, key = case
    rows = client.ro_query(baseline_name(sid), queries.REACH_BFS, {"src": key["src"]})
    assert set(rows[0][0]) == set(key["reachable_jewels"])  # unbounded: includes long-path jewels


def test_q11_every_valid_need_is_ok_on_the_baseline(case):
    sid, key = case
    ok = dict(client.ro_query(baseline_name(sid), queries.NEEDS_CHECK))
    valid = [n["id"] for n in key["business_needs"] if n["valid"]]
    assert valid and all(ok.get(need_id) is True for need_id in valid)


def test_q6_max_flow_equals_min_cut_size(case):
    sid, key = case
    sbx = sandbox_name(sid, "p32test")
    client.delete_graph(sbx)
    client.get_graph(baseline_name(sid)).copy(sbx)
    try:
        protect_cap = client.ro_query(sbx, queries.PROTECT_CAP)[0][0]
        assert protect_cap == key["protect_cap"]
        protected = client.query(sbx, queries.PROTECT_NEEDS, {"protect_cap": protect_cap})
        # not asserting protected_eids: SPpaths may pick a different equal-length path than networkx (LLD 10.4)
        assert len(protected) == sum(n["valid"] for n in key["business_needs"])
        ((_, _, max_flow),) = client.ro_query(sbx, queries.MAX_FLOW, {"src": key["src"]})
        assert int(max_flow) == key["min_cut_size"]
    finally:
        client.delete_graph(sbx)

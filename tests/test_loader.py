"""Integration test: needs a running FalkorDB, otherwise the whole module is skipped."""
import json
from collections import Counter

import pytest

from core.graph import client, loader
from core.graph.schema import baseline_name

GRAPH = baseline_name("p26test")


@pytest.fixture(scope="module")
def data(falkordb):
    with open(loader.GENERATED_DIR / "s2.json", encoding="utf-8") as f:
        data = json.load(f)
    yield data
    client.delete_graph(GRAPH)


def test_counts_match_the_json(data):
    r = loader.load_graph(GRAPH, data)
    assert r == {"graph": GRAPH, "nodes": len(data["nodes"]), "edges": len(data["edges"]), "needs": len(data["needs"])}


def test_every_edge_is_attack_with_its_kind(data):
    loader.load_graph(GRAPH, data)
    kinds = dict(client.ro_query(GRAPH, "MATCH ()-[r:ATTACK]->() RETURN r.kind, count(r)"))
    assert kinds == dict(Counter(e["type"] for e in data["edges"]))
    assert client.ro_query(GRAPH, "MATCH ()-[r]->() RETURN count(r)")[0][0] == sum(kinds.values())  # nothing but :ATTACK


def test_edge_properties(data):
    loader.load_graph(GRAPH, data)
    (row,) = client.ro_query(GRAPH, "MATCH ()-[r:ATTACK {eid: 'e:s2:06'}]->() RETURN r.kind, r.cap, r.w, r.last_used_days, r.technique")
    assert row == ["CAN_PASS_ROLE", 1, 1, 300, "PassRole+CreateFunction"]
    (row,) = client.ro_query(GRAPH, "MATCH ()-[r {eid: 'e:s2:01'}]->() RETURN r.technique")
    assert row == [None]  # no technique on this edge


def test_business_needs_are_not_assets_and_have_no_edges(data):
    loader.load_graph(GRAPH, data)
    assert client.ro_query(GRAPH, "MATCH (n:BusinessNeed)-[r]-() RETURN count(r)")[0][0] == 0
    assert client.ro_query(GRAPH, "MATCH (n:BusinessNeed:Asset) RETURN count(n)")[0][0] == 0


def test_loading_twice_gives_the_same_counts(data):
    assert loader.load_graph(GRAPH, data) == loader.load_graph(GRAPH, data)

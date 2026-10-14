import copy
from collections import Counter

import pytest

from data.generator import TEAMS, build_background, summary, validate


@pytest.fixture(scope="module")
def graph():
    return build_background(42)


def test_same_seed_same_graph_different_seed_differs(graph):
    assert build_background(42) == graph
    assert build_background(7) != graph


def test_exact_node_counts_and_no_crown_jewels(graph):
    nodes, _, _ = summary(graph)
    assert dict(nodes) == {"User": 50, "Group": 12, "Role": 15, "Policy": 25, "Server": 10, "Database": 10, "Secret": 6}
    users = [n for n in graph["nodes"] if n["label"] == "User"]
    assert Counter(n["props"]["user_type"] for n in users) == {"human": 40, "service": 10}
    assert {n["props"]["team"] for n in users} == set(TEAMS)
    assert not any(n["props"]["crown_jewel"] for n in graph["nodes"] if n["label"] == "Database")


def test_every_human_in_one_or_two_groups_of_own_team(graph):
    by_id = {n["id"]: n for n in graph["nodes"]}
    humans = [n for n in graph["nodes"] if n["props"].get("user_type") == "human"]
    for h in humans:
        member = [e for e in graph["edges"] if e["type"] == "MEMBER_OF" and e["src"] == h["id"]]
        assert 1 <= len(member) <= 2
        for e in member:
            assert e["dst"].startswith(f"group:{h['props']['team']}-")
            assert by_id[e["dst"]]["label"] == "Group"


def test_secrets_and_service_users(graph):
    edges = graph["edges"]
    assert Counter(e["src"] for e in edges if e["type"] == "AUTHENTICATES_AS") == {f"secret:{i:02d}": 1 for i in range(1, 7)}
    stored = [e["dst"] for e in edges if e["type"] == "STORES_SECRET"]
    assert len(stored) == len(set(stored))  # each secret on at most one server
    svc = [n["id"] for n in graph["nodes"] if n["props"].get("user_type") == "service"]
    assert all(any(e["type"] == "CAN_ASSUME" and e["src"] == s for e in edges) for s in svc)


def test_eids_and_recent_share(graph):
    assert graph["edges"][0]["eid"] == "e:bg:0001"
    assert graph["edges"][-1]["eid"] == f"e:bg:{len(graph['edges']):04d}"
    _, _, recent = summary(graph)
    assert 30 <= recent <= 50


def test_validate_passes_and_catches_breakage(graph):
    validate(graph)
    bad = copy.deepcopy(graph)
    bad["edges"][0]["type"] = "CAN_READ"  # User -> Group is not allowed for CAN_READ
    with pytest.raises(ValueError):
        validate(bad)
    bad = copy.deepcopy(graph)
    bad["edges"][1]["eid"] = bad["edges"][0]["eid"]
    with pytest.raises(ValueError):
        validate(bad)
    bad = copy.deepcopy(graph)
    bad["nodes"].append(bad["nodes"][0])
    with pytest.raises(ValueError):
        validate(bad)
    bad = copy.deepcopy(graph)
    bad["edges"][0]["dst"] = "group:nope"
    with pytest.raises(ValueError):
        validate(bad)

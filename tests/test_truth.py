from pathlib import Path

import pytest

from data.generator import build_background
from data.needs import build_needs
from data.scenarios import load_scenario, plant
from data.truth import build_graph, check_expect, compute_truth, shortest_edge_path

DIR = Path(__file__).parent.parent / "data" / "scenarios"


def toy(edges, needs=(), jewel="db:j"):
    """edges: (eid, src, dst). Every node is a User except the jewel."""
    ids = {x for _, s, d in edges for x in (s, d)}
    nodes = [{"id": i, "label": "Database" if i == jewel else "User", "props": {"crown_jewel": i == jewel}} for i in sorted(ids)]
    es = [{"eid": eid, "type": "CAN_ASSUME", "src": s, "dst": d, "last_used_days": 1} for eid, s, d in edges]
    need_rows = [{"id": f"need:{a}-{b}", "principal_id": a, "resource_id": b, "reason": "x", "valid": True} for a, b in needs]
    return {"scenario": "toy", "src": "user:a", "nodes": nodes, "edges": es, "needs": need_rows}


TWO_PATHS = [("e1", "user:a", "user:x"), ("e2", "user:x", "db:j"), ("e3", "user:a", "user:y"), ("e4", "user:y", "db:j")]


def test_parallel_edges_pick_the_lower_eid():
    G = build_graph(toy([("e2", "user:a", "user:b"), ("e1", "user:a", "user:b")]))
    assert shortest_edge_path(G, "user:a", "user:b") == ("e1",)


def test_two_paths_cut_is_two_and_safe():
    truth = compute_truth(toy(TWO_PATHS))
    assert truth["min_cut_size"] == 2 and truth["protect_cap"] == 5 and truth["safe_fix_exists"] is True


def test_fully_protected_paths_have_no_safe_fix():
    needs = [("user:a", "user:x"), ("user:x", "db:j"), ("user:a", "user:y"), ("user:y", "db:j")]
    truth = compute_truth(toy(TWO_PATHS, needs))
    assert truth["protected_eids"] == ["e1", "e2", "e3", "e4"]
    assert truth["min_cut_size"] >= truth["protect_cap"] and truth["safe_fix_exists"] is False


def test_invalid_needs_are_not_protected():
    graph = toy(TWO_PATHS, [("user:a", "user:x")])
    graph["needs"][0]["valid"] = False
    assert compute_truth(graph)["protected_eids"] == []


def test_seven_hop_chain_is_a_long_path_jewel():
    nodes = ["user:a", "user:b", "user:c", "user:d", "user:e", "user:f", "user:g", "db:j"]
    edges = [(f"e{i}", nodes[i], nodes[i + 1]) for i in range(7)]
    truth = compute_truth(toy(edges))
    assert truth["reachable_jewels"] == ["db:j"] and truth["long_path_jewels"] == ["db:j"]
    assert truth["true_paths"] == []


def test_no_reachable_jewel_means_cut_zero():
    truth = compute_truth(toy([("e1", "user:a", "user:b"), ("e2", "user:c", "db:j")]))
    assert truth["reachable_jewels"] == [] and truth["min_cut_size"] == 0


@pytest.fixture(scope="module")
def scenarios():
    background = build_background(42)
    out = {}
    for sid in ("s1", "s2", "s3"):
        sc = load_scenario(DIR / f"{sid}.yaml")
        graph = plant(background, sc)
        graph["needs"] = build_needs(graph)
        out[sid] = (sc, graph)
    return out


@pytest.mark.parametrize("sid", ["s1", "s2", "s3"])
def test_scenarios_match_their_expect_block(scenarios, sid):
    sc, graph = scenarios[sid]
    check_expect(compute_truth(graph), sc["expect"])


def test_wrong_expect_raises_and_lists_every_mismatch(scenarios):
    sc, graph = scenarios["s2"]
    with pytest.raises(ValueError, match="min_cut") as err:
        check_expect(compute_truth(graph), {**sc["expect"], "min_cut": 99, "paths": 50})
    assert "s2" in str(err.value) and "paths" in str(err.value)


def test_answer_key_has_exactly_the_lld_keys(scenarios):
    _, graph = scenarios["s2"]
    assert sorted(compute_truth(graph)) == sorted(
        ["scenario", "src", "crown_jewels", "reachable_jewels", "true_paths", "long_path_jewels",
         "protected_eids", "protect_cap", "min_cut_size", "safe_fix_exists", "business_needs"])


@pytest.mark.parametrize("sid", ["s1", "s2", "s3"])
def test_same_input_twice_gives_identical_answer_key(scenarios, sid):
    _, graph = scenarios[sid]
    assert compute_truth(graph) == compute_truth(graph)

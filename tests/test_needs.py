import logging
from pathlib import Path

import pytest

from data.generator import build_background
from data.needs import build_needs
from data.scenarios import load_scenario, plant

DIR = Path(__file__).parent.parent / "data" / "scenarios"


def toy(last_used, needs=()):
    """u -> g -> db, with the second edge's last_used_days set by the test."""
    nodes = [{"id": "user:u", "label": "User"}, {"id": "group:g", "label": "Group"}, {"id": "db:d", "label": "Database"}]
    edges = [{"eid": "e1", "type": "MEMBER_OF", "src": "user:u", "dst": "group:g", "last_used_days": 1},
             {"eid": "e2", "type": "CAN_READ", "src": "group:g", "dst": "db:d", "last_used_days": last_used}]
    return {"nodes": nodes, "edges": edges, "needs": list(needs)}


def test_edge_one_day_too_old_gives_no_usage_need():
    assert build_needs(toy(31), usage_days=30) == []


def test_edge_exactly_at_the_limit_gives_a_usage_need():
    (need,) = build_needs(toy(30), usage_days=30)
    assert need == {"id": "need:usage:user:u:db:d", "principal_id": "user:u", "resource_id": "db:d",
                    "reason": "used in the last 30 days", "source": "usage", "valid": True}


def test_explicit_need_without_a_path_is_invalid_and_warns(caplog):
    explicit = {"id": "need:x", "principal_id": "user:u", "resource_id": "db:d", "reason": "why"}
    graph = toy(99, [explicit])
    graph["edges"].pop()  # no group -> db edge, so no path at all
    with caplog.at_level(logging.WARNING):
        (need,) = build_needs(graph)
    assert need["source"] == "explicit" and need["valid"] is False
    assert "need need:x: no path from user:u to db:d" in caplog.text


def test_explicit_need_with_a_path_is_valid_and_not_duplicated_by_usage():
    explicit = {"id": "need:x", "principal_id": "user:u", "resource_id": "db:d", "reason": "why"}
    needs = build_needs(toy(5, [explicit]))
    assert [(n["id"], n["source"], n["valid"]) for n in needs] == [("need:x", "explicit", True)]


@pytest.fixture(scope="module")
def scenarios():
    background = build_background(42)
    out = {}
    for sid in ("s1", "s2", "s3"):
        sc = load_scenario(DIR / f"{sid}.yaml")
        out[sid] = (sc, plant(background, sc))
    return out


def test_s3_explicit_need_is_not_duplicated_by_usage(scenarios):
    _, graph = scenarios["s3"]
    pair = [n for n in build_needs(graph) if (n["principal_id"], n["resource_id"]) == ("user:mia", "db:s3-wiki")]
    assert [(n["id"], n["source"]) for n in pair] == [("need:mia-wiki", "explicit")]


@pytest.mark.parametrize("sid", ["s1", "s2", "s3"])
def test_each_scenario_has_a_usage_need_for_a_background_user(scenarios, sid):
    sc, graph = scenarios[sid]
    planted = {n["id"] for n in sc["nodes"]}
    assert [n for n in build_needs(graph) if n["source"] == "usage" and n["principal_id"] not in planted]


@pytest.mark.parametrize("sid", ["s1", "s2", "s3"])
def test_same_input_twice_gives_identical_output(scenarios, sid):
    _, graph = scenarios[sid]
    assert build_needs(graph) == build_needs(graph)

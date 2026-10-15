import copy
from pathlib import Path

import pytest

from data.generator import build_background, validate
from data.scenarios import load_scenario, plant, reaches
from tests.helpers import SCENARIOS

DIR = Path(__file__).parent.parent / "data" / "scenarios"
IDS = SCENARIOS


@pytest.fixture(scope="module")
def background():
    return build_background(42)


def load(sid):
    return load_scenario(DIR / f"{sid}.yaml")


@pytest.mark.parametrize("sid", IDS)
def test_scenario_plants_validates_and_jewels_reachable(background, sid):
    sc = load(sid)
    combined = plant(background, sc)
    validate(combined)
    assert set(sc["expect"]["reachable_jewels"]) <= reaches(combined, sc["src"])


@pytest.mark.parametrize("sid", IDS)
def test_background_edges_never_point_at_planted_nodes(background, sid):
    sc = load(sid)
    combined = plant(background, sc)
    planted = {n["id"] for n in sc["nodes"]}
    assert not [e for e in combined["edges"] if e["eid"].startswith("e:bg:") and e["dst"] in planted]


@pytest.mark.parametrize("sid", IDS)
def test_crown_jewels_only_on_planted_nodes(background, sid):
    sc = load(sid)
    combined = plant(background, sc)
    planted = {n["id"] for n in sc["nodes"]}
    jewels = {n["id"] for n in combined["nodes"] if n["props"].get("crown_jewel")}
    assert jewels and jewels <= planted


@pytest.mark.parametrize("sid", IDS)
def test_planting_twice_is_identical(background, sid):
    assert plant(background, load(sid)) == plant(build_background(42), load(sid))


def test_planting_does_not_change_the_background(background):
    before = copy.deepcopy(background)
    plant(background, load("s2"))
    assert background == before


def test_missing_key_raises(tmp_path):
    (tmp_path / "bad.yaml").write_text("id: x\ntitle: t\n")
    with pytest.raises(ValueError, match="missing key"):
        load_scenario(tmp_path / "bad.yaml")


def test_wrong_eid_prefix_raises(tmp_path):
    text = (DIR / "s1.yaml").read_text().replace('"e:s1:01"', '"e:zz:01"')
    (tmp_path / "bad.yaml").write_text(text)
    with pytest.raises(ValueError, match="must start with"):
        load_scenario(tmp_path / "bad.yaml")


def test_background_link_to_planted_node_raises(background):
    sc = load("s1")
    sc["background_links"][0]["dst"] = "group:s1-analysts"
    with pytest.raises(ValueError, match="background node"):
        plant(background, sc)


def test_planted_id_clashing_with_background_raises(background):
    sc = load("s1")
    sc["nodes"][1]["id"] = "group:finance-1"
    with pytest.raises(ValueError, match="already in the background"):
        plant(background, sc)

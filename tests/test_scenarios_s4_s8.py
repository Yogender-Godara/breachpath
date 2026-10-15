"""Scenario-specific checks for S4, S6, S7, S8 on the real FalkorDB. Expected values were worked out by hand from the
residual BFS (the S4 path, the S6 hub, the S7 8-hop chain, the S8 collateral counts), not copied from a run."""
import json

import pytest

from core.graph import client, loader, queries
from core.graph.mincut import compute_min_cut
from core.graph.schema import baseline_name, sandbox_name
from core.tools import write
from tests.helpers import protected_copy


def eids(sid, *numbers):
    return [f"e:{sid}:{n:02d}" for n in numbers]


def cuts(sid, src, tag):
    """(max_flow, source-side eids, sink-side eids, witnesses) on a copy with every valid need protected."""
    with protected_copy(sid, tag) as sbx:
        result = compute_min_cut(sbx, src)
    source, sink = result["candidates"]
    assert (source["name"], sink["name"]) == ("source_side", "sink_side")
    return int(result["max_flow"]), source["eids"], sink["eids"], source["witness"]


def baseline(sid, cypher, **params):
    loader.load_scenario(sid)
    return client.ro_query(baseline_name(sid), cypher, params)


# --- S4 precision: decoys must not show up ---

def test_s4_exactly_one_path_and_one_jewel(falkordb):
    rows = baseline("s4", queries.ATTACK_PATHS, src="user:leo", k=50, target=None)
    assert [r[2] for r in rows] == [eids("s4", 1, 2, 3, 4)]  # not the db:s4-logs path, not the auditor's db:Read
    assert [r[0] for r in baseline("s4", queries.BLAST_RADIUS, src="user:leo")] == ["db:s4-vault"]


def test_s4_cuts(falkordb):
    max_flow, source, sink, _ = cuts("s4", "user:leo", "p47s4")
    assert (max_flow, source, sink) == (1, ["e:s4:01"], ["e:s4:04"])


# --- S6 trap: the busiest node (the hub group) is on no attack path ---

def test_s6_betweenness_top_is_the_hub_but_the_cuts_avoid_it(falkordb):
    n = baseline("s6", queries.NODE_COUNT)[0][0]
    top = baseline("s6", queries.BETWEENNESS, n=n, top=1)
    assert top[0][0] == "group:s6-all-staff"

    with open(loader.GENERATED_DIR / "s6.json", encoding="utf-8") as f:
        graph = json.load(f)
    hub_eids = {e["eid"] for e in graph["edges"] if "group:s6-all-staff" in (e["src"], e["dst"])}
    assert len(hub_eids) == 23  # 20 staff memberships + 3 background role assumptions

    max_flow, source, sink, _ = cuts("s6", "user:kai", "p47s6")
    assert (max_flow, source, sink) == (2, eids("s6", 1, 5), eids("s6", 4, 9))
    assert not hub_eids & (set(source) | set(sink))


# --- S7 completeness: a path longer than MAX_HOPS is invisible to Q1/Q2 but not to the BFS and the flow ---

def test_s7_eight_hop_jewel_only_the_unbounded_queries_see(falkordb):
    assert baseline("s7", queries.BLAST_RADIUS, src="user:nia") == []  # beyond 6 hops
    assert baseline("s7", queries.ATTACK_PATHS, src="user:nia", k=50, target=None) == []
    assert baseline("s7", queries.REACH_BFS, src="user:nia")[0][0] == ["db:s7-billing"]


def test_s7_cuts_and_witness_is_the_whole_chain(falkordb):
    max_flow, source, sink, witness = cuts("s7", "user:nia", "p47s7")
    assert (max_flow, source, sink) == (1, ["e:s7:01"], ["e:s7:08"])
    assert witness["e:s7:01"] == eids("s7", *range(1, 9))


# --- S8 collateral: two equal-size cuts, the Planner's real decision ---

INC = "p47inc"
TOKEN = "worker-1"
AUTH = {"incident_id": INC, "lock_token": TOKEN}
S8_SBX = sandbox_name("s8", INC)
ANALYST_PAIRS = [[f"user:s8-analyst-{i:02d}", "db:s8-reports"] for i in range(1, 13)]


def clean(redis):
    for g in client.get_db().list_graphs():
        if f":sbx:{INC}" in g:
            client.delete_graph(g)
    redis.zrem("sbx:expiry", S8_SBX)
    redis.delete(f"lock:inc:{INC}", f"quota:inc:{INC}:sims", *redis.keys(f"reach:*:sbx:{INC}:*"))


@pytest.fixture
def s8_prepared(falkordb):
    redis = client.get_db().connection
    loader.load_scenario("s8")
    clean(redis)
    redis.set(f"lock:inc:{INC}", TOKEN)
    assert write.create_sandbox("s8", **AUTH)["ok"]
    assert write.protect_business_paths(S8_SBX, **AUTH)["ok"]
    yield S8_SBX
    clean(redis)


def simulate(sbx, cut):
    result = write.simulate_cut(sbx, "user:max", cut, **AUTH)
    assert result["ok"] is True, result
    return result["data"]


def test_s8_equal_size_cuts_differ_only_in_collateral(s8_prepared):
    result = compute_min_cut(s8_prepared, "user:max")
    source, sink = result["candidates"]
    assert (int(result["max_flow"]), source["eids"], sink["eids"]) == (1, ["e:s8:05"], ["e:s8:07"])

    cheap = simulate(s8_prepared, source["eids"])
    print("s8 source_side", json.dumps(cheap))
    assert cheap["blocks_attack"] is True and cheap["needs_ok"] is True
    assert cheap["collateral"] == {"count": 0, "weighted": 0, "pairs": []}

    costly = simulate(s8_prepared, sink["eids"])
    print("s8 sink_side", json.dumps(costly))
    assert costly["blocks_attack"] is True and costly["needs_ok"] is True
    # all 12 analyst pairs were last used 150 days ago (> 90): stale, weight 1 each
    assert costly["collateral"] == {"count": 12, "weighted": 12, "pairs": ANALYST_PAIRS}

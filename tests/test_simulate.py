"""simulate_cut and collateral. A test incident owns its graphs; they and the Redis keys are removed afterwards."""
import json

import pytest
import yaml

from core.graph import client, loader
from core.graph.schema import baseline_name, sandbox_name
from core.tools import collateral, write
from data.scenarios import SCENARIO_DIR

INC = "p44inc"
TOKEN = "worker-1"
AUTH = {"incident_id": INC, "lock_token": TOKEN}
SBX = sandbox_name("s2", INC)
TOY = sandbox_name("toy", INC)
SRC = "user:ravi"


def data(result):
    assert result["ok"] is True, result
    return result["data"]


def code(result):
    assert result["ok"] is False
    return result["error"]["code"]


def clean(redis):
    for g in client.get_db().list_graphs():
        if f":sbx:{INC}" in g:
            client.delete_graph(g)
    redis.zrem("sbx:expiry", SBX)
    redis.delete(f"lock:inc:{INC}", f"quota:inc:{INC}:sims", *redis.keys(f"reach:*:sbx:{INC}:*"))


@pytest.fixture
def redis(falkordb):
    r = client.get_db().connection
    loader.load_scenario("s2")
    clean(r)
    r.set(f"lock:inc:{INC}", TOKEN)
    yield r
    clean(r)


@pytest.fixture
def prepared(redis):
    data(write.create_sandbox("s2", **AUTH))
    data(write.protect_business_paths(SBX, **AUTH))
    return SBX


def stale_days(eid):
    with open(SCENARIO_DIR / "s2.yaml", encoding="utf-8") as f:
        return next(e["last_used_days"] for e in yaml.safe_load(f)["edges"] if e["eid"] == eid)


def test_s2_source_side_cut(prepared):
    result = data(write.simulate_cut(prepared, SRC, ["e:s2:05", "e:s2:10"], **AUTH))
    print("source_side", json.dumps(result))
    assert result["blocks_attack"] is True and result["needs_ok"] is True and result["broken_needs"] == []
    assert (result["size"], result["min_size"]) == (2, 2)
    assert result["collateral"] == {"count": 0, "weighted": 0, "pairs": []}
    assert result["stale_days_total"] == 610 == stale_days("e:s2:05") + stale_days("e:s2:10")


def test_s2_sink_side_cut(prepared):
    result = data(write.simulate_cut(prepared, SRC, ["e:s2:08", "e:s2:11"], **AUTH))
    print("sink_side", json.dumps(result))
    assert result["blocks_attack"] is True and result["needs_ok"] is True
    assert (result["size"], result["min_size"], result["collateral"]["count"]) == (2, 2, 0)
    assert result["stale_days_total"] == stale_days("e:s2:08") + stale_days("e:s2:11") == 121


def test_partial_and_protected_cuts_do_not_block(prepared):
    assert data(write.simulate_cut(prepared, SRC, ["e:s2:05"], **AUTH))["blocks_attack"] is False
    protected = data(write.simulate_cut(prepared, SRC, ["e:s2:01"], **AUTH))
    assert protected["refused_protected"] == ["e:s2:01"] and protected["blocks_attack"] is False and protected["size"] == 0
    unknown = data(write.simulate_cut(prepared, SRC, ["e:nope"], **AUTH))
    assert unknown["missing"] == ["e:nope"]


def test_sandbox_is_unchanged_nothing_is_left_behind_and_the_seventh_is_refused(prepared):
    for _ in range(6):
        data(write.simulate_cut(prepared, SRC, ["e:s2:05", "e:s2:10"], **AUTH))
    assert code(write.simulate_cut(prepared, SRC, ["e:s2:05"], **AUTH)) == "LIMIT_EXCEEDED"
    assert client.ro_query(prepared, "MATCH ()-[r]->() RETURN count(r)")[0][0] == 179
    assert not [g for g in client.get_db().list_graphs() if "-sim-" in g]


def test_guards_and_input_checks(prepared):
    assert code(write.simulate_cut(prepared, SRC, ["e:s2:05"], incident_id=INC, lock_token="wrong")) == "GUARD_REFUSED"
    assert code(write.simulate_cut(baseline_name("s2"), SRC, ["e:s2:05"], **AUTH)) == "GUARD_REFUSED"
    assert code(write.simulate_cut(prepared, SRC, [], **AUTH)) == "BAD_INPUT"
    assert code(write.simulate_cut(prepared, "user:nobody", ["e:s2:05"], **AUTH)) == "NOT_FOUND"


def test_new_copy_deletes_the_cached_snapshot(prepared, redis):
    data(write.simulate_cut(prepared, SRC, ["e:s2:05"], **AUTH))
    assert redis.exists(f"reach:{SBX}:{SRC}")
    data(write.create_sandbox("s2", **AUTH))  # reused: the snapshot stays
    assert redis.exists(f"reach:{SBX}:{SRC}")
    client.delete_graph(SBX)
    data(write.create_sandbox("s2", **AUTH))  # a new copy: the snapshot goes
    assert not redis.exists(f"reach:{SBX}:{SRC}")


def test_collateral_is_weighted_by_recent_use(redis):
    def node(label, nid, **props):
        return {"id": nid, "label": label, "props": {"id": nid, "name": nid, **props}}

    def edge(eid, kind, src, dst, days):
        return {"eid": eid, "type": kind, "src": src, "dst": dst, "last_used_days": days}

    nodes = [node("User", "user:s"), node("User", "user:u"), node("User", "user:w"), node("Policy", "policy:a"),
             node("Database", "db:j", crown_jewel=True)]
    edges = [edge("e1", "HAS_POLICY", "user:s", "policy:a", 5), edge("e2", "HAS_POLICY", "user:u", "policy:a", 10),
             edge("e3", "HAS_POLICY", "user:w", "policy:a", 200), edge("e4", "CAN_READ", "policy:a", "db:j", 10)]
    loader.load_graph(TOY, {"nodes": nodes, "edges": edges, "needs": []})
    result = data(write.simulate_cut(TOY, "user:s", ["e4"], **AUTH))
    assert result["blocks_attack"] is True and result["needs_ok"] is True
    assert result["collateral"] == {"count": 2, "weighted": 4, "pairs": [["user:u", "db:j"], ["user:w", "db:j"]]}


# --- pure, no database ---

EDGES = [("e1", "u", "a", 1, "HAS_POLICY", 10), ("e2", "a", "j", 1, "CAN_READ", 10), ("e3", "w", "a", 1, "HAS_POLICY", 200)]
REACHES = {"u": ["j"], "w": ["j"]}


def test_recent_pairs_needs_every_edge_on_the_path_recent():
    assert collateral.recent_pairs(EDGES, REACHES, 90) == [["u", "j"]]  # w -> a was last used 200 days ago
    assert collateral.recent_pairs(EDGES, REACHES, 5) == []
    assert collateral.recent_pairs(EDGES, REACHES, 200) == [["u", "j"], ["w", "j"]]


def test_collateral_counts_lost_pairs_and_weights_them():
    before = {"reaches": REACHES, "recent": [["u", "j"]]}
    assert collateral.collateral(before, {"u": [], "w": []}) == {
        "count": 2, "weighted": 4, "pairs": [["u", "j"], ["w", "j"]]}
    assert collateral.collateral(before, {"u": ["j"], "w": []}) == {"count": 1, "weighted": 1, "pairs": [["w", "j"]]}

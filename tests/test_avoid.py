"""compute_min_cut(avoid_eids): an operator instruction as a graph constraint (guarded throw-away copy). Real FalkorDB, no LLM."""
import pytest

from core.graph import client, loader
from core.graph.schema import sandbox_name
from core.tools import read, write

INC = "avoid1"
TOKEN = "worker-avoid"


def data(result):
    assert result["ok"] is True, result
    return result["data"]


def code(result):
    assert result["ok"] is False
    return result["error"]["code"]


@pytest.fixture
def lock(falkordb):
    r = client.get_db().connection
    r.set(f"lock:inc:{INC}", TOKEN)
    yield {"incident_id": INC, "lock_token": TOKEN}
    for sid in ("s2", "s5", "s8"):
        client.delete_graph(sandbox_name(sid, INC))
        r.zrem("sbx:expiry", sandbox_name(sid, INC))
    r.delete(f"lock:inc:{INC}", f"quota:inc:{INC}:sims")


def prepared(sid, auth):
    loader.load_scenario(sid)
    sandbox = sandbox_name(sid, INC)
    client.delete_graph(sandbox)
    data(write.create_sandbox(sid, **auth))
    data(write.protect_business_paths(sandbox, **auth))
    return sandbox


def sims(sid):
    return [g for g in client.get_db().list_graphs() if f"{INC}-sim-" in g]


def test_s8_avoiding_the_legacy_trust_edge_moves_the_cut_one_edge_down(lock):
    sandbox = prepared("s8", lock)
    plain = {c["name"]: c["eids"] for c in data(read.compute_min_cut(sandbox, "user:max"))["candidates"]}
    assert plain == {"source_side": ["e:s8:05"], "sink_side": ["e:s8:07"]}  # the default fix
    cut = data(read.compute_min_cut(sandbox, "user:max", ["e:s8:05"], **lock))
    assert cut["safe"] is True and cut["avoided"] == ["e:s8:05"]
    assert {c["name"]: c["eids"] for c in cut["candidates"]} == {"source_side": ["e:s8:06"], "sink_side": ["e:s8:07"]}
    assert sims("s8") == []  # the throw-away copy is gone
    assert client.get_db().connection.get(f"quota:inc:{INC}:sims") == "1"  # and it counted as a simulation
    # the sandbox itself is unchanged: the avoided edge is neither deleted nor protected
    assert data(read.compute_min_cut(sandbox, "user:max"))["candidates"][0]["eids"] == ["e:s8:05"]


def test_s2_avoiding_the_build_server_secret_edge_cuts_the_next_edge_of_that_path(lock):
    sandbox = prepared("s2", lock)
    # by hand: path 2 is e:s2:09 -> 10 -> 11 -> 12 -> 13 and 09, 12, 13 are business-protected; with 10 avoided the only cuttable edge left is 11
    cut = data(read.compute_min_cut(sandbox, "user:ravi", ["e:s2:10"], **lock))
    assert cut["safe"] is True
    for candidate in cut["candidates"]:
        assert "e:s2:11" in candidate["eids"] and "e:s2:10" not in candidate["eids"]
    assert sims("s2") == []


def test_when_the_constraint_makes_a_safe_cut_impossible_it_says_which_avoided_edges(lock):
    sandbox = prepared("s2", lock)
    cut = data(read.compute_min_cut(sandbox, "user:ravi", ["e:s2:10", "e:s2:11"], **lock))  # nothing else on path 2 may be cut
    assert cut["safe"] is False and cut["impossible_because"] == ["e:s2:10", "e:s2:11"]
    assert "e:s2:10" in cut["note"] and "every cut would have to remove it" in cut["note"]
    plain_unsafe = prepared("s5", lock)  # S5 has no safe cut with or without the constraint
    cut = data(read.compute_min_cut(plain_unsafe, "user:zoe", ["e:s5:04"], **lock))
    assert cut["safe"] is False and cut["impossible_because"] == [] and cut["note"] == "no safe cut exists even without the constraint"


def test_avoid_is_validated_and_guarded(lock):
    sandbox = prepared("s8", lock)
    assert code(read.compute_min_cut(sandbox, "user:max", ["e:nope"], **lock)) == "BAD_INPUT"  # an unknown eid
    assert code(read.compute_min_cut(sandbox, "user:max", ["e:s8:05"])) == "BAD_INPUT"  # no incident / lock given
    assert code(read.compute_min_cut(sandbox, "user:max", ["e:s8:05"], incident_id=INC, lock_token="someone-else")) == "GUARD_REFUSED"
    assert code(read.compute_min_cut(sandbox, "user:max", ["e:s8:05"], incident_id="other", lock_token=TOKEN)) == "GUARD_REFUSED"  # not this incident's sandbox
    baseline = "t:s8:baseline"
    assert code(read.compute_min_cut(baseline, "user:max", ["e:s8:05"], **lock)) == "GUARD_REFUSED"  # never on a baseline
    assert data(read.compute_min_cut(sandbox, "user:max", [], **lock))["candidates"][0]["eids"] == ["e:s8:05"]  # empty = the normal tool, no copy
    assert sims("s8") == []


def test_every_constrained_call_uses_one_of_the_simulations(lock):
    sandbox = prepared("s8", lock)
    for _ in range(6):
        data(read.compute_min_cut(sandbox, "user:max", ["e:s8:05"], **lock))
    assert code(read.compute_min_cut(sandbox, "user:max", ["e:s8:05"], **lock)) == "LIMIT_EXCEEDED"  # 6 per incident, like simulate_cut
    assert sims("s8") == []

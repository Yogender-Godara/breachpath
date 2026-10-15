"""Write tools on S2. A test incident owns the sandbox; its sandbox, expiry entry, lock and quota keys are removed afterwards."""
import time

import pytest

from core.graph import client, loader, queries
from core.graph.schema import baseline_name, sandbox_name
from core.tools import read, write

BASE = baseline_name("s2")
INC = "p43inc"
TOKEN = "worker-1"
SBX = sandbox_name("s2", INC)
SRC = "user:ravi"
AUTH = {"incident_id": INC, "lock_token": TOKEN}


@pytest.fixture
def redis(falkordb):
    r = client.get_db().connection
    loader.load_scenario("s2")
    client.delete_graph(SBX)
    r.set(f"lock:inc:{INC}", TOKEN)
    yield r
    client.delete_graph(SBX)
    r.zrem("sbx:expiry", SBX)
    r.delete(f"lock:inc:{INC}", f"quota:inc:{INC}:sims")


def data(result):
    assert result["ok"] is True, result
    return result["data"]


def code(result):
    assert result["ok"] is False
    return result["error"]["code"]


def test_happy_path_cuts_the_attack_and_keeps_the_business(redis):
    assert data(write.create_sandbox("s2", **AUTH)) == {"sandbox": SBX, "reused": False}
    data(write.protect_business_paths(SBX, **AUTH))
    fix = data(write.apply_fix(SBX, ["e:s2:05", "e:s2:10"], "cut the escalation and the saved password", **AUTH))
    assert fix["deleted"] == ["e:s2:05", "e:s2:10"] and fix["missing"] == [] and fix["refused_protected"] == []
    assert client.ro_query(SBX, queries.REACH_BFS, {"src": SRC})[0][0] == []  # no jewel reachable any more
    assert client.ro_query(SBX, queries.MAX_FLOW, {"src": SRC})[0][2] == 0.0
    access = data(read.check_business_access(SBX))
    assert access and all(a["ok"] for a in access)


def test_protect_business_paths(redis):
    data(write.create_sandbox("s2", **AUTH))
    first = data(write.protect_business_paths(SBX, **AUTH))
    assert "e:s2:01" in first["protected_eids"] and "e:s2:05" not in first["protected_eids"]
    assert first["unreachable_needs"] == [] and first["protect_cap"] == 180
    needs = client.ro_query(SBX, "MATCH ()-[r {eid: 'e:s2:01'}]->() RETURN r.needs")[0][0]
    assert "need:ravi-staging" in needs and len(needs) == len(set(needs))  # recorded, no duplicates
    assert data(write.protect_business_paths(SBX, **AUTH)) == first  # twice gives the same result


def test_apply_fix_refuses_protected_and_reports_missing_and_is_idempotent(redis):
    data(write.create_sandbox("s2", **AUTH))
    data(write.protect_business_paths(SBX, **AUTH))
    refused = data(write.apply_fix(SBX, ["e:s2:01"], "try a protected edge", **AUTH))
    assert refused["refused_protected"] == ["e:s2:01"] and refused["deleted"] == []
    assert client.ro_query(SBX, "MATCH ()-[r {eid: 'e:s2:01'}]->() RETURN count(r)")[0][0] == 1
    assert data(write.apply_fix(SBX, ["e:nope"], "unknown", **AUTH))["missing"] == ["e:nope"]
    assert data(write.apply_fix(SBX, ["e:s2:05"], "first", **AUTH))["deleted"] == ["e:s2:05"]
    second = data(write.apply_fix(SBX, ["e:s2:05"], "again", **AUTH))
    assert second["deleted"] == [] and second["missing"] == ["e:s2:05"]


def test_create_sandbox_twice_is_reused_and_expires(redis):
    assert data(write.create_sandbox("s2", **AUTH))["reused"] is False
    assert data(write.create_sandbox("s2", **AUTH))["reused"] is True
    assert redis.zscore("sbx:expiry", SBX) > time.time()
    assert code(write.create_sandbox("s9", **AUTH)) == "NOT_FOUND"


def test_guards_apply_through_the_tools(redis):
    data(write.create_sandbox("s2", **AUTH))
    assert code(write.apply_fix(SBX, ["e:s2:05"], "x", incident_id=INC, lock_token="wrong")) == "GUARD_REFUSED"
    assert code(write.apply_fix(BASE, ["e:s2:05"], "x", **AUTH)) == "GUARD_REFUSED"
    assert code(write.protect_business_paths(BASE, **AUTH)) == "GUARD_REFUSED"
    assert code(write.create_sandbox("s2", incident_id=INC, lock_token="wrong")) == "GUARD_REFUSED"
    assert code(write.apply_fix(SBX, [], "x", **AUTH)) == "BAD_INPUT"
    assert code(write.apply_fix(SBX, ["e:s2:05"], "  ", **AUTH)) == "BAD_INPUT"
    assert code(write.apply_fix(SBX, [f"e{i}" for i in range(51)], "x", **AUTH)) == "BAD_INPUT"
    assert client.ro_query(SBX, "MATCH ()-[r {eid: 'e:s2:05'}]->() RETURN count(r)")[0][0] == 1  # nothing was deleted


def test_baseline_is_unchanged_after_all_of_it(redis):
    data(write.create_sandbox("s2", **AUTH))
    data(write.protect_business_paths(SBX, **AUTH))
    data(write.apply_fix(SBX, ["e:s2:05", "e:s2:10"], "cut", **AUTH))
    assert client.ro_query(BASE, "MATCH ()-[r]->() RETURN count(r)")[0][0] == 179
    assert client.ro_query(BASE, "MATCH ()-[r]->() WHERE r.protected IS NOT NULL RETURN count(r)")[0][0] == 0
    assert len(data(read.find_attack_paths(BASE, SRC))["paths"]) == 2

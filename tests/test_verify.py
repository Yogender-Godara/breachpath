"""verify_fix. A test incident owns its sandbox; it and the Redis keys are removed afterwards."""
import json

import pytest

from core.graph import client, loader
from core.graph.schema import baseline_name, sandbox_name
from core.tools import verify, write

INC = "p45inc"
TOKEN = "worker-1"
AUTH = {"incident_id": INC, "lock_token": TOKEN}
SBX = sandbox_name("s2", INC)
SRC = "user:ravi"


def data(result):
    assert result["ok"] is True, result
    return result["data"]


def code(result):
    assert result["ok"] is False
    return result["error"]["code"]


@pytest.fixture
def redis(falkordb):
    r = client.get_db().connection
    loader.load_scenario("s2")
    client.delete_graph(SBX)
    r.set(f"lock:inc:{INC}", TOKEN)
    yield r
    client.delete_graph(SBX)
    r.zrem("sbx:expiry", SBX)
    r.delete(f"lock:inc:{INC}", f"quota:inc:{INC}:sims", *r.keys(f"reach:*:sbx:{INC}:*"))


@pytest.fixture
def prepared(redis):
    data(write.create_sandbox("s2", **AUTH))
    data(write.protect_business_paths(SBX, **AUTH))
    return SBX


def fix(eids):
    data(write.apply_fix(SBX, eids, "test", **AUTH))


def test_s2_done_right_passes(prepared):
    fix(["e:s2:05", "e:s2:10"])
    verdict = data(verify.verify_fix(SBX, SRC))
    print(json.dumps(verdict, indent=1)[:900])
    assert verdict["pass"] is True and verdict["attack_max_flow"] == 0.0 and verdict["bfs_reached_jewels"] == []
    assert verdict["business"] and all(b["ok"] for b in verdict["business"])
    assert verdict["collateral"]["count"] == 0 and verdict["already_broken"] == [] and verdict["failure_detail"] is None


def test_unfixed_sandbox_fails(prepared):
    verdict = data(verify.verify_fix(SBX, SRC))
    assert verdict["pass"] is False and verdict["failure_detail"].startswith("attack not blocked: max_flow 2.0")


def test_partial_fix_still_reaches_one_jewel(prepared):
    fix(["e:s2:05"])
    verdict = data(verify.verify_fix(SBX, SRC))
    assert verdict["pass"] is False and verdict["bfs_reached_jewels"] == ["db:s2-payments"]


def test_a_broken_need_is_named(prepared):
    fix(["e:s2:05", "e:s2:10"])
    client.query(SBX, "MATCH ()-[r {eid: 'e:s2:09'}]->() DELETE r")  # a bug: a protected edge is gone
    verdict = data(verify.verify_fix(SBX, SRC))
    assert verdict["pass"] is False and verdict["failure_detail"] == "need need:ravi-build broken"


def test_already_broken_needs_are_listed_but_do_not_fail(prepared):
    fix(["e:s2:05", "e:s2:10"])
    client.query(SBX, "CREATE (:BusinessNeed {id: 'need:ghost', principal_id: 'user:ravi', resource_id: 'server:nowhere', "
                      "reason: 'x', source: 'explicit', valid: false})")
    verdict = data(verify.verify_fix(SBX, SRC))
    assert verdict["already_broken"] == ["need:ghost"] and verdict["pass"] is True


def test_baseline_is_refused_and_unchanged(prepared):
    assert code(verify.verify_fix(baseline_name("s2"), SRC)) == "BAD_INPUT"
    assert code(verify.verify_fix(SBX, "user:nobody")) == "NOT_FOUND"
    assert code(verify.verify_fix(sandbox_name("s2", "p45none"), SRC)) == "NOT_FOUND"
    assert client.ro_query(baseline_name("s2"), "MATCH ()-[r]->() RETURN count(r)")[0][0] == 179
    assert client.ro_query(baseline_name("s2"), "MATCH ()-[r]->() WHERE r.protected IS NOT NULL RETURN count(r)")[0][0] == 0


# --- the pass rule and the failure_detail formats, no database ---

OK = [{"need_id": "n1", "ok": True}]


def test_judge_pass():
    assert verify.judge(0.0, [], OK) == (True, None)


def test_judge_proof_mismatch_is_flagged():
    passed, detail = verify.judge(0.0, ["db:x"], [])
    assert passed is False and detail == "proof mismatch: max_flow 0.0, bfs reached ['db:x']"
    assert verify.judge(2.0, [], OK)[1].startswith("proof mismatch")


def test_judge_attack_and_needs_formats():
    assert verify.judge(2.0, ["db:x"], OK) == (False, "attack not blocked: max_flow 2.0, reached ['db:x']")
    one = [{"need_id": "n1", "ok": False}]
    two = [{"need_id": "n1", "ok": False}, {"need_id": "n2", "ok": False}]
    assert verify.judge(0.0, [], one) == (False, "need n1 broken")
    assert verify.judge(0.0, [], two) == (False, "needs n1, n2 broken")

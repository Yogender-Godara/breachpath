"""Write guards. Test incident ids only; their lock and quota keys and graphs are removed afterwards."""
import pytest

from core import config
from core.graph import client, loader
from core.graph.schema import baseline_name, sandbox_name
from core.tools import guards
from core.tools.common import GuardRefused, LimitExceeded, tool

BASE = baseline_name("s2")
INCIDENTS = ["a1", "a12", "p42quota", "p42sbx"]
TOKEN = "worker-1"


@pytest.fixture
def redis(falkordb):
    r = client.get_db().connection
    loader.load_scenario("s2")
    yield r
    for inc in INCIDENTS:
        r.delete(f"lock:inc:{inc}", f"quota:inc:{inc}:sims")
    for g in client.get_db().list_graphs():
        if ":sbx:" in g and any(g.endswith(f":sbx:{i}") or f":sbx:{i}-" in g for i in INCIDENTS):
            client.delete_graph(g)


def lock(redis, incident, token=TOKEN):
    redis.set(f"lock:inc:{incident}", token)


def test_baseline_write_is_refused_even_with_a_valid_lock(redis):  # AC-5
    lock(redis, "a1")
    with pytest.raises(GuardRefused):
        guards.check_write(BASE, "a1", TOKEN)


def test_bad_name_other_incident_and_prefix_trick_are_refused(redis):
    lock(redis, "a1")
    lock(redis, "a12")
    with pytest.raises(GuardRefused):
        guards.check_write("foo", "a1", TOKEN)
    with pytest.raises(GuardRefused):
        guards.check_write(sandbox_name("s2", "a12"), "a1", TOKEN)  # a1 must not pass for a12
    with pytest.raises(GuardRefused):
        guards.check_write(sandbox_name("s2", "a1-other"), "a1", TOKEN)
    guards.check_write(sandbox_name("s2", "a12"), "a12", TOKEN)  # the owner of a12 passes


def test_owner_checks(redis):
    sbx = sandbox_name("s2", "a1")
    with pytest.raises(GuardRefused):  # no lock
        guards.check_write(sbx, "a1", TOKEN)
    lock(redis, "a1")
    with pytest.raises(GuardRefused):  # wrong token
        guards.check_write(sbx, "a1", "someone-else")
    guards.check_write(sbx, "a1", TOKEN)
    guards.check_write(sandbox_name("s2", "a1", "-split"), "a1", TOKEN)
    guards.check_write(sandbox_name("s2", "a1", "-sim-1"), "a1", TOKEN)


def test_seventh_simulation_is_refused(redis):
    for _ in range(config.MAX_SIMULATIONS):
        guards.check_quota("p42quota", "simulation")
    with pytest.raises(LimitExceeded, match="simulations"):
        guards.check_quota("p42quota", "simulation")
    assert redis.ttl("quota:inc:p42quota:sims") > 0


def test_fourth_sandbox_is_refused(redis):
    # three tiny graphs of the same incident (sandbox and -split names, in different scenarios)
    for name in ["t:g1:sbx:p42sbx", "t:g2:sbx:p42sbx", "t:g3:sbx:p42sbx-split"]:
        guards.check_quota("p42sbx", "sandbox")
        client.query(name, "CREATE (:X)")
    with pytest.raises(LimitExceeded, match="sandboxes"):
        guards.check_quota("p42sbx", "sandbox")
    guards.check_quota("a1", "sandbox")  # another incident is not affected


def test_total_graph_limit(redis, monkeypatch):
    monkeypatch.setattr(config, "MAX_GRAPHS", len(client.get_db().list_graphs()))
    with pytest.raises(LimitExceeded, match="graphs"):
        guards.check_quota("a1", "sandbox")
    with pytest.raises(LimitExceeded, match="graphs"):
        guards.check_quota("a1", "simulation")


def test_baseline_is_unchanged_after_all_of_it(redis):
    assert client.ro_query(BASE, "MATCH ()-[r]->() RETURN count(r)")[0][0] == 179


# --- the decorator maps the two new errors, no database ---

def test_decorator_maps_guard_refused():
    @tool
    def refused():
        raise GuardRefused("no")

    assert refused() == {"ok": False, "error": {"code": "GUARD_REFUSED", "message": "no"}}


def test_decorator_maps_limit_exceeded():
    @tool
    def limited():
        raise LimitExceeded("too many")

    assert limited() == {"ok": False, "error": {"code": "LIMIT_EXCEEDED", "message": "too many"}}

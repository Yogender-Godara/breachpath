"""Read tools on S2. Reads use the baseline; min-cut and the business check use a protected sandbox copy."""
import json

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from core.graph import client, loader
from core.graph.mincut import MinCutError
from core.graph.schema import baseline_name
from core.tools import read
from core.tools.common import tool
from tests.helpers import protected_copy

BASE = baseline_name("s2")
SRC = "user:ravi"


@pytest.fixture(scope="module")
def s2(falkordb):
    loader.load_scenario("s2")
    return BASE


def data(result):
    assert result["ok"] is True, result
    return result["data"]


def code(result):
    assert result["ok"] is False
    return result["error"]["code"]


# --- each tool once on S2 ---

def test_get_blast_radius(s2):
    assert {r["id"] for r in data(read.get_blast_radius(s2, SRC))} == {"db:s2-customers", "db:s2-payments"}


def test_find_attack_paths(s2):
    result = data(read.find_attack_paths(s2, SRC))
    assert len(result["paths"]) == 2 and result["truncated"] is False
    one = data(read.find_attack_paths(s2, SRC, k=1))
    assert len(one["paths"]) == 1 and one["truncated"] is True
    only = data(read.find_attack_paths(s2, SRC, target="db:s2-payments"))
    assert [p["target"] for p in only["paths"]] == ["db:s2-payments"] and only["truncated"] is False


def test_shortest_paths(s2):
    paths = data(read.shortest_paths(s2, SRC, "db:s2-payments"))
    assert paths[0] == {"eids": ["e:s2:09", "e:s2:10", "e:s2:11", "e:s2:12", "e:s2:13"], "hops": 5}


def test_edge_path_frequency(s2):
    rows = data(read.edge_path_frequency(s2, SRC, top=3))
    assert len(rows) == 3 and {r["paths_through"] for r in rows} == {1}


def test_choke_points(s2):
    result = data(read.choke_points(s2, SRC, top=2))
    scores = [r["score"] for r in result["betweenness"]]
    assert len(scores) == 2 and scores == sorted(scores, reverse=True) and len(result["edge_frequency"]) == 2


def test_get_node(s2):
    node = data(read.get_node(s2, "role:s2-deploy-role"))
    assert "Role" in node["labels"] and node["props"]["env"] == "staging"
    assert {(e["eid"], e["kind"]) for e in node["out"]} == {("e:s2:03", "HAS_POLICY"), ("e:s2:05", "HAS_POLICY")}
    assert ("e:s2:02", "CAN_ASSUME", "group:s2-ci-deployers") in {(e["eid"], e["kind"], e["from"]) for e in node["in"]}


def test_get_edge(s2):
    edge = data(read.get_edge(s2, "e:s2:06"))
    assert (edge["kind"], edge["src"], edge["dst"]) == ("CAN_PASS_ROLE", "policy:s2-lambda-admin", "role:s2-data-admin")
    assert edge["props"]["technique"] == "PassRole+CreateFunction" and edge["props"]["cap"] == 1
    assert code(read.get_edge(s2, "e:nope")) == "NOT_FOUND"


def test_run_readonly_cypher_rows_are_plain_dicts(s2):
    result = data(read.run_readonly_cypher(s2, "MATCH (n) RETURN n"))  # S2 has 165 nodes, under the cap
    assert len(result["rows"]) == 165 and result["truncated"] is False
    assert set(result["rows"][0]) == {"n"} and set(result["rows"][0]["n"]) == {"labels", "properties"}
    json.dumps(result)  # JSON-able
    small = data(read.run_readonly_cypher(s2, "MATCH (n:Database) WHERE n.id = $id RETURN n.id AS id", {"id": "db:s2-customers"}))
    assert small == {"rows": [{"id": "db:s2-customers"}], "truncated": False}


def test_run_readonly_cypher_caps_rows_at_200(s2):
    result = data(read.run_readonly_cypher(s2, "UNWIND range(1, 300) AS i RETURN i"))
    assert len(result["rows"]) == 200 and result["truncated"] is True and result["rows"][0] == {"i": 1}


def test_run_readonly_cypher_refuses_writes(s2):
    def counts():
        return client.ro_query(s2, "MATCH (n) RETURN count(n)")[0][0], client.ro_query(s2, "MATCH ()-[r]->() RETURN count(r)")[0][0]

    before = counts()
    assert code(read.run_readonly_cypher(s2, "CREATE (:X)")) == "BAD_INPUT"
    assert code(read.run_readonly_cypher(s2, "THIS IS NOT CYPHER")) == "BAD_INPUT"
    assert counts() == before


def test_check_business_access_and_min_cut_on_a_protected_copy(falkordb):
    with protected_copy("s2", "p41test") as sbx:
        access = data(read.check_business_access(sbx))
        cut = data(read.compute_min_cut(sbx, SRC))
    assert all(a["ok"] for a in access) and "need:ravi-staging" in {a["need_id"] for a in access}
    assert cut["max_flow"] == 2 and cut["safe"] is True and "split_suggestion" not in cut
    assert cut["candidates"][0]["eids"] == ["e:s2:05", "e:s2:10"]


# --- errors ---

def test_error_codes(s2):
    assert code(read.get_blast_radius("t:s9:baseline", SRC)) == "NOT_FOUND"
    assert code(read.get_blast_radius(s2, "user:nobody")) == "NOT_FOUND"
    assert code(read.find_attack_paths(s2, SRC, k=51)) == "BAD_INPUT"
    assert code(read.find_attack_paths(s2, SRC, k=0)) == "BAD_INPUT"
    assert code(read.choke_points(s2, SRC, top=21)) == "BAD_INPUT"
    assert code(read.get_blast_radius("foo", SRC)) == "BAD_INPUT"


# --- the decorator, no database ---

def test_decorator_maps_connection_errors_to_db_unavailable():
    @tool
    def f():
        raise RedisConnectionError("Connection refused")

    assert code(f()) == "DB_UNAVAILABLE"


def test_decorator_maps_timeouts_and_other_response_errors():
    @tool
    def slow():
        raise ResponseError("Query timed out")

    @tool
    def bad():
        raise ResponseError("Invalid input")

    assert code(slow()) == "TIMEOUT" and code(bad()) == "BAD_INPUT"


def test_decorator_lets_min_cut_errors_through():
    @tool
    def broken():
        raise MinCutError("source_side cut capacity 5 != max_flow 1")

    with pytest.raises(MinCutError):
        broken()


def test_decorator_wraps_success():
    @tool
    def ok():
        return [1]

    assert ok() == {"ok": True, "data": [1]}

"""split_suggestion (no safe fix). A test incident owns its graphs; they and the Redis keys are removed afterwards."""
import pytest

from core.graph import client, loader
from core.graph.schema import baseline_name, sandbox_name
from core.tools import read, split, write
from data.generator import build_background
from data.needs import build_needs
from data.scenarios import SCENARIO_DIR, load_scenario, plant
from data.truth import check_expect, compute_truth

INC = "p46inc"
TOKEN = "worker-1"
AUTH = {"incident_id": INC, "lock_token": TOKEN}


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
    redis.zrem("sbx:expiry", *[sandbox_name(t, INC, s) for t in ("s2", "s5", "toy") for s in ("", "-split")])
    redis.delete(f"lock:inc:{INC}", f"quota:inc:{INC}:sims", *redis.keys(f"reach:*:sbx:{INC}*"))


@pytest.fixture
def redis(falkordb):
    r = client.get_db().connection
    clean(r)
    r.set(f"lock:inc:{INC}", TOKEN)
    yield r
    clean(r)


def prepare(sid):
    loader.load_scenario(sid)
    sandbox = sandbox_name(sid, INC)
    data(write.create_sandbox(sid, **AUTH))
    data(write.protect_business_paths(sandbox, **AUTH))
    return sandbox


def node(label, nid, **props):
    return {"id": nid, "label": label, "props": {"id": nid, "name": nid, **props}}


def edge(eid, kind, src, dst):
    return {"eid": eid, "type": kind, "src": src, "dst": dst, "last_used_days": 1}


def need(nid, principal, resource):
    return {"id": nid, "principal_id": principal, "resource_id": resource, "reason": "x", "source": "explicit", "valid": True}


def toy(nodes, edges, needs):
    sandbox = sandbox_name("toy", INC)
    loader.load_graph(sandbox, {"nodes": nodes, "edges": edges, "needs": needs})
    data(write.protect_business_paths(sandbox, **AUTH))
    return sandbox


def test_s5_has_no_safe_cut_and_gets_a_proven_policy_split(redis):
    sandbox = prepare("s5")
    cut = data(read.compute_min_cut(sandbox, "user:zoe"))
    assert cut["safe"] is False and cut["protected_in_way"] and cut["max_flow"] >= cut["protect_cap"]

    s = data(split.split_suggestion(sandbox, "user:zoe", **AUTH))
    print(s)
    assert (s["suggestion_type"], s["split_node"], s["new_node"], s["proven"]) == (
        "split_node", "policy:s5-shared-ops", "policy:s5-shared-ops-biz", True)
    assert s["then_remove"] == [] and s["moved_edges"] == ["e:s5:03"] and s["copied_edges"] == ["e:s5:04-biz"]
    assert "user:zoe keeps policy:s5-shared-ops-biz (only CAN_SSH -> server:s5-deploy)" in s["text"]
    proof = s["proof"]  # what the split sandbox measured
    assert (proof["max_flow_after_split"], proof["bfs_reached_jewels"], proof["needs_ok"]) == (0, [], True) and proof["needs_checked"] > 0

    # the -split sandbox is kept; the main sandbox and the baseline are unchanged
    assert sandbox_name("s5", INC, "-split") in client.get_db().list_graphs()
    for graph in (sandbox, baseline_name("s5")):
        assert client.ro_query(graph, "MATCH ()-[r]->() RETURN count(r)")[0][0] == 174
        assert client.ro_query(graph, "MATCH (n:Asset {id: 'policy:s5-shared-ops-biz'}) RETURN count(n)")[0][0] == 0
    assert client.ro_query(baseline_name("s5"), "MATCH ()-[r]->() WHERE r.protected IS NOT NULL RETURN count(r)")[0][0] == 0


def test_a_safe_cut_needs_no_split(redis):
    sandbox = prepare("s2")
    assert code(split.split_suggestion(sandbox, "user:ravi", **AUTH)) == "BAD_INPUT"


def test_case_0_the_source_legitimately_needs_the_jewel(redis):
    nodes = [node("User", "user:s"), node("Policy", "policy:p"), node("Database", "db:j", crown_jewel=True)]
    edges = [edge("e1", "HAS_POLICY", "user:s", "policy:p"), edge("e2", "CAN_READ", "policy:p", "db:j")]
    sandbox = toy(nodes, edges, [need("need:sj", "user:s", "db:j")])
    s = data(split.split_suggestion(sandbox, "user:s", **AUTH))
    assert (s["suggestion_type"], s["proven"]) == ("rotate_credentials", True)
    assert "legitimately needs db:j" in s["text"]


def test_a_server_on_the_path_is_never_split(redis):
    # s -SSH-> x(server) -> k(secret) -> t -> p -> j. need1 protects e1, need3 (the host reads the db) protects e2-e5.
    # The business use and the attack part ways at the server, which is not a role, policy or group.
    nodes = [node("User", "user:s"), node("Server", "server:x"), node("Secret", "secret:k"), node("User", "user:t"),
             node("Policy", "policy:p"), node("Database", "db:j", crown_jewel=True)]
    edges = [edge("e1", "CAN_SSH", "user:s", "server:x"), edge("e2", "STORES_SECRET", "server:x", "secret:k"),
             edge("e3", "AUTHENTICATES_AS", "secret:k", "user:t"), edge("e4", "HAS_POLICY", "user:t", "policy:p"),
             edge("e5", "CAN_READ", "policy:p", "db:j")]
    needs = [need("need:1", "user:s", "server:x"), need("need:2", "user:t", "db:j"), need("need:3", "server:x", "db:j")]
    sandbox = toy(nodes, edges, needs)
    s = data(split.split_suggestion(sandbox, "user:s", **AUTH))
    assert (s["suggestion_type"], s["proven"], s["split_node"]) == ("manual", False, "server:x")
    assert "server:x" in s["text"] and sandbox_name("toy", INC, "-split") not in client.get_db().list_graphs()


def test_s5_answer_key_matches_its_expect_block():
    sc = load_scenario(SCENARIO_DIR / "s5.yaml")
    graph = plant(build_background(42), sc)
    graph["needs"] = build_needs(graph)
    truth = compute_truth(graph)
    check_expect(truth, sc["expect"])
    assert truth["safe_fix_exists"] is False and len(truth["true_paths"]) == 1


# --- the divergence rule, no database ---

LABELS = {"s": "User", "x": "Policy", "y": "Role", "z": "Server", "j": "Database"}


def test_divergence_where_business_and_attack_part_ways():
    edges = {"a": ("s", "x", ["n1"]), "b": ("x", "y", ["n2"]), "c": ("y", "j", ["n2"])}
    assert split.divergence_points(["a", "b", "c"], edges, LABELS) == [
        {"node": "x", "e_in": "a", "e_out": "b", "business": ["n1"], "splittable": True}]  # b, c share n2: no point at y


def test_a_shared_need_is_a_conflict_not_a_divergence():
    edges = {"a": ("s", "x", ["n1", "n2"]), "b": ("x", "y", ["n2"])}
    assert split.divergence_points(["a", "b"], edges, LABELS) == []  # n1 would be split off but n2 needs both sides


def test_only_roles_policies_and_groups_are_splittable():
    edges = {"a": ("s", "z", ["n1"]), "b": ("z", "j", ["n2"])}
    (point,) = split.divergence_points(["a", "b"], edges, LABELS)
    assert point["node"] == "z" and point["splittable"] is False
    assert split.divergence_points(["a"], edges, LABELS) == []

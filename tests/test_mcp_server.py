"""The MCP server over a real stdio subprocess and the real FalkorDB (P6.1). asyncio.run inside plain tests: no extra dependency."""
import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from mcp import ClientSession, stdio_client

from core.graph import client, loader
from core.graph.schema import baseline_name, sandbox_name
from mcp_server.launch import server_params

READ = {"get_blast_radius", "find_attack_paths", "shortest_paths", "edge_path_frequency", "choke_points", "compute_min_cut",
        "check_business_access", "get_node", "get_edge", "verify_fix"}
FULL = READ | {"run_readonly_cypher", "simulate_cut", "create_sandbox", "protect_business_paths", "delete_sandbox", "apply_fix",
               "split_suggestion"}
INC, TOKEN = "p61inc", "worker-1"
BASE, SBX = baseline_name("s2"), sandbox_name("s2", INC)


@asynccontextmanager
async def session(profile):
    async with stdio_client(server_params(profile)) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


def run(profile, fn):
    """Start the server, run `await fn(session)`, stop the server. Returns whatever fn returns."""
    async def go():
        async with session(profile) as s:
            return await fn(s)
    return asyncio.run(go())


def call(profile, name, args):
    """One tool call over stdio -> the tool's own {"ok", ...} dict."""
    async def go(s):
        result = await s.call_tool(name, args)
        assert not result.is_error, result
        return json.loads(result.content[0].text)
    return run(profile, go)


def tools(profile):
    async def go(s):
        return (await s.list_tools()).tools
    return run(profile, go)


@pytest.fixture
def s2(falkordb):
    """The S2 baseline, plus a clean slate for the incident's sandbox, lock and counters before and after."""
    r = client.get_db().connection
    loader.load_scenario("s2")

    def clean():
        client.delete_graph(SBX)
        r.zrem("sbx:expiry", SBX)
        r.delete(f"lock:inc:{INC}", f"quota:inc:{INC}:sims", *r.keys(f"reach:*:sbx:{INC}:*"))
    clean()
    r.set(f"lock:inc:{INC}", TOKEN)
    yield r
    clean()


def test_full_profile_lists_the_17_tools():
    assert {t.name for t in tools("full")} == FULL and len(tools("full")) == 17


def test_readonly_profile_lists_only_the_10_read_tools():
    names = {t.name for t in tools("readonly")}
    assert names == READ and "run_readonly_cypher" not in names


def test_every_tool_has_a_description_and_a_typed_schema():
    for profile in ("full", "readonly"):
        for t in tools(profile):
            assert t.description and t.description.strip(), t.name
            props = t.input_schema["properties"]
            assert props, t.name
            for name, schema in props.items():
                assert {"type", "anyOf"} & set(schema), f"{t.name}.{name} has no type"
    print("\n--- tools (name: first line of the description) ---")
    for profile in ("full", "readonly"):
        print(f"[{profile}]")
        for t in tools(profile):
            print(f"  {t.name}: {t.description.splitlines()[0]}")


def test_get_blast_radius_matches_the_answer_key(s2):
    key = json.load(open("data/answer_keys/s2.json"))
    out = call("readonly", "get_blast_radius", {"graph": BASE, "src": key["src"]})
    assert out["ok"] is True
    jewels = {n["id"] for n in out["data"]} & set(key["crown_jewels"])
    assert jewels == set(key["reachable_jewels"])


def test_the_error_model_survives_mcp(s2):
    out = call("readonly", "find_attack_paths", {"graph": BASE, "src": "user:ravi", "k": 999})
    assert out["ok"] is False and out["error"]["code"] == "BAD_INPUT"


def test_writes_are_guarded_over_mcp(s2):
    auth = {"incident_id": INC, "lock_token": TOKEN}
    refused = call("full", "apply_fix", {"sandbox": BASE, "eids": ["e:s2:05"], "reason": "test", **auth})
    assert refused["ok"] is False and refused["error"]["code"] == "GUARD_REFUSED"  # a baseline

    made = call("full", "create_sandbox", {"tenant": "s2", **auth})  # the right token works through MCP
    assert made["ok"] is True and made["data"]["sandbox"] == SBX
    wrong = call("full", "apply_fix", {"sandbox": SBX, "eids": ["e:s2:05"], "reason": "test", "incident_id": INC,
                                       "lock_token": "not-the-owner"})
    assert wrong["ok"] is False and wrong["error"]["code"] == "GUARD_REFUSED"  # a real sandbox, the wrong lock token
    assert client.ro_query(SBX, "MATCH ()-[r {eid: 'e:s2:05'}]->() RETURN count(r)")[0][0] == 1  # the edge is still there


def test_compute_min_cut_has_the_avoid_parameter_and_the_guarded_ones_are_optional():
    schema = {t.name: t.input_schema for t in tools("full")}["compute_min_cut"]
    assert set(schema["properties"]) == {"graph", "src", "avoid_eids", "incident_id", "lock_token"}
    assert schema["required"] == ["graph", "src"]  # avoid_eids and the guard values are optional: a normal call is unchanged
    assert "avoid_eids" in {t.name: t.description for t in tools("full")}["compute_min_cut"]  # the model is told what it is for

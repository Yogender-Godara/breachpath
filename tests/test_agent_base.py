"""The agent loop. A scripted fake gateway (no LLM calls) + the REAL MCP stdio subprocess + the real FalkorDB (S2).
The last test is live: a dummy agent on OpenRouter (-m live)."""
import asyncio
import json
import os
import uuid
from types import SimpleNamespace

import pytest

from agents import base, llm_gateway
from agents.base import AgentSpec, ToolCrashed, open_session, run_agent, tools_for
from agents.llm_gateway import LLMUnavailable
from agents.schemas import SUBMIT_TOOLS, FindingsOut
from core import config
from core.graph import client, loader
from core.graph.schema import sandbox_name
from tests.agent_fakes import Gateway, message

BASE, SRC = "t:s2:baseline", "user:ravi"
INC, TOKEN = "p63inc", "worker-1"
SBX = sandbox_name("s2", INC)
GOOD = {"reachable_jewels": ["db:s2-customers"], "paths": [{"target": "db:s2-customers", "eids": ["e:s2:01"]}], "truncated": False, "notes": "n"}


def spec(allow=("get_blast_radius", "find_attack_paths"), **kw):
    return AgentSpec(name="investigator", system="Role: TEST.", allow_list=list(allow), submit=SUBMIT_TOOLS["submit_findings"],
                     output_model=FindingsOut, **kw)


def run(spec_, task="task", session_wrapper=lambda s: s, **kw):
    """Open a session, run the agent, close it. An exception is caught INSIDE the session and re-raised after it closed:
    anyio would wrap one that leaves the `async with` in an ExceptionGroup (the worker must catch inside the session too)."""
    async def go():
        error = None
        async with open_session() as session:
            try:
                return await run_agent(session_wrapper(session), spec_, task, **kw)
            except Exception as e:
                error = e
        raise error
    return asyncio.run(go())


@pytest.fixture
def s2(falkordb):
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


def tool_messages(gateway, call_index):
    return [m for m in gateway.calls[call_index]["messages"] if m["role"] == "tool"]


# --- providers: a fallback restarts the whole run on the next provider (D18) ---

def test_an_unavailable_provider_restarts_the_run_on_the_next_one_with_a_fresh_conversation(s2, monkeypatch):
    call = message(("get_blast_radius", {"graph": BASE, "src": SRC}))
    gw = Gateway(monkeypatch, {"p1": [call, LLMUnavailable("provider rejected the call: RateLimit (after 1 retry)")],
                               "p2": [call, message(("submit_findings", GOOD))]})
    events = []
    result = run(spec(), emit=lambda type_, msg, data, **kw: events.append((type_, msg)))
    assert not result.degraded and result.output == FindingsOut(**GOOD) and result.provider == "p2"
    assert [c["provider"] for c in gw.calls] == ["p1", "p1", "p2", "p2"]
    assert (result.llm_calls, result.tokens_in, result.tokens_out) == (3, 30, 15)  # adds up across attempts (the failed call has no usage)
    first_p2 = gw.calls[2]["messages"]
    assert [m["role"] for m in first_p2] == ["system", "user"]  # p2 never sees p1's messages
    assert not any("p1" in json.dumps(m) or m["role"] in ("assistant", "tool") for m in first_p2)
    restart = [msg for type_, msg in events if type_ == "step" and "restarting on p2" in msg]
    assert restart and restart[0].startswith("provider p1 unavailable: provider rejected the call")


def test_the_incident_budget_counts_every_call_across_provider_restarts_and_ends_the_run_at_once(s2, monkeypatch):
    monkeypatch.setattr(config, "LLM_MAX_CALLS_PER_INCIDENT", 3)
    call = message(("get_blast_radius", {"graph": BASE, "src": SRC}))
    gw = Gateway(monkeypatch, {"p1": [call, LLMUnavailable("down")], "p2": [call, call]})
    state, events = {"llm_calls": 0}, []
    result = run(spec(), state=state, emit=lambda type_, msg, data, **kw: events.append(msg))
    assert result.degraded and result.reason == base.BUDGET_REASON == "LLM budget for this incident used up"
    assert [c["provider"] for c in gw.calls] == ["p1", "p1", "p2"] and state["llm_calls"] == 3  # the failed attempt counts too
    assert any(base.BUDGET_REASON in m for m in events) and not any("restarting" in m and "budget" in m for m in events)  # a budget stop is not a provider failure
    gw2 = Gateway(monkeypatch, {"p1": [call], "p2": [call]})  # the same incident again (e.g. a retry attempt): not one more call
    again = run(spec(), state=state)
    assert again.degraded and again.reason == base.BUDGET_REASON and gw2.calls == [] and state["llm_calls"] == 3


def test_a_run_without_a_state_has_no_budget_and_a_normal_run_counts_its_calls(s2, monkeypatch):
    call = message(("get_blast_radius", {"graph": BASE, "src": SRC}))
    Gateway(monkeypatch, call, message(("submit_findings", GOOD)))
    assert not run(spec()).degraded  # no state: nothing is counted or limited
    Gateway(monkeypatch, call, message(("submit_findings", GOOD)))
    state = {"llm_calls": 5}
    result = run(spec(), state=state)
    assert not result.degraded and result.llm_calls == 2 and state["llm_calls"] == 7


def test_invalid_output_twice_is_final_no_restart(s2, monkeypatch):
    gw = Gateway(monkeypatch, {"p1": [message(("submit_findings", {"x": 1})), message(("submit_findings", {"y": 2}))],
                               "p2": [message(("submit_findings", GOOD))]})
    result = run(spec())
    assert result.degraded and result.provider == "p1" and result.reason.startswith("invalid output twice")
    assert [c["provider"] for c in gw.calls] == ["p1", "p1"]  # p2 was never tried


def test_all_providers_unavailable_degrades_with_every_reason(s2, monkeypatch):
    Gateway(monkeypatch, {"p1": [LLMUnavailable("p1 is down")], "p2": [LLMUnavailable("p2 is down")]})
    result = run(spec())
    assert result.degraded and result.output is None and result.reason == "p1: p1 is down; p2: p2 is down"
    assert result.llm_calls == 0


def test_no_provider_configured_degrades(s2, monkeypatch):
    Gateway(monkeypatch, {})
    result = run(spec())
    assert result.degraded and "no LLM provider configured" in result.reason


# --- the happy path ---

def test_two_tools_in_parallel_then_submit(s2, monkeypatch):
    gw = Gateway(monkeypatch,
                 message(("get_blast_radius", {"graph": BASE, "src": SRC}), ("find_attack_paths", {"graph": BASE, "src": SRC, "target": "db:s2-customers"})),
                 message(("submit_findings", GOOD)))
    events = []
    result = run(spec(), emit=lambda type_, msg, data, **kw: events.append((type_, data, kw)))
    assert not result.degraded and result.output == FindingsOut(**GOOD)
    assert (result.turns, result.llm_calls, result.tokens_in, result.tokens_out) == (2, 2, 20, 10)
    sent = tool_messages(gw, 1)
    assert [m["tool_call_id"] for m in sent] == ["c0", "c1"] and all(json.loads(m["content"])["ok"] for m in sent)
    assert [m["role"] for m in gw.calls[1]["messages"]] == ["system", "user", "assistant", "tool", "tool"]
    assert "BreachPath" in gw.calls[0]["messages"][0]["content"] and "Role: TEST." in gw.calls[0]["messages"][0]["content"]
    kinds = [e[0] for e in events]
    assert kinds.count("step") == 2 and kinds.count("tool_call") == 2
    tool_event = next(e for e in events if e[0] == "tool_call")
    assert tool_event[1]["ok"] is True and tool_event[1]["provider"] == "fake" and tool_event[2]["latency_ms"] >= 0
    assert "key" not in json.dumps(events).lower()


def test_on_tool_result_sees_every_successful_call_without_the_hidden_params(s2, monkeypatch):
    Gateway(monkeypatch, message(("get_blast_radius", {"graph": BASE, "src": SRC}), ("get_blast_radius", {"graph": BASE})),
            message(("create_sandbox", {"tenant": "s2", "incident_id": "evil"})), message(("submit_findings", GOOD)))
    seen = []
    run(spec(("get_blast_radius", "create_sandbox")), inject={"incident_id": INC, "lock_token": TOKEN},
        on_tool_result=lambda name, args, result: seen.append((name, args, result["ok"])))
    assert [s[0] for s in seen] == ["get_blast_radius", "create_sandbox"]  # the call with a missing argument was not a successful call
    assert seen[0][1] == {"graph": BASE, "src": SRC} and seen[1][1] == {"tenant": "s2"} and all(s[2] is True for s in seen)


def test_injected_params_are_hidden_from_the_llm_and_win_over_what_it_sends(s2, monkeypatch):
    inject = {"graph": BASE, "src": SRC}

    async def tools():
        async with open_session() as session:
            return await tools_for(session, spec(("get_blast_radius", "find_attack_paths", "run_readonly_cypher")), inject)
    schemas = {t["function"]["name"]: t["function"]["parameters"] for t in asyncio.run(tools())}
    assert schemas["get_blast_radius"]["properties"] == {} and schemas["get_blast_radius"]["required"] == []
    assert set(schemas["find_attack_paths"]["properties"]) == {"target", "k"} and schemas["find_attack_paths"]["required"] == []
    assert set(schemas["run_readonly_cypher"]["properties"]) == {"query", "params"} and schemas["run_readonly_cypher"]["required"] == ["query"]
    # the model tries another graph and identity: the injected ones are what the MCP tool gets
    seen = []
    gw = Gateway(monkeypatch, message(("get_blast_radius", {"graph": "t:s7:baseline", "src": "user:nia"})), message(("submit_findings", GOOD)))
    run(spec(("get_blast_radius",)), inject=inject, on_tool_result=lambda name, args, result: seen.append((name, args)))
    reply = json.loads(tool_messages(gw, 1)[0]["content"])
    assert reply["ok"] is True and {j["id"] for j in reply["data"]} == {"db:s2-customers", "db:s2-payments"}  # S2's jewels, not S7's
    assert seen == [("get_blast_radius", {})]  # and the hook sees only what the model chose


def test_tool_results_are_compact_sorted_json_and_other_text_is_left_alone(s2, monkeypatch):
    gw = Gateway(monkeypatch, message(("get_blast_radius", {"graph": BASE, "src": SRC})), message(("submit_findings", GOOD)))
    run(spec())
    content = tool_messages(gw, 1)[0]["content"]
    assert content == json.dumps(json.loads(content), separators=(",", ":"), sort_keys=True) and "\n" not in content and ": " not in content

    class Plain:  # the real session, except that the tool answers with text that is not JSON
        def __init__(self, session):
            self.session = session

        async def list_tools(self):
            return await self.session.list_tools()

        async def call_tool(self, name, args):
            return SimpleNamespace(is_error=False, content=[SimpleNamespace(text="plain  text,  not json")])
    gw = Gateway(monkeypatch, message(("get_blast_radius", {"graph": BASE, "src": SRC})), message(("submit_findings", GOOD)))
    run(spec(), session_wrapper=Plain)
    assert tool_messages(gw, 1)[0]["content"] == "plain  text,  not json"


def test_hidden_params_are_not_in_the_tools_and_are_injected_on_the_call(s2, monkeypatch):
    async def tools():
        async with open_session() as session:
            return await tools_for(session, spec(("create_sandbox", "apply_fix", "get_blast_radius")))
    openai_tools = {t["function"]["name"]: t["function"]["parameters"] for t in asyncio.run(tools())}
    for name in ("create_sandbox", "apply_fix"):
        assert not {"incident_id", "lock_token"} & set(openai_tools[name]["properties"])
        assert not {"incident_id", "lock_token"} & set(openai_tools[name].get("required", []))
    assert set(openai_tools) == {"create_sandbox", "apply_fix", "get_blast_radius", "submit_findings"}
    assert "tenant" in openai_tools["create_sandbox"]["required"] and "sandbox" in openai_tools["apply_fix"]["required"]

    # the model sends only the tenant (and even a wrong incident_id): the loop's values win, so the guards pass
    gw = Gateway(monkeypatch, message(("create_sandbox", {"tenant": "s2", "incident_id": "evil"})), message(("submit_findings", GOOD)))
    result = run(spec(("create_sandbox",)), inject={"incident_id": INC, "lock_token": TOKEN})
    reply = json.loads(tool_messages(gw, 1)[0]["content"])
    assert not result.degraded and reply["ok"] is True and reply["data"]["sandbox"] == SBX


def test_a_tool_that_needs_auth_without_auth_is_a_bug(s2):
    with pytest.raises(ValueError, match="need incident_id and lock_token"):
        run(spec(("create_sandbox",)))


# --- mistakes by the model: error messages, the loop continues ---

def test_a_tool_outside_the_allow_list_is_refused(s2, monkeypatch):
    gw = Gateway(monkeypatch, message(("apply_fix", {"sandbox": BASE, "eids": ["e:s2:05"], "reason": "x"})), message(("submit_findings", GOOD)))
    assert not run(spec(), inject={"incident_id": INC, "lock_token": TOKEN}).degraded
    assert tool_messages(gw, 1)[0]["content"] == "error: tool not allowed"
    assert client.ro_query(BASE, "MATCH ()-[r {eid: 'e:s2:05'}]->() RETURN count(r)")[0][0] == 1


def test_bad_json_and_bad_arguments_are_error_messages_not_crashes(s2, monkeypatch):
    gw = Gateway(monkeypatch,
                 message(("get_blast_radius", "{not json")),
                 message(("find_attack_paths", {"graph": BASE, "src": SRC, "k": "five"}), ("get_blast_radius", {"graph": BASE})),
                 message(("submit_findings", GOOD)))
    result = run(spec())
    assert not result.degraded and result.turns == 3
    assert tool_messages(gw, 1)[0]["content"].startswith("error: invalid JSON")
    assert [m["content"].startswith("error: invalid arguments") for m in tool_messages(gw, 2)[-2:]] == [True, True]


def test_no_tool_calls_gets_a_reminder_that_counts_as_a_turn(s2, monkeypatch):
    gw = Gateway(monkeypatch, message(content="I think it is fine."), message(("submit_findings", GOOD)))
    result = run(spec())
    assert not result.degraded and result.turns == 2
    assert gw.calls[1]["messages"][-1] == {"role": "user", "content": "Call submit_findings now."}


def test_an_invalid_submit_is_sent_back_once_then_a_valid_one_is_accepted(s2, monkeypatch):
    gw = Gateway(monkeypatch, message(("submit_findings", {"reachable_jewels": "all"})), message(("submit_findings", GOOD)))
    result = run(spec())
    assert not result.degraded and result.output == FindingsOut(**GOOD)
    assert tool_messages(gw, 1)[0]["content"].startswith("error: invalid submit_findings")


def test_two_invalid_submits_degrade(s2, monkeypatch):
    Gateway(monkeypatch, message(("submit_findings", {"x": 1})), message(("submit_findings", "{oops")))
    result = run(spec())
    assert result.degraded and result.output is None and result.reason.startswith("invalid output twice")


def test_max_turns_without_a_valid_submit_degrades_and_the_last_turn_forces_the_submit(s2, monkeypatch):
    call = message(("get_blast_radius", {"graph": BASE, "src": SRC}))
    gw = Gateway(monkeypatch, call, call)
    result = run(spec(max_turns=2))
    assert result.degraded and "2 turns" in result.reason and result.llm_calls == 2
    assert [c["tool_choice"] for c in gw.calls] == ["auto", {"type": "function", "function": {"name": "submit_findings"}}]


def test_llm_unavailable_degrades(s2, monkeypatch):
    Gateway(monkeypatch, LLMUnavailable("provider rejected the call: AuthenticationError 401 (no retry)"))
    result = run(spec())
    assert result.degraded and "AuthenticationError" in result.reason and result.llm_calls == 0


def test_a_long_tool_result_is_cut(s2, monkeypatch):
    gw = Gateway(monkeypatch, message(("run_readonly_cypher", {"graph": BASE, "query": "MATCH (n) RETURN n LIMIT 150"})), message(("submit_findings", GOOD)))
    run(spec(("run_readonly_cypher",)))
    content = tool_messages(gw, 1)[0]["content"]
    assert content.endswith("...(truncated)") and len(content) == base.MAX_TOOL_RESULT_CHARS + len("...(truncated)")


# --- configuration errors and crashes ---

def test_an_allow_listed_name_that_mcp_does_not_have_fails_loudly(s2):
    with pytest.raises(ValueError, match="nope"):
        run(spec(("get_blast_radius", "nope")))


def test_a_crash_inside_a_tool_is_tool_crashed_not_a_tool_error(s2, monkeypatch):
    Gateway(monkeypatch, message(("get_blast_radius", {"graph": BASE, "src": SRC})))

    class Crashing:  # the real session, except that every call_tool reports an exception inside the tool
        def __init__(self, session):
            self.session = session

        async def list_tools(self):
            return await self.session.list_tools()

        async def call_tool(self, name, args):
            return SimpleNamespace(is_error=True, content=[SimpleNamespace(text="Error executing tool: MinCutError: cut does not add up")])

    with pytest.raises(ToolCrashed, match="MinCutError"):
        run(spec(), session_wrapper=Crashing)


# --- live: the "Done when" (a dummy agent completes a 2-tool task; about 3 LLM calls per provider) ---

DUMMY = ("Role: DUMMY INVESTIGATOR. Steps: 1. Call get_blast_radius(). 2. Call find_attack_paths(target) for EACH "
         "reachable jewel (in parallel). 3. Call submit_findings with reachable_jewels, the paths exactly as returned (target + eids), "
         "truncated and notes.")
CHOSEN = {"GROQ": "openai/gpt-oss-120b", "OPENROUTER": "nvidia/nemotron-3-super-120b-a12b:free"}  # VERIFY_RESULTS P1.4


def live_run(monkeypatch, need):
    """Providers groq, openrouter. Skip (naming the variable) if a key in `need` is missing. An empty MODEL_INVESTIGATOR_* line in
    .env falls back to the P1.4 model. Runs the dummy agent and prints what happened."""
    for name in need:
        if not os.getenv(name):
            pytest.skip(f"{name} not set")
    monkeypatch.setattr(config, "LLM_PROVIDERS", ["groq", "openrouter"])
    for name, model in CHOSEN.items():
        if not os.getenv(f"MODEL_INVESTIGATOR_{name}"):
            monkeypatch.setenv(f"MODEL_INVESTIGATOR_{name}", model)
    events = []
    dummy = AgentSpec(name="investigator", system=DUMMY, allow_list=["get_blast_radius", "find_attack_paths"],
                      submit=SUBMIT_TOOLS["submit_findings"], output_model=FindingsOut)
    result = run(dummy, f"The compromised identity is {SRC}.", inject={"graph": BASE, "src": SRC}, emit=lambda type_, msg, data, **kw: events.append({"type": type_, "msg": msg, **(data or {}), **kw}))
    steps = [e for e in events if e["type"] == "step"]
    turns = [e for e in steps if "cached" in e]
    print(f"\nprovider={result.provider} turns={result.turns} llm_calls={result.llm_calls} tokens={result.tokens_in}/{result.tokens_out} "
          f"degraded={result.degraded} reason={result.reason!r}")
    print(f"  LLM latency per turn: {[e['latency_ms'] for e in turns]} ms, cached flags: {[e['cached'] for e in turns]}")
    for e in events:
        print("  ", {k: v for k, v in e.items() if k in ("type", "msg", "tool", "ok", "latency_ms", "cached")})
    print("   output:", result.output.model_dump_json() if result.output else None)
    assert not result.degraded, result.reason
    assert {"get_blast_radius", "find_attack_paths"} <= {e["tool"] for e in events if e["type"] == "tool_call" and e["ok"]}
    assert sorted(result.output.reachable_jewels) == sorted(json.load(open("data/answer_keys/s2.json"))["reachable_jewels"])
    return result, events


@pytest.mark.live
def test_live_dummy_agent_finishes_on_groq(s2, monkeypatch):
    result, _ = live_run(monkeypatch, ("GROQ_API_KEY",))
    assert result.provider == "groq"


@pytest.mark.live
def test_live_a_bad_groq_key_restarts_the_run_on_openrouter(s2, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "bad-key")
    real_key = llm_gateway.cache_key  # the cache key has no API key: a cached groq answer would hide the bad key, so groq never hits it
    monkeypatch.setattr(llm_gateway, "cache_key", lambda provider, *a: f"llmcache:nocache-{uuid.uuid4()}" if provider == "groq" else real_key(provider, *a))
    result, events = live_run(monkeypatch, ("OPENROUTER_API_KEY",))
    assert result.provider == "openrouter"
    assert any("provider groq unavailable" in e["msg"] and "restarting on openrouter" in e["msg"] for e in events)
    assert "bad-key" not in json.dumps(events)


def test_short_reason_names_the_cause_and_note_degraded_records_the_step_once():
    from agents.base import note_degraded, short_reason
    assert short_reason("groq: provider still failing after 1 retry: RateLimitError") == "rate limit"
    assert short_reason("429 Too Many Requests") == "rate limit" and short_reason("no LLM provider configured (LLM_PROVIDERS=groq)") == "no provider configured"
    assert short_reason("openai.APITimeoutError: Request timeout") == "provider timeout" and short_reason("403 permission_denied") == "provider refused the call"
    assert short_reason("groq: provider still failing after 1 retry: BadRequestError") == "model output unusable"
    assert short_reason("x" * 200) == "x" * 80
    state = {}
    note_degraded(state, "reporter", "RateLimitError")
    note_degraded(state, "reporter", "429")  # the same step with the same label is listed once
    note_degraded(state, "planner", "timeout")
    assert state["degraded_steps"] == ["reporter: rate limit", "planner: provider timeout"]

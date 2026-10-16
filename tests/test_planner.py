"""The Planner. Unit tests: a scripted fake gateway + the REAL MCP subprocess + FalkorDB + a prepared sandbox. Live tests (-m live): S8 and S2."""
import asyncio
import json
import os
from contextlib import contextmanager

import pytest

from agents import planner
from agents.base import open_session
from agents.llm_gateway import LLMUnavailable
from core.graph import client, loader
from core.graph.schema import sandbox_name
from core.tools import read, write
from core.tools.common import unwrap
from orchestrator.lock import hold_lock
from orchestrator.state import new_state
from tests.agent_fakes import Gateway, message

FAST_KEYS = {"cut_eids", "chosen", "source", "reasons", "notes", "safe", "max_flow", "witness", "simulation", "suggestion", "alternatives"}
S8 = ("s8", "user:max")  # source_side [e:s8:05] has collateral 0, sink_side [e:s8:07] has collateral 12


@contextmanager
def prepared(sid, src, tried=()):
    """A prepared sandbox (business edges protected) under a held lock, like the pipeline's Prepare. Yields (state, lock token)."""
    loader.load_scenario(sid)
    state = new_state(sid, src, "agent")
    state["tried_changes"] = list(tried)
    redis = client.get_db().connection
    with hold_lock(state["id"]) as lock:
        auth = {"incident_id": state["id"], "lock_token": lock.token}
        state["sandbox_graph"] = sandbox_name(sid, state["id"])
        unwrap(write.create_sandbox(sid, **auth))
        unwrap(write.protect_business_paths(state["sandbox_graph"], **auth))
        try:
            yield state, lock.token
        finally:
            for suffix in ("", "-split"):
                write.delete_sandbox(sandbox_name(sid, state["id"], suffix), **auth)
            redis.delete(f"quota:inc:{state['id']}:sims", *redis.keys(f"reach:*:sbx:{state['id']}*"))


def run_plan(state, token, emit=None):
    """Open a session, run the agent Planner, close it. Returns (plan, degraded)."""
    async def go():
        error = None
        async with open_session() as session:
            try:
                return await planner.plan(session, state, lock_token=token, emit=emit)
            except Exception as e:  # caught inside the session: anyio would wrap it in an ExceptionGroup
                error = e
        raise error
    return asyncio.run(go())


def sims_used(state):
    raw = client.get_db().connection.get(f"quota:inc:{state['id']}:sims")
    return int(raw) if raw else 0


def sim(state, eids):
    return ("simulate_cut", {"eids": eids})  # sandbox and src are injected by the loop


def submit(eids, chosen="source_side", why="low risk", notes="Chose the cheapest."):
    return message(("submit_fix_plan", {"cut_eids": eids, "chosen": chosen, "reasons": [{"eid": e, "why": why} for e in eids], "notes": notes}))


@pytest.fixture
def db(falkordb):
    return None


# --- no LLM when it adds nothing ---

def test_an_unsafe_cut_makes_no_llm_call_and_returns_the_fast_plan(db, monkeypatch):
    key = json.load(open("data/answer_keys/s5.json"))
    gw = Gateway(monkeypatch)  # an empty script: any chat call would fail the test
    with prepared("s5", key["src"]) as (state, token):
        plan, degraded = run_plan(state, token)
    assert gw.calls == [] and degraded is False
    assert plan["safe"] is False and plan["source"] == "code" and plan["cut_eids"] == [] and plan["suggestion"]["proven"] is True
    assert set(plan) == FAST_KEYS


def test_a_failed_planner_cut_means_the_code_candidate_with_no_llm_call(db, monkeypatch):
    gw = Gateway(monkeypatch)
    failed = {"type": "use_code_candidate", "exclude": ["e:s2:05", "e:s2:10"]}  # the retry change after a Planner cut failed verification
    with prepared("s2", "user:ravi", tried=[failed]) as (state, token):
        plan, degraded = run_plan(state, token)
        used = sims_used(state)
    assert gw.calls == [] and not degraded and plan["source"] == "code"
    assert plan["chosen"] == "sink_side" and plan["cut_eids"] == ["e:s2:08", "e:s2:11"]  # the excluded candidate is never chosen
    assert used == 1  # and not even simulated


# --- the LLM chooses, the code verifies ---

def test_a_simulated_choice_is_accepted_and_enriched_in_code(db, monkeypatch):
    with prepared(*S8) as (state, token):
        gw = Gateway(monkeypatch, message(sim(state, ["e:s8:05"]), sim(state, ["e:s8:07"])),
                     submit(["e:s8:05"], why="a stale legacy role (300 days)", notes="Zero collateral."))
        events = []
        plan, degraded = run_plan(state, token, emit=lambda t, m, d, **kw: events.append((t, m, d)))
        used = sims_used(state)
    assert not degraded and set(plan) == FAST_KEYS and used == 2
    assert (plan["cut_eids"], plan["source"], plan["chosen"], plan["safe"], plan["max_flow"], plan["suggestion"]) == (["e:s8:05"], "planner", "source_side", True, 1, None)
    assert plan["simulation"]["collateral"]["count"] == 0 and plan["simulation"]["blocks_attack"] and plan["simulation"]["needs_ok"]
    assert plan["witness"] == {"e:s8:05": ["e:s8:01", "e:s8:05", "e:s8:06", "e:s8:07"]}  # the attack path it cuts
    assert plan["reasons"][0]["eid"] == "e:s8:05" and "Planner: a stale legacy role (300 days)" in plan["reasons"][0]["why"]
    assert plan["notes"] == "Zero collateral. size 1 vs minimum 1."
    # the trade-off: the cut it did NOT take, from the simulate_cut result (code, never the LLM)
    assert plan["alternatives"] == [{"cut_eids": ["e:s8:07"], "name": "sink_side", "blocks_attack": True, "needs_ok": True,
                                     "collateral": {"count": 12, "weighted": 12}, "size": 1, "stale_days_total": 150}]
    final = [d for t, m, d in events if t == "plan"][0]
    assert final == {"chosen": "source_side", "cut_eids": ["e:s8:05"], "source": "planner", "llm_choice": ["e:s8:05"], "llm_chosen": "source_side", "accepted": True,
                     "collateral": 0, "sims_used": 2, "provider": "fake", "turns": 2}
    assert not [m for t, m, d in events if "rejected" in m]


def test_a_cut_the_llm_never_simulated_is_simulated_once_in_code_then_accepted(db, monkeypatch):
    with prepared(*S8) as (state, token):
        Gateway(monkeypatch, submit(["e:s8:05"]))
        plan, degraded = run_plan(state, token)
        used = sims_used(state)
    assert not degraded and plan["source"] == "planner" and plan["cut_eids"] == ["e:s8:05"] and used == 1
    assert plan["alternatives"] == []  # nothing else was simulated: nothing to compare


def test_the_label_is_set_by_the_code_not_by_the_llm(db, monkeypatch):
    with prepared(*S8) as (state, token):  # the LLM calls the min-cut candidate "custom": the code names it
        Gateway(monkeypatch, submit(["e:s8:05"], chosen="custom"))
        events = []
        plan, _ = run_plan(state, token, emit=lambda t, m, d, **kw: events.append((t, m, d)))
    assert plan["source"] == "planner" and plan["chosen"] == "source_side"
    assert [d for t, m, d in events if t == "plan"][0]["llm_chosen"] == "custom"
    with prepared("s2", "user:ravi") as (state, token):  # a mix of the two candidates is the Planner's own cut: "custom"
        Gateway(monkeypatch, submit(["e:s2:05", "e:s2:11"], chosen="source_side"))
        plan, _ = run_plan(state, token)
    assert plan["source"] == "planner" and plan["chosen"] == "custom" and plan["cut_eids"] == ["e:s2:05", "e:s2:11"]


def test_two_incidents_send_identical_messages_so_the_cache_can_hit(db, monkeypatch):
    captured = []
    for _ in range(2):  # two prepared sandboxes with DIFFERENT incident ids
        with prepared(*S8) as (state, token):
            script = [message(("compute_min_cut", {}), sim(state, ["e:s8:05"]), sim(state, ["e:s8:07"])), submit(["e:s8:05"])]
            gw = Gateway(monkeypatch, *script)
            run_plan(state, token)
            captured.append((state["id"], gw.calls))
    (id_a, calls_a), (id_b, calls_b) = captured
    assert id_a != id_b and len(calls_a) == len(calls_b) == 2
    for a, b in zip(calls_a, calls_b):
        assert (a["messages"], a["tools"], a["tool_choice"]) == (b["messages"], b["tools"], b["tool_choice"])
    seen = json.dumps(calls_a)
    assert id_a not in seen and "sbx" not in seen and "t:s8" not in seen  # no sandbox or graph name anywhere the LLM can see


def test_the_same_set_is_never_simulated_twice_even_if_the_llm_asks_again(db, monkeypatch):
    with prepared(*S8) as (state, token):
        gw = Gateway(monkeypatch, message(sim(state, ["e:s8:05"])), message(sim(state, ["e:s8:05"])), submit(["e:s8:05"]))
        plan, _ = run_plan(state, token)
        used = sims_used(state)
    assert used == 1 and plan["source"] == "planner"  # the repeat was answered from the results already seen
    repeat = [m for m in gw.calls[2]["messages"] if m["role"] == "tool"]
    assert len(repeat) == 2 and repeat[0]["content"] == repeat[1]["content"]  # byte for byte the same answer


@pytest.mark.parametrize("bad,why", [(["e:s8:01"], "protected"), (["e:s8:09"], "blocks_attack=False"), ([], "empty")])
def test_a_bad_choice_is_rejected_and_the_code_ranking_decides(db, monkeypatch, bad, why):
    with prepared(*S8) as (state, token):
        Gateway(monkeypatch, submit(bad))
        events = []
        plan, degraded = run_plan(state, token, emit=lambda t, m, d, **kw: events.append((t, m, d)))
        used = sims_used(state)
    assert not degraded and plan["source"] == "code" and plan["cut_eids"] == ["e:s8:05"] and plan["simulation"]["collateral"]["count"] == 0
    rejected = [(m, d) for t, m, d in events if "planner_choice_rejected" in m]
    assert len(rejected) == 1 and why in rejected[0][0] and rejected[0][1]["cut_eids"] == bad
    assert used == (2 if not bad else 3)  # the two candidates, plus the bad set once; nothing twice
    final = [d for t, m, d in events if t == "plan"][0]
    assert final["accepted"] is False and final["llm_choice"] == bad and final["source"] == "code"


def test_a_degraded_run_falls_back_to_the_code_ranking_without_simulating_twice(db, monkeypatch):
    with prepared(*S8) as (state, token):
        Gateway(monkeypatch, message(sim(state, ["e:s8:05"])), LLMUnavailable("down"))
        events = []
        plan, degraded = run_plan(state, token, emit=lambda t, m, d, **kw: events.append((t, m, d)))
        used = sims_used(state)
    assert degraded is True and plan["source"] == "code" and plan["cut_eids"] == ["e:s8:05"] and set(plan) == FAST_KEYS
    assert used == 2  # the LLM's one simulation was reused; only the other candidate was simulated
    assert [m for t, m, d in events if "unavailable" in m] and [d for t, m, d in events if t == "plan"][0]["accepted"] is False


def test_a_retry_task_carries_the_failure_and_the_changes_tried(db, monkeypatch):
    tried = [{"type": "protect_second_path", "needs": ["need:x"]}]
    with prepared(*S8, tried=tried) as (state, token):
        state["verdict"] = {"failure_detail": "need need:x broken"}
        gw = Gateway(monkeypatch, submit(["e:s8:05"]))
        run_plan(state, token)
    task = gw.calls[0]["messages"][1]["content"]
    assert "This is a retry. failure_detail: need need:x broken" in task and "protect_second_path" in task and "max_flow" in task


# --- live: the real LLMs (Groq first, then OpenRouter) ---

MODELS = {"GROQ": "openai/gpt-oss-120b", "OPENROUTER": "nvidia/nemotron-3-super-120b-a12b:free"}  # VERIFY_RESULTS P1.4


def live(monkeypatch, sid, src):
    """One agent Planner run on a NEW incident. Prints a row. Returns a dict (plan, degraded, final event, step events, sims used)."""
    if not (os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY")):
        pytest.skip("neither GROQ_API_KEY nor OPENROUTER_API_KEY is set")
    for name, model in MODELS.items():
        if not os.getenv(f"MODEL_PLANNER_{name}"):
            monkeypatch.setenv(f"MODEL_PLANNER_{name}", model)
    events = []
    with prepared(sid, src) as (state, token):
        plan, degraded = run_plan(state, token, emit=lambda t, m, d, **kw: events.append((t, m, d, kw)))
        used = sims_used(state)
    steps = [(d, kw) for t, m, d, kw in events if t == "step" and d and "turn" in d]
    final = next((d for t, m, d, kw in events if t == "plan"), {})
    tokens = (sum(kw["tokens_in"] or 0 for d, kw in steps), sum(kw["tokens_out"] or 0 for d, kw in steps))
    print(f"\n{sid} (incident {state['id']}): provider={final.get('provider')} turns={final.get('turns')} llm_calls={len(steps)} tokens={tokens[0]}/{tokens[1]} "
          f"cached={sum(d['cached'] for d, kw in steps)}/{len(steps)} sims_used={used} degraded={degraded} source={plan['source']} accepted={final.get('accepted')}\n"
          f"  LLM choice: {final.get('llm_choice')} (llm_chosen {final.get('llm_chosen')}) -> plan {plan['cut_eids']} ({plan['chosen']}), "
          f"collateral {plan['simulation']['collateral']['count']}\n  notes: {plan['notes']}")
    for r in plan["reasons"]:
        print("  reason:", r["eid"], r["why"][:230])
    for t, m, d, kw in events:
        if "rejected" in m or "unavailable" in m:
            print("  EVENT:", m)
    return {"plan": plan, "degraded": degraded, "final": final, "steps": steps, "tokens": tokens}


@pytest.mark.live
def test_live_s8_picks_the_zero_collateral_cut_and_a_new_incident_is_served_from_the_cache(db, monkeypatch):
    a = live(monkeypatch, *S8)  # incident A: real LLM calls
    assert not a["degraded"] and a["plan"]["source"] == "planner", f"the code overrode the LLM: {a['final']}"  # an override must FAIL, not pass silently
    assert a["plan"]["cut_eids"] == ["e:s8:05"] and a["plan"]["simulation"]["collateral"]["count"] == 0
    b = live(monkeypatch, *S8)  # incident B: a different incident id, the same messages -> every LLM step from the cache
    assert all(d["cached"] for d, kw in b["steps"]) and len(b["steps"]) == len(a["steps"])
    assert b["plan"]["cut_eids"] == a["plan"]["cut_eids"] and b["plan"]["source"] == "planner"
    print(f"\nA/B S8: tokens A {a['tokens'][0]}+{a['tokens'][1]} (earlier run: ~5.4k in), B cached {len(b['steps'])}/{len(b['steps'])} (0 real calls), same cut {b['plan']['cut_eids']}")


@pytest.mark.live
def test_live_s2_gives_a_valid_accepted_plan(db, monkeypatch):
    r = live(monkeypatch, "s2", "user:ravi")
    plan, degraded, final = r["plan"], r["degraded"], r["final"]
    assert not degraded and plan["source"] == "planner", f"the code overrode the LLM: {final}"
    assert plan["simulation"]["blocks_attack"] and plan["simulation"]["needs_ok"] and len(plan["cut_eids"]) == plan["max_flow"] == 2

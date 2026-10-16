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

FAST_KEYS = {"cut_eids", "chosen", "source", "reasons", "notes", "safe", "max_flow", "witness", "simulation", "suggestion", "alternatives", "avoid", "instruction", "default_fix"}
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
    assert not degraded and plan["source"] == "planner" and plan["cut_eids"] == ["e:s8:05"] and used == 2  # both candidates, simulated once by the code
    assert [a["cut_eids"] for a in plan["alternatives"]] == [["e:s8:07"]]  # the other candidate, for the trade-off


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
    assert used == 2 and plan["source"] == "planner"  # the 2 candidates once (the code's); the LLM's repeat was answered from the results already seen
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
    assert used == 2  # both candidates were simulated once before the LLM ran: the fallback simulates nothing again
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



# --- operator instructions (P7.7): the Planner maps free text to edges to avoid; the code checks, recomputes and proves ---

LEGACY = "Max's team still uses the legacy role every quarter for audits. Don't change who can assume role s8-legacy."
QUOTE = "Don't change who can assume role s8-legacy"


def submit_avoid(eids, avoid, why="low risk", notes="The instruction ruled out e:s8:05."):
    return message(("submit_fix_plan", {"cut_eids": eids, "chosen": "custom", "reasons": [{"eid": e, "why": why} for e in eids], "notes": notes,
                                         "avoid": [{"eid": e, "quote": q} for e, q in avoid]}))


def with_instruction(state, text):
    state["instructions"] = text
    return state


def avoid_script(avoid_eids, simulate, submit_msg):
    """The LLM maps the instruction to an edge, asks for the constrained candidates, simulates them, submits."""
    return [message(("compute_min_cut", {"avoid_eids": avoid_eids}), *[("simulate_cut", {"eids": e}) for e in simulate]), submit_msg]


def default_fix_of(state, token):
    from core import fast_brains
    return fast_brains.plan(state, lock_token=token)


def test_an_honoured_instruction_gives_a_different_cut_that_is_still_proven(db, monkeypatch):
    with prepared(*S8) as (state, token):
        gw = Gateway(monkeypatch, *avoid_script(["e:s8:05"], [["e:s8:06"], ["e:s8:07"]], submit_avoid(["e:s8:06"], [("e:s8:05", QUOTE)])))
        events = []
        plan, degraded = run_plan(with_instruction(state, LEGACY), token, emit=lambda t, m, d, **kw: events.append((t, m, d)))
        used = sims_used(state)
    assert not degraded and plan["source"] == "planner" and plan["cut_eids"] == ["e:s8:06"] and set(plan) == FAST_KEYS
    assert plan["simulation"]["blocks_attack"] and plan["simulation"]["needs_ok"] and plan["simulation"]["collateral"]["count"] == 0  # verified by simulate_cut
    assert plan["avoid"] == [{"eid": "e:s8:05", "quote": QUOTE}] and plan["instruction"] == {"honoured": True, "note": "avoided e:s8:05"}
    assert plan["default_fix"]["cut_eids"] == ["e:s8:05"] and plan["default_fix"]["collateral"] == {"count": 0, "weighted": 0}  # what fast mode picks
    by_cut = {tuple(a["cut_eids"]): a for a in plan["alternatives"]}
    assert by_cut[("e:s8:05",)]["touches_avoided"] == ["e:s8:05"] and by_cut[("e:s8:07",)]["touches_avoided"] == []  # the default is ruled out by the instruction
    assert by_cut[("e:s8:07",)]["collateral"]["count"] == 12 and plan["chosen"] == "source_side"  # e:s8:06 is the constrained source_side candidate
    first_user, first_system = gw.calls[0]["messages"][1]["content"], gw.calls[0]["messages"][0]["content"]
    assert used <= 6 and "Operator instructions" in first_user and "Max's team" in first_user
    assert "OPERATOR INSTRUCTIONS" in first_system  # the instruction block is in the system prompt
    assert [m for t, m, d in events if "rejected" in m] == []


def test_a_normal_run_has_no_instruction_block_so_its_cache_keys_do_not_change(db, monkeypatch):
    with prepared(*S8) as (state, token):
        gw = Gateway(monkeypatch, submit(["e:s8:05"]))
        run_plan(state, token)
    assert "OPERATOR INSTRUCTIONS" not in gw.calls[0]["messages"][0]["content"] and "Operator instructions" not in gw.calls[0]["messages"][1]["content"]


def test_a_cut_that_contains_an_avoided_edge_is_rejected_and_the_retry_gets_the_problem(db, monkeypatch):
    with prepared(*S8) as (state, token):
        gw = Gateway(monkeypatch, submit_avoid(["e:s8:05"], [("e:s8:05", QUOTE)]),  # avoids e:s8:05 and cuts it anyway
                     submit_avoid(["e:s8:06"], [("e:s8:05", QUOTE)]))
        events = []
        plan, _ = run_plan(with_instruction(state, LEGACY), token, emit=lambda t, m, d, **kw: events.append((t, m, d)))
    assert plan["source"] == "planner" and plan["cut_eids"] == ["e:s8:06"] and plan["instruction"]["honoured"] is True
    retry_task = gw.calls[1]["messages"][1]["content"]
    assert len(gw.calls) == 2 and "Your previous answer was rejected: the cut contains the avoided edge(s) ['e:s8:05']" in retry_task
    assert len([m for t, m, d in events if "planner_choice_rejected" in m]) == 1


@pytest.mark.parametrize("avoid,why", [([("e:s8:05", "never touch the audit role")], "is not a substring of the operator instruction"),
                                       ([("e:nope", QUOTE)], "avoid eid e:nope does not exist")])
def test_an_invented_quote_or_eid_is_rejected_twice_then_the_code_candidate_with_a_note(db, monkeypatch, avoid, why):
    with prepared(*S8) as (state, token):
        gw = Gateway(monkeypatch, submit_avoid(["e:s8:06"], avoid), submit_avoid(["e:s8:06"], avoid))
        plan, degraded = run_plan(with_instruction(state, LEGACY), token)
    assert len(gw.calls) == 2 and not degraded and plan["source"] == "code" and plan["cut_eids"] == ["e:s8:05"]  # the code candidate
    assert plan["instruction"]["honoured"] is False and why in plan["instruction"]["note"]
    assert plan["notes"].startswith("Instruction could not be honoured: ")  # never silently ignored
    assert plan["avoid"] == [] and set(plan) == FAST_KEYS


def test_a_fix_with_an_avoided_edge_that_is_not_verified_is_never_accepted(db, monkeypatch):
    with prepared(*S8) as (state, token):  # the avoid list is fine, but the cut blocks nothing: the simulation says no
        Gateway(monkeypatch, submit_avoid(["e:s8:09"], [("e:s8:05", QUOTE)]), submit_avoid(["e:s8:09"], [("e:s8:05", QUOTE)]))
        plan, _ = run_plan(with_instruction(state, LEGACY), token)
    assert plan["source"] == "code" and plan["instruction"]["honoured"] is False and "blocks_attack=False" in plan["instruction"]["note"]


def test_an_instruction_that_rules_out_no_edge_is_reported_as_such(db, monkeypatch):
    with prepared(*S8) as (state, token):
        Gateway(monkeypatch, message(sim(state, ["e:s8:05"]), sim(state, ["e:s8:07"])), submit_avoid(["e:s8:05"], [], notes="Nothing to avoid."))
        plan, _ = run_plan(with_instruction(state, "Please be careful."), token)
    assert plan["source"] == "planner" and plan["avoid"] == [] and plan["default_fix"] is None
    assert plan["instruction"] == {"honoured": True, "note": "the instruction rules out no edge"}


def test_s2_leave_the_build_server_alone_gives_a_cut_without_the_secret_edge(db, monkeypatch):
    text = "Don't touch the build server, CI depends on it."
    with prepared("s2", "user:ravi") as (state, token):
        Gateway(monkeypatch, *avoid_script(["e:s2:10"], [["e:s2:08", "e:s2:11"]], submit_avoid(["e:s2:08", "e:s2:11"], [("e:s2:10", "Don't touch the build server")])))
        plan, _ = run_plan(with_instruction(state, text), token)
        default = default_fix_of(state, token)
    assert plan["source"] == "planner" and "e:s2:10" not in plan["cut_eids"] and "e:s2:11" in plan["cut_eids"]
    assert plan["simulation"]["blocks_attack"] and plan["simulation"]["needs_ok"] and plan["avoid"][0]["eid"] == "e:s2:10"
    assert "e:s2:10" in default["cut_eids"]  # the default fix is the one that touches the build server


def test_without_a_safe_cut_the_instruction_is_not_applied_and_says_so(db, monkeypatch):
    key = json.load(open("data/answer_keys/s5.json"))
    gw = Gateway(monkeypatch)
    with prepared("s5", key["src"]) as (state, token):
        plan, _ = run_plan(with_instruction(state, "Leave the shared policy alone."), token)
    assert gw.calls == [] and plan["instruction"] == {"honoured": False, "note": "no safe cut exists even without the instruction"}
    assert plan["notes"].startswith("Instruction could not be honoured: no safe cut exists")


def test_when_the_llm_is_unavailable_the_instruction_is_not_honoured_and_the_step_is_named(db, monkeypatch):
    with prepared(*S8) as (state, token):
        Gateway(monkeypatch, LLMUnavailable("429 RateLimitError"))
        plan, degraded = run_plan(with_instruction(state, LEGACY), token)
        steps = state["degraded_steps"]
    assert degraded is True and plan["instruction"] == {"honoured": False, "note": "the LLM was unavailable"} and steps == ["planner: rate limit"]


def test_a_repeated_constrained_call_is_answered_from_the_first_one(db, monkeypatch):
    with prepared(*S8) as (state, token):  # the retry runs the agent again: its compute_min_cut(avoid) must not cost a 2nd guarded copy
        avoid = [("e:s8:05", "not in the instruction")]
        Gateway(monkeypatch, *avoid_script(["e:s8:05"], [["e:s8:06"]], submit_avoid(["e:s8:06"], avoid)),
                *avoid_script(["e:s8:05"], [["e:s8:06"]], submit_avoid(["e:s8:06"], avoid)))
        run_plan(with_instruction(state, LEGACY), token)
        used = sims_used(state)
    # ONE constrained copy + ONE simulate_cut for both attempts (the repeats were answered from what was already seen: 6 without that),
    # plus the 2 candidates the code fallback simulates once both answers were rejected
    assert used == 4


# --- LLM budget and robustness (P9.0): call the LLM only when it can add something, and never let it break the code's fallback ---

S3_SRC = json.load(open("data/answer_keys/s3.json"))["src"]
IMPOSSIBLE = "Do not change anything between user:max and the reports database."
IMPOSSIBLE_QUOTE = "anything between user:max and the reports database"


def plan_events(events):
    return [d for t, m, d in events if t == "plan"]


def test_one_verified_option_skips_the_llm_and_says_so(db, monkeypatch):
    gw = Gateway(monkeypatch)  # an empty script: any chat call would fail the test
    events = []
    with prepared("s3", S3_SRC) as (state, token):  # S3: both min-cut candidates are the SAME edge
        plan, degraded = run_plan(state, token, emit=lambda t, m, d, **kw: events.append((t, m, d)))
        used = sims_used(state)
    assert gw.calls == [] and degraded is False and state["llm_calls"] == 0
    assert plan["source"] == "code" and plan["cut_eids"] == ["e:s3:02"] and set(plan) == FAST_KEYS and plan["simulation"]["blocks_attack"]
    assert used == 1  # the one distinct set was simulated once; fast_brains.plan reused it
    assert "planner skipped: one verified option" in [m for t, m, d in events]
    assert plan_events(events) == [{"chosen": plan["chosen"], "cut_eids": ["e:s3:02"], "source": "code", "llm_choice": None, "llm_chosen": None, "accepted": None,
                                    "collateral": 0, "sims_used": 1, "provider": None, "turns": 0}]


def test_two_candidates_but_only_one_passes_simulate_cut_also_skips_the_llm(db, monkeypatch):
    real = write.simulate_cut

    def sim_that_breaks_a_need_on_the_source_side(sandbox, src, eids, **kw):
        result = real(sandbox, src, eids, **kw)
        if list(eids) == ["e:s8:05"]:
            result["data"]["needs_ok"] = False
        return result
    monkeypatch.setattr(write, "simulate_cut", sim_that_breaks_a_need_on_the_source_side)
    gw = Gateway(monkeypatch)
    with prepared(*S8) as (state, token):
        plan, degraded = run_plan(state, token)
        used = sims_used(state)
    assert gw.calls == [] and not degraded and plan["source"] == "code" and plan["cut_eids"] == ["e:s8:07"] and used == 2
    assert plan["alternatives"][0]["cut_eids"] == ["e:s8:05"] and plan["alternatives"][0]["needs_ok"] is False


def test_two_different_verified_candidates_still_run_the_llm_and_count_its_calls(db, monkeypatch):
    with prepared(*S8) as (state, token):  # S8 (and S2): two verified options with different collateral
        gw = Gateway(monkeypatch, message(sim(state, ["e:s8:05"]), sim(state, ["e:s8:07"])), submit(["e:s8:05"]))
        plan, _ = run_plan(state, token)
        used = sims_used(state)
    assert len(gw.calls) == 2 and state["llm_calls"] == 2 and plan["source"] == "planner" and used == 2  # the LLM's repeats cost no simulation


def test_an_instruction_always_runs_the_llm_even_when_there_is_one_option(db, monkeypatch):
    with prepared("s3", S3_SRC) as (state, token):
        gw = Gateway(monkeypatch, submit_avoid(["e:s3:02"], [], notes="Nothing to avoid."))
        plan, _ = run_plan(with_instruction(state, "Please be careful."), token)
    assert len(gw.calls) == 1 and plan["source"] == "planner" and plan["instruction"]["honoured"] is True


def test_the_llm_cannot_use_the_two_simulations_reserved_for_the_code(db, monkeypatch):
    # The model keeps trying new avoid sets. Its 5th constrained cut is refused (LIMIT_EXCEEDED, a normal tool error it can read), and the
    # incident still ends with the code's proven cut: the code's own simulations had their 2 left.
    avoids = [["e:s8:05"], ["e:s8:06"], ["e:s8:07"], ["e:s8:08"], ["e:s8:09"]]
    with prepared(*S8) as (state, token):
        gw = Gateway(monkeypatch, *[message(("compute_min_cut", {"avoid_eids": a})) for a in avoids], submit_avoid([], []), submit_avoid([], []))
        plan, degraded = run_plan(with_instruction(state, LEGACY), token)
        used = sims_used(state)
    replies = [[m["content"] for m in call["messages"] if m["role"] == "tool"] for call in gw.calls]
    assert all('"ok":true' in r[-1] for r in replies[1:5])  # constrained cuts 1-4 worked
    assert '"code":"LIMIT_EXCEEDED"' in replies[5][-1] and "reserved for the code" in replies[5][-1]  # the 5th was refused
    assert not degraded and plan["source"] == "code" and plan["cut_eids"] == ["e:s8:05"]  # the code cut, proven: not a FAILED incident
    assert plan["simulation"]["blocks_attack"] and plan["simulation"]["needs_ok"] and plan["instruction"]["honoured"] is False
    assert used == 6  # 4 for the LLM + the code's 2; the refused 5th call used no number


def test_the_code_may_use_all_six_simulations_but_the_llm_only_four(db):
    from core.tools import guards
    from core.tools.common import LimitExceeded
    redis = client.get_db().connection
    try:
        for n in range(1, 5):
            assert guards.check_quota("p90quota", "simulation", llm=True) == n
        with pytest.raises(LimitExceeded, match="reserved for the code"):
            guards.check_quota("p90quota", "simulation", llm=True)
        assert redis.get("quota:inc:p90quota:sims") == "4"  # a refused call gives its number back
        assert [guards.check_quota("p90quota", "simulation"), guards.check_quota("p90quota", "simulation")] == [5, 6]
        with pytest.raises(LimitExceeded):
            guards.check_quota("p90quota", "simulation")
    finally:
        redis.delete("quota:inc:p90quota:sims")


def test_a_proved_impossible_instruction_is_not_retried_and_the_note_says_why(db, monkeypatch):
    with prepared(*S8) as (state, token):
        avoid = [(e, IMPOSSIBLE_QUOTE) for e in ("e:s8:05", "e:s8:06", "e:s8:07")]
        gw = Gateway(monkeypatch, message(("compute_min_cut", {"avoid_eids": ["e:s8:05", "e:s8:06", "e:s8:07"]})),
                     submit_avoid([], avoid, notes="Everything between them is ruled out."), submit_avoid([], avoid))  # the 3rd message must stay unused
        events = []
        plan, degraded = run_plan(with_instruction(state, IMPOSSIBLE), token, emit=lambda t, m, d, **kw: events.append((t, m, d)))
        used = sims_used(state)
    assert len(gw.calls) == 2 and len(gw.scripts["fake"]) == 1 and not degraded  # no retry: one tool turn and the submit
    assert plan["source"] == "code" and plan["cut_eids"] == ["e:s8:05"] and plan["instruction"]["honoured"] is False
    note = plan["instruction"]["note"]
    assert note.startswith("the instruction rules out every safe cut") and "no safe cut keeps" in note and "although" not in note
    assert plan["notes"].startswith("Instruction could not be honoured: the instruction rules out every safe cut")
    assert used == 3  # the constrained copy + the code's 2 candidates
    assert len([m for t, m, d in events if "planner_choice_rejected" in m]) == 1


def test_an_empty_cut_without_that_proof_keeps_the_old_wording_and_the_one_retry(db, monkeypatch):
    with prepared(*S8) as (state, token):
        gw = Gateway(monkeypatch, submit_avoid([], []), submit_avoid([], []))
        plan, _ = run_plan(with_instruction(state, LEGACY), token)
    assert len(gw.calls) == 2 and "the cut is empty although a safe cut exists" in plan["instruction"]["note"]


def test_an_unsupported_avoid_list_is_not_taken_as_proof(db, monkeypatch):
    with prepared(*S8) as (state, token):  # the constrained cut IS unsafe, but the quote the model gives is not in the instruction: retry, not a verdict
        bad = [(e, "not in the instruction") for e in ("e:s8:05", "e:s8:06", "e:s8:07")]
        gw = Gateway(monkeypatch, message(("compute_min_cut", {"avoid_eids": ["e:s8:05", "e:s8:06", "e:s8:07"]})), submit_avoid([], bad), submit_avoid([], bad))
        plan, _ = run_plan(with_instruction(state, IMPOSSIBLE), token)
    assert len(gw.calls) == 3 and "is not a substring of the operator instruction" in plan["instruction"]["note"]


def test_the_incident_budget_ends_the_planner_at_once_and_the_code_decides(db, monkeypatch):
    from core import config
    with prepared(*S8) as (state, token):
        state["llm_calls"] = config.LLM_MAX_CALLS_PER_INCIDENT  # all spent (e.g. by the Investigator and earlier attempts)
        gw = Gateway(monkeypatch)
        plan, degraded = run_plan(state, token)
    assert gw.calls == [] and degraded is True and plan["source"] == "code" and plan["cut_eids"] == ["e:s8:05"]
    assert state["degraded_steps"] == ["planner: LLM budget for this incident used up"]

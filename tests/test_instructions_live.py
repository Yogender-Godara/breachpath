"""Operator instructions on the REAL LLMs (-m live): an instruction gives a DIFFERENT cut than fast mode, and the code still proves it.
Each test runs fast mode and agent mode (with the instruction) on the same scenario and prints the two results side by side."""
import os

import pytest

from core.graph import loader
from data.scenarios import build
from eval import run_eval

MODELS = {"GROQ": "openai/gpt-oss-120b", "OPENROUTER": "nvidia/nemotron-3-super-120b-a12b:free"}  # VERIFY_RESULTS P1.4
S8_TEXT = "Max's team still uses the legacy role every quarter for audits. Don't change who can assume role s8-legacy."
S2_TEXT = "Don't touch the build server, CI depends on it."


def both(monkeypatch, sid, text):
    """(fast state, agent state with the instruction). Prints them side by side."""
    if not (os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY")):
        pytest.skip("neither GROQ_API_KEY nor OPENROUTER_API_KEY is set")
    for agent in ("INVESTIGATOR", "PLANNER", "REPORTER"):
        for name, model in MODELS.items():
            if not os.getenv(f"MODEL_{agent}_{name}"):
                monkeypatch.setenv(f"MODEL_{agent}_{name}", model)
    graph = build(sid)
    restore = run_eval.throttled(20)  # a pause after every real LLM call: the free tier allows few tokens per minute
    try:
        fast, _, _ = run_eval.run_product(sid, "fast", graph)
        agent, events, wall = run_eval.run_product(sid, "agent", graph, text)
    finally:
        restore()
    if not agent["plan"]:
        print(f"\n{sid}: the agent run ended {agent['status']} with no plan: {agent.get('failure_detail')}  degraded={agent['degraded_steps']}")
        for e in events[-6:]:
            print("  EVENT", e["agent"], e["type"], e["msg"][:160])
    row = lambda label, s: (f"  {label:<26} cut {s['plan']['cut_eids']}  collateral {s['plan']['simulation']['collateral']['count']} "
                            f"(w {s['plan']['simulation']['collateral']['weighted']})  proof {'PASS' if (s['verdict'] or {}).get('pass') else 'none/FAIL'}  "
                            f"status {s['status']}  source {s['plan']['source']}")
    print(f"\n{sid}: instruction: {text!r}\n{row('fast mode (no instruction)', fast)}\n{row('agent mode (instruction)', agent)}")
    print(f"  avoided: {agent['plan']['avoid']}\n  instruction: {agent['plan']['instruction']}\n  default fix: {agent['plan']['default_fix']}")
    print(f"  degraded: {agent['degraded']} {agent['degraded_steps']}  [{wall} ms]")
    for a in agent["plan"]["alternatives"]:
        print(f"  alternative {a['cut_eids']}: collateral {a['collateral']}, touches avoided {a.get('touches_avoided')}")
    for e in events:
        if e["agent"] == "reporter" and (e["data"] or {}).get("problems"):
            print("  reporter guard problems:", e["data"]["problems"])
    print("  --- Fix section of the report ---\n" + agent["report"]["markdown"].split("## Fix")[1].split("## Proof")[0].rstrip())
    return fast, agent


def assert_honoured(agent):
    assert agent["status"] == "DONE" and agent["verdict"]["pass"] is True, agent.get("failure_detail")  # proven by the code, not by the LLM
    assert not any(s.startswith(("planner", "investigator")) for s in agent["degraded_steps"]), agent["degraded_steps"]  # the cut came from the LLM
    assert agent["plan"]["source"] == "planner" and agent["plan"]["instruction"]["honoured"] is True, agent["plan"]["instruction"]
    assert agent["plan"]["avoid"] and not set(e["eid"] for e in agent["plan"]["avoid"]) & set(agent["plan"]["cut_eids"])
    assert "instruction" in agent["report"]["markdown"].lower()  # the report shows it (the guard requires this)


@pytest.mark.live
def test_live_s8_legacy_role_instruction_changes_the_cut_to_e_s8_06(falkordb, monkeypatch):
    loader.load_scenario("s8")
    fast, agent = both(monkeypatch, "s8", S8_TEXT)
    assert fast["plan"]["cut_eids"] == ["e:s8:05"]  # the default: the cheapest cut edits the legacy role's trust
    assert_honoured(agent)
    assert agent["plan"]["cut_eids"] == ["e:s8:06"] and agent["plan"]["simulation"]["collateral"]["count"] == 0
    assert [a["eid"] for a in agent["plan"]["avoid"]] == ["e:s8:05"] and agent["plan"]["avoid"][0]["quote"] in S8_TEXT


@pytest.mark.live
def test_live_s2_build_server_instruction_gives_a_cut_without_the_secret_edge(falkordb, monkeypatch):
    loader.load_scenario("s2")
    fast, agent = both(monkeypatch, "s2", S2_TEXT)
    assert "e:s2:10" in fast["plan"]["cut_eids"]  # the default removes the saved password from the build server
    assert_honoured(agent)
    assert "e:s2:10" not in agent["plan"]["cut_eids"] and "e:s2:11" in agent["plan"]["cut_eids"]

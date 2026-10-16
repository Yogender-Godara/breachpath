"""The Investigator. Unit tests: a scripted fake gateway + the REAL MCP subprocess + FalkorDB. Live tests (-m live): S2, S4, S7 on the real LLMs."""
import asyncio
import json
import os

import pytest

from agents import investigator
from agents.base import open_session
from agents.schemas import PathOut
from core import config, fast_brains
from core.graph import client, loader, queries
from core.graph.schema import baseline_name
from orchestrator.state import new_state
from tests.agent_fakes import Gateway, message

CUSTOMERS = ["e:s2:01", "e:s2:02", "e:s2:05", "e:s2:06", "e:s2:07", "e:s2:08"]
PAYMENTS = ["e:s2:09", "e:s2:10", "e:s2:11", "e:s2:12", "e:s2:13"]


def findings(paths, jewels=("db:s2-customers", "db:s2-payments"), truncated=False, notes="n"):
    return message(("submit_findings", {"reachable_jewels": list(jewels), "paths": paths, "truncated": truncated, "notes": notes}))


GOOD = findings([{"target": "db:s2-customers", "eids": CUSTOMERS}, {"target": "db:s2-payments", "eids": PAYMENTS}])
BAD = findings([{"target": "db:s2-customers", "eids": ["e:s2:01", "e:s2:05"]}])  # starts at src but skips a link: not a chain


def investigate(sid, src, emit=None):
    """Open a session, run the agent Investigator, close it. Returns (findings, degraded)."""
    state = new_state(sid, src, "agent")

    async def go():
        async with open_session() as session:
            return await investigator.investigate(session, state, emit=emit)
    return asyncio.run(go()), state


@pytest.fixture
def s2(falkordb):
    loader.load_scenario("s2")


@pytest.fixture
def s2_s7(falkordb):
    loader.load_scenario("s2")
    loader.load_scenario("s7")


def fast_keys():
    state = new_state("s2", "user:ravi", "fast")
    return set(fast_brains.investigate(state))


# --- the post-check (pure) ---

def test_the_post_check_drops_everything_that_is_not_a_real_attack_path(s2):
    edges = {r[0]: (r[1], r[2]) for r in client.ro_query(baseline_name("s2"), queries.ALL_EDGES)}
    jewels = {r[0] for r in client.ro_query(baseline_name("s2"), queries.JEWELS)}
    first_hop_target = edges["e:s2:01"][1]  # a real chain, but it ends at a group
    paths = [PathOut(target="db:s2-customers", eids=CUSTOMERS),  # valid
             PathOut(target="db:s2-customers", eids=["e:s2:01", "e:s2:05"]),  # not a connected chain
             PathOut(target="db:s2-customers", eids=CUSTOMERS[:2] + ["e:s2:nope"]),  # unknown eid
             PathOut(target="db:s2-customers", eids=CUSTOMERS[1:]),  # does not start at src
             PathOut(target="db:s2-payments", eids=CUSTOMERS),  # ends elsewhere than its target
             PathOut(target=first_hop_target, eids=["e:s2:01"]),  # target is not a crown jewel
             PathOut(target="db:s2-customers", eids=CUSTOMERS),  # duplicate
             PathOut(target="db:s2-customers", eids=[]),  # empty
             PathOut(target="db:s2-payments", eids=PAYMENTS)]  # valid
    valid, dropped = investigator.check_paths(paths, "user:ravi", edges, jewels)
    assert [p["eids"] for p in valid] == [CUSTOMERS, PAYMENTS]
    assert [d.split(": ", 1)[1] for d in dropped] == ["edges do not form a connected chain", "unknown eid e:s2:nope", "does not start at user:ravi",
                                                      "does not end at its target db:s2-payments", f"{first_hop_target} is not a crown jewel",
                                                      "duplicate path", "no edges"]
    state = new_state("s2", "user:ravi", "fast")
    fast = {tuple(p["eids"]): p for p in fast_brains.investigate(state)["paths"]}
    assert all(p == fast[tuple(p["eids"])] for p in valid)  # the very same dicts as the fast brain's (node_ids, hops)


# --- the agent around it ---

def test_the_findings_have_exactly_the_keys_of_the_fast_brain(s2, monkeypatch):
    Gateway(monkeypatch, GOOD)
    (result, degraded), _ = investigate("s2", "user:ravi")
    assert not degraded and set(result) == fast_keys()
    fast = fast_brains.investigate(new_state("s2", "user:ravi", "fast"))
    assert result["reachable_jewels"] == fast["reachable_jewels"] and result["top_edges"] == fast["top_edges"]
    assert result["path_count_by_target"] == {"db:s2-customers": 1, "db:s2-payments": 1} and result["beyond_hops_jewels"] == []
    assert sorted(p["eids"] for p in result["paths"]) == sorted(p["eids"] for p in fast["paths"])


def test_an_llm_that_misses_a_jewel_still_gets_the_bfs_jewels_and_a_note(s2, monkeypatch):
    Gateway(monkeypatch, findings([{"target": "db:s2-customers", "eids": CUSTOMERS}], jewels=["db:s2-customers"], notes="Found one."))
    (result, degraded), _ = investigate("s2", "user:ravi")
    assert not degraded and result["reachable_jewels"] == ["db:s2-customers", "db:s2-payments"]  # decided by code
    assert result["notes"] == "Found one. LLM missed: db:s2-payments." and len(result["paths"]) == 1


def test_an_llm_that_invents_a_jewel_is_not_trusted(s2, monkeypatch):
    Gateway(monkeypatch, findings([{"target": "db:s2-customers", "eids": CUSTOMERS}], jewels=["db:s2-customers", "db:s2-payments", "db:fake"]))
    (result, _), _ = investigate("s2", "user:ravi")
    assert "db:fake" not in result["reachable_jewels"] and "BFS does not reach: db:fake" in result["notes"]


def test_all_paths_invalid_retries_once_with_the_reasons_then_falls_back_to_the_fast_brain(s2, monkeypatch):
    gw = Gateway(monkeypatch, BAD, BAD)
    events = []
    (result, degraded), state = investigate("s2", "user:ravi", emit=lambda t, m, d, **kw: events.append((t, m, d)))
    assert degraded is True and result == fast_brains.investigate(state)
    assert state["degraded_steps"] == ["investigator: no valid path after a retry"]  # WHICH step fell back
    assert len(gw.calls) == 2 and "Your previous paths were invalid" not in gw.calls[0]["messages"][1]["content"]
    retry = gw.calls[1]["messages"]
    assert [m["role"] for m in retry] == ["system", "user"]  # a fresh conversation
    assert "Your previous paths were invalid: " in retry[1]["content"] and "connected chain" in retry[1]["content"]
    assert "Copy paths exactly from tool results." in retry[1]["content"]
    assert [m for t, m, d in events if "falling back" in m] and sum(1 for t, m, d in events if "dropped path" in m) == 2


def test_a_retry_that_succeeds_is_not_degraded(s2, monkeypatch):
    gw = Gateway(monkeypatch, BAD, GOOD)
    (result, degraded), _ = investigate("s2", "user:ravi")
    assert not degraded and len(result["paths"]) == 2 and len(gw.calls) == 2


def test_a_degraded_run_agent_retries_once_then_falls_back(s2, monkeypatch):
    from agents.llm_gateway import LLMUnavailable
    gw = Gateway(monkeypatch, *[LLMUnavailable("down"), LLMUnavailable("down")])
    (result, degraded), state = investigate("s2", "user:ravi")
    assert degraded is True and result == fast_brains.investigate(state) and len(gw.calls) == 2


def test_no_jewel_within_the_hop_bound_means_zero_paths_is_correct_and_no_retry(s2_s7, monkeypatch):
    key = json.load(open("data/answer_keys/s7.json"))
    gw = Gateway(monkeypatch, findings([], jewels=[]))
    events = []
    (result, degraded), _ = investigate("s7", key["src"], emit=lambda t, m, d, **kw: events.append((t, d)))
    assert not degraded and len(gw.calls) == 1
    assert result["paths"] == [] and result["reachable_jewels"] == key["reachable_jewels"] and result["beyond_hops_jewels"] == key["long_path_jewels"]
    assert "LLM missed" in result["notes"]


def test_the_finding_event_carries_what_the_eval_needs(s2, monkeypatch):
    Gateway(monkeypatch, findings([{"target": "db:s2-customers", "eids": CUSTOMERS}, {"target": "db:s2-customers", "eids": ["e:s2:02"]}],
                                  jewels=["db:s2-customers"]))
    events = []
    investigate("s2", "user:ravi", emit=lambda t, m, d, **kw: events.append((t, m, d)))
    assert {t for t, _, _ in events} == {"step", "finding"}
    summary = [d for t, m, d in events if t == "finding" and "valid path" in m][0]
    assert summary == {"llm_jewels": ["db:s2-customers"], "code_jewels": ["db:s2-customers", "db:s2-payments"], "valid_paths": 1,
                       "dropped_paths": 1, "provider": "fake", "turns": 1, "llm_calls": 1}


def test_two_incidents_send_identical_messages_so_the_cache_can_hit(s2, monkeypatch):
    calls = []
    for _ in range(2):
        gw = Gateway(monkeypatch, message(("get_blast_radius", {})), GOOD)
        (_, _), state = investigate("s2", "user:ravi")
        calls.append((state["id"], gw.calls))
    (id_a, a), (id_b, b) = calls
    assert id_a != id_b and [(c["messages"], c["tools"]) for c in a] == [(c["messages"], c["tools"]) for c in b]
    assert "t:s2" not in json.dumps(a)  # the graph name is injected, not in the messages
    assert "graph" not in {p for t in a[0]["tools"] for p in t["function"]["parameters"]["properties"]}


# --- live: the real LLMs (Groq first, then OpenRouter) ---

MODELS = {"GROQ": "openai/gpt-oss-120b", "OPENROUTER": "nvidia/nemotron-3-super-120b-a12b:free"}  # VERIFY_RESULTS P1.4


def live(monkeypatch, sid):
    """Run the agent Investigator on a scenario (a NEW incident each call); print one row; return (findings, answer key, found, true, step events)."""
    if not (os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY")):
        pytest.skip("neither GROQ_API_KEY nor OPENROUTER_API_KEY is set")
    for name, model in MODELS.items():
        if not os.getenv(f"MODEL_INVESTIGATOR_{name}"):
            monkeypatch.setenv(f"MODEL_INVESTIGATOR_{name}", model)
    loader.load_scenario(sid)
    key = json.load(open(f"data/answer_keys/{sid}.json"))
    events = []
    (result, degraded), state = investigate(sid, key["src"], emit=lambda t, m, d, **kw: events.append((t, m, d, kw)))
    data = next((d for t, m, d, kw in events if t == "finding" and d and "llm_jewels" in d), {})
    steps = [(d, kw) for t, m, d, kw in events if t == "step" and d and "turn" in d]
    true = {tuple(p) for p in key["true_paths"]}
    found = {tuple(p["eids"]) for p in result["paths"]}
    print(f"\n{sid} (incident {state['id']}): provider={data.get('provider')} turns={data.get('turns')} llm_calls={data.get('llm_calls')} degraded={degraded} "
          f"tokens={sum(kw['tokens_in'] or 0 for d, kw in steps)}/{sum(kw['tokens_out'] or 0 for d, kw in steps)} cached={sum(d['cached'] for d, kw in steps)}/{len(steps)} "
          f"found/true paths={len(found & true)}/{len(true)} dropped={data.get('dropped_paths')} llm_jewels={data.get('llm_jewels')} "
          f"code_jewels={result['reachable_jewels']} beyond={result['beyond_hops_jewels']}\n  notes: {result['notes']}")
    assert not degraded and result["reachable_jewels"] == key["reachable_jewels"] and found <= true  # precision 100%
    return result, key, found, true, steps


@pytest.mark.live
def test_live_s2_matches_the_answer_key_and_a_new_incident_is_served_from_the_cache(falkordb, monkeypatch):
    result, key, found, true, steps_a = live(monkeypatch, "s2")  # incident A: real LLM calls
    assert found  # it found something; recall is printed above (found/true)
    result_b, _, found_b, _, steps_b = live(monkeypatch, "s2")  # incident B: a different incident id
    assert all(d["cached"] for d, kw in steps_b) and len(steps_b) == len(steps_a) and found_b == found and result_b["paths"] == result["paths"]
    print(f"\nA/B S2 investigator: B cached {len(steps_b)}/{len(steps_b)} (0 real calls), same paths")


@pytest.mark.live
def test_live_s4_decoys_do_not_fool_it(falkordb, monkeypatch):
    live(monkeypatch, "s4")  # decoys: precision is asserted in live()


@pytest.mark.live
def test_live_s7_beyond_the_hop_bound_has_no_paths(falkordb, monkeypatch):
    result, key, found, true, _ = live(monkeypatch, "s7")
    assert result["paths"] == [] and result["beyond_hops_jewels"] == key["long_path_jewels"]

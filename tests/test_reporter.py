"""The Reporter (agents/reporter.py). Unit tests: a scripted fake gateway (no LLM) over the states of real fast-mode runs of S2 (DONE)
and S5 (NEEDS_HUMAN) on FalkorDB. Live tests (-m live): the real LLMs write the S2 and S5 reports."""
import copy
import json
import os
import re

import pytest

from agents import reporter
from agents.llm_gateway import LLMUnavailable
from core import fast_brains
from core.graph import client, loader
from core.graph.schema import sandbox_name
from core.pipeline import run_incident
from core.tools import write as write_tools
from orchestrator.lock import hold_lock
from orchestrator.state import new_state
from tests.agent_fakes import Gateway, message

HEADINGS = ["Summary", "What was at risk", "Attack paths", "Fix", "Proof", "Business impact", "Next steps"]


def fast_state(sid, src):
    """The state of a finished fast-mode run (real FalkorDB), with its graphs and keys removed afterwards."""
    loader.load_scenario(sid)
    state = new_state(sid, src, "fast")
    redis = client.get_db().connection
    with hold_lock(state["id"]) as lock:
        run_incident(state, "fast", lock_token=lock.token, sink=[])
        for suffix in ("", "-split"):
            write_tools.delete_sandbox(sandbox_name(sid, state["id"], suffix), incident_id=state["id"], lock_token=lock.token)
        redis.delete(f"quota:inc:{state['id']}:sims")
    return state


@pytest.fixture(scope="module")
def states(falkordb):
    return {"s2": fast_state("s2", "user:ravi"), "s5": fast_state("s5", "user:zoe")}


@pytest.fixture
def s2(states):
    return copy.deepcopy(states["s2"])


@pytest.fixture
def s5(states):
    return copy.deepcopy(states["s5"])


def template(state):
    """The fast-mode report: it has the 7 headings and only facts of the state, so it is also a good 'LLM' answer."""
    return fast_brains.report(state)["markdown"]


def say(text):
    return message(content=text)


def slice_of(state):
    return reporter._slice(state, reporter._cut_kinds(state))


# --- write: the guard, the retry and the fallback ---

def test_a_good_report_passes_the_guard_and_is_returned_as_is(s2, monkeypatch):
    good = template(s2)
    assert reporter.check(good, slice_of(s2)) == []
    events = []
    gateway = Gateway(monkeypatch, say(good))
    markdown, degraded = reporter.write(s2, emit=lambda *a, **kw: events.append((a, kw)))
    assert (markdown, degraded) == (good, False) and len(gateway.calls) == 1
    call = gateway.calls[0]
    assert call["agent"] == "reporter" and call["tools"] is None and call["temperature"] == 0.2  # docs/06 section 6
    system, user = call["messages"]
    assert system["role"] == "system" and "REPORTER" in system["content"] and "BreachPath" in system["content"]  # preamble + reporter prompt
    assert user["content"].startswith("Incident state JSON:\n") and json.loads(user["content"].split("\n", 1)[1])["src"] == "user:ravi"
    assert [(a[0], a[2]["attempt"] if a[0] == "step" else a[2]["source"]) for a, kw in events] == [("step", 1), ("report", "llm")]


def test_an_invented_eid_is_retried_with_the_problem_and_the_second_answer_is_used(s2, monkeypatch):
    good = template(s2)
    bad = good.replace("e:s2:05", "e:s2:99")
    assert "e:s2:99" in bad
    gateway = Gateway(monkeypatch, say(bad), say(good))
    assert reporter.write(s2) == (good, False)
    assert len(gateway.calls) == 2
    first, second = gateway.calls[0]["messages"], gateway.calls[1]["messages"]
    assert second[:2] == first and second[2] == {"role": "assistant", "content": bad.strip()}
    assert second[3]["role"] == "user" and "e:s2:99" in second[3]["content"] and "not in the incident JSON" in second[3]["content"]


def test_a_missing_heading_twice_falls_back_to_the_template_and_degraded(s2, monkeypatch):
    bad = template(s2).replace("## Proof", "Proof")
    gateway = Gateway(monkeypatch, say(bad), say(bad))
    markdown, degraded = reporter.write(s2)
    assert degraded is True and len(gateway.calls) == 2
    assert markdown == fast_brains.report({**s2, "degraded": True})["markdown"]
    assert markdown.endswith(reporter.DEGRADED_LINE + "\n") and [h for h in HEADINGS if f"## {h}" in markdown] == HEADINGS


def test_still_bad_after_the_retry_does_not_try_the_next_provider(s2, monkeypatch):
    bad = template(s2).replace("## Fix", "## Repair")
    gateway = Gateway(monkeypatch, {"p1": [say(bad), say(bad)], "p2": [say(template(s2))]})
    assert reporter.write(s2)[1] is True
    assert [c["provider"] for c in gateway.calls] == ["p1", "p1"]


def test_the_word_verified_is_rejected_unless_the_suggestion_is_proven(s2, s5):
    text = template(s2) + "\n- The fix was verified in the sandbox.\n"
    problems = reporter.check(text, slice_of(s2))
    assert len(problems) == 1 and '"verified"' in problems[0]
    assert reporter.check(template(s2) + "\nUnverified claims are not made here.\n", slice_of(s2)) == []  # 'unverified' is another word
    assert "Verified" in template(s5) and reporter.check(template(s5), slice_of(s5)) == []  # S5's suggestion is proven
    s5["plan"]["suggestion"]["proven"] = False
    assert any("verified" in p for p in reporter.check(template(s5), slice_of(s5)))


def test_a_proven_suggestion_must_be_called_verified(s5):
    slice_ = slice_of(s5)
    plain = re.sub(r"verified", "checked", template(s5), flags=re.IGNORECASE)
    problems = reporter.check(plain, slice_)
    assert len(problems) == 1 and "proven is true" in problems[0] and "verified" in problems[0]
    assert reporter.check(template(s5), slice_) == []
    s5["plan"]["suggestion"]["proven"] = False  # not proven: no 'verified' needed (and not allowed)
    assert reporter.check(plain, slice_of(s5)) == []


def test_pronouns_for_identities_are_rejected_as_whole_words(s2):
    slice_ = slice_of(s2)
    good = template(s2)
    problems = reporter.check(good + "\nShe can reach the data. Her access was removed; THEY and the shell are fine.\n", slice_)
    assert len(problems) == 1 and "she, her" in problems[0]
    assert reporter.check(good + "\nHis, him and he are rejected too.\n", slice_)[0].count(",") == 2
    assert reporter.check(good + "\nThe cache, a shelf and other things are fine.\n", slice_) == []  # not whole words


def test_a_generated_in_mode_line_is_only_allowed_as_the_exact_degraded_line_when_degraded(s2):
    normal = "\n*Generated in normal mode.*\n"
    good = template(s2)
    assert any("Generated in normal mode" in p for p in reporter.check(good + normal, slice_of(s2)))
    assert any("only when degraded" in p for p in reporter.check(good + "\n" + reporter.DEGRADED_LINE + "\n", slice_of(s2)))  # not degraded
    s2["degraded"] = True
    slice_ = slice_of(s2)
    assert reporter.check(good + "\n" + reporter.DEGRADED_LINE + "\n", slice_) == []
    assert reporter.check(good + "\n*" + reporter.DEGRADED_LINE + "*\n", slice_) == []  # emphasis is fine
    assert reporter.check(good + "\nGenerated in degraded mode.\n", slice_) != []  # not the exact line
    assert reporter.check(good + normal, slice_) != []


def test_a_verified_answer_is_retried(s2, monkeypatch):
    good = template(s2)
    gateway = Gateway(monkeypatch, say(good + "\nIt was verified.\n"), say(good))
    assert reporter.write(s2) == (good, False) and "verified" in gateway.calls[1]["messages"][-1]["content"]


def test_all_providers_unavailable_falls_back_to_the_template_and_degraded(s2, monkeypatch):
    events = []
    gateway = Gateway(monkeypatch, {"p1": [LLMUnavailable("p1 down")], "p2": [LLMUnavailable("p2 down")]})
    markdown, degraded = reporter.write(s2, emit=lambda *a, **kw: events.append(a))
    assert degraded is True and [c["provider"] for c in gateway.calls] == ["p1", "p2"]
    assert markdown == fast_brains.report({**s2, "degraded": True})["markdown"]
    assert [a[0] for a in events] == ["step", "step", "report"] and events[-1][2]["source"] == "template"


def test_no_provider_configured_falls_back_without_a_call(s2, monkeypatch):
    gateway = Gateway(monkeypatch, {})
    assert reporter.write(s2)[1] is True and gateway.calls == []


def test_an_unavailable_provider_is_skipped_for_the_next_one(s2, monkeypatch):
    good = template(s2)
    gateway = Gateway(monkeypatch, {"p1": [LLMUnavailable("down")], "p2": [say(good)]})
    assert reporter.write(s2) == (good, False) and [c["provider"] for c in gateway.calls] == ["p1", "p2"]


def test_a_failing_retry_goes_to_the_next_provider(s2, monkeypatch):
    good = template(s2)
    gateway = Gateway(monkeypatch, {"p1": [say(good.replace("e:s2:05", "e:s2:99")), LLMUnavailable("down")], "p2": [say(good)]})
    assert reporter.write(s2) == (good, False) and [c["provider"] for c in gateway.calls] == ["p1", "p1", "p2"]


def test_the_degraded_line_is_added_when_the_incident_is_already_degraded(s2, monkeypatch):
    s2["degraded"] = True
    Gateway(monkeypatch, say(template({**s2, "degraded": False})))
    markdown, degraded = reporter.write(s2)
    assert degraded is False and markdown.count(reporter.DEGRADED_LINE) == 1 and markdown.endswith(reporter.DEGRADED_LINE + "\n")


def test_a_fenced_answer_is_unwrapped(s2, monkeypatch):
    good = template(s2)
    Gateway(monkeypatch, say("```markdown\n" + good + "```"))
    assert reporter.write(s2) == (good, False)


def test_unicode_hyphens_are_normalised_before_the_guard_and_the_returned_text_is_the_normalised_one(s2, monkeypatch):
    good = template(s2)
    odd = good.replace("secret:s2-billing-pw", "secret:s2-billing\u2011pw").replace("e:s2:09", "e:s2:09\u00a0").replace("2 attack path", "2\u202fattack path")
    assert "\u2011" in odd
    assert any("secret:s2-billing is not in the incident JSON" in p for p in reporter.check(odd, slice_of(s2)))  # what the guard saw before the fix
    gateway = Gateway(monkeypatch, say(odd))
    markdown, degraded = reporter.write(s2)
    assert degraded is False and len(gateway.calls) == 1 and "secret:s2-billing-pw" in markdown and "\u2011" not in markdown
    assert reporter._normalise("business\u2011critical \u2010\u2012\u2013\u2014\u2015\u2212") == "business-critical ------"


# --- the fact guard ---

def test_the_s2_and_s5_templates_pass_the_guard(states):
    for sid in ("s2", "s5"):
        assert reporter.check(template(states[sid]), slice_of(states[sid])) == [], sid
    assert "not assessed" in template(states["s5"]).lower().split("## business impact")[1]  # no collateral analysis for S5: the template says so


def test_the_no_risk_template_passes_the_guard_too(s2):
    s2.update(status="DONE_NO_RISK", findings={"reachable_jewels": [], "paths": [], "truncated": False}, plan=None, action=None, verdict=None)
    assert reporter.check(template(s2), slice_of(s2)) == []


def test_without_collateral_data_business_impact_must_say_not_assessed(s2, s5):
    slice_ = slice_of(s5)
    assert not slice_["plan"]["simulation"] and "verdict" not in slice_  # S5: no collateral analysis at all
    base = template(s5).split("## Business impact")[0]
    tail = "\n## Next steps\n- Verified suggestion. no safe fix\n"
    for claim in ("Collateral: nobody", "No one lost access", "Collateral loss: None", "not assessed, but nobody is affected", "not applicable"):
        problems = reporter.check(base + "## Business impact\n" + claim + "\n" + tail, slice_)
        assert any("no collateral data" in p for p in problems), claim
    assert reporter.check(base + "## Business impact\nCollateral: not assessed\n" + tail, slice_) == []
    # with collateral data (S2) "nobody" is a fact and allowed
    assert reporter.check(template(s2), slice_of(s2)) == [] and "nobody" in template(s2).lower()


def test_headings_with_markdown_decoration_are_accepted_and_normalised(s2, monkeypatch):
    good = template(s2)
    decorated = good.replace("## Summary", "**## Summary**").replace("## Fix", "## **Fix**").replace("## Proof", "__## Proof__").replace("## Next steps", "  ## `Next steps` ")
    assert reporter.check(decorated, slice_of(s2)) == []
    Gateway(monkeypatch, say(decorated))
    assert reporter.write(s2) == (good, False)  # the kept text has plain "## Heading" lines


def with_impact(text, body):
    """The report with the Business impact body replaced."""
    head, rest = text.split("## Business impact")
    return head + "## Business impact\n" + body + "\n\n## Next steps" + rest.split("## Next steps")[1]


def test_a_collateral_claim_without_data_is_repaired_in_code_after_the_retry(s5, monkeypatch):
    good = template(s5)
    claim = with_impact(good, "- Collateral loss: nobody")
    events = []
    gateway = Gateway(monkeypatch, say(claim), say(claim))
    markdown, degraded = reporter.write(s5, emit=lambda *a, **kw: events.append(a))
    assert degraded is False and len(gateway.calls) == 2 and "nobody" not in markdown
    assert reporter.NOT_ASSESSED in markdown.split("## Business impact")[1].split("## Next steps")[0]
    assert markdown.split("## Business impact")[0] == good.split("## Business impact")[0] and "## Next steps" in markdown  # the rest is the LLM's
    assert reporter.check(markdown, slice_of(s5)) == [] and events[-1][2]["source"] == "llm+repaired"


def test_a_collateral_claim_plus_another_problem_is_not_repaired(s5, monkeypatch):
    bad = with_impact(template(s5), "- Collateral loss: nobody") + "\nSee e:s5:99.\n"
    Gateway(monkeypatch, say(bad), say(bad))
    markdown, degraded = reporter.write(s5)
    assert degraded is True and markdown == fast_brains.report({**s5, "degraded": True})["markdown"]


def test_a_collateral_claim_fixed_by_the_retry_is_not_repaired(s5, monkeypatch):
    good = template(s5)
    events = []
    Gateway(monkeypatch, say(with_impact(good, "- nobody")), say(good))
    assert reporter.write(s5, emit=lambda *a, **kw: events.append(a)) == (good, False) and events[-1][2]["source"] == "llm"


def test_node_ids_are_checked_and_need_ids_are_not_mistaken_for_nodes(s2):
    slice_ = slice_of(s2)
    good = template(s2)
    assert reporter.check(good + "\nSee need:usage:user:ravi:db:08 and `user:ravi`.\n", slice_) == []
    problems = reporter.check(good + "\nAlso server:ghost and group:s2-ci-deployers and secret:made-up.\n", slice_)
    assert [p.split(" is not")[0] for p in problems] == ["node id server:ghost", "node id secret:made-up"]  # the real group is in the paths


def test_headings_must_be_present_once_and_in_order(s2):
    slice_ = slice_of(s2)
    good = template(s2)
    swapped = good.replace("## Proof", "## TMP").replace("## Fix", "## Proof").replace("## TMP", "## Fix")
    assert any("headings" in p for p in reporter.check(swapped, slice_))
    assert any("wrong or missing: Next steps" in p for p in reporter.check(good.replace("## Next steps", "## Next"), slice_))
    assert any("headings" in p for p in reporter.check(good + "\n## Summary\nagain\n", slice_))
    assert reporter.check(good.replace("## Summary", "## Summary – one paragraph"), slice_) == []  # the prompt's own layout


def test_needs_human_must_say_there_is_no_safe_automatic_fix(s5):
    slice_ = slice_of(s5)
    assert slice_["status"] == "NEEDS_HUMAN"
    weak = re.sub(r"no safe (automatic )?fix", "nothing could be done", template(s5), flags=re.IGNORECASE)  # the failure detail says "no safe fix" too
    assert any("no safe automatic fix" in p for p in reporter.check(weak, slice_))
    assert reporter.check(template(s5), slice_) == []
    for phrase in ("no safe automatic fix", "No safe fix", "no automatic safe cut"):  # natural word orders are accepted
        assert reporter.check(weak + f"\n{phrase} was found.\n", slice_) == []


# --- the slice ---

def test_the_slice_is_small_has_no_raw_tool_results_and_adds_the_remediation_verbs(s2):
    slice_ = slice_of(s2)
    text = reporter._dump(slice_)
    assert len(text) < 6000, len(text)
    assert set(slice_) == {"id", "tenant", "src", "degraded", "attempt", "status", "findings", "plan", "action", "verdict"}
    assert slice_["status"] == "DONE" and slice_["src"] == "user:ravi" and slice_["attempt"] == 1
    for raw in ("top_edges", "business", "protected_eids", "history", "tried_changes", "path_count_by_target", "e:bg:", "sbx"):
        assert raw not in text
    assert slice_["findings"]["path_count"] == 2 and len(slice_["findings"]["paths"]) == 2
    assert set(slice_["findings"]["paths"][0]) == {"target", "node_ids", "eids"}
    assert slice_["plan"]["cut_actions"] == [
        {"eid": "e:s2:05", "action": "Detach the policy", "host_action": False},
        {"eid": "e:s2:10", "action": "Delete the file and rotate the secret", "host_action": True}]
    assert slice_["plan"]["simulation"] == {"collateral": {"count": 0, "weighted": 0, "pairs": []}, "size": 2, "min_size": 2}
    assert slice_["action"] == {"applied_eids": ["e:s2:05", "e:s2:10"], "protected_count": len(s2["action"]["protected_eids"])}
    verdict = slice_["verdict"]
    assert (verdict["pass"], verdict["attack_max_flow"], verdict["bfs_reached_jewels"]) == (True, 0, [])
    assert (verdict["needs_checked"], verdict["needs_ok"]) == (len(s2["verdict"]["business"]), len(s2["verdict"]["business"]))


def test_the_slice_of_s2_keeps_the_max_flow_measured_before_the_fix(s2):
    plan = slice_of(s2)["plan"]
    assert plan["attack_max_flow_before_fix"] == s2["plan"]["max_flow"] == 2 and "max_flow" not in plan


def test_the_slice_of_s5_has_the_suggestion_and_no_verdict(s5):
    slice_ = slice_of(s5)
    assert slice_["status"] == "NEEDS_HUMAN" and "verdict" not in slice_ and len(reporter._dump(slice_)) < 6000
    assert slice_["plan"]["suggestion"]["proven"] is True and slice_["plan"]["cut_actions"] == []
    assert not any("max_flow" in k for k in slice_["plan"])  # not safe: max_flow is the protect cap, not a meaningful flow
    proof = slice_["plan"]["suggestion"]["proof"]  # the split's own measurements
    assert (proof["max_flow_after_split"], proof["bfs_reached_jewels"], proof["needs_ok"]) == (0, [], True)
    assert slice_["action"]["applied_eids"] == []


def test_the_slice_keeps_the_5_shortest_paths_and_caps_the_pairs(s2):
    paths = [{"target": "db:s2-customers", "node_ids": ["user:ravi"] * (n + 1), "eids": [f"e:s2:{i:02d}" for i in range(n)], "hops": n} for n in (7, 3, 6, 2, 5, 4, 8)]
    s2["findings"]["paths"] = paths
    s2["verdict"]["collateral"] = {"count": 30, "weighted": 30, "pairs": [[f"user:u{i}", "db:x"] for i in range(30)]}
    slice_ = slice_of(s2)
    assert slice_["findings"]["path_count"] == 7 and [len(p["eids"]) for p in slice_["findings"]["paths"]] == [2, 3, 4, 5, 6]
    assert slice_["verdict"]["collateral"]["count"] == 30 and len(slice_["verdict"]["collateral"]["pairs"]) == 10


def test_the_status_reporting_is_shown_as_done_and_the_incident_id_is_not_sent_to_the_model(s2):
    s2["status"] = "REPORTING"  # the pipeline writes the report before it advances to DONE
    slice_ = slice_of(s2)
    assert slice_["status"] == "DONE" and slice_["id"] == s2["id"]
    assert s2["id"] not in reporter._user_message(slice_)  # same messages for every incident of a scenario: the LLM cache hits


# --- live: the real LLMs (Groq first, then OpenRouter) ---

MODELS = {"GROQ": "openai/gpt-oss-120b", "OPENROUTER": "nvidia/nemotron-3-super-120b-a12b:free"}  # VERIFY_RESULTS P1.4


def live(monkeypatch, state):
    if not (os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY")):
        pytest.skip("neither GROQ_API_KEY nor OPENROUTER_API_KEY is set")
    for name, model in MODELS.items():
        if not os.getenv(f"MODEL_REPORTER_{name}"):
            monkeypatch.setenv(f"MODEL_REPORTER_{name}", model)
    events = []
    markdown, degraded = reporter.write(state, emit=lambda t, m, d, **kw: events.append((t, m, d, kw)))
    final = next(d for t, m, d, kw in events if t == "report")
    print(f"\n{state['status']} {state['src']}: provider={final['provider']} model={final['model']} llm_calls={final['llm_calls']} "
          f"degraded={degraded} retry_problems={final['retry_problems']}")
    for t, m, d, kw in events:
        print("  EVENT:", t, m, f"tokens={kw.get('tokens_in')}/{kw.get('tokens_out')}", "cached" if (d or {}).get("cached") else "")
    print("-" * 70 + "\n" + markdown + "-" * 70)
    return markdown, degraded, final


@pytest.mark.live
def test_live_s2_report(states, monkeypatch):
    state = copy.deepcopy(states["s2"])
    markdown, degraded, final = live(monkeypatch, state)
    assert not degraded and final["source"].startswith("llm"), f"the template was used: {final}"  # a fallback must FAIL, not pass silently
    assert reporter.check(markdown, slice_of(state)) == []
    assert "e:s2:05" in markdown and "e:s2:10" in markdown and "host action" in markdown.lower()


@pytest.mark.live
def test_live_s5_report(states, monkeypatch):
    state = copy.deepcopy(states["s5"])
    markdown, degraded, final = live(monkeypatch, state)
    assert not degraded and final["source"].startswith("llm"), f"the template was used: {final}"
    assert reporter.check(markdown, slice_of(state)) == []
    assert "policy:s5-shared-ops" in markdown

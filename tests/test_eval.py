"""The evaluation (eval/): metrics on a toy graph, the brute force vs the min-cut on the real scenarios, the baselines, the JSON baseline with a
fake gateway, the table and paragraph, and the /eval/latest API. No LLM and (except the API test) no database."""
import json
from pathlib import Path

import pytest

from agents.llm_gateway import LLMUnavailable
from data.scenarios import build
from eval import baseline_json_llm, baselines, bruteforce, metrics, run_eval
from tests.agent_fakes import Gateway, message

ROOT = Path(__file__).resolve().parent.parent


def node(i, label, jewel=False):
    return {"id": i, "label": label, "props": {"crown_jewel": jewel}}


def edge(eid, src, dst, stale=5):
    return {"eid": eid, "src": src, "dst": dst, "type": "X", "last_used_days": stale}


# a (the attacker) -> g -> r -> p -> j (crown jewel); p -> s (a server a needs); b -> g (stale), c -> r (recent): both reach j through p
TOY = {"scenario": "toy", "src": "user:a",
       "nodes": [node("user:a", "User"), node("user:b", "User"), node("user:c", "User"), node("group:g", "Group"), node("role:r", "Role"),
                 node("policy:p", "Policy"), node("db:j", "Database", True), node("server:s", "Server")],
       "edges": [edge("e1", "user:a", "group:g"), edge("e2", "group:g", "role:r"), edge("e3", "role:r", "policy:p"), edge("e4", "policy:p", "db:j"),
                 edge("e5", "policy:p", "server:s"), edge("e6", "user:b", "group:g", 200), edge("e7", "user:c", "role:r", 10)],
       "needs": [{"id": "need:a-s", "principal_id": "user:a", "resource_id": "server:s", "valid": True},
                 {"id": "need:bad", "principal_id": "user:a", "resource_id": "db:j", "valid": False}]}
TOY_KEY = {"true_paths": [["e1", "e2", "e3", "e4"]], "reachable_jewels": ["db:j"], "safe_fix_exists": True, "protected_eids": ["e1", "e2", "e3", "e5"]}


def result(cut, jewels=None, paths=None, safe=None):
    return {"found_jewels": jewels, "found_paths": paths, "cut_eids": cut, "verdict_safe": safe}


# --- metrics ---

def test_a_perfect_answer_on_the_toy_graph():
    s = metrics.score(TOY, TOY_KEY, result(["e4"], ["db:j"], [["e1", "e2", "e3", "e4"]], True), optimum=1)
    assert (s["blast_recall"], s["path_recall"], s["path_precision"], s["fix_gap"]) == (1.0, 1.0, 1.0, 0)
    assert (s["residual_flow"], s["residual_jewels"], s["broken_needs"], s["verdict_correct"], s["safe_fix"]) == (0, [], [], True, True)
    # b loses j through g -> r -> p -> j, which includes the stale e6 (weight 1); c loses it through the recent e7, e3, e4 (weight 3)
    assert s["collateral"] == {"count": 2, "weighted": 4}


def test_a_cut_that_breaks_a_need_and_one_that_does_not_block():
    disable = metrics.score(TOY, TOY_KEY, result(["e1"]), optimum=1)
    assert disable["residual_flow"] == 0 and disable["broken_needs"] == ["need:a-s"] and not disable["safe_fix"]  # the invalid need is not counted
    assert disable["collateral"] == {"count": 0, "weighted": 0}  # b and c still reach j and s
    nothing = metrics.score(TOY, TOY_KEY, result([]), optimum=1)
    assert nothing["residual_flow"] == 1 and nothing["residual_jewels"] == ["db:j"] and not nothing["fixes_attack"]
    assert metrics.score(TOY, TOY_KEY, result(["e1", "e4"]), optimum=1)["fix_gap"] == 1


def test_recall_precision_and_unknown_eids():
    s = metrics.score(TOY, TOY_KEY, result(["e99", "e4"], [], [["e1", "e2", "e3", "e4"], ["e1", "e9"]]), optimum=1)
    assert s["blast_recall"] == 0.0 and s["path_recall"] == 1.0 and s["path_precision"] == 0.5
    assert s["unknown_eids"] == ["e99"] and s["residual_flow"] == 0  # an invented eid removes nothing, the real one still blocks


def test_a_method_that_does_not_report_paths_has_no_path_metrics():
    s = metrics.score(TOY, TOY_KEY, result(["e4"]), optimum=None)
    assert (s["blast_recall"], s["path_recall"], s["path_precision"], s["fix_gap"], s["verdict_correct"]) == (None, None, None, None, None)


def test_nothing_to_measure_is_none_not_nan():
    key = {**TOY_KEY, "true_paths": [], "reachable_jewels": []}  # like S7: no path within the hop bound
    s = metrics.score(TOY, key, result([], [], []), optimum=1)
    assert s["blast_recall"] is None and s["path_recall"] is None and s["path_precision"] is None
    assert metrics.score(TOY, key, result([], [], [["e1", "e2"]]), optimum=1)["path_precision"] == 0.0  # a fake path is precision 0
    json.dumps(s)  # plain JSON: no NaN


def test_the_verdict_is_compared_with_the_key():
    key = {**TOY_KEY, "safe_fix_exists": False}
    assert metrics.score(TOY, key, result([], safe=False), 1)["verdict_correct"] is True
    assert metrics.score(TOY, key, result(["e4"], safe=True), 1)["verdict_correct"] is False


def test_only_the_first_20_found_paths_count_for_recall():
    many = [["x%d" % i] for i in range(25)] + [["e1", "e2", "e3", "e4"]]  # the true path is the 26th: beyond K
    s = metrics.score(TOY, TOY_KEY, result([], [], many), optimum=1)
    assert s["path_recall"] == 0.0 and s["path_precision"] == 0.0


# --- the brute force ---

def test_bruteforce_on_the_toy_graph():
    got = bruteforce.optimum(TOY, TOY_KEY)
    assert got == {"optimum": 1, "cut": ["e4"], "candidates": 1, "note": ""}
    assert bruteforce.optimum(TOY, TOY_KEY, max_candidates=0)["note"].startswith("not computed: 1 candidates")
    assert bruteforce.optimum(TOY, {**TOY_KEY, "protected_eids": TOY_KEY["protected_eids"] + ["e4"]})["note"] == "no safe fix"


def test_bruteforce_keeps_every_valid_need():
    # the only edge that blocks the attack is e1; need a -> s needs e1 too, so there is no safe subset
    graph = {**TOY, "edges": [e for e in TOY["edges"] if e["eid"] in ("e1", "e2", "e3", "e4", "e5")]}
    assert bruteforce.optimum(graph, {**TOY_KEY, "protected_eids": []})["optimum"] == 1  # e4 blocks and keeps the need
    graph["needs"] = TOY["needs"] + [{"id": "need:a-j", "principal_id": "user:a", "resource_id": "db:j", "valid": True}]
    assert bruteforce.optimum(graph, {**TOY_KEY, "protected_eids": []})["note"] == "no safe fix"


SAFE = ["s1", "s2", "s3", "s4", "s6", "s7", "s8"]


@pytest.fixture(scope="module")
def scenarios():
    out = {}
    for sid in ["s%d" % i for i in range(1, 9)]:
        out[sid] = (build(sid), json.loads((ROOT / "data" / "answer_keys" / f"{sid}.json").read_text(encoding="utf-8")))
    return out


@pytest.mark.parametrize("sid", SAFE)
def test_the_brute_force_optimum_equals_the_min_cut_of_the_answer_key(scenarios, sid):
    graph, key = scenarios[sid]
    got = bruteforce.optimum(graph, key)
    assert got["optimum"] == key["min_cut_size"], got
    scored = metrics.score(graph, key, result(got["cut"]), got["optimum"])  # and the optimum itself is a safe fix with no gap
    assert scored["safe_fix"] and scored["fix_gap"] == 0


def test_s5_has_no_safe_fix_and_s7_counts_its_long_path(scenarios):
    graph, key = scenarios["s5"]
    assert bruteforce.optimum(graph, key) == {"optimum": None, "cut": None, "candidates": 0, "note": "no safe fix"}
    graph, key = scenarios["s7"]
    assert key["true_paths"] == [] and key["long_path_jewels"]  # nothing within 6 hops...
    assert bruteforce.optimum(graph, key)["optimum"] == 1  # ...but the unbounded brute force still finds the cut
    assert metrics.score(graph, key, result(bruteforce.optimum(graph, key)["cut"], [], []), 1)["residual_flow"] == 0


def test_the_products_s2_fix_scores_perfectly_in_networkx(scenarios):
    graph, key = scenarios["s2"]
    s = metrics.score(graph, key, result(["e:s2:05", "e:s2:10"], key["reachable_jewels"], key["true_paths"], True), 2)
    assert (s["fix_gap"], s["residual_flow"], s["broken_needs"], s["collateral"]["count"], s["blast_recall"], s["path_recall"], s["path_precision"]) == (
        0, 0, [], 0, 1.0, 1.0, 1.0)


# --- the baselines ---

def test_the_baselines_on_the_toy_graph_are_deterministic():
    G = metrics.nx_graph(TOY)
    assert baselines.b_disable(G, "user:a") == ["e1"]
    assert baselines.b_frequency(G, "user:a") == ["e1"]  # all edges of the path are used once: the lowest eid wins
    assert baselines.b_betweenness(G, "user:a") == baselines.b_betweenness(G, "user:a")
    for name, fn in baselines.BASELINES.items():
        assert metrics.reached_jewels(metrics.without(G, fn(G, "user:a")), "user:a") == [], name  # every baseline blocks the attack


def test_the_baselines_ignore_business_needs_on_purpose(scenarios):
    graph, key = scenarios["s2"]
    G = metrics.nx_graph(graph)
    disable = baselines.b_disable(G, graph["src"])
    s = metrics.score(graph, key, result(disable), 2)
    assert s["residual_flow"] == 0 and s["broken_needs"] and s["fix_gap"] == len(disable) - 2  # blocks everything, breaks Ravi's needs


# --- the JSON baseline ---

def test_the_json_baseline_prompt_has_the_graph_the_valid_needs_and_the_compromised_identity():
    system, user = baseline_json_llm.messages_for(TOY)
    assert system["role"] == "system" and "security analyst" in system["content"]
    text = user["content"]
    assert '["e1","X","user:a","group:g"]' in text and '"crown_jewels":["db:j"]' in text and "need:bad" not in text and '["user:a","server:s"]' in text
    assert '["user:a","db:j"]' not in text  # the invalid need is not sent
    assert 'COMPROMISED: "user:a"' in text and "SMALLEST set of eids" in text and "props" not in text  # compact rows, no node properties


def test_a_valid_json_answer_is_parsed_and_scored(monkeypatch):
    answer = {"reachable_jewels": ["db:j"], "paths": [["e1", "e2", "e3", "e4"]], "cut_eids": ["e4"]}
    gateway = Gateway(monkeypatch, message(content="```json\n" + json.dumps(answer) + "\n```"))
    got = baseline_json_llm.ask(TOY)
    assert got["answer"].cut_eids == ["e4"] and got["failed"] == "" and got["not_run"] == "" and got["provider"] == "fake"
    assert gateway.calls[0]["agent"] == "baseline"


@pytest.mark.parametrize("text,why", [("not json at all", "invalid JSON"), ('{"reachable_jewels": "db:j"}', "wrong schema"), ("", "invalid JSON")])
def test_an_invalid_answer_is_a_failed_answer_and_is_not_retried(monkeypatch, text, why):
    gateway = Gateway(monkeypatch, message(content=text))
    got = baseline_json_llm.ask(TOY)
    assert got["answer"] is None and got["failed"].startswith(why) and len(gateway.calls) == 1


def test_a_provider_that_cannot_answer_is_skipped_and_when_none_can_the_row_is_not_run(monkeypatch):
    answer = {"reachable_jewels": [], "paths": [], "cut_eids": ["e4"]}
    Gateway(monkeypatch, {"p1": [LLMUnavailable("p1 429")], "p2": [RuntimeError("413 request too large")]})
    got = baseline_json_llm.ask(TOY)
    assert got["answer"] is None and "p1: p1 429" in got["not_run"] and "p2: RuntimeError" in got["not_run"]
    Gateway(monkeypatch, {"p1": [LLMUnavailable("down")], "p2": [message(content=json.dumps(answer))]})
    assert baseline_json_llm.ask(TOY)["provider"] == "p2"


def test_the_llm_json_row_scores_a_failed_answer_as_an_empty_cut(monkeypatch, scenarios):
    Gateway(monkeypatch, message(content="sorry"))
    monkeypatch.setattr(run_eval.time, "sleep", lambda s: None)
    graph, key = scenarios["s2"]
    row = run_eval.row_llm_json("s2", graph, key, 2)
    assert row["status"] == "failed answer" and row["ran"] and row["metrics"]["cut_size"] == 0 and row["metrics"]["residual_flow"] > 0
    Gateway(monkeypatch, {"p": [LLMUnavailable("RateLimitError")] * 3})
    row = run_eval.row_llm_json("s2", graph, key, 2)
    assert row["status"] == "not run" and not row["ran"] and row["note"].startswith("not run (quota)")


# --- the table and the paragraph ---

def fake_row(sid, method, cut=1, gap=0, residual=0, broken=(), weighted=0, verdict=None, degraded=False, status="ok", recall=None):
    m = {"cut_size": cut, "fix_gap": gap, "residual_flow": residual, "broken_needs": list(broken), "collateral": {"count": weighted, "weighted": weighted},
         "blast_recall": recall, "path_recall": recall, "path_precision": recall, "verdict_correct": verdict,
         "fixes_attack": residual == 0, "safe_fix": residual == 0 and not broken}
    return {"scenario": sid, "method": method, "ran": True, "status": status, "note": "", "degraded": degraded, "metrics": m}


def test_a_cell_says_what_happened():
    assert run_eval.cell(None) == "not run"
    text = run_eval.cell(fake_row("s2", "fast", cut=2, weighted=3, recall=1.0, verdict=True, degraded=True))
    assert text == "cut 2 (gap 0) · residual 0 · broken 0 · collateral 3 (w 3) · jewels 100%, paths R 100% / P 100% · verdict ✔ · †degraded"
    assert "gap +1" in run_eval.cell(fake_row("s2", "disable", gap=1)) and "gap –" in run_eval.cell(fake_row("s5", "disable", gap=None))
    quota = {"scenario": "s2", "method": "llm_json", "ran": False, "status": "not run", "note": "not run (quota): groq: 429"}
    assert run_eval.cell(quota) == "not run (quota)"


def test_the_paragraph_is_written_from_the_numbers_and_stays_honest(scenarios):
    keys = {sid: key for sid, (_, key) in scenarios.items()}
    sids = ["s2", "s5", "s8"]
    rows = [fake_row("s2", "fast", cut=2, verdict=True), fake_row("s5", "fast", cut=0, gap=None, residual=3, verdict=True), fake_row("s8", "fast", verdict=True),
            fake_row("s2", "disable", cut=3, gap=1, broken=["n1", "n2"]), fake_row("s5", "disable", cut=2, gap=None, broken=["n3"]),
            fake_row("s8", "disable", cut=2, gap=1, weighted=12), fake_row("s2", "frequency", cut=2), fake_row("s8", "frequency")]
    text = run_eval.summarize(rows, sids, ["fast", "disable", "frequency"], keys)
    assert "3 scenarios, 2 of which have a safe fix (s5 has none)" in text
    assert "BreachPath (fast): a safe fix in 2 of 2 scenarios, 0 business need(s) broken in total, weighted collateral 0" in text
    assert "B-disable: a safe fix in 1 of 2 scenarios, 2 business need(s) broken in total, weighted collateral 12, mean fix gap 1.00" in text
    assert "B-frequency matched it" in text and "B-disable did worse" in text  # a tie is reported as a tie
    assert "BreachPath (fast) gave the right safe / NEEDS_HUMAN verdict in 3 of 3 scenarios" in text
    assert "Not run: s5/B-frequency." in text


def test_the_table_has_a_row_per_scenario_and_a_column_per_method():
    rows = [fake_row("s2", "fast")]
    table = run_eval.build_table(rows, ["s1", "s2"], ["fast", "disable"])
    assert [r["scenario"] for r in table] == ["s1", "s2"] and set(table[0]) == {"scenario", "BreachPath (fast)", "B-disable"}
    assert table[0]["BreachPath (fast)"] == "not run" and table[1]["BreachPath (fast)"].startswith("cut 1")


def test_the_markdown_has_the_table_the_legend_and_the_paragraph(scenarios):
    keys = {sid: key for sid, (_, key) in scenarios.items()}
    doc = run_eval.document([fake_row("s2", "fast")], ["s2"], ["fast"], keys, 8)
    md = doc["markdown"]
    assert md.startswith("# BreachPath evaluation") and "| scenario | BreachPath (fast) |" in md and "## What the table shows" in md and "networkx" in md
    assert doc["meta"]["methods"] == ["fast"] and doc["meta"]["pause_s"] == 8


# --- /v1/eval/latest ---

@pytest.fixture
def api(falkordb):
    from fastapi.testclient import TestClient
    from api.main import app
    from core.graph.client import get_db
    redis = get_db().connection
    saved = redis.get("eval:latest")
    redis.delete("eval:latest")
    yield TestClient(app), redis
    redis.delete("eval:latest")
    if saved:
        redis.set("eval:latest", saved)


def test_eval_latest_reads_redis_then_the_file_then_404(api, monkeypatch, tmp_path):
    client, redis = api
    from api.routes import evaluation
    monkeypatch.setattr(evaluation, "RESULTS_FILE", tmp_path / "latest.json")
    r = client.get("/v1/eval/latest")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    (tmp_path / "latest.json").write_text(json.dumps({"meta": {"from": "file"}}), encoding="utf-8")
    assert client.get("/v1/eval/latest").json() == {"meta": {"from": "file"}}
    redis.set("eval:latest", json.dumps({"meta": {"from": "redis"}}))
    assert client.get("/v1/eval/latest").json() == {"meta": {"from": "redis"}}

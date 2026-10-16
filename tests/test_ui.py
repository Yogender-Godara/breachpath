"""The UI: pure helpers (ui/view.py, ui/run.py) and the Streamlit app run headless with AppTest and a fake API (no network, no database)."""
import copy

import pytest
from streamlit.testing.v1 import AppTest

from orchestrator.state_machine import STATUSES, TERMINAL
from ui import api_client, run as run_mod, view

TS = "2026-10-16T10:00:03+00:00"
NODES = [{"id": "user:ravi", "label": "User", "name": "ravi", "crown_jewel": False, "is_src": True},
         {"id": "role:r", "label": "Role", "name": 'the "role"', "crown_jewel": False, "is_src": False},
         {"id": "db:x", "label": "Database", "name": "x", "crown_jewel": True, "is_src": False}]
EDGES = [{"eid": "e:1", "src": "user:ravi", "dst": "role:r", "kind": "CAN_ASSUME", "on_path": True, "cut": False, "protected": True},
         {"eid": "e:2", "src": "role:r", "dst": "db:x", "kind": "CAN_READ", "on_path": True, "cut": True, "protected": False},
         {"eid": "e:3", "src": "user:ravi", "dst": "db:x", "kind": "CAN_READ", "on_path": False, "cut": False, "protected": False}]


# --- ui/view.py ---

def test_every_status_has_a_phase_and_a_status_line():
    assert {view.phase_of(s) for s in STATUSES} == set(view.PHASES)
    assert [view.phase_of(s) for s in ("QUEUED", "PREPARING", "EXECUTING", "REPORTING")] == ["INVESTIGATING", "PLANNING", "PLANNING", "VERIFYING"]
    assert all(view.phase_of(s) == "DONE" for s in TERMINAL)
    assert view.status_line("PLANNING") == "🟡 PLANNING (2/3)" and view.status_line("QUEUED") == "🟡 INVESTIGATING (1/3)"
    assert [view.status_line(s) for s in ("DONE", "DONE_NO_RISK", "NEEDS_HUMAN", "FAILED")] == ["🟢 DONE", "🟢 DONE_NO_RISK", "🟠 NEEDS_HUMAN", "🔴 FAILED"]


def test_the_graph_dot_follows_the_colour_rules_and_escapes_names():
    dot = view.graph_dot({"nodes": NODES, "edges": EDGES, "more": 0})
    line = lambda needle: next(l for l in dot.splitlines() if needle in l)
    assert dot.startswith("digraph G {") and dot.endswith("}")
    assert 'fillcolor="#d62728"' in line('"user:ravi" [label') and "fillcolor=\"#f2b600\"" in line('"db:x" [label')  # src red, crown jewel gold
    assert 'the \\"role\\"\\nRole' in line('"role:r" [label')  # a quote in a name is escaped, the line break is a DOT \n
    protected, cut, other = line('label="e:1"'), line('label="e:2"'), line('label="e:3"')
    assert 'color="#3fb950"' in protected and "penwidth=2.8" in protected  # protected = green, on a path = bold
    assert 'color="#ff4d4d"' in cut and "style=dashed" in cut  # cut = red dashed (beats protected)
    assert 'color="#9aa0a6"' in other and "penwidth=1.0" in other and "style=solid" in other  # other = light grey, readable on a dark theme
    assert '"user:ravi"; }' in dot and "rank=source" in dot and "rankdir=LR" in dot  # left to right, the src first


def test_an_attack_path_edge_that_is_neither_cut_nor_protected_is_amber_and_bold():
    edge = {"eid": "e:s2:06", "src": "role:r", "dst": "db:x", "kind": "HAS_POLICY", "on_path": True, "cut": False, "protected": False}
    dot = view.graph_dot({"nodes": NODES, "edges": [edge], "more": 0})
    line = next(l for l in dot.splitlines() if 'label="e:s2:06"' in l)
    assert 'color="#ffb000"' in line and "penwidth=2.8" in line and "style=solid" in line
    assert "amber bold = attack path" in view.LEGEND


def test_event_rows_and_the_tool_name():
    rows = view.event_rows([{"agent": "planner", "type": "tool_call", "msg": "simulate_cut → ok", "data": {"tool": "simulate_cut"}, "latency_ms": 42,
                             "tokens_in": None, "tokens_out": None, "ts": TS},
                            {"agent": "reporter", "type": "step", "msg": "call", "data": None, "latency_ms": None, "tokens_in": 10, "tokens_out": 5, "ts": TS}])
    assert rows[0] == {"time": "10:00:03", "agent": "planner", "step": "simulate_cut", "result": "simulate_cut → ok", "latency ms": "42", "tokens": ""}
    assert rows[1]["step"] == "step" and rows[1]["tokens"] == "10/5" and rows[1]["latency ms"] == ""  # a missing latency is empty, not "None"


def test_cut_rows_show_one_row_per_edge_with_the_action_and_a_host_tag_and_the_reasons_are_separate():
    code = "Detach the policy: role:r → policy:p; on every attack path, e.g. e:1, e:2"
    host = "Delete the file and rotate the secret: server:s → secret:k [host action]; on every attack path, e.g. e:9, e:10 Planner: the password is stale"
    plan = {"reasons": [{"eid": "e:2", "why": code}, {"eid": "e:10", "why": host}], "witness": {"e:2": ["e:1", "e:2"], "e:10": ["e:9", "e:10"]}}
    assert view.cut_rows(plan) == [
        {"edge": "e:2", "action": "Detach the policy: role:r → policy:p", "witness path": "e:1 → e:2"},
        {"edge": "e:10", "action": "Delete the file and rotate the secret: server:s → secret:k · host action", "witness path": "e:9 → e:10"}]
    assert view.split_reason(host) == ("Delete the file and rotate the secret: server:s → secret:k", True, "the password is stale")
    # the Planner's reason when it gave one, else the action; never the "on every attack path" clause
    assert view.reason_lines(plan) == [("e:2", "Detach the policy: role:r → policy:p"), ("e:10", "the password is stale")]
    assert not any("on every attack path" in r for _, r in view.reason_lines(plan))


def test_the_proof_checklist():
    good = {"attack_max_flow": 0.0, "bfs_reached_jewels": [], "business": [{"need_id": "n1", "ok": True}, {"need_id": "n2", "ok": True}],
            "collateral": {"count": 0, "weighted": 0, "pairs": []}}
    assert [icon for icon, _ in view.checks(good)] == ["✅"] * 4 and view.checks(good)[2][1] == "Business needs: 2/2 ok"
    bad = {**good, "attack_max_flow": 2.0, "bfs_reached_jewels": ["db:x"], "business": [{"need_id": "n1", "ok": False}],
           "collateral": {"count": 1, "weighted": 3, "pairs": [["user:a", "db:x"]]}}
    checks = view.checks(bad)
    assert [icon for icon, _ in checks] == ["❌", "❌", "❌", "⚠️"] and "broken: n1" in checks[2][1] and "user:a loses db:x" in checks[3][1]
    assert view.checks(None) == []


# --- ui/run.py: polling ---

def test_poll_appends_events_follows_the_status_and_reports_a_phase_change(monkeypatch):
    calls = []
    answers = iter([{"events": [{"msg": "a"}], "last_id": "1-0"}])
    monkeypatch.setattr(api_client, "events", lambda iid, after: (calls.append(("events", after)), next(answers))[1])
    monkeypatch.setattr(api_client, "incident", lambda iid: {"status": "PREPARING"})
    run = {"incident_id": "i1", "status": "QUEUED", "events": [], "last_id": "0-0", "state": None}
    assert run_mod.poll(run) is True  # INVESTIGATING -> PLANNING
    assert run["events"] == [{"msg": "a"}] and run["last_id"] == "1-0" and run["status"] == "PREPARING" and calls == [("events", "0-0")]
    answers = iter([{"events": [], "last_id": "1-0"}])
    assert run_mod.poll(run) is False and run["status"] == "PREPARING"  # same phase: no full rerun


def test_poll_reads_the_events_once_more_when_the_incident_is_over(monkeypatch):
    answers = iter([{"events": [{"msg": "a"}], "last_id": "1-0"}, {"events": [{"msg": "last"}], "last_id": "2-0"}])
    monkeypatch.setattr(api_client, "events", lambda iid, after: next(answers))
    monkeypatch.setattr(api_client, "incident", lambda iid: {"status": "DONE", "report": {"markdown": "x"}})
    run = {"incident_id": "i1", "status": "VERIFYING", "events": [], "last_id": "0-0", "state": None}
    assert run_mod.poll(run) is True
    assert [e["msg"] for e in run["events"]] == ["a", "last"] and run["last_id"] == "2-0" and run["state"]["report"]["markdown"] == "x"


# --- the app, headless ---

def fast_result(**plan_overrides):
    plan = {"cut_eids": ["e:2"], "notes": "Chose source_side.", "safe": True, "max_flow": 1, "suggestion": None,
            "reasons": [{"eid": "e:2", "why": "Detach the policy: role:r → db:x"}], "witness": {"e:2": ["e:1", "e:2"]}, **plan_overrides}
    return {"incident_id": "abc12345", "status": "DONE", "failure_detail": None, "findings": {"paths": []}, "plan": plan, "action": {},
            "verdict": {"pass": True, "attack_max_flow": 0.0, "bfs_reached_jewels": [], "business": [{"need_id": "n1", "ok": True}],
                        "collateral": {"count": 0, "weighted": 0, "pairs": []}, "already_broken": [], "failure_detail": None},
            "report": {"markdown": "## Summary\nthe canned report\n"}, "graph": {"nodes": NODES, "edges": EDGES, "more": 2, "hidden_protected": 41},
            "events": [{"agent": "investigator", "type": "finding", "msg": "1 crown jewel reachable", "data": None, "latency_ms": 5,
                        "tokens_in": None, "tokens_out": None, "ts": TS},
                       {"agent": "reporter", "type": "report", "msg": "report written", "data": {"source": "llm", "provider": "groq", "model": "m"},
                        "latency_ms": 9, "tokens_in": None, "tokens_out": None, "ts": TS}],
            "timings_ms": {}}


@pytest.fixture
def app(monkeypatch):
    """The app with a fake API. app.api_calls records what the UI asked for."""
    calls = []
    monkeypatch.setattr(api_client, "scenarios", lambda: [{"id": "s2", "title": "CI deployer", "src": "user:ravi", "description": "d"},
                                                          {"id": "s5", "title": "Shared policy", "src": "user:zoe", "description": "d"}])
    monkeypatch.setattr(api_client, "load_scenario", lambda sid: (calls.append(("load", sid)), {"graph": "g", "nodes": 128, "edges": 165})[1])
    monkeypatch.setattr(api_client, "analyze", lambda tenant, src: (calls.append(("analyze", tenant, src)), fast_result())[1])
    monkeypatch.setattr(api_client, "start_incident",
                        lambda tenant, src, token: (calls.append(("start", tenant, src, token)), {"incident_id": "inc00001", "status": "QUEUED"})[1])
    monkeypatch.setattr(api_client, "events", lambda iid, after: {"events": [], "last_id": after})
    monkeypatch.setattr(api_client, "incident", lambda iid: {"status": "QUEUED", "plan": None, "report": None})
    monkeypatch.setattr(api_client, "eval_latest", lambda: (_ for _ in ()).throw(api_client.ApiError("NOT_FOUND", "no eval")))
    from streamlit import cache_data
    cache_data.clear()
    at = AppTest.from_file("../ui/app.py", default_timeout=15)
    at.api_calls = calls
    return at.run()


def button(at, label):
    return next(b for b in at.sidebar.button if b.label == label)


def test_the_sidebar_lists_the_scenarios_and_agent_mode_needs_a_token(app):
    assert not app.exception
    assert list(app.sidebar.selectbox[0].options) == ["s2 · CI deployer", "s5 · Shared policy"]
    assert app.sidebar.radio(key="mode").disabled is True and app.sidebar.radio(key="mode").value == "Fast"  # no token: the Agent radio is disabled
    app.sidebar.text_input[0].input("tok").run()
    assert app.sidebar.radio(key="mode").disabled is False


def test_load_calls_the_api_and_says_how_many_nodes(app):
    button(app, "Load scenario").click().run()
    assert app.api_calls == [("load", "s2")] and any("128 nodes, 165 edges" in s.value for s in app.sidebar.success)


def test_a_fast_run_fills_every_tab(app):
    button(app, "Run").click().run()
    assert not app.exception and app.api_calls == [("analyze", "s2", "user:ravi")]
    assert any("DONE" in m.value for m in app.sidebar.markdown)  # the status line
    assert len(app.dataframe) >= 2  # timeline + removed edges (+ eval is empty)
    texts = " ".join(m.value for m in app.markdown)
    assert "the canned report" in texts and "Written by m (groq)" in " ".join(c.value for c in app.caption)
    assert any("Max flow after the fix = 0" in m.value for m in app.markdown) and any("✅" in m.value for m in app.markdown)
    assert any("+2 more edges not shown" in c.value for c in app.caption)
    assert any(c.value == "+41 other protected business edges (company-wide) hidden" for c in app.caption)
    assert any("No evaluation results yet" in i.value for i in app.info)


def test_an_unverified_suggestion_is_a_warning_and_a_verified_one_a_success(app, monkeypatch):
    proven = {"suggestion_type": "split_node", "proven": True, "text": "Split p into p and p-biz.", "proof": {"max_flow_after_split": 0.0}}
    monkeypatch.setattr(api_client, "analyze", lambda tenant, src: fast_result(suggestion=proven))
    button(app, "Run").click().run()
    assert any(s.value.startswith("Verified") and "Split p into p and p-biz." in s.value for s in app.success)  # the leading emoji becomes the icon
    monkeypatch.setattr(api_client, "analyze", lambda tenant, src: fast_result(suggestion={**proven, "proven": False, "suggestion_type": "manual"}))
    button(app, "Run").click().run()
    assert any("Not verified" in w.value for w in app.warning) and not app.success


def test_an_api_error_is_shown_in_the_sidebar(app, monkeypatch):
    def boom(tenant, src):
        raise api_client.ApiError("NOT_FOUND", "no baseline for that tenant")
    monkeypatch.setattr(api_client, "analyze", boom)
    button(app, "Run").click().run()
    assert any("NOT_FOUND: no baseline for that tenant" in e.value for e in app.sidebar.error)


def test_an_agent_run_starts_with_the_token_and_the_run_button_is_disabled_while_it_runs(app):
    app.sidebar.text_input[0].input("tok").run()
    app.sidebar.radio(key="mode").set_value("Agent").run()
    button(app, "Run").click().run()
    assert not app.exception and app.api_calls == [("start", "s2", "user:ravi", "tok")]
    run = app.session_state["run"]
    assert run["mode"] == "agent" and run["incident_id"] == "inc00001"
    assert button(app, "Run").disabled is True and any("INVESTIGATING (1/3)" in m.value for m in app.sidebar.markdown)
    assert any("Waiting for the worker" in i.value for i in app.info)


def test_the_graph_is_redrawn_only_when_the_phase_changes(app):
    button(app, "Run").click().run()
    drawn = app.session_state["drawn"]
    assert list(drawn) == [("abc12345", "DONE")] and drawn[("abc12345", "DONE")][1:] == (2, 41)
    first = drawn[("abc12345", "DONE")][0]
    app.run()  # another rerun in the same phase: the cached drawing is reused as it is
    assert app.session_state["drawn"][("abc12345", "DONE")][0] is first


def test_the_eval_tab_shows_the_table_the_paragraph_and_the_run_info(app, monkeypatch):
    doc = {"meta": {"date": "2026-10-16 12:00 UTC", "commit": "abc1234", "models": ["planner: groq/m"]},
           "table": [{"scenario": "s2", "BreachPath (fast)": "cut 2 (gap 0)"}], "summary": "Everything is written from the numbers."}
    monkeypatch.setattr(api_client, "eval_latest", lambda: doc)
    app.run()
    assert any("commit abc1234" in c.value and "planner: groq/m" in c.value for c in app.caption)
    assert any(m.value == "Everything is written from the numbers." for m in app.markdown)
    assert any(df.value.iloc[0]["scenario"] == "s2" for df in app.dataframe)
    assert not any("No evaluation results yet" in i.value for i in app.info)

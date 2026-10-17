"""BreachPath UI (docs/03_LLD.md section 15). Run: streamlit run ui/app.py

The UI only calls the API (ui/api_client.py): no database access. One run is kept in st.session_state["run"]:
  fast mode  -> one POST /analyze: the whole result (state parts, events, graph) is stored at once.
  agent mode -> POST /incidents, then the timeline fragment polls events + state every second. A FULL rerun happens only when the
                phase changes (INVESTIGATING -> PLANNING -> VERIFYING -> DONE): that redraws the graph, the sidebar and the other tabs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # `streamlit run ui/app.py` only puts ui/ on the path

import streamlit as st  # noqa: E402

from orchestrator.state_machine import TERMINAL  # noqa: E402
from ui import api_client as api, view  # noqa: E402
from ui.api_client import ApiError  # noqa: E402
from ui.run import poll  # noqa: E402

STATE_KEYS = ("status", "findings", "plan", "action", "verdict", "report", "failure_detail", "attempt", "degraded", "degraded_steps", "instructions")  # what /analyze returns of the state
st.set_page_config(page_title="BreachPath", layout="wide")


@st.cache_data(ttl=30, show_spinner=False)
def scenarios():
    return api.scenarios()


def running(run):
    return bool(run) and run["mode"] == "agent" and run["status"] not in TERMINAL


# --- the run: start (polling lives in ui/run.py) ---

def start(sid, src, mode, token, instructions=""):
    try:
        if mode == "Fast":
            with st.spinner("Running fast mode..."):
                res = api.analyze(sid, src)
            st.session_state.run = {"mode": "fast", "tenant": sid, "src": src, "incident_id": res["incident_id"], "status": res["status"],
                                    "events": res["events"], "last_id": "0-0", "state": {k: res.get(k) for k in STATE_KEYS}, "graph": res["graph"]}
        else:
            created = api.start_incident(sid, src, token, instructions)
            st.session_state.run = {"mode": "agent", "tenant": sid, "src": src, "incident_id": created["incident_id"], "status": created["status"],
                                    "events": [], "last_id": "0-0", "state": None, "graph": None}
    except ApiError as e:
        st.sidebar.error(view.error_text(e))
        return
    if mode == "Agent":
        st.rerun()  # the Run button was drawn before this click: redraw it disabled, so a second incident cannot be started


# --- sidebar ---

def sidebar():
    with st.sidebar:
        st.title("BreachPath")
        items = scenarios()
        titles = {s["id"]: s["title"] for s in items}
        sid = st.selectbox("Scenario", list(titles), format_func=lambda i: f"{i} · {titles[i]}")
        if st.button("Load scenario", width="stretch"):
            try:
                with st.spinner("Loading..."):
                    info = api.load_scenario(sid)
                st.success(f"Loaded {sid}: {info['nodes']} nodes, {info['edges']} edges")
            except ApiError as e:
                st.error(view.error_text(e))
        default_src = next(s["src"] for s in items if s["id"] == sid)
        try:
            users = api.users(sid)
        except ApiError:  # not loaded yet: only the scenario's own src is known
            users = []
        src = st.selectbox("Src identity", view.src_options(default_src, users), key=f"src-{sid}")
        token = st.text_input("Demo token", type="password", help="Agent mode needs the demo token (the API's DEMO_TOKEN)")
        if not token:
            st.session_state["mode"] = "Fast"
        instructions = st.text_area("Operator instructions (agent mode)", key="instructions", max_chars=500, disabled=not token, height=110,
                                    placeholder="e.g. Don't touch the build server, CI depends on it.",
                                    help="Free text. In agent mode the Planner turns it into edges the fix must avoid; the code recomputes and proves the result. Fast mode ignores it.")
        mode = st.radio("Mode", ["Fast", "Agent"], key="mode", horizontal=True, disabled=not token)
        if not token:
            st.caption("Agent mode needs a demo token.")
        run = st.session_state.get("run")
        if st.button("Run", type="primary", width="stretch", disabled=running(run)):
            start(sid, src, mode, token, instructions if mode == "Agent" else "")
            run = st.session_state.get("run")
        if run:
            st.markdown(f"**Status:** {view.status_line(run['status'], (run['state'] or {}).get('attempt'))}")
            if (run["state"] or {}).get("degraded_steps"):
                st.warning("Fell back to the code: " + "; ".join(run["state"]["degraded_steps"]))
            st.caption(f"{run['mode']} · {run['tenant']} · {run['src']} · incident {run['incident_id']}")


# --- tabs ---

def graph_tab(run):
    key = (run["incident_id"], view.phase_of(run["status"]))  # redrawn only when the phase changes, so zoom and scroll stay put
    drawn = st.session_state.setdefault("drawn", {})
    if key not in drawn:
        graph = run["graph"]
        if run["mode"] == "agent":
            try:
                graph = api.graph(run["incident_id"])
            except ApiError:  # NOT_FOUND: no findings yet
                graph = None
        if graph:
            drawn[key] = (view.graph_dot(graph), graph["more"], graph.get("hidden_protected", 0))
    if key not in drawn:
        st.info("The graph appears when the Investigator has its findings.")
        return
    dot, more, hidden = drawn[key]
    st.graphviz_chart(dot, width="stretch")
    st.caption(view.LEGEND + (f" · +{more} more edges not shown" if more else ""))
    if hidden:
        st.caption(f"+{hidden} other protected business edges (company-wide) hidden")


def show_timeline(run):
    rows = view.event_rows(run["events"])
    if not rows:
        st.info("Waiting for the worker to pick up the incident...")
        return
    st.dataframe(rows, hide_index=True, width="stretch")


def timeline_tab(run):
    if not running(run):
        show_timeline(run)
        return

    @st.fragment(run_every=1)
    def live():  # reruns alone every second: the rest of the page stays still
        current = st.session_state.run
        try:
            changed = poll(current)
        except ApiError as e:
            st.warning(view.error_text(e))
            return
        show_timeline(current)
        if changed:
            st.rerun()  # a full rerun: graph, sidebar status and the other tabs follow the new phase
    live()


def fix_tab(run):
    state = run["state"]
    plan = state and state.get("plan")
    if not plan:
        st.info("No plan yet." if state is None or state["status"] not in ("DONE_NO_RISK", "FAILED")
                else "Nothing to fix: " + (state.get("failure_detail") or "no crown jewel is reachable."))
        return
    if state.get("failure_detail"):
        st.warning(state["failure_detail"])
    instruction = view.instruction_view(state)
    if instruction:
        st.subheader("Operator instruction")
        st.info(f"“{instruction['text']}”")
        if not instruction["honoured"]:
            st.warning(f"The instruction could not be honoured: {instruction['note']}")
        else:
            for a in instruction["avoid"]:
                st.markdown(f"**Avoided {a['eid']}** — from the words: “{a['quote']}”")
            st.markdown(instruction["changed"])
    st.subheader("Removed edges")
    if plan["cut_eids"]:
        st.dataframe(view.cut_rows(plan), hide_index=True, width="stretch")
        for eid, reason in view.reason_lines(plan):  # wrapped text: a table cell would cut it
            st.markdown(f"**{eid}**: {reason}")
        if view.alternative_rows(plan):
            st.subheader("Alternatives considered")
            st.markdown(view.tradeoff(plan))
            st.dataframe(view.alternative_rows(plan), hide_index=True, width="stretch")
        st.caption(plan["notes"])
    else:
        st.write("No automatic fix was applied. " + plan["notes"])
    checks = view.checks(state.get("verdict"))
    if checks:
        st.subheader("Proof")
        for icon, text in checks:
            st.write(f"{icon} {text}")
    suggestion = plan.get("suggestion")
    if suggestion:
        st.subheader("Suggestion")
        label = "✅ Verified" if suggestion["proven"] else "⚠️ Not verified (manual review needed)"
        (st.success if suggestion["proven"] else st.warning)(f"{label} · {suggestion['suggestion_type']}\n\n{suggestion['text']}")
        if suggestion.get("proof"):
            st.caption(f"Measured in the split sandbox: {suggestion['proof']}")


def report_tab(run):
    report = run["state"] and run["state"].get("report")
    if not report:
        st.info("The report is written at the end of the run.")
        return
    made = next((e["data"] for e in reversed(run["events"]) if e["agent"] == "reporter" and e["type"] == "report" and (e["data"] or {}).get("source")), None)
    if made:
        st.caption({"llm": f"Written by {made['model']} ({made['provider']}), facts checked in code.",
                    "llm+repaired": f"Written by {made['model']} ({made['provider']}); the code added what the fact check found missing: {', '.join(made.get('repairs') or ['a section'])}.",
                    "template": "Template report (the LLM was unavailable or its answer failed the fact check)."}[made["source"]])
    st.markdown(report["markdown"])


def eval_tab():
    try:
        doc = api.eval_latest()
    except ApiError:
        st.info("No evaluation results yet.")
        return
    meta = doc["meta"]
    st.caption(f"Run {meta['date']} · commit {meta['commit']} · models: {'; '.join(meta['models']) or 'none'}")
    st.dataframe(doc["table"], hide_index=True, width="stretch")
    st.markdown("**What the table shows**")
    st.markdown(doc["summary"])


def main():
    try:
        sidebar()
    except ApiError as e:
        st.error(view.error_text(e))
        st.stop()
    run = st.session_state.get("run")
    graph, timeline, fix, report, eval_ = st.tabs(["Attack graph", "Agent timeline", "Fix & proof", "Report", "Eval"])
    with eval_:
        eval_tab()
    if not run:
        with graph:
            st.info("Pick a scenario, load it, and press Run.")
        return
    with graph:
        graph_tab(run)
    with timeline:
        timeline_tab(run)
    with fix:
        fix_tab(run)
    with report:
        report_tab(run)


main()

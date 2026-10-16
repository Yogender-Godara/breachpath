"""Polling an agent run (no Streamlit): the timeline fragment calls poll() once a second."""
from orchestrator.state_machine import TERMINAL
from ui import api_client as api, view


def poll(run):
    """One short poll of an agent run: new events, then the state. True when the phase changed (the page needs a full rerun)."""
    got = api.events(run["incident_id"], run["last_id"])
    run["events"] += got["events"]
    run["last_id"] = got["last_id"]
    state = api.incident(run["incident_id"])
    if state["status"] in TERMINAL:  # the state is saved after the last event is written: read once more so none is missed
        got = api.events(run["incident_id"], run["last_id"])
        run["events"] += got["events"]
        run["last_id"] = got["last_id"]
    before = view.phase_of(run["status"])
    run["state"], run["status"] = state, state["status"]
    return view.phase_of(state["status"]) != before

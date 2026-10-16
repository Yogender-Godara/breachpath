"""Thin HTTP client for the BreachPath API (docs/03_LLD.md section 9). The UI never touches FalkorDB or Redis: it only calls the API."""
import os

import httpx

API_URL = os.getenv("API_URL", "http://127.0.0.1:8000")
TIMEOUT_S = 60  # /analyze runs the whole fast pipeline inside the request; a scenario load takes a few seconds


class ApiError(Exception):
    """The API said no (code + message from its error body), or could not be reached (code API_UNREACHABLE)."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def _call(method, path, **kwargs):
    try:
        response = httpx.request(method, f"{API_URL}/v1{path}", timeout=TIMEOUT_S, **kwargs)
    except httpx.HTTPError as e:
        raise ApiError("API_UNREACHABLE", f"cannot reach the API at {API_URL} ({type(e).__name__})") from e
    if response.status_code >= 400:
        try:
            error = response.json()["error"]
        except (ValueError, KeyError, TypeError):  # not our error format (e.g. a route that does not exist yet)
            error = {"code": str(response.status_code), "message": response.text[:200]}
        raise ApiError(error.get("code", str(response.status_code)), error.get("message", ""))
    return response.json()


def scenarios():
    return _call("GET", "/scenarios")


def load_scenario(sid):
    return _call("POST", f"/scenarios/{sid}/load", json={"tenant": sid})


def analyze(tenant, src):
    """Fast mode: synchronous, no token."""
    return _call("POST", "/analyze", json={"tenant": tenant, "src": src})


def start_incident(tenant, src, token):
    """Agent mode: queues the job. -> {incident_id, status}"""
    return _call("POST", "/incidents", json={"tenant": tenant, "src": src, "mode": "agent"}, headers={"X-Demo-Token": token})


def incident(incident_id):
    return _call("GET", f"/incidents/{incident_id}")


def events(incident_id, after):
    """A short poll (wait=0): the UI never blocks inside Streamlit."""
    return _call("GET", f"/incidents/{incident_id}/events", params={"after": after, "wait": 0})


def graph(incident_id):
    return _call("GET", f"/incidents/{incident_id}/graph")


def eval_latest():
    return _call("GET", "/eval/latest")

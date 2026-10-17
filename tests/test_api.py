"""The API through FastAPI's TestClient, on the real FalkorDB. Incidents, keys and sandboxes made here are removed afterwards."""
import json
import subprocess
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from api import graph_view
from api.main import app
from core import config
from core.graph import client as graph_client, loader
from core.graph.schema import sandbox_name
from core.pipeline import run_incident
from core.tools import write
from data.scenarios import build
from orchestrator import events, state as state_store
from orchestrator.jobs import STREAM
from orchestrator.lock import hold_lock
from orchestrator.state import new_state
from tests.helpers import SCENARIOS

TOKEN = "p54-token"
KEY_PATTERNS = ("active:*", "ratelimit:*", "idem:p54-*")


@pytest.fixture
def api(falkordb, monkeypatch):
    """TestClient + a list for incident ids; wipes the API's Redis keys before and after, and removes the jobs entries made here."""
    monkeypatch.setattr(config, "DEMO_TOKEN", TOKEN)
    redis = graph_client.get_db().connection
    made = []
    had_jobs = redis.exists(STREAM)

    def wipe():
        for pattern in KEY_PATTERNS:
            for key in redis.scan_iter(match=pattern):
                redis.delete(key)

    wipe()
    loader.load_scenario("s2")
    loader.load_scenario("s5")
    c = TestClient(app)
    c.made = made
    yield c
    wipe()
    for incident_id in made:
        redis.delete(f"inc:{incident_id}", f"inc:{incident_id}:events", f"lock:inc:{incident_id}", f"quota:inc:{incident_id}:sims")
    for stream_id, fields in redis.xrange(STREAM):
        if (fields.get(b"incident_id") or fields.get("incident_id")) in {i.encode() for i in made} | set(made):
            redis.xdel(STREAM, stream_id)
    if not had_jobs and redis.xlen(STREAM) == 0:
        redis.delete(STREAM)  # the stream did not exist before this test
    for g in graph_client.get_db().list_graphs():
        if any(f":sbx:{i}" in g for i in made):
            graph_client.delete_graph(g)


def err(resp, status, code):
    """Every error has the same shape: {"error": {"code", "message"}}."""
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert list(body) == ["error"] and sorted(body["error"]) == ["code", "message"]
    assert body["error"]["code"] == code and body["error"]["message"]


def sandboxes_of(incident_id):
    return [g for g in graph_client.get_db().list_graphs() if f":sbx:{incident_id}" in g]


def post_incident(api, src="user:ravi", mode="agent", token=TOKEN, **headers):
    h = {"X-Demo-Token": token, **headers} if token else headers
    r = api.post("/v1/incidents", json={"tenant": "s2", "src": src, "mode": mode}, headers=h)
    if r.status_code in (200, 202):
        api.made.append(r.json()["incident_id"])
    return r


# --- product path ---

def test_scenarios_module_does_not_import_networkx():
    code = "import sys, data.scenarios, api.main; assert 'networkx' not in sys.modules"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


@pytest.mark.parametrize("sid", SCENARIOS)
def test_build_equals_the_generated_json(sid):
    path = loader.GENERATED_DIR / f"{sid}.json"
    if not path.exists():
        pytest.skip("data/generated is not in this image")
    with open(path, encoding="utf-8") as f:
        assert json.loads(json.dumps(build(sid))) == json.load(f)


# --- health ---

def test_healthz_and_readyz(api):
    assert api.get("/healthz").json() == {"ok": True}
    assert api.get("/readyz").json() == {"ok": True}


def test_readyz_is_503_when_the_db_is_down(api, monkeypatch):
    class Down:
        def ping(self):
            raise ConnectionError("no route to host")

    class FakeDb:
        connection = Down()

    monkeypatch.setattr("api.routes.health.get_db", lambda: FakeDb())
    err(api.get("/readyz"), 503, "DB_UNAVAILABLE")


def test_docs_page_exists(api):
    assert api.get("/docs").status_code == 200


# --- scenarios ---

def test_scenarios_list_has_the_eight(api):
    rows = api.get("/v1/scenarios").json()
    assert [r["id"] for r in rows] == SCENARIOS
    assert all(sorted(r) == ["description", "id", "src", "title"] for r in rows)


def test_scenario_users_lists_only_users_and_404_when_not_loaded(api):
    api.post("/v1/scenarios/s8/load")
    users = api.get("/v1/scenarios/s8/users").json()["users"]
    assert "user:max" in users and users == sorted(users) and all(u.startswith("user:") for u in users)  # no role, group or policy
    assert not any(u.startswith(("role:", "group:", "policy:")) for u in users) and len(users) > 20
    err(api.get("/v1/scenarios/s99/users"), 404, "NOT_FOUND")
    err(api.get("/v1/scenarios/..%2Fx/users"), 404, "NOT_FOUND")


def test_load_s2_returns_the_counts(api):
    assert api.post("/v1/scenarios/s2/load").json() == {"graph": "t:s2:baseline", "nodes": 142, "edges": 179, "needs": 23}


def test_load_is_409_while_an_incident_is_active_and_404_for_unknown(api):
    redis = graph_client.get_db().connection
    redis.sadd("active:s2", "someone")
    err(api.post("/v1/scenarios/s2/load"), 409, "SCENARIO_BUSY")
    redis.srem("active:s2", "someone")
    assert api.post("/v1/scenarios/s2/load").status_code == 200
    err(api.post("/v1/scenarios/s99/load"), 404, "NOT_FOUND")
    err(api.post("/v1/scenarios/..%2Fx/load"), 404, "NOT_FOUND")


# --- analyze (fast mode) ---

def test_analyze_s2_done_and_cleans_up(api):
    r = api.post("/v1/analyze", json={"tenant": "s2", "src": "user:ravi"})
    assert r.status_code == 200, r.text
    out = r.json()
    api.made.append(out["incident_id"])
    print(json.dumps({"status": out["status"], "plan.cut_eids": out["plan"]["cut_eids"], "verdict.pass": out["verdict"]["pass"],
                      "timings_ms": out["timings_ms"], "events": [e["type"] for e in out["events"]]}))
    assert out["status"] == "DONE" and out["plan"]["cut_eids"] == ["e:s2:05", "e:s2:10"] and out["verdict"]["pass"] is True
    assert "## Summary" in out["report"]["markdown"] and len(out["events"]) == 6
    assert set(out["timings_ms"]) == {"investigator", "executor", "planner", "verifier", "reporter", "total"}
    assert sandboxes_of(out["incident_id"]) == []
    assert graph_client.get_db().connection.scard("active:s2") == 0
    graph = out["graph"]  # the UI cannot ask /graph for a fast run (its state is never stored), so /analyze carries it
    assert {e["eid"] for e in graph["edges"] if e["cut"]} == {"e:s2:05", "e:s2:10"} and any(n["is_src"] for n in graph["nodes"])


def test_analyze_s5_needs_human_and_the_split_sandbox_is_cleaned_up(api):
    out = api.post("/v1/analyze", json={"tenant": "s5", "src": "user:zoe"}).json()
    api.made.append(out["incident_id"])
    assert out["status"] == "NEEDS_HUMAN" and out["plan"]["suggestion"]["proven"] is True
    assert sandboxes_of(out["incident_id"]) == []  # the proven -split sandbox is deleted too


def test_analyze_errors(api):
    err(api.post("/v1/analyze", json={"tenant": "s2", "src": "user:nobody"}), 400, "UNKNOWN_SRC")
    err(api.post("/v1/analyze", json={"tenant": "s2", "src": "db:s2-customers"}), 400, "UNKNOWN_SRC")  # not a User
    err(api.post("/v1/analyze", json={"tenant": "nope", "src": "user:leo"}), 404, "NOT_FOUND")  # no baseline for that tenant
    err(api.post("/v1/analyze", json={"tenant": "Bad Tenant", "src": "user:x"}), 400, "BAD_INPUT")
    err(api.post("/v1/analyze", json={"tenant": "s2"}), 400, "BAD_INPUT")  # validation error -> our format


def test_unknown_route_has_the_same_error_shape(api):
    err(api.get("/v1/nothing"), 404, "NOT_FOUND")


# --- incidents ---

def test_incident_needs_the_demo_token(api):
    err(post_incident(api, token=None), 403, "FORBIDDEN")
    err(post_incident(api, token="wrong"), 403, "FORBIDDEN")


def test_incident_fast_mode_is_refused(api):
    err(post_incident(api, mode="fast"), 400, "BAD_INPUT")
    err(post_incident(api, mode="turbo"), 400, "BAD_INPUT")


def test_incident_created_stays_queued_and_is_in_the_jobs_stream(api):
    r = post_incident(api)
    assert r.status_code == 202 and r.json()["status"] == "QUEUED"
    incident_id = r.json()["incident_id"]
    jobs = [f for _, f in graph_client.get_db().connection.xrevrange(STREAM, count=50)]
    assert any((f.get(b"incident_id") or f.get("incident_id")) in (incident_id.encode(), incident_id) for f in jobs)
    state = api.get(f"/v1/incidents/{incident_id}").json()
    assert state["status"] == "QUEUED" and state["mode"] == "agent" and state["src"] == "user:ravi"
    err(api.get("/v1/incidents/nope"), 404, "NOT_FOUND")


def test_an_operator_instruction_is_stored_in_the_state_and_analyze_ignores_it(api):
    text = "  Don't touch the build server, CI depends on it.  "
    r = api.post("/v1/incidents", json={"tenant": "s2", "src": "user:ravi", "mode": "agent", "instructions": text}, headers={"X-Demo-Token": TOKEN})
    assert r.status_code == 202
    api.made.append(r.json()["incident_id"])
    state = api.get(f"/v1/incidents/{r.json()['incident_id']}").json()
    assert state["instructions"] == "Don't touch the build server, CI depends on it."  # trimmed
    plain = post_incident(api)  # no instruction: an empty string
    assert api.get(f"/v1/incidents/{plain.json()['incident_id']}").json()["instructions"] == ""
    err(api.post("/v1/incidents", json={"tenant": "s2", "src": "user:ravi", "mode": "agent", "instructions": "x" * 501}, headers={"X-Demo-Token": TOKEN}),
        400, "BAD_INPUT")  # too long: refused before anything is queued
    from api.routes.incidents import IncidentBody
    assert IncidentBody(tenant="s2", src="user:ravi", mode="agent", instructions="x" * 500).instructions == "x" * 500  # 500 is allowed, 501 is not
    done = api.post("/v1/analyze", json={"tenant": "s2", "src": "user:ravi", "instructions": "Do not touch e:s2:05"}).json()  # fast mode: ignored
    api.made.append(done["incident_id"])
    assert done["plan"]["cut_eids"] == ["e:s2:05", "e:s2:10"] and done["plan"]["avoid"] == [] and done["plan"]["instruction"] is None
    assert done["attempt"] == 1 and done["degraded"] is False and done["degraded_steps"] == []


def test_incident_unknown_src(api):
    err(post_incident(api, src="user:nobody"), 400, "UNKNOWN_SRC")


def test_idempotency_key(api):
    first = post_incident(api, **{"Idempotency-Key": "p54-a"})
    again = post_incident(api, **{"Idempotency-Key": "p54-a"})
    assert first.status_code == 202 and again.status_code == 200
    assert again.json()["incident_id"] == first.json()["incident_id"]
    err(post_incident(api, src="user:eng-01", **{"Idempotency-Key": "p54-a"}), 409, "IDEMPOTENCY_CONFLICT")
    assert graph_client.get_db().connection.scard("active:agent") == 1  # the replay did not start a second incident


def test_third_active_agent_incident_is_busy(api):
    assert config.MAX_ACTIVE_AGENT == 2
    assert post_incident(api, src="user:ravi").status_code == 202
    assert post_incident(api, src="user:eng-01").status_code == 202
    err(post_incident(api, src="user:eng-02"), 429, "BUSY")


def test_rate_limit(api, monkeypatch):
    monkeypatch.setattr(config, "RATE_LIMIT_PER_MIN", 2)
    monkeypatch.setattr(config, "MAX_ACTIVE_AGENT", 99)
    assert [post_incident(api).status_code for _ in range(2)] == [202, 202]
    err(post_incident(api), 429, "RATE_LIMITED")


@pytest.fixture
def same_minute(monkeypatch):
    """The rate limit counts per clock minute: freeze it so a slow test cannot cross a minute boundary."""
    monkeypatch.setattr("api.checks.time", type("T", (), {"time": staticmethod(lambda: 1_800_000_000.0)}))


def test_analyze_allows_60_per_minute_and_the_61st_gets_429(api, same_minute):
    assert config.ANALYZE_RATE_LIMIT_PER_MIN == 60 and config.RATE_LIMIT_PER_MIN == 10  # the defaults
    body = {"tenant": "s2", "src": "user:ravi"}
    assert {api.post("/v1/analyze", json=body).status_code for _ in range(60)} == {200}  # open: no token, and normal use is never blocked
    err(api.post("/v1/analyze", json=body), 429, "RATE_LIMITED")
    assert "60 requests per minute" in api.post("/v1/analyze", json=body).json()["error"]["message"]
    assert graph_client.get_db().connection.scard("active:s2") == 0  # the 429 happened before the run: nothing left active


def test_incidents_stay_at_10_per_minute_and_the_two_scopes_do_not_share_a_counter(api, same_minute, monkeypatch):
    monkeypatch.setattr(config, "MAX_ACTIVE_AGENT", 99)
    body = {"tenant": "s2", "src": "user:ravi"}
    for _ in range(11):  # the analyze counter is at 11 ...
        assert api.post("/v1/analyze", json=body).status_code == 200
    assert [post_incident(api).status_code for _ in range(10)] == [202] * 10  # ... and the agent counter still has its 10
    err(post_incident(api), 429, "RATE_LIMITED")
    assert api.post("/v1/analyze", json=body).status_code == 200  # agent mode used up its limit: fast mode is not affected
    redis = graph_client.get_db().connection
    assert (redis.get("ratelimit:analyze:testclient:30000000"), redis.get("ratelimit:incidents:testclient:30000000")) == ("12", "11")


def test_a_rate_limit_setting_changes_only_its_own_scope(api, same_minute, monkeypatch):
    monkeypatch.setattr(config, "ANALYZE_RATE_LIMIT_PER_MIN", 2)
    body = {"tenant": "s2", "src": "user:ravi"}
    assert [api.post("/v1/analyze", json=body).status_code for _ in range(2)] == [200, 200]
    err(api.post("/v1/analyze", json=body), 429, "RATE_LIMITED")
    assert [post_incident(api).status_code for _ in range(2)] == [202, 202]  # RATE_LIMIT_PER_MIN is still 10


# --- events and graph ---

def saved_incident(api):
    state = new_state("s2", "user:ravi", "agent")
    state_store.save(state)
    api.made.append(state["id"])
    return state


def test_events_long_poll(api):
    state = saved_incident(api)
    url = f"/v1/incidents/{state['id']}/events"
    assert api.get(url, params={"wait": 0}).json() == {"events": [], "last_id": "0-0"}
    for i in range(3):
        events.emit(state["id"], "planner", "plan", f"m{i}")
    all_events = api.get(url).json()
    assert [e["msg"] for e in all_events["events"]] == ["m0", "m1", "m2"]
    first_id = events.read(state["id"])[0][0]
    assert [e["msg"] for e in api.get(url, params={"after": first_id}).json()["events"]] == ["m1", "m2"]

    started = time.time()  # nothing new: the call waits, then returns empty
    quiet = api.get(url, params={"after": all_events["last_id"], "wait": 1}).json()
    assert quiet == {"events": [], "last_id": all_events["last_id"]} and time.time() - started >= 0.9

    timer = threading.Timer(0.3, events.emit, args=(state["id"], "reporter", "report", "late"))
    timer.start()  # a new event during the wait: returned at once, well before the wait is over
    started = time.time()
    woken = api.get(url, params={"after": all_events["last_id"], "wait": 5}).json()
    timer.join()
    assert [e["msg"] for e in woken["events"]] == ["late"] and time.time() - started < 3
    err(api.get("/v1/incidents/nope/events"), 404, "NOT_FOUND")


def test_graph_marks_the_cut_the_path_and_the_protected_edges(api):
    state = new_state("s2", "user:ravi", "fast")
    with hold_lock(state["id"]) as lock:
        run_incident(state, "fast", lock_token=lock.token, sink=[])
        for suffix in ("", "-split"):
            write.delete_sandbox(sandbox_name("s2", state["id"], suffix), incident_id=state["id"], lock_token=lock.token)
    assert state["status"] == "DONE"
    state_store.save(state)
    api.made.append(state["id"])

    view = api.get(f"/v1/incidents/{state['id']}/graph").json()
    by_eid = {e["eid"]: e for e in view["edges"]}
    assert {e for e, v in by_eid.items() if v["cut"]} == {"e:s2:05", "e:s2:10"}
    assert by_eid["e:s2:05"]["on_path"] and by_eid["e:s2:01"]["on_path"] and by_eid["e:s2:01"]["protected"]
    assert not by_eid["e:s2:05"]["protected"]
    assert {e for e, v in by_eid.items() if v["protected"]} <= set(state["action"]["protected_eids"])
    # the company-wide background is hidden: a protected edge is drawn only if it touches a node of an attack path (or is next to a cut edge)
    path_nodes = {n for p in state["findings"]["paths"] for n in p["node_ids"]}
    cut_nodes = {n for e in ("e:s2:05", "e:s2:10") for n in (by_eid[e]["src"], by_eid[e]["dst"])}
    drawn_protected = {e for e, v in by_eid.items() if v["protected"]}
    assert all({by_eid[e]["src"], by_eid[e]["dst"]} & (path_nodes | cut_nodes) for e in drawn_protected)
    assert view["hidden_protected"] == len(state["action"]["protected_eids"]) - len(drawn_protected) > 30  # S2: about 40 background edges hidden
    assert len(view["edges"]) < 25 and view["more"] == 0
    node_ids = {n["id"] for n in view["nodes"]}
    assert all(e["src"] in node_ids and e["dst"] in node_ids for e in view["edges"])
    assert len(view["nodes"]) + len(view["edges"]) <= graph_view.MAX_ELEMENTS
    src = next(n for n in view["nodes"] if n["id"] == "user:ravi")
    assert src["is_src"] and any(n["crown_jewel"] for n in view["nodes"])
    err(api.get("/v1/incidents/nope/graph"), 404, "NOT_FOUND")
    err(api.get(f"/v1/incidents/{saved_incident(api)['id']}/graph"), 404, "NOT_FOUND")  # no findings yet

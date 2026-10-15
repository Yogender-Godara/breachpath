"""The worker loop, the janitor and the lock-lost stop (real FalkorDB, no LLM: the agent brains are the fast brains).
The live tests at the end run the whole path API -> jobs -> worker -> agents -> events on the real LLMs (-m live)."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from api.main import app
from core import config, fast_brains
from core.graph import client, loader
from core.graph.schema import baseline_name
from core.pipeline import run_incident
from orchestrator import events, state as state_store
from orchestrator.jobs import GROUP, STREAM, ensure_group
from orchestrator.lock import hold_lock
from orchestrator.state import new_state
from worker import main as worker

CONSUMER = "test-consumer"
TOKEN = "p66-token"


@pytest.fixture
def stream(falkordb):
    """A clean `jobs` stream + group, and a list of the incident ids made here; every key and graph of theirs is removed afterwards.
    (No real worker may be running against this Redis while the tests run.)"""
    redis = client.get_db().connection
    redis.delete(STREAM)
    ensure_group(redis)
    made = []
    yield redis, made
    redis.delete(STREAM)
    for iid in made:
        saved = state_store.load(iid)
        for name in {saved and saved.get("sandbox_graph"), saved and saved.get("sandbox_graph") and saved["sandbox_graph"] + "-split"} - {None}:
            client.delete_graph(name)
            redis.zrem("sbx:expiry", name)
        redis.delete(f"inc:{iid}", f"inc:{iid}:events", f"quota:inc:{iid}:sims", f"lock:inc:{iid}", *redis.keys(f"reach:*:sbx:{iid}*"))
        for key in ("active:agent", "active:s2", "active:s5"):
            redis.srem(key, iid)


@pytest.fixture
def fast_agents(monkeypatch):
    """The agent brains replaced by the fast brains: the worker and the pipeline run for real, no LLM."""
    from agents import brains
    monkeypatch.setattr(brains, "investigate", fast_brains.investigate)
    monkeypatch.setattr(brains, "plan", fast_brains.plan)
    loader.load_scenario("s2")


def enqueue(redis, made, sid="s2", src="user:ravi"):
    """What POST /v1/incidents does: the saved QUEUED state, the active sets and the job."""
    state = new_state(sid, src, "agent")
    state_store.save(state)
    made.append(state["id"])
    redis.sadd(f"active:{sid}", state["id"])
    redis.sadd("active:agent", state["id"])
    redis.xadd(STREAM, {"incident_id": state["id"]})
    return state


def pending(redis):
    return redis.xpending(STREAM, GROUP)["pending"]


# --- the worker loop ---

def test_a_job_is_consumed_the_incident_is_done_and_everything_is_cleaned_up(stream, fast_agents):
    redis, made = stream
    state = enqueue(redis, made)
    assert worker.process_one(redis, CONSUMER, block_ms=1000) == state["id"]
    saved = state_store.load(state["id"])
    assert saved["status"] == "DONE" and saved["verdict"]["pass"] and saved["report"]["markdown"]
    assert pending(redis) == 0  # acked
    assert not redis.sismember("active:s2", state["id"]) and not redis.sismember("active:agent", state["id"])
    assert not redis.exists(f"lock:inc:{state['id']}")
    assert saved["sandbox_graph"] in client.get_db().list_graphs()  # the agent sandbox is kept (the janitor removes it later)
    assert [e["type"] for _, e in events.read(state["id"])][-1] == "report"  # the pipeline wrote its events to the incident's stream
    assert worker.process_one(redis, CONSUMER, block_ms=200) is None  # nothing left


def test_a_finished_incident_is_acked_without_running(stream, fast_agents, monkeypatch):
    redis, made = stream
    state = enqueue(redis, made)
    state["status"] = "DONE"
    state_store.save(state)
    monkeypatch.setattr(worker, "run_incident", lambda *a, **k: pytest.fail("a finished incident must not run again"))
    assert worker.process_one(redis, CONSUMER, block_ms=1000) == state["id"]
    assert pending(redis) == 0 and not redis.sismember("active:agent", state["id"])


def test_a_job_whose_incident_is_gone_is_dropped(stream, fast_agents):
    redis, made = stream
    redis.xadd(STREAM, {"incident_id": "nosuchid"})
    assert worker.process_one(redis, CONSUMER, block_ms=1000) == "nosuchid" and pending(redis) == 0


def test_a_busy_lock_leaves_the_job_unacked_and_it_resumes_later(stream, fast_agents):
    redis, made = stream
    state = enqueue(redis, made)
    with hold_lock(state["id"]):  # another holder owns the incident
        assert worker.process_one(redis, CONSUMER, block_ms=1000) == state["id"]
        assert pending(redis) == 1 and state_store.load(state["id"])["status"] == "QUEUED"
        assert redis.sismember("active:agent", state["id"])  # still running somewhere: the sets are not touched
    assert worker.process_one(redis, CONSUMER, pending=True) == state["id"]  # the lock is free: the un-acked job is read again and runs
    assert state_store.load(state["id"])["status"] == "DONE" and pending(redis) == 0


def test_a_crashed_run_resumes_from_its_saved_status(stream, fast_agents):
    redis, made = stream
    state = enqueue(redis, made)
    with hold_lock(state["id"]):  # run only the first steps, as a worker that died would have
        state["status"] = "INVESTIGATING"
        state["findings"] = fast_brains.investigate(state)
        state_store.save(state)
    assert state_store.load(state["id"])["status"] == "INVESTIGATING"
    worker.handle(redis, "0-0", state["id"])  # the job is read again: the saved status INVESTIGATING is where it continues
    assert state_store.load(state["id"])["status"] == "DONE"  # run_incident loops on the saved status


# --- the janitor ---

def test_the_janitor_deletes_expired_sandboxes_and_never_a_baseline(falkordb):
    redis = client.get_db().connection
    loader.load_scenario("s2")
    expired, fresh, baseline = "t:s2:sbx:p66jan1", "t:s2:sbx:p66jan2", baseline_name("s2")
    for name in (expired, fresh):
        client.get_graph(name).query("CREATE (:X {id: 'x'})")
    redis.set(f"reach:{expired}:user:ravi", "{}")
    now = time.time()
    redis.zadd("sbx:expiry", {expired: now - 5, fresh: now + 3600, baseline: now - 5})  # a baseline put in by mistake, already "expired"
    try:
        worker.janitor(redis)
        graphs = client.get_db().list_graphs()
        assert expired not in graphs and fresh in graphs and baseline in graphs  # the baseline is never deleted
        assert not redis.exists(f"reach:{expired}:user:ravi")
        assert redis.zscore("sbx:expiry", expired) is None and redis.zscore("sbx:expiry", baseline) is None  # entries handled
        assert redis.zscore("sbx:expiry", fresh) is not None
    finally:
        client.delete_graph(expired)
        client.delete_graph(fresh)
        redis.zrem("sbx:expiry", expired, fresh, baseline)


# --- the lock-lost stop (in the pipeline) ---

def test_a_lost_lock_stops_the_pipeline_with_failed(falkordb):
    loader.load_scenario("s2")
    state, sink = new_state("s2", "user:ravi", "fast"), []
    with hold_lock(state["id"]) as lock:
        lock.lost.set()  # the heartbeat found the lock is no longer ours
        run_incident(state, "fast", lock_token=lock.token, sink=sink, lock=lock)
    assert state["status"] == "FAILED" and state["failure_detail"] == "lock lost"
    assert [e["type"] for e in sink] == ["error"] and sink[0]["msg"] == "lock lost"
    assert not [g for g in client.get_db().list_graphs() if f":sbx:{state['id']}" in g]  # no step ran


def test_a_lock_that_is_held_does_not_stop_the_pipeline(falkordb):
    loader.load_scenario("s2")
    state, sink = new_state("s2", "user:ravi", "fast"), []
    with hold_lock(state["id"]) as lock:
        try:
            run_incident(state, "fast", lock_token=lock.token, sink=sink, lock=lock)
        finally:
            from core.tools import write
            write.delete_sandbox(state["sandbox_graph"], incident_id=state["id"], lock_token=lock.token)
    assert state["status"] == "DONE"


# --- live: API -> jobs -> worker -> agents -> events (real LLMs) ---

MODELS = {"GROQ": ("openai/gpt-oss-120b", "openai/gpt-oss-20b"), "OPENROUTER": ("nvidia/nemotron-3-super-120b-a12b:free",) * 2}  # VERIFY_RESULTS P1.4


def through_the_stack(redis, made, monkeypatch, sid, src):
    """POST /v1/incidents, run the worker for this one job in-process, read the incident and its events through the API."""
    if not (os.getenv("GROQ_API_KEY") or os.getenv("OPENROUTER_API_KEY")):
        pytest.skip("neither GROQ_API_KEY nor OPENROUTER_API_KEY is set")
    for name, (heavy, light) in MODELS.items():
        for agent, model in (("INVESTIGATOR", heavy), ("PLANNER", heavy), ("REPORTER", light)):
            if not os.getenv(f"MODEL_{agent}_{name}"):
                monkeypatch.setenv(f"MODEL_{agent}_{name}", model)
    monkeypatch.setattr(config, "DEMO_TOKEN", TOKEN)
    api = TestClient(app)
    assert api.post(f"/v1/scenarios/{sid}/load").status_code == 200
    posted = api.post("/v1/incidents", json={"tenant": sid, "src": src, "mode": "agent"}, headers={"X-Demo-Token": TOKEN})
    assert posted.status_code == 202, posted.text
    iid = posted.json()["incident_id"]
    made.append(iid)
    assert posted.json()["status"] == "QUEUED"
    started = time.perf_counter()
    assert worker.process_one(redis, "live-consumer", block_ms=3000) == iid
    total = time.perf_counter() - started
    incident = api.get(f"/v1/incidents/{iid}").json()
    evs = api.get(f"/v1/incidents/{iid}/events", params={"wait": 0}).json()["events"]
    print(f"\n{sid}: status={incident['status']} degraded={incident['degraded']} total {total:.1f} s, {len(evs)} events")
    if incident.get("failure_detail"):
        print(f"  failure_detail: {incident['failure_detail'][:900]}")
    for e in evs:
        print(f"  {e['agent']:<12} {e['type']:<10} {str(e['latency_ms'] or ''):>6}  {e['msg'][:95]}")
    return incident, evs


def first(evs, agent, type_):
    return next(i for i, e in enumerate(evs) if (e["agent"], e["type"]) == (agent, type_))


@pytest.mark.live
def test_live_s2_posted_to_the_api_is_done_through_the_worker_with_events(stream, monkeypatch):
    redis, made = stream
    incident, evs = through_the_stack(redis, made, monkeypatch, "s2", "user:ravi")
    assert incident["status"] == "DONE" and incident["verdict"]["pass"] is True, incident.get("failure_detail")
    assert incident["verdict"]["attack_max_flow"] == 0 and incident["verdict"]["bfs_reached_jewels"] == []
    assert len(incident["plan"]["cut_eids"]) == 2 and incident["action"]["applied_eids"] == sorted(incident["plan"]["cut_eids"])
    order = [first(evs, "investigator", "tool_call"), first(evs, "investigator", "finding"), first(evs, "executor", "step"),
             first(evs, "planner", "tool_call"), first(evs, "planner", "plan"), first(evs, "executor", "action"),
             first(evs, "verifier", "verdict"), first(evs, "reporter", "report")]
    assert order == sorted(order), order
    assert not redis.sismember("active:agent", incident["id"]) and not redis.sismember("active:s2", incident["id"])
    assert "key" not in json.dumps(evs).lower().replace("keys", "")


@pytest.mark.live
def test_live_s5_has_no_safe_cut_so_no_planner_llm_call_and_a_proven_split(stream, monkeypatch):
    redis, made = stream
    incident, evs = through_the_stack(redis, made, monkeypatch, "s5", json.load(open("data/answer_keys/s5.json"))["src"])
    assert incident["status"] == "NEEDS_HUMAN" and incident["plan"]["safe"] is False and incident["plan"]["suggestion"]["proven"] is True
    assert incident["plan"]["suggestion"]["suggestion_type"] == "split_node" and incident["verdict"] is None
    llm_planner = [e for e in evs if e["agent"] == "planner" and (e["type"] == "tool_call" or (e["type"] == "step" and (e["data"] or {}).get("turn")))]
    assert llm_planner == []  # the Planner made no LLM call at all

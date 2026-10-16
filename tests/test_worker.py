"""The worker loop, the janitor and the lock-lost stop (real FalkorDB, no LLM: the agent brains are the fast brains).
The live tests at the end run the whole path API -> jobs -> worker -> agents -> events on the real LLMs (-m live)."""
import json
import os
import threading
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
    monkeypatch.setattr(brains, "report", fast_brains.report)
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
    first = events.read(state["id"])[0][1]  # NFR-L3: something is visible at once, before the first MCP session even starts
    assert (first["agent"], first["type"], first["msg"]) == ("orchestrator", "step", "worker picked up the incident (status QUEUED)")
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


def test_resume_pending_waits_for_a_crashed_workers_lock_to_expire_then_runs_the_job(stream, fast_agents):
    """The real chaos case: the worker is killed, the container restarts in a second, but the dead worker's lock lives for LOCK_TTL_MS."""
    redis, made = stream
    state = enqueue(redis, made)
    redis.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=1)  # this consumer read the job and then "died" before acking
    assert pending(redis) == 1
    redis.set(f"lock:inc:{state['id']}", "dead-worker", px=1500)  # nobody extends it any more
    ran = worker.resume_pending(redis, CONSUMER, wait_s=10, pause_s=0.5)
    assert ran == [state["id"]] and state_store.load(state["id"])["status"] == "DONE" and pending(redis) == 0


def test_resume_pending_gives_up_after_wait_s_and_leaves_the_job_unacked(stream, fast_agents):
    redis, made = stream
    state = enqueue(redis, made)
    redis.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=1)
    redis.set(f"lock:inc:{state['id']}", "alive-worker", px=30000)
    try:
        assert worker.resume_pending(redis, CONSUMER, wait_s=1, pause_s=0.3) == [] and pending(redis) == 1
        assert state_store.load(state["id"])["status"] == "QUEUED"
    finally:
        redis.delete(f"lock:inc:{state['id']}")


def test_the_loop_survives_a_deleted_jobs_stream(falkordb, monkeypatch):
    """The worker used to crash with NOGROUP when the stream disappeared (the restart policy then hid it). It creates the group again and goes on."""
    redis = client.get_db().connection
    redis.delete(STREAM)
    calls, stop = [], threading.Event()

    def fake_process_one(r, consumer, **kw):
        calls.append(1)
        if len(calls) == 1:
            r.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1)  # raises NOGROUP, like the real call
        else:
            stop.set()
    monkeypatch.setattr(worker, "process_one", fake_process_one)
    try:
        worker.loop(redis, "p92-nogroup", stop)
        assert len(calls) == 2 and redis.exists(STREAM)  # the group exists again
    finally:
        redis.delete(STREAM, f"{worker.ALIVE_PREFIX}p92-nogroup")


# --- liveness (the compose healthcheck asks whether any worker:alive:* key exists) ---

def test_the_liveness_key_has_a_60_second_ttl_and_the_check_command_sees_it(falkordb):
    redis = client.get_db().connection
    for key in redis.keys(f"{worker.ALIVE_PREFIX}*"):
        redis.delete(key)
    try:
        assert not redis.keys(f"{worker.ALIVE_PREFIX}*")  # no worker: the healthcheck would fail
        worker.beat(redis, "p92-consumer")
        assert redis.keys(f"{worker.ALIVE_PREFIX}*") == [f"{worker.ALIVE_PREFIX}p92-consumer"]
        assert 0 < redis.ttl(f"{worker.ALIVE_PREFIX}p92-consumer") <= 60 == worker.ALIVE_TTL_S
        assert worker.ALIVE_EVERY_S < worker.ALIVE_TTL_S  # a long job refreshes it before it expires
    finally:
        redis.delete(f"{worker.ALIVE_PREFIX}p92-consumer")


def test_a_running_job_keeps_refreshing_the_liveness_key(stream, fast_agents, monkeypatch):
    redis, made = stream
    key = f"{worker.ALIVE_PREFIX}p92-job"
    redis.delete(key)
    monkeypatch.setattr(worker, "ALIVE_EVERY_S", 0.1)  # a job longer than the 60 s TTL must not look dead
    real, seen = worker.run_incident, []

    def slow_job(*args, **kwargs):
        redis.delete(key)
        time.sleep(0.6)
        seen.append(bool(redis.exists(key)))  # the keep-alive thread set it again while the job was running
        return real(*args, **kwargs)
    monkeypatch.setattr(worker, "run_incident", slow_job)
    try:
        enqueue(redis, made)
        worker.process_one(redis, "p92-job", block_ms=1000)
        assert seen == [True]
    finally:
        redis.delete(key)


def test_trim_keeps_the_newest_sandboxes_and_never_deletes_an_active_one_or_a_baseline(falkordb):
    """Kept agent sandboxes count toward MAX_GRAPHS: before a job the worker deletes the oldest ones (P9.3: 20 graphs reached -> every incident FAILED)."""
    redis = client.get_db().connection
    saved = redis.zrange("sbx:expiry", 0, -1, withscores=True)  # the real entries stay untouched: this test works on its own zset
    redis.delete("sbx:expiry")
    names = [f"t:s2:sbx:p92t{i}" for i in range(5)]
    baseline = baseline_name("s2")
    loader.load_scenario("s2")
    try:
        for name in names:
            client.get_graph(name).query("CREATE (:X {id: 'x'})")
        now = time.time()
        redis.zadd("sbx:expiry", {baseline: now - 1000, **{n: now + 100 + i for i, n in enumerate(names)}})  # the oldest entry is a baseline put in by mistake
        redis.sadd("active:p92trim", "p92t0")  # the oldest sandbox belongs to a running incident
        gone = worker.trim_sandboxes(redis, keep=2)
        graphs = client.get_db().list_graphs()
        assert gone == [names[1], names[2]]  # the 2 newest (p92t3, p92t4) stay; the baseline and the active p92t0 are skipped
        assert all(n not in graphs for n in gone) and names[0] in graphs and names[3] in graphs and names[4] in graphs and baseline in graphs
        assert [n for n in redis.zrange("sbx:expiry", 0, -1)] == [baseline, names[0], names[3], names[4]]
        assert worker.trim_sandboxes(redis, keep=10) == []  # under the limit: nothing happens
    finally:
        redis.srem("active:p92trim", "p92t0")
        redis.delete("sbx:expiry", "active:p92trim")
        for name in names:
            client.delete_graph(name)
        if saved:
            redis.zadd("sbx:expiry", {n: s for n, s in saved})


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

"""P9.3: measure NFR-L1 .. L5 (docs/01_Requirements.md 4.1) and print a Markdown table: target vs measured.

Run in the toolbox next to the running stack (it reaches the API at API_URL, default http://api:8000):
    docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm toolbox python -m scripts.latency [--n 5] [--fresh] [--json out.json]

L1  in-process: the read tools on the biggest scenario baseline (30 calls each, p95 over all calls)
L2  fast mode /analyze: S1-S8 x n through FastAPI's TestClient (rate limit lifted: a measurement, not a load test) and n real HTTP calls to the API
L3  agent mode: POST /incidents -> the first event is readable (the UI polls once a second on top of this)
L4  agent mode: POST /incidents -> terminal status, through the real API + worker, S2, n runs served by the LLM cache (every LLM answer replayed)
    and, with --fresh, ONE run with the cache OFF (real LLM calls), in-process
L5  POST /incidents -> 202
Plain numbers only: nothing is estimated. A step that cannot run says so.
"""
import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if "--fresh" in sys.argv:
    os.environ["LLM_CACHE"] = "off"  # read by core.config at import time

from core import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
API = os.getenv("API_URL", "http://api:8000")
UI = os.getenv("UI_URL", "http://ui:8501")
TERMINAL = ("DONE", "DONE_NO_RISK", "NEEDS_HUMAN", "FAILED")


def pct(values, p):
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def stats(ms):
    return {"n": len(ms), "p50": round(pct(ms, 50), 1), "p95": round(pct(ms, 95), 1), "max": round(max(ms), 1)}


def http(method, path, body=None, headers=None, base=API, timeout=120):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


# --- L1 ---

def l1():
    from core.graph import loader
    from core.graph.client import get_db, ro_query
    from core.tools import read
    best = None
    for sid in (f"s{i}" for i in range(1, 9)):
        loader.load_scenario(sid)
        graph = f"t:{sid}:baseline"
        nodes = ro_query(graph, "MATCH (n) RETURN count(n)")[0][0]
        if best is None or nodes > best[1]:
            best = (sid, nodes, graph)
    sid, nodes, graph = best
    src = json.load(open(f"data/answer_keys/{sid}.json"))["src"]
    edge = ro_query(graph, "MATCH ()-[r]->() RETURN r.eid LIMIT 1")[0][0]
    target = read.get_blast_radius(graph, src)["data"][0]["id"]
    calls = {"get_blast_radius": lambda: read.get_blast_radius(graph, src), "find_attack_paths(k=20)": lambda: read.find_attack_paths(graph, src, k=20),
             "shortest_paths": lambda: read.shortest_paths(graph, src, target), "edge_path_frequency": lambda: read.edge_path_frequency(graph, src),
             "choke_points": lambda: read.choke_points(graph, src), "compute_min_cut": lambda: read.compute_min_cut(graph, src),
             "get_node": lambda: read.get_node(graph, src), "get_edge": lambda: read.get_edge(graph, edge)}
    every, per_tool = [], {}
    for name, fn in calls.items():
        fn(), fn()  # warm-up
        ms = []
        for _ in range(30):
            began = time.perf_counter()
            result = fn()
            ms.append((time.perf_counter() - began) * 1000)
            assert result["ok"], (name, result)
        per_tool[name] = stats(ms)
        every += ms
    return {"graph": f"{graph} ({nodes} nodes, {len(get_db().list_graphs())} graphs in the database)", "per_tool": per_tool, "all": stats(every)}


# --- L2 ---

def l2(n):
    from fastapi.testclient import TestClient
    from api.main import app
    from core.graph import loader
    config.ANALYZE_RATE_LIMIT_PER_MIN = 10**6
    client, ms, per_scenario = TestClient(app), [], {}
    for i in range(1, 9):
        sid = f"s{i}"
        loader.load_scenario(sid)
        src = json.load(open(f"data/answer_keys/{sid}.json"))["src"]
        client.post("/v1/analyze", json={"tenant": sid, "src": src})  # warm-up
        runs = []
        for _ in range(n):
            began = time.perf_counter()
            r = client.post("/v1/analyze", json={"tenant": sid, "src": src})
            runs.append((time.perf_counter() - began) * 1000)
            assert r.status_code == 200, r.text
        per_scenario[sid] = stats(runs)
        ms += runs
    out = {"testclient": stats(ms), "per_scenario": per_scenario}
    http("POST", "/v1/scenarios/s2/load")
    real = []
    for _ in range(n):  # the real API container over the Docker network (the per-IP limit is 10/min: n must stay below it)
        began = time.perf_counter()
        code, _ = http("POST", "/v1/analyze", {"tenant": "s2", "src": "user:ravi"})
        real.append((time.perf_counter() - began) * 1000)
        assert code == 200, code
    out["http_s2"] = stats(real)
    return out


# --- L3 / L4 / L5 ---

def agent_runs(n):
    token = os.getenv("DEMO_TOKEN", "")
    http("POST", "/v1/scenarios/s2/load")
    accept, first, total, cached = [], [], [], []
    for _ in range(n):
        began = time.perf_counter()
        code, inc = http("POST", "/v1/incidents", {"tenant": "s2", "src": "user:ravi", "mode": "agent"}, {"X-Demo-Token": token})
        accept.append((time.perf_counter() - began) * 1000)
        if code != 202:
            return {"error": f"POST /incidents answered {code}: {inc}"}
        iid, seen_first = inc["incident_id"], None
        while True:
            code, got = http("GET", f"/v1/incidents/{iid}/events?after=0-0&wait=1") if seen_first is None else (None, None)
            if seen_first is None and got and got["events"]:
                seen_first = (time.perf_counter() - began) * 1000
            code, st = http("GET", f"/v1/incidents/{iid}")
            if st["status"] in TERMINAL:
                break
            if time.perf_counter() - began > 240:
                return {"error": f"incident {iid} not terminal after 240 s ({st['status']})"}
            time.sleep(0.2)
        total.append((time.perf_counter() - began) * 1000)
        first.append(seen_first)
        _, ev = http("GET", f"/v1/incidents/{iid}/events?after=0-0&wait=0")
        llm = [e for e in ev["events"] if (e["data"] or {}).get("turn") or e["type"] == "report"]
        steps = [e for e in ev["events"] if "cached" in (e["data"] or {})]
        cached.append(f"{sum(bool(e['data']['cached']) for e in steps)}/{len(steps)}")
        time.sleep(1)
    return {"accept": stats(accept), "first_event": stats(first), "total_cached": stats(total), "llm_steps_cached": cached, "last_status": st["status"], "degraded": st["degraded"]}


def fresh_run():
    from core.graph import loader
    from core.graph.client import get_db
    from core.graph.schema import sandbox_name
    from core.pipeline import run_incident
    from core.tools import write
    from orchestrator.lock import hold_lock
    from orchestrator.state import new_state
    loader.load_scenario("s2")
    state, redis = new_state("s2", "user:ravi", "agent"), get_db().connection
    began = time.perf_counter()
    with hold_lock(state["id"]) as lock:
        try:
            run_incident(state, "agent", lock_token=lock.token)
        finally:
            for suffix in ("", "-split"):
                write.delete_sandbox(sandbox_name("s2", state["id"], suffix), incident_id=state["id"], lock_token=lock.token)
    wall = (time.perf_counter() - began) * 1000
    redis.delete(f"inc:{state['id']}", f"inc:{state['id']}:events", f"quota:inc:{state['id']}:sims", *redis.keys(f"reach:*:sbx:{state['id']}*"))
    return {"cache": config.LLM_CACHE, "status": state["status"], "degraded": state["degraded"], "degraded_steps": state["degraded_steps"],
            "llm_calls": state["llm_calls"], "total_ms": round(wall)}


# --- UI ---

def ui_paint(n=3):
    """The Streamlit page renders in the browser over a websocket, so a real first paint is not measurable here. Measured instead:
    the HTML shell (time to first byte) and the first script run of ui/app.py (AppTest: everything the server computes before the first render)."""
    from streamlit.testing.v1 import AppTest
    os.environ["API_URL"] = API
    shell = []
    for _ in range(n):
        began = time.perf_counter()
        with urllib.request.urlopen(UI + "/", timeout=30) as r:
            r.read()
        shell.append((time.perf_counter() - began) * 1000)
    runs = []
    for _ in range(n):
        began = time.perf_counter()
        at = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=30).run()
        runs.append((time.perf_counter() - began) * 1000)
        assert not at.exception, at.exception
    return {"html_shell": stats(shell), "first_script_run": stats(runs)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--fresh", action="store_true", help="one more agent run with the LLM cache OFF (real LLM calls)")
    ap.add_argument("--json", help="write the numbers to this file")
    ap.add_argument("--skip", default="", help="comma list of: l1,l2,agent,ui")
    args = ap.parse_args(argv)
    skip, out = set(args.skip.split(",")), {}
    for name, fn in (("l1", l1), ("l2", lambda: l2(args.n)), ("agent", lambda: agent_runs(args.n)), ("ui", ui_paint)):
        if name in skip:
            continue
        began = time.perf_counter()
        try:
            out[name] = fn()
        except Exception as e:  # say so, never invent a number
            out[name] = {"error": f"{type(e).__name__}: {e}"}
        print(f"{name}: {json.dumps(out[name])}  [{time.perf_counter() - began:.1f}s]", flush=True)
    if args.fresh:
        out["fresh"] = fresh_run()
        print(f"fresh: {json.dumps(out['fresh'])}", flush=True)
    if args.json:
        with open(args.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(out, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

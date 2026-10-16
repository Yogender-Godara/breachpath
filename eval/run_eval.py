"""Run the evaluation (docs/03_LLD.md section 11): scenarios x methods -> eval/results/latest.json + latest.md (+ Redis `eval:latest`).

    python -m eval.run_eval [--scenarios s1 s2 ...] [--methods fast,agent,disable,betweenness,frequency,llm_json] [--pause 8] [--fresh]

fast and agent run the real pipeline in-process (a new incident each; the sandbox and the -split sandbox are removed afterwards).
The baselines are networkx only. EVERY method is scored by the same independent networkx reference (eval/metrics.py).
A run that covers only some scenarios or methods is MERGED into the saved results (so a run cut short by a free-tier quota is
finished by running it again; the LLM cache makes the finished cells free). A cell that was not run says so: no value is ever made up.
"""
import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from agents import llm_gateway
from core.graph import client, loader
from core.graph.schema import sandbox_name
from core.pipeline import run_incident
from core.tools import write as write_tools
from data.scenarios import build
from eval import baseline_json_llm, baselines, bruteforce, metrics
from orchestrator import events as event_store
from orchestrator.lock import hold_lock
from orchestrator.state import new_state

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"
SCENARIOS = [f"s{i}" for i in range(1, 9)]
METHODS = ["fast", "agent", "disable", "betweenness", "frequency", "llm_json"]
LABELS = {"fast": "BreachPath (fast)", "agent": "BreachPath (agent)", "disable": "B-disable", "betweenness": "B-betweenness",
          "frequency": "B-frequency", "llm_json": "B-LLM-JSON"}
LLM_METHODS = ("agent", "llm_json")
REDIS_KEY = "eval:latest"


# --- one method on one scenario: -> a row ---

def _tokens(events):
    pairs = [(e["tokens_in"], e["tokens_out"]) for e in events if e.get("tokens_in") is not None]
    return sum(p[0] for p in pairs), sum(p[1] or 0 for p in pairs)


def _models(events):
    """{agent: 'provider/model'} from the events that name a provider."""
    out = {}
    for e in events:
        provider = (e.get("data") or {}).get("provider")
        if provider and e["agent"] in ("investigator", "planner", "reporter"):
            try:
                out[e["agent"]] = f"{provider}/{llm_gateway._settings(e['agent'], provider)[2]}"
            except llm_gateway.LLMUnavailable:
                out[e["agent"]] = provider
    return out


def run_product(sid, mode, graph, instructions=""):
    """The real pipeline, in-process. instructions: an operator instruction (agent mode). -> (state, events, wall ms)."""
    loader.load_scenario(sid)  # the baseline graph (idempotent)
    state = new_state(sid, graph["src"], mode, instructions)
    sink = [] if mode == "fast" else None  # agent mode writes its events to Redis
    redis = client.get_db().connection
    started = time.perf_counter()
    try:
        with hold_lock(state["id"]) as lock:
            auth = {"incident_id": state["id"], "lock_token": lock.token}
            try:
                run_incident(state, mode, lock_token=lock.token, sink=sink)
            finally:
                for suffix in ("", "-split"):
                    write_tools.delete_sandbox(sandbox_name(sid, state["id"], suffix), **auth)
        wall = round((time.perf_counter() - started) * 1000)
        evs = sink if mode == "fast" else [e for _, e in event_store.read(state["id"], count=1000)]
    finally:
        redis.delete(f"inc:{state['id']}", f"inc:{state['id']}:events", f"quota:inc:{state['id']}:sims", *redis.keys(f"reach:*:sbx:{state['id']}*"))
    return state, evs, wall


def row_product(sid, mode, graph, key, opt):
    state, evs, wall = run_product(sid, mode, graph)
    tin, tout = _tokens(evs)
    base = {"scenario": sid, "method": mode, "provider": "code" if mode == "fast" else None, "models": {} if mode == "fast" else _models(evs),
            "latency_ms": wall, "tokens_in": tin, "tokens_out": tout, "degraded": state["degraded"], "status": state["status"], "note": ""}
    if state["status"] == "FAILED" or state["findings"] is None:
        return {**base, "ran": False, "note": f"FAILED: {state.get('failure_detail')}"}
    findings = state["findings"]
    result = {"found_jewels": findings["reachable_jewels"], "found_paths": [p["eids"] for p in findings["paths"]],
              "cut_eids": list((state["action"] or {}).get("applied_eids", [])), "verdict_safe": state["status"] in ("DONE", "DONE_NO_RISK")}
    return {**base, "ran": True, "cut_eids": result["cut_eids"], "metrics": metrics.score(graph, key, result, opt)}


def row_baseline(sid, method, graph, key, opt):
    G = metrics.nx_graph(graph)
    started = time.perf_counter()
    cut = baselines.BASELINES[method](G, graph["src"])
    ms = round((time.perf_counter() - started) * 1000)
    result = {"found_jewels": None, "found_paths": None, "cut_eids": cut, "verdict_safe": None}
    return {"scenario": sid, "method": method, "provider": "code", "models": {}, "latency_ms": ms, "tokens_in": 0, "tokens_out": 0,
            "degraded": False, "status": "ok", "note": "", "ran": True, "cut_eids": cut, "metrics": metrics.score(graph, key, result, opt)}


def row_llm_json(sid, graph, key, opt, retries=(30, 60)):
    for wait in (0, *retries):  # back off when a free tier says 429
        time.sleep(wait)
        got = baseline_json_llm.ask(graph)
        if not got["not_run"] or "RateLimit" not in got["not_run"]:
            break
    if got["not_run"]:
        quota = any(w in got["not_run"] for w in ("RateLimit", "quota", "429", "unavailable"))
        return {"scenario": sid, "method": "llm_json", "provider": None, "models": {}, "latency_ms": 0, "tokens_in": 0, "tokens_out": 0, "degraded": False,
                "status": "not run", "ran": False, "note": ("not run (quota): " if quota else "not run: ") + got["not_run"][:300]}
    answer = got["answer"]
    result = {"found_jewels": answer.reachable_jewels if answer else [], "found_paths": answer.paths if answer else [],
              "cut_eids": answer.cut_eids if answer else [], "verdict_safe": None}
    return {"scenario": sid, "method": "llm_json", "provider": got["provider"], "models": {"baseline": f"{got['provider']}/{got['model']}"},
            "latency_ms": got["latency_ms"], "tokens_in": got["tokens_in"], "tokens_out": got["tokens_out"], "degraded": False,
            "status": "failed answer" if got["failed"] else "ok", "ran": True, "cached": got["cached"], "note": got["failed"],
            "cut_eids": result["cut_eids"], "metrics": metrics.score(graph, key, result, opt)}


def run_one(sid, method, truth):
    graph, key, opt = truth["graph"], truth["key"], truth["optimum"]["optimum"]
    if method in ("fast", "agent"):
        return row_product(sid, method, graph, key, opt)
    if method == "llm_json":
        return row_llm_json(sid, graph, key, opt)
    return row_baseline(sid, method, graph, key, opt)


def load_truth(sid):
    graph = build(sid)
    key = json.loads((ROOT / "data" / "answer_keys" / f"{sid}.json").read_text(encoding="utf-8"))
    return {"graph": graph, "key": key, "optimum": bruteforce.optimum(graph, key)}


# --- the table and the paragraph (written by code from the numbers) ---

def _pct(x):
    return "–" if x is None else f"{round(x * 100)}%"


def cell(row):
    """One table cell: the numbers of one method on one scenario."""
    if row is None:
        return "not run"
    if not row["ran"]:
        return row["note"].split(":")[0] if row["status"] == "not run" else row["note"][:60]
    m = row["metrics"]
    gap = "–" if m["fix_gap"] is None else f"{m['fix_gap']:+d}" if m["fix_gap"] else "0"
    parts = [f"cut {m['cut_size']} (gap {gap})", f"residual {m['residual_flow']}", f"broken {len(m['broken_needs'])}",
             f"collateral {m['collateral']['count']} (w {m['collateral']['weighted']})"]
    if m["blast_recall"] is not None:
        parts.append(f"jewels {_pct(m['blast_recall'])}, paths R {_pct(m['path_recall'])} / P {_pct(m['path_precision'])}")
    if m["verdict_correct"] is not None:
        parts.append("verdict ✔" if m["verdict_correct"] else "verdict ✘")
    if row["status"] == "failed answer":
        parts.append(f"FAILED ANSWER ({row['note']})")
    if row.get("degraded"):
        parts.append("†degraded")
    return " · ".join(parts)


def build_table(rows, scenarios, methods):
    """[{scenario, <method label>: cell, ...}] for the UI and the Markdown file."""
    by = {(r["scenario"], r["method"]): r for r in rows}
    return [{"scenario": sid, **{LABELS[m]: cell(by.get((sid, m))) for m in methods}} for sid in scenarios]


def summarize(rows, scenarios, methods, keys):
    """The paragraph under the table, from the numbers only. Says what happened, whatever it is."""
    by = {(r["scenario"], r["method"]): r for r in rows if r["ran"]}
    safe_sids = [s for s in scenarios if keys[s]["safe_fix_exists"]]
    unsafe = [s for s in scenarios if s not in safe_sids]
    lines = [f"{len(scenarios)} scenarios, {len(safe_sids)} of which have a safe fix ({', '.join(unsafe) or 'none'} {'has' if len(unsafe) == 1 else 'have'} none). "
             "A fix is safe when no crown jewel stays reachable and no business need breaks, measured by networkx."]
    stats = {}
    for m in methods:
        ran = [by[(s, m)] for s in safe_sids if (s, m) in by]
        if not ran:
            continue
        ok = sum(r["metrics"]["safe_fix"] for r in ran)
        gaps = [r["metrics"]["fix_gap"] for r in ran if r["metrics"]["fix_gap"] is not None and r["metrics"]["fixes_attack"]]
        broken = sum(len(r["metrics"]["broken_needs"]) for r in ran)
        coll = sum(r["metrics"]["collateral"]["weighted"] for r in ran)
        stats[m] = {"n": len(ran), "ok": ok, "broken": broken, "coll": coll, "gap": (sum(gaps) / len(gaps)) if gaps else None}
        lines.append(f"{LABELS[m]}: a safe fix in {ok} of {len(ran)} scenarios, {broken} business need(s) broken in total, weighted collateral {coll}"
                     + (f", mean fix gap {stats[m]['gap']:.2f}" if gaps else "") + ".")
    ours = [m for m in ("fast", "agent") if m in stats]
    others = [m for m in stats if m not in ours]
    if ours and others:
        best = min(ours, key=lambda m: (-stats[m]["ok"], stats[m]["coll"]))
        tied = [m for m in others if stats[m]["ok"] == stats[best]["ok"] and stats[m]["coll"] <= stats[best]["coll"] and stats[m]["broken"] == stats[best]["broken"]]
        worse = [LABELS[m] for m in others if m not in tied]
        lines.append(f"Compared with {LABELS[best]}: " + (f"{', '.join(LABELS[m] for m in tied)} matched it on safe fixes, broken needs and collateral over these scenarios" if tied else "no baseline matched it on all three")
                     + (f"; {', '.join(worse)} did worse on at least one of them." if worse else "."))
    verdicts = [(m, [by[(s, m)]["metrics"]["verdict_correct"] for s in scenarios if (s, m) in by]) for m in ours]
    for m, v in verdicts:
        if v:
            lines.append(f"{LABELS[m]} gave the right safe / NEEDS_HUMAN verdict in {sum(bool(x) for x in v)} of {len(v)} scenarios.")
    if any(by.get((s, m), {}).get("degraded") for s in scenarios for m in ours):
        lines.append("† a step ran in degraded mode (the LLM was unavailable and the code fallback answered).")
    missing = [f"{s}/{LABELS[m]}" for s in scenarios for m in methods if (s, m) not in by]
    if missing:
        total = len(scenarios) * len(methods)
        lines.append("Not run: " + (", ".join(missing) if len(missing) <= 8 else f"{len(missing)} of {total} cells have not been run yet") + ".")
    return " ".join(lines)


def markdown(doc):
    meta = doc["meta"]
    cols = ["scenario", *[LABELS[m] for m in meta["methods"]]]
    table = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    table += ["| " + " | ".join(str(r[c]).replace("|", "/") for c in cols) + " |" for r in doc["table"]]
    models = "; ".join(sorted(meta["models"])) or "none"
    return (f"# BreachPath evaluation\n\nRun {meta['date']} · commit {meta['commit']} · models: {models}\n\n" + "\n".join(table)
            + "\n\ngap = cut size − brute-force optimum (0 = perfect, – = no optimum); residual = max flow left after the cut (0 = blocked); "
              "broken = business needs that lose their path; collateral = (other user, resource) pairs that lose access, w = weighted "
              "(recent 3, stale 1); jewels = blast-radius recall; R / P = path recall@20 / precision (– = nothing to measure); "
              "all measured by networkx, independent of the product.\n\n## What the table shows\n\n" + doc["summary"] + "\n")


# --- run and save ---

def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):  # no git binary (the toolbox image): read .git directly
        try:
            head = (ROOT / ".git" / "HEAD").read_text().strip()
            if head.startswith("ref: "):
                ref = head[5:]
                path = ROOT / ".git" / ref
                if path.exists():
                    return path.read_text().strip()[:7]
                for line in (ROOT / ".git" / "packed-refs").read_text().splitlines():
                    if line.endswith(" " + ref):
                        return line.split()[0][:7]
            return head[:7]
        except OSError:
            return "unknown"


def models_of(rows):
    return {f"{agent}: {pm}" if agent != "baseline" else f"B-LLM-JSON: {pm}" for r in rows for agent, pm in (r.get("models") or {}).items()}


def document(rows, scenarios, methods, keys, pause):
    meta = {"date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "commit": git_commit(), "scenarios": scenarios, "methods": methods,
            "models": sorted(models_of(rows)), "pause_s": pause}
    doc = {"meta": meta, "rows": rows, "table": build_table(rows, scenarios, methods), "summary": summarize(rows, scenarios, methods, keys)}
    doc["markdown"] = markdown(doc)
    return doc


def save(doc):
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    (RESULTS / "latest.md").write_text(doc["markdown"], encoding="utf-8")
    try:
        client.get_db().connection.set(REDIS_KEY, json.dumps(doc, ensure_ascii=False))
    except Exception as e:  # Redis is a convenience: the file is the source
        print(f"(Redis not updated: {type(e).__name__})")


def throttled(pause):
    """Sleep `pause` seconds after every real (not cached) LLM call: the free tiers allow few tokens per minute."""
    real = llm_gateway.chat

    def chat(*args, **kwargs):
        result = real(*args, **kwargs)
        if not result.cached and pause:
            time.sleep(pause)
        return result
    llm_gateway.chat = chat
    return lambda: setattr(llm_gateway, "chat", real)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenarios", nargs="+", default=SCENARIOS)
    ap.add_argument("--methods", default=",".join(METHODS))
    ap.add_argument("--pause", type=float, default=8.0, help="seconds to wait after every real LLM call")
    ap.add_argument("--fresh", action="store_true", help="ignore the saved results instead of merging into them")
    args = ap.parse_args(argv)
    methods = [m for m in args.methods.split(",") if m]
    bad = [m for m in methods if m not in METHODS] + [s for s in args.scenarios if s not in SCENARIOS]
    if bad:
        raise SystemExit(f"unknown: {bad}")

    old = []
    if not args.fresh and (RESULTS / "latest.json").exists():
        old = json.loads((RESULTS / "latest.json").read_text(encoding="utf-8"))["rows"]
    rows = {(r["scenario"], r["method"]): r for r in old}
    truths = {sid: load_truth(sid) for sid in SCENARIOS}
    keys = {sid: t["key"] for sid, t in truths.items()}
    for sid in args.scenarios:
        o = truths[sid]["optimum"]
        print(f"{sid}: optimum {o['optimum']} {o['note']}".rstrip())
    restore = throttled(args.pause)
    try:
        for sid in args.scenarios:
            for method in methods:
                print(f"  {sid} {LABELS[method]} ...", end=" ", flush=True)
                try:
                    row = run_one(sid, method, truths[sid])
                except Exception as e:  # one failing cell never loses the others
                    row = {"scenario": sid, "method": method, "provider": None, "models": {}, "latency_ms": 0, "tokens_in": 0, "tokens_out": 0, "degraded": False,
                           "status": "failed", "ran": False, "note": f"ERROR {type(e).__name__}: {str(e)[:200]}"}
                rows[(sid, method)] = row
                print(cell(row)[:110], f"[{row['latency_ms']} ms]")
                save(document(list(rows.values()), SCENARIOS, METHODS, keys, args.pause))  # after every cell: nothing is lost
    finally:
        restore()
    print((RESULTS / "latest.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()

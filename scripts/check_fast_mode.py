"""Fast mode vs the answer keys, through the real API (P5.5).

Run: python -m scripts.check_fast_mode [s1 s2 ...]   (default: all 8)
For every scenario: load the baseline, POST /v1/analyze, and judge only the HTTP response against data/answer_keys/{sid}.json.
"""
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from core import config
from core.graph.client import get_db
from data import scenarios

SCENARIOS = [f"s{i}" for i in range(1, 9)]
ANSWER_DIR = Path(__file__).resolve().parent.parent / "data" / "answer_keys"
# the scenario stories (LLD section 10): sid -> (what to read in the response, expected value)
EXPECT = {
    "s5": ("suggestion_type", "split_node"),
    "s8": ("collateral_count", 0),
}


def independent_check(sid, key, cut_eids):
    """No FalkorDB, no networkx: cut the planted company in memory and walk it with data.scenarios.reaches()."""
    failures = []
    graph = scenarios.build(sid)
    graph["edges"] = [e for e in graph["edges"] if e["eid"] not in set(cut_eids)]
    seen = scenarios.reaches(graph, key["src"])
    jewels = [n["id"] for n in graph["nodes"] if n["props"].get("crown_jewel")]
    if [j for j in jewels if j in seen]:
        failures.append(f"independent: {key['src']} still reaches {[j for j in jewels if j in seen]} after the cut")
    for need in graph["needs"]:
        if need["valid"] and need["resource_id"] not in scenarios.reaches(graph, need["principal_id"]):
            failures.append(f"independent: need {need['id']} is broken by the cut")
    return failures


def check(client, sid):
    """Returns (row, failures). row is a dict for the table, failures a list of strings (empty = pass)."""
    key = json.loads((ANSWER_DIR / f"{sid}.json").read_text(encoding="utf-8"))
    failures = []
    row = {"sid": sid, "status": "-", "jewels": "-", "paths": "-", "cut": "-", "proof": "-", "needs": "-", "collateral": "-", "ms": "-"}

    loaded = client.post(f"/v1/scenarios/{sid}/load")
    resp = client.post("/v1/analyze", json={"tenant": sid, "src": key["src"]}) if loaded.status_code == 200 else loaded
    if resp.status_code != 200:
        return row, [f"HTTP {resp.status_code}: {resp.text[:300]}"]
    body = resp.json()
    findings, plan, verdict, action = body["findings"], body["plan"], body["verdict"], body["action"]
    if findings is None or plan is None:  # the pipeline stopped early: say why instead of crashing
        row["status"] = body["status"]
        return row, [f"status {body['status']}, no {'findings' if findings is None else 'plan'}: {body['failure_detail']}"]
    true_paths = {tuple(p) for p in key["true_paths"]}
    found = [tuple(p["eids"]) for p in findings["paths"]]
    k = config.TOP_K_PATHS
    safe = key["safe_fix_exists"]

    # findings: jewels and paths
    if findings["reachable_jewels"] != key["reachable_jewels"]:
        failures.append(f"reachable_jewels {findings['reachable_jewels']} != {key['reachable_jewels']}")
    if findings["beyond_hops_jewels"] != key["long_path_jewels"]:
        failures.append(f"beyond_hops_jewels {findings['beyond_hops_jewels']} != {key['long_path_jewels']}")
    if not set(found) <= true_paths:
        failures.append(f"paths not in the answer key (precision): {sorted(set(found) - true_paths)}")
    recall = len(set(found) & true_paths) / min(len(true_paths), k) if true_paths else 1.0
    if recall != 1:
        failures.append(f"recall@{k} is {recall:.2f}: found {len(set(found) & true_paths)} of {min(len(true_paths), k)}")
    if findings["truncated"] != (len(true_paths) > k):
        failures.append(f"truncated is {findings['truncated']}, expected {len(true_paths) > k}")

    # plan, and what happened to it
    if plan["safe"] != safe:
        failures.append(f"plan.safe is {plan['safe']}, expected {safe}")
    if safe:
        if body["status"] != "DONE":
            failures.append(f"status {body['status']}, expected DONE ({body['failure_detail']})")
        if verdict is None:
            failures.append("no verdict")
        else:
            if not verdict["pass"]:
                failures.append(f"verdict failed: {verdict['failure_detail']}")
            if verdict["attack_max_flow"] != 0:
                failures.append(f"attack_max_flow {verdict['attack_max_flow']}, expected 0")
            if verdict["bfs_reached_jewels"]:
                failures.append(f"bfs still reaches {verdict['bfs_reached_jewels']}")
            broken = [n["need_id"] for n in verdict["business"] if not n["ok"]]
            if broken:
                failures.append(f"business needs broken: {broken}")
        if not len(plan["cut_eids"]) == key["min_cut_size"] == plan["max_flow"]:
            failures.append(f"cut size {len(plan['cut_eids'])}, min_cut_size {key['min_cut_size']}, max_flow {plan['max_flow']}")
        if action["applied_eids"] != sorted(plan["cut_eids"]):
            failures.append(f"applied {action['applied_eids']} != cut {sorted(plan['cut_eids'])}")
        failures += independent_check(sid, key, plan["cut_eids"])
    else:
        if body["status"] != "NEEDS_HUMAN":
            failures.append(f"status {body['status']}, expected NEEDS_HUMAN")
        if plan["cut_eids"]:
            failures.append(f"cut_eids {plan['cut_eids']}, expected none")
        if verdict is not None:
            failures.append("verdict should be None when nothing was applied")
        if (plan["suggestion"] or {}).get("proven") is not True:
            failures.append(f"suggestion not proven: {plan['suggestion']}")

    # scenario story
    if sid in EXPECT:
        what, want = EXPECT[sid]
        got = (plan["suggestion"] or {}).get("suggestion_type") if what == "suggestion_type" else (
            ((plan["simulation"] or {}).get("collateral") or {}).get("count"))
        if got != want:
            failures.append(f"{sid} story: {what} is {got}, expected {want}")
    if not body["report"]["markdown"].strip():
        failures.append("report.markdown is empty")

    needs = verdict["business"] if verdict else []
    row.update(status=body["status"], jewels=len(findings["reachable_jewels"]),
               paths=f"{len(set(found) & true_paths)}/{len(true_paths)}",
               cut=f"{len(plan['cut_eids'])}/{key['min_cut_size']}",
               proof=f"{plan['max_flow']:g}, {len(verdict['bfs_reached_jewels'])}" if verdict else f"{plan['max_flow']:g}, -",
               needs=f"{sum(n['ok'] for n in needs)}/{len(needs)}" if verdict else "-",
               collateral=verdict["collateral"]["count"] if verdict else "-", ms=body["timings_ms"]["total"])
    return row, failures


def main(ids):
    client = TestClient(app)
    cols = ["sid", "status", "jewels", "paths", "cut", "proof", "needs", "collateral", "ms"]
    print("sid | status | jewels | paths found/true | cut size/min | maxflow, bfs | needs ok | collateral | ms | result")
    failed = 0
    for sid in ids:
        row, failures = check(client, sid)
        print(" | ".join(str(row[c]) for c in cols), "|", "FAIL" if failures else "PASS")
        for f in failures:
            print(f"    - {f}")
        failed += bool(failures)
    leftovers = [g for g in get_db().list_graphs() if ":sbx:" in g]
    if leftovers:
        print(f"LEFTOVER sandbox graphs: {leftovers}")
    print(f"{len(ids) - failed}/{len(ids)} scenarios pass")
    return 1 if failed or leftovers else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or SCENARIOS))

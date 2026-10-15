"""Scenario files (docs/03_LLD.md section 10.3): load, validate, plant into the background company."""
import argparse
import json
from collections import Counter, deque
from pathlib import Path

import yaml

from data.generator import build_background, validate

SCENARIO_DIR = Path(__file__).parent / "scenarios"
OUT_DIR = Path("data/generated")
ANSWER_DIR = Path("data/answer_keys")
REQUIRED = ["id", "title", "src", "nodes", "edges", "background_links", "needs", "expect"]
EDGE_KEYS = ["eid", "type", "src", "dst", "last_used_days"]


def load_scenario(path):
    with open(path, encoding="utf-8") as f:
        sc = yaml.safe_load(f)
    for key in REQUIRED:
        if key not in sc:
            raise ValueError(f"{path}: missing key '{key}'")
    for n in sc["nodes"]:
        for key in ("id", "label", "name"):
            if key not in n:
                raise ValueError(f"{sc['id']}: node {n} lacks '{key}'")
    prefix = f"e:{sc['id']}:"
    for e in sc["edges"] + sc["background_links"]:
        for key in EDGE_KEYS:
            if key not in e:
                raise ValueError(f"{sc['id']}: edge {e} lacks '{key}'")
        if not e["eid"].startswith(prefix):
            raise ValueError(f"{sc['id']}: eid {e['eid']} must start with '{prefix}'")
    src = [n for n in sc["nodes"] if n["id"] == sc["src"]]
    if not src or src[0]["label"] != "User":
        raise ValueError(f"{sc['id']}: src {sc['src']} is not a planted User node")
    return sc


def _edge(e):
    out = {k: e[k] for k in EDGE_KEYS}
    if "technique" in e:
        out["technique"] = e["technique"]
    return out


def plant(background, scenario):
    sid = scenario["id"]
    background_ids = {n["id"] for n in background["nodes"]}
    planted_ids = {n["id"] for n in scenario["nodes"]}
    clash = planted_ids & background_ids
    if clash:
        raise ValueError(f"{sid}: planted node ids already in the background: {sorted(clash)}")
    for e in scenario["edges"]:
        if e["src"] not in planted_ids or e["dst"] not in planted_ids:
            raise ValueError(f"{sid}: edge {e['eid']} must connect planted nodes")
    for e in scenario["background_links"]:
        if e["src"] not in planted_ids or e["dst"] not in background_ids:
            raise ValueError(f"{sid}: link {e['eid']} must go from a planted node to a background node")
    for need in scenario["needs"]:
        for key in ("principal_id", "resource_id"):
            if need[key] not in planted_ids | background_ids:
                raise ValueError(f"{sid}: need {need['id']} has unknown {key} {need[key]}")

    nodes = [{"id": n["id"], "label": n["label"], "props": {k: v for k, v in n.items() if k != "label"}}
             for n in scenario["nodes"]]
    combined = {
        "seed": background["seed"],
        "scenario": sid,
        "title": scenario["title"],
        "src": scenario["src"],
        "nodes": background["nodes"] + nodes,
        "edges": background["edges"] + [_edge(e) for e in scenario["edges"] + scenario["background_links"]],
        "needs": scenario["needs"],
        "expect": scenario["expect"],
    }
    validate(combined)
    return combined


def reaches(combined, src):
    """Node ids reachable from src, following edge direction (a cheap sanity check, not the ground truth)."""
    out = {}
    for e in combined["edges"]:
        out.setdefault(e["src"], []).append(e["dst"])
    seen, queue = {src}, deque([src])
    while queue:
        for nxt in out.get(queue.popleft(), []):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def build(sid):
    """The planted company with its business needs, built in memory (the API's product path: no truth, no JSON files)."""
    from data.needs import build_needs  # imported here: needs.py imports reaches() from this module

    combined = plant(build_background(), load_scenario(SCENARIO_DIR / f"{sid}.yaml"))
    combined["needs"] = build_needs(combined)
    return combined


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", nargs="*", help="scenario ids, e.g. s1 s2 s3")
    ap.add_argument("--all", action="store_true", help="every data/scenarios/*.yaml")
    args = ap.parse_args()
    ids = sorted(p.stem for p in SCENARIO_DIR.glob("*.yaml")) if args.all else args.ids
    from data.needs import build_needs  # imported here: needs.py imports reaches() from this module
    from data.truth import check_expect, compute_truth  # networkx: generator and eval only, never the API

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for sid in ids:
        sc = load_scenario(SCENARIO_DIR / f"{sid}.yaml")
        combined = plant(build_background(), sc)
        found = reaches(combined, sc["src"])
        missing = [j for j in sc["expect"]["reachable_jewels"] if j not in found]
        if missing:
            raise ValueError(f"{sid}: expected jewels not reachable from {sc['src']}: {missing}")
        combined["needs"] = build_needs(combined)
        truth = compute_truth(combined)
        check_expect(truth, sc["expect"])
        ANSWER_DIR.mkdir(parents=True, exist_ok=True)
        with open(ANSWER_DIR / f"{sid}.json", "w", newline="\n") as f:
            json.dump(truth, f, sort_keys=True, indent=1)
            f.write("\n")
        with open(OUT_DIR / f"{sid}.json", "w", newline="\n") as f:
            json.dump(combined, f, sort_keys=True, indent=1)
            f.write("\n")
        jewels = sorted(n["id"] for n in combined["nodes"] if n["props"].get("crown_jewel") and n["id"] in found)
        print(f"{sid}: {len(combined['nodes'])} nodes, {len(combined['edges'])} edges, reachable jewels {jewels}")
        by_source = Counter(n["source"] for n in combined["needs"])
        invalid = sum(not n["valid"] for n in combined["needs"])
        print(f"{sid}: {len(combined['needs'])} needs ({by_source['explicit']} explicit, {by_source['usage']} usage, {invalid} invalid)")
        print(f"{sid}: answer key ok: {len(truth['true_paths'])} paths, cut {truth['min_cut_size']}, "
              f"safe {truth['safe_fix_exists']}, {len(truth['protected_eids'])} protected edges")


if __name__ == "__main__":
    main()

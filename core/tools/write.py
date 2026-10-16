"""Write tools (docs/03_LLD.md section 5). Sandbox only, never the baseline.

Every tool takes incident_id and lock_token as keyword arguments and runs guards.check_write first.
In agent mode the worker injects these two values; the LLM never supplies them (and the Executor is code anyway).
"""
import time

from core import config
from core.graph import mincut, queries
from core.graph.client import delete_graph, get_db, get_graph, query, ro_query
from core.graph.schema import baseline_name, sandbox_name
from core.tools import collateral, guards, verify
from core.tools.common import BadInput, NotFound, tool

MAX_FIX_EIDS = 50


def _name(make, *args):
    try:
        return make(*args)
    except ValueError as e:  # the schema helpers reject ids outside a-z, 0-9 and '-'
        raise BadInput(str(e))


def _existing(sandbox):
    if sandbox not in get_db().list_graphs():
        raise NotFound(f"sandbox {sandbox} does not exist")


def _check_eids(eids):
    if not (isinstance(eids, list) and eids and all(isinstance(e, str) for e in eids)):
        raise BadInput("eids must be a non-empty list of strings")
    if len(set(eids)) > MAX_FIX_EIDS:
        raise BadInput(f"at most {MAX_FIX_EIDS} eids per call")


def _apply(graph, eids):
    """Classify each eid (missing / protected / deletable), delete the deletable ones.
    Returns (deleted, missing, refused_protected, stale_days_total) with sorted lists."""
    missing, refused, deletable, stale_total = [], [], [], 0
    for eid, found, protected, stale_days in ro_query(graph, queries.EDGE_STATE, {"eids": sorted(set(eids))}):
        if not found:
            missing.append(eid)
        elif protected:
            refused.append(eid)
        else:
            deletable.append(eid)
            stale_total += stale_days
    if deletable:
        query(graph, queries.APPLY_FIX, {"eids": deletable})
    return sorted(deletable), sorted(missing), sorted(refused), stale_total


def min_cut_avoiding(sandbox, src, avoid, *, incident_id, lock_token, llm=False):
    """compute_min_cut under an operator constraint: the edges in `avoid` get cap = PROTECT_CAP on a throw-away copy (-sim-n, guarded, counts toward
    the simulation quota, deleted in finally), then the candidates are computed as usual. Returns the min-cut dict + "avoided" (+ why it is
    impossible when no safe cut is left)."""
    guards.check_write(sandbox, incident_id, lock_token)
    _check_eids(avoid)
    _existing(sandbox)
    avoid = sorted(set(avoid))
    unknown = [eid for eid, found, _, _ in ro_query(sandbox, queries.EDGE_STATE, {"eids": avoid}) if not found]
    if unknown:
        raise BadInput(f"unknown eid(s) in avoid_eids: {unknown}")
    plain = mincut.compute_min_cut(sandbox, src)  # without the constraint: tells whether the constraint is what makes a safe cut impossible
    n = guards.check_quota(incident_id, "simulation", llm=llm)
    sim = _name(sandbox_name, sandbox.split(":")[1], incident_id, f"-sim-{n}")
    guards.check_write(sim, incident_id, lock_token)
    get_graph(sandbox).copy(sim)
    try:
        protect_cap = ro_query(sim, queries.PROTECT_CAP)[0][0]
        query(sim, queries.AVOID_EDGES, {"eids": avoid, "protect_cap": protect_cap})
        cut = mincut.compute_min_cut(sim, src)
    finally:
        delete_graph(sim)
    cut["avoided"] = avoid
    if not cut["safe"]:
        # the avoided edges on the flow that cannot be cut safely (the cut edges and their witness paths)
        blocking = {e for c in cut["candidates"] for e in c["eids"]} | {e for c in cut["candidates"] for w in c["witness"].values() for e in w}
        in_cut = sorted(set(avoid) & blocking)
        cut["impossible_because"] = in_cut if plain["safe"] else []
        cut["note"] = (f"no safe cut keeps {', '.join(in_cut)} untouched: every cut would have to remove it" if plain["safe"] and in_cut
                       else "no safe cut exists" + (" even without the constraint" if not plain["safe"] else " under the constraint"))
    return cut


@tool
def create_sandbox(tenant, *, incident_id, lock_token):
    sandbox = _name(sandbox_name, tenant, incident_id)
    guards.check_write(sandbox, incident_id, lock_token)
    graphs = get_db().list_graphs()
    baseline = _name(baseline_name, tenant)
    if baseline not in graphs:
        raise NotFound(f"baseline {baseline} does not exist")
    if sandbox in graphs:  # idempotent: no copy, no quota use, keep the cached snapshot
        return {"sandbox": sandbox, "reused": True}
    guards.check_quota(incident_id, "sandbox")
    get_graph(baseline).copy(sandbox)
    redis = get_db().connection
    redis.zadd("sbx:expiry", {sandbox: time.time() + config.SANDBOX_TTL_HOURS * 3600})
    for key in redis.scan_iter(match=f"reach:{sandbox}:*"):  # a fresh copy must never use an old snapshot
        redis.delete(key)
    return {"sandbox": sandbox, "reused": False}


@tool
def delete_sandbox(sandbox, *, incident_id, lock_token):
    """Delete a sandbox, its expiry entry and its cached snapshots. Idempotent."""
    guards.check_write(sandbox, incident_id, lock_token)
    existed = sandbox in get_db().list_graphs()
    delete_graph(sandbox)
    redis = get_db().connection
    redis.zrem("sbx:expiry", sandbox)
    for key in redis.scan_iter(match=f"reach:{sandbox}:*"):
        redis.delete(key)
    return {"deleted": existed}


@tool
def protect_business_paths(sandbox, second_path_needs=None, *, incident_id, lock_token):
    """second_path_needs: need ids whose 2nd shortest path is protected too (the retry change "protect_second_path")."""
    guards.check_write(sandbox, incident_id, lock_token)
    _existing(sandbox)
    if second_path_needs and not (isinstance(second_path_needs, list) and all(isinstance(n, str) for n in second_path_needs)):
        raise BadInput("second_path_needs must be a list of need ids")
    protect_cap = ro_query(sandbox, queries.PROTECT_CAP)[0][0]
    rows = query(sandbox, queries.PROTECT_NEEDS, {"protect_cap": protect_cap})
    if second_path_needs:
        rows += query(sandbox, queries.PROTECT_SECOND_PATHS, {"protect_cap": protect_cap, "ids": second_path_needs})
    reached = {r[0] for r in rows}
    valid = [r[0] for r in ro_query(sandbox, queries.VALID_NEEDS)]
    return {"protected_eids": sorted({eid for r in rows for eid in r[1]}),
            "unreachable_needs": sorted(n for n in valid if n not in reached),  # SPpaths gives no row when there is no path
            "protect_cap": protect_cap}


@tool
def apply_fix(sandbox, eids, reason, *, incident_id, lock_token):
    guards.check_write(sandbox, incident_id, lock_token)
    _check_eids(eids)
    if not (isinstance(reason, str) and reason.strip()):
        raise BadInput("reason must be a non-empty string")
    _existing(sandbox)
    deleted, missing, refused, _ = _apply(sandbox, eids)
    return {"deleted": deleted, "missing": missing, "refused_protected": refused, "reason": reason}


@tool
def simulate_cut(sandbox, src, eids, *, incident_id, lock_token, llm=False):
    """Try a cut on a throw-away copy of the prepared sandbox and report what it does. The sandbox is not changed.
    llm=True: the call came from the LLM (MCP wrapper): it may not use the simulations reserved for the code (guards.check_quota)."""
    guards.check_write(sandbox, incident_id, lock_token)
    _check_eids(eids)
    _existing(sandbox)
    if not ro_query(sandbox, queries.NODE_INFO, {"id": src}):
        raise NotFound(f"node {src} not found in {sandbox}")
    n = guards.check_quota(incident_id, "simulation", llm=llm)
    sim = _name(sandbox_name, sandbox.split(":")[1], incident_id, f"-sim-{n}")
    guards.check_write(sim, incident_id, lock_token)

    before = collateral.snapshot(sandbox, src)
    min_size = int(verify.attack_status(sandbox, src)["max_flow"])
    get_graph(sandbox).copy(sim)
    try:
        deleted, missing, refused, stale_total = _apply(sim, eids)
        attack = verify.attack_status(sim, src)
        needs = verify.needs_status(sim)
        after_reaches = collateral.reach_sets(sim, src)
    finally:
        delete_graph(sim)
    broken = sorted(x["need_id"] for x in needs if not x["ok"])
    return {"blocks_attack": attack["max_flow"] == 0 and not attack["reached_jewels"],
            "needs_ok": not broken, "broken_needs": broken,
            "collateral": collateral.collateral(before, after_reaches),
            "size": len(deleted), "min_size": min_size, "stale_days_total": stale_total,
            "refused_protected": refused, "missing": missing}

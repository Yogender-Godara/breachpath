"""Collateral for simulate_cut (docs/03_LLD.md section 4.2): who else loses access, weighted by recent use.

"Recent" = the (principal, resource) pair was reachable before the fix through edges that were ALL used within
COLLATERAL_RECENT_DAYS. Weight 3 if recent, else 1.
"""
import json
from collections import deque

from core import config
from core.graph import queries
from core.graph.client import get_db, ro_query

RECENT_WEIGHT, STALE_WEIGHT = 3, 1


def recent_pairs(edges, reaches, days):
    """Pure. edges: ALL_EDGES rows (eid, u, v, cap, kind, stale_days). reaches: {principal: [resources]}.
    Returns the sorted [principal, resource] pairs reachable over edges with stale_days <= days."""
    out = {}
    for _, u, v, _, _, stale in edges:
        if stale <= days:
            out.setdefault(u, []).append(v)
    pairs = []
    for principal, resources in reaches.items():
        seen, queue = {principal}, deque([principal])
        while queue:
            for nxt in out.get(queue.popleft(), []):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        pairs += [[principal, r] for r in resources if r in seen]
    return sorted(pairs)


def reach_sets(graph, src):
    """Q12: {principal: [resources]} for every user except src."""
    return {principal: reaches for principal, reaches in ro_query(graph, queries.REACH_SETS, {"src": src})}


def before(graph, src):
    """The 'before' picture of a graph, not cached: who reaches what, and which of those pairs are recent."""
    reaches = reach_sets(graph, src)
    return {"reaches": reaches,
            "recent": recent_pairs(ro_query(graph, queries.ALL_EDGES), reaches, config.COLLATERAL_RECENT_DAYS)}


def snapshot(sandbox, src):
    """before() of a prepared sandbox, cached in Redis (create_sandbox deletes it when it re-copies)."""
    redis, key = get_db().connection, f"reach:{sandbox}:{src}"
    cached = redis.get(key)
    if cached:
        return json.loads(cached)
    snap = before(sandbox, src)
    redis.set(key, json.dumps(snap), ex=config.SANDBOX_TTL_HOURS * 3600)
    return snap


def collateral(before, after_reaches):
    """Pairs in before but not in after, and their weight."""
    recent = {tuple(p) for p in before["recent"]}
    lost = sorted([p, r] for p, rs in before["reaches"].items() for r in rs if r not in after_reaches.get(p, ()))
    weighted = sum(RECENT_WEIGHT if (p, r) in recent else STALE_WEIGHT for p, r in lost)
    return {"count": len(lost), "weighted": weighted, "pairs": lost[:20]}

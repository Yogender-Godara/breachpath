"""No safe fix: a proven split suggestion (docs/03_LLD.md section 4.1).

When the min-cut is not safe, the attack path is made only of business-protected edges. We look for a node D on that
path where the business use and the attack part ways, split D into D and D-biz in a -split sandbox, and PROVE the result
(min-cut safe, attack gone, every need ok). The pipeline calls this tool; the LLM never builds it.
"""
import time

from core import config
from core.graph import mincut, queries
from core.graph.client import delete_graph, get_db, get_graph, query, ro_query
from core.graph.schema import NODE_LABELS, sandbox_name
from core.tools import guards, verify
from core.tools.common import BadInput, tool
from core.tools.write import _apply, _existing, _name

CONFIG_LABELS = ("Role", "Policy", "Group")  # only config objects can be split
MAX_TRIES = 3


def divergence_points(witness, edges, labels):
    """Pure. Where do the business use and the attack part ways along one witness path?

    witness: eids in path order. edges: {eid: (src, dst, needs)}. labels: {node id: label}.
    For each pair (e_in, e_out) around D = e_in's end node:
        business = needs(e_in) - needs(e_out); conflict = needs(e_in) & needs(e_out)
    D is a divergence point when business is non-empty and conflict is empty. It is splittable if it is a
    Role, Policy or Group. Returns the points in path order.
    """
    points = []
    for e_in, e_out in zip(witness, witness[1:]):
        needs_in, needs_out = set(edges[e_in][2]), set(edges[e_out][2])
        business, conflict = needs_in - needs_out, needs_in & needs_out
        if business and not conflict:
            node = edges[e_in][1]
            points.append({"node": node, "e_in": e_in, "e_out": e_out, "business": sorted(business),
                           "splittable": labels[node] in CONFIG_LABELS})
    return points


def _suggestion(kind, proven, text, node=None, new_node=None, moved=(), copied=(), then_remove=(), proof=None):
    """proof: what the split sandbox measured (max flow, BFS, needs) when proven by a split; None otherwise."""
    return {"suggestion_type": kind, "split_node": node, "new_node": new_node, "moved_edges": list(moved),
            "copied_edges": list(copied), "then_remove": list(then_remove), "proven": proven, "text": text, "proof": proof}


def _do_split(graph, point, labels):
    """Create D-biz, move e_in to it, copy the business out-edges to it, reset the protected caps."""
    node, e_in, business = point["node"], point["e_in"], point["business"]
    new_node = f"{node}-biz"
    _, props = ro_query(graph, queries.NODE_INFO, {"id": node})[0]
    query(graph, queries.CREATE_NODE[labels[node]], {"props": {**props, "id": new_node, "name": f"{props['name']}-biz"}})

    # move e_in: delete it and create it again (same eid and properties) pointing at D-biz
    _, _, src, _, e_props = ro_query(graph, queries.EDGE_INFO, {"eid": e_in})[0]
    query(graph, queries.DELETE_EDGE, {"eid": e_in})
    query(graph, queries.CREATE_EDGE, {"src": src, "dst": new_node, "props": dict(e_props)})

    # copy D's out-edges that serve the business needs
    copied = []
    for dst, out_props in ro_query(graph, queries.NODE_OUT_PROPS, {"id": node}):
        served = sorted(set(out_props.get("needs", [])) & set(business))
        if served:
            new_eid = out_props["eid"] + "-biz"
            query(graph, queries.CREATE_EDGE, {"src": new_node, "dst": dst, "props": {**out_props, "eid": new_eid,
                                                                                       "protected": True, "needs": served}})
            copied.append(new_eid)
    protect_cap = ro_query(graph, queries.PROTECT_CAP)[0][0]  # the edge count grew, so the protected caps must follow
    query(graph, queries.PROTECT_RESET, {"protect_cap": protect_cap})
    return new_node, [e_in], sorted(copied)


def _describe(graph, copied):
    """'CAN_SSH -> server:x' for each copied edge."""
    rows = [ro_query(graph, queries.EDGE_INFO, {"eid": eid})[0] for eid in copied]
    return ", ".join(f"{kind} -> {dst}" for _, kind, _, dst, _ in rows)


@tool
def split_suggestion(sandbox, src, *, incident_id, lock_token):
    guards.check_write(sandbox, incident_id, lock_token)
    _existing(sandbox)
    cut = mincut.compute_min_cut(sandbox, src)
    if cut["safe"]:
        raise BadInput("a safe cut exists, no split is needed")

    # CASE 0: the compromised identity legitimately needs a jewel. No permission change can help.
    reached = set(verify.attack_status(sandbox, src)["reached_jewels"])
    for need_id, principal, resource in ro_query(sandbox, queries.VALID_NEEDS_FULL):
        if principal == src and resource in reached:
            return _suggestion("rotate_credentials", True,
                               f"Unavoidable: {src} legitimately needs {resource} ({need_id}). Rotate or disable its credentials.")

    labels = {i: next(l for l in ls if l in NODE_LABELS) for i, ls in ro_query(sandbox, queries.NODE_LABEL_ROWS)}
    edges = {eid: (s, d, needs) for eid, s, d, needs in ro_query(sandbox, queries.EDGES_WITH_NEEDS)}
    protected = set(cut["protected_in_way"])
    witnesses = []
    for candidate in cut["candidates"]:
        for eid, witness in candidate["witness"].items():
            if eid in protected and witness not in witnesses:
                witnesses.append(witness)
    points = [p for w in witnesses for p in divergence_points(w, edges, labels)]
    splittable, seen = [], set()
    for p in points:
        if p["splittable"] and (p["node"], p["e_in"]) not in seen:
            seen.add((p["node"], p["e_in"]))
            splittable.append(p)

    split = _name(sandbox_name, sandbox.split(":")[1], incident_id, "-split")
    proven = None
    try:
        for point in splittable[:MAX_TRIES]:
            guards.check_write(split, incident_id, lock_token)
            delete_graph(split)  # reset: every try starts from the prepared sandbox
            guards.check_quota(incident_id, "sandbox")
            get_graph(sandbox).copy(split)
            get_db().connection.zadd("sbx:expiry", {split: time.time() + config.SANDBOX_TTL_HOURS * 3600})
            new_node, moved, copied = _do_split(split, point, labels)
            after = mincut.compute_min_cut(split, src)
            if not after["safe"]:
                continue
            then_remove = after["candidates"][0]["eids"]
            if then_remove:
                _apply(split, then_remove)
            attack = verify.attack_status(split, src)
            business = verify.needs_status(split)
            passed, _ = verify.judge(attack["max_flow"], attack["reached_jewels"], business)
            if passed:
                needs = ro_query(sandbox, queries.NEEDS_BY_IDS, {"ids": point["business"]})
                who = ", ".join(sorted({n[1] for n in needs}))
                text = (f"Split {point['node']} into {point['node']} and {new_node}. {who} keeps {new_node} "
                        f"(only {_describe(split, copied)}). "
                        f"Then remove: {', '.join(then_remove) if then_remove else 'nothing'}. "
                        f"Verified: maxFlow = 0, all needs ok.")
                proof = {"max_flow_after_split": attack["max_flow"], "bfs_reached_jewels": attack["reached_jewels"],
                         "needs_checked": len(business), "needs_ok": all(n["ok"] for n in business)}
                proven = _suggestion("split_node", True, text, point["node"], new_node, moved, copied, then_remove, proof)
                return proven  # the finally below keeps the -split sandbox
    finally:
        if proven is None:
            delete_graph(split)  # only a proven split keeps its -split sandbox (the UI shows it; the TTL cleans it)

    stuck = next((p for p in points if not p["splittable"]), None)
    if stuck:
        return _suggestion("manual", False, f"{stuck['node']} cannot be split (it is not a role, policy or group). "
                                            f"Rotate credentials / isolate host for {stuck['node']}.", stuck["node"])
    return _suggestion("manual", False, "No split could be proven. Manual review needed.")

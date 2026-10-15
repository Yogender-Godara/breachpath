"""The Verifier (docs/03_LLD.md section 6.6): proof helpers shared with simulate_cut, and the verify_fix tool."""
from core.graph import queries
from core.graph.client import get_db, ro_query
from core.graph.schema import baseline_of, is_sandbox
from core.tools import collateral
from core.tools.common import BadInput, NotFound, tool


def reached_jewels(graph, src):
    """Crown jewels a full BFS from src reaches (Q9). algo.bfs gives NO row when src has no outgoing edge: that means none."""
    rows = ro_query(graph, queries.REACH_BFS, {"src": src})
    return sorted(rows[0][0]) if rows else []


def attack_status(graph, src):
    """How much attack is left from src: max flow (Q6) and the jewels a full BFS reaches (Q9)."""
    if not ro_query(graph, queries.JEWELS):
        return {"max_flow": 0.0, "reached_jewels": []}
    max_flow = ro_query(graph, queries.MAX_FLOW, {"src": src})[0][2]
    return {"max_flow": max_flow, "reached_jewels": reached_jewels(graph, src)}


def needs_status(graph):
    """[{need_id, ok}] for every valid need (Q11). SPpaths gives no row when there is no path, so a missing row = not ok."""
    ok = dict(ro_query(graph, queries.NEEDS_CHECK))
    return [{"need_id": r[0], "ok": ok.get(r[0], False)} for r in ro_query(graph, queries.VALID_NEEDS)]


def judge(max_flow, reached, business):
    """Pure. Returns (pass, failure_detail). failure_detail is text for humans (the retry policy reads the verdict fields, not this string):
        "proof mismatch: max_flow <A>, bfs reached <B>"   (the two proofs disagree: a bug)
        "attack not blocked: max_flow <A>, reached <B>"
        "need <id> broken"  or  "needs <id1>, <id2> broken"
    """
    if (max_flow == 0) != (not reached):
        return False, f"proof mismatch: max_flow {max_flow}, bfs reached {reached}"
    if max_flow != 0:
        return False, f"attack not blocked: max_flow {max_flow}, reached {reached}"
    broken = [n["need_id"] for n in business if not n["ok"]]
    if broken:
        return False, f"need {broken[0]} broken" if len(broken) == 1 else f"needs {', '.join(broken)} broken"
    return True, None


@tool
def verify_fix(sandbox, src):
    if not is_sandbox(sandbox):
        raise BadInput(f"{sandbox!r} is not a sandbox graph")
    graphs = get_db().list_graphs()
    baseline = baseline_of(sandbox)
    if sandbox not in graphs or baseline not in graphs:
        raise NotFound(f"sandbox {sandbox} or its baseline {baseline} does not exist")
    if not ro_query(sandbox, queries.NODE_INFO, {"id": src}):
        raise NotFound(f"node {src} not found in {sandbox}")

    attack = attack_status(sandbox, src)  # A and B: two independent proofs
    business = needs_status(sandbox)  # C
    # D: the baseline is the exact "before" (a fresh sandbox = baseline + cap/protected properties), never stale
    lost = collateral.collateral(collateral.before(baseline, src), collateral.reach_sets(sandbox, src))
    passed, detail = judge(attack["max_flow"], attack["reached_jewels"], business)
    return {"pass": passed, "attack_max_flow": attack["max_flow"], "bfs_reached_jewels": attack["reached_jewels"],
            "business": business, "collateral": lost,
            "already_broken": [r[0] for r in ro_query(sandbox, queries.INVALID_NEEDS)],  # E: listed, ignored for pass
            "failure_detail": detail}

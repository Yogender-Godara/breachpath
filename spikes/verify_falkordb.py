"""P1.3: observe FalkorDB behaviour (B1-B7 + E1-E3). Observes only; prints a Markdown table."""
import os
from falkordb import FalkorDB

db = FalkorDB(
    host=os.environ["FALKORDB_HOST"],
    port=int(os.environ["FALKORDB_PORT"]),
    password=os.environ["FALKORDB_PASSWORD"],
)


def load(g, edges, rel, extra=""):
    """edges: (src, dst, props). Nodes are (:N {name}); `extra` is a Cypher suffix, e.g. a SET."""
    for s, d, p in edges:
        g.query(f"MERGE (a:N {{name:$s}}) MERGE (b:N {{name:$d}}) CREATE (a)-[r:{rel}]->(b) SET r += $p",
                {"s": s, "d": d, "p": p})
    if extra:
        g.query(extra)


def rows(g, q, params=None):
    return g.query(q, params).result_set


AD = "MATCH (a:N {name:'A'}), (d:N {name:'D'}) "
FLOW = "CALL algo.maxFlow({sourceNodes:[a], targetNodes:[d], relationshipTypes:['PIPE'], capacityProperty:'cap'}) "
PIPES = [("A", "B", 3), ("A", "C", 2), ("B", "D", 2), ("C", "D", 3), ("B", "C", 1)]
PIPE_EDGES = [(s, d, {"cap": c, "eid": f"e{i + 1}"}) for i, (s, d, c) in enumerate(PIPES)]
SP = "{sourceNode:s, targetNode:t, relTypes:['R'], %s weightProp:'w', pathCount:2, maxLen:8}"
R_EDGES = [(s, d, {"w": 1, "eid": f"r{i}"}) for i, (s, d) in enumerate([("A", "B"), ("B", "D"), ("A", "C"), ("C", "D")])]
ST = "MATCH (s:N {name:'%s'}), (t:N {name:'%s'}) "


def b1(g):
    load(g, PIPE_EDGES, "PIPE")
    r = rows(g, AD + FLOW + "YIELD edges, edgeFlows, maxFlow RETURN maxFlow")
    return ("PASS" if r and r[0][0] == 5 else "FAIL"), f"rows={r}, expected maxFlow=5"


def b1b(g):
    load(g, [("D", "A", {"cap": 1})], "PIPE")
    r = rows(g, AD + FLOW + "YIELD maxFlow RETURN maxFlow")
    return ("PASS" if not r or r[0][0] == 0 else "FAIL"), f"rows={r} ({'no row' if not r else 'row'})"


def b2a(g):
    load(g, PIPE_EDGES, "PIPE")
    r = rows(g, AD + FLOW + "YIELD edges, edgeFlows RETURN [e IN edges | e.eid], edgeFlows, "
             "[e IN edges | e.cap], [e IN edges | startNode(e).name + endNode(e).name]")
    eids, flows, caps, ends = r[0]
    ok = all(e is not None for e in eids) and len(eids) == len(flows) and all(f <= c for f, c in zip(flows, caps))
    return ("PASS" if ok else "FAIL"), f"eids={eids} flows={flows} caps={caps} ends={ends} (flow<=cap per position: {ok})"


def b2b(g):
    load(g, [("A", "B", {"cap": 1}), ("C", "D", {"cap": 1})], "PIPE")
    r = rows(g, AD + FLOW + "YIELD maxFlow RETURN maxFlow")
    return "INFO", f"rows={r} ({'NO row' if not r else 'row with maxFlow'})"


def b2c(g):
    load(g, [("A", "B", {"cap": 1}), ("A", "C", {"cap": 1})], "PIPE", "MATCH (n) WHERE n.name IN ['B','C'] SET n:Sink")
    r = rows(g, "MATCH (s {name:'A'}) MATCH (t:Sink) WITH s, collect(t) AS ts "
             "CALL algo.maxFlow({sourceNodes:[s], targetNodes:ts, relationshipTypes:['PIPE'], capacityProperty:'cap'}) "
             "YIELD maxFlow RETURN maxFlow")
    return ("PASS" if r and r[0][0] == 2 else "FAIL"), f"rows={r}, expected 2 (also covers B7)"


def b3(g):
    load(g, R_EDGES, "R")
    out = []
    ret = "YIELD path, pathWeight RETURN pathWeight, [r IN relationships(path) | r.eid]"
    try:
        r = rows(g, ST % ("A", "D") + "CALL algo.SPpaths(" + SP % "relDirection:'outgoing'," + ") " + ret)
        out.append(f"a) rows={r}")
        status = "PASS" if len(r) == 2 and all(x[0] == 2 for x in r) else "FAIL"
    except Exception as e:
        out.append(f"a) ERROR {e}")
        status = "FAIL"
        try:
            r = rows(g, ST % ("A", "D") + "CALL algo.SPpaths(" + SP % "" + ") " + ret)
            out.append(f"b) without relDirection OK rows={r}")
        except Exception as e2:
            out.append(f"b) without relDirection ERROR {e2}")
    try:
        r = rows(g, ST % ("D", "A") + "CALL algo.SPpaths(" + SP % "relDirection:'outgoing'," + ") " + ret)
        out.append(f"c) no-path rows={r}")
    except Exception as e:
        out.append(f"c) no-path ERROR {e}")
    return status, "; ".join(out)


def b4(g):
    load(g, [("A", "B", {}), ("C", "A", {})], "R")
    out = []
    for arg in ("null", "'R'"):
        r = rows(g, f"MATCH (a:N {{name:'A'}}) CALL algo.bfs(a, 5, {arg}) YIELD nodes RETURN [n IN nodes | n.name]")
        out.append(f"bfs(a,5,{arg})={r}")
    names = r[0][0] if r else []
    return "INFO", f"{'; '.join(out)}; follows direction (no C): {'C' not in names}"


def b5(g):
    load(g, [("A", "B", {}), ("B", "C", {})], "R")
    cp = None
    try:
        try:
            cp = g.copy("vf_b5_copy")
            how = "g.copy()"
        except AttributeError:
            db.connection.execute_command("GRAPH.COPY", "vf_b5", "vf_b5_copy")
            cp, how = db.select_graph("vf_b5_copy"), "GRAPH.COPY"
        cnt = "MATCH (n) RETURN count(n)"
        ecnt = "MATCH ()-[r]->() RETURN count(r)"
        same = (rows(g, cnt), rows(g, ecnt)) == (rows(cp, cnt), rows(cp, ecnt))
        cp.query("MATCH ()-[r]->() WITH r LIMIT 1 DELETE r")
        src_ok = rows(g, ecnt)[0][0] == 2 and rows(cp, ecnt)[0][0] == 1
        try:
            g.copy("vf_b5_copy")
            again = "second copy onto existing: no error"
        except Exception as e:
            again = f"second copy onto existing: ERROR {e}"
        return ("PASS" if same and src_ok else "FAIL"), f"{how}; counts match={same}; source unchanged after copy delete={src_ok}; {again}"
    finally:
        if cp is not None:
            db.select_graph("vf_b5_copy").delete()


def b6(g):
    load(g, [("A", "B", {"eid": "x"})], "R")
    g.query("CREATE INDEX FOR ()-[r:R]-() ON (r.eid)")
    r = rows(g, "CALL db.indexes()")
    ok = any("eid" in str(x) and "RELATIONSHIP" in str(x).upper() for x in r)
    return ("PASS" if ok else "FAIL"), f"rows={r}"


def e1(g):
    g.query("CREATE (:N {name:'A'})-[:T1]->(:N {name:'B'})-[:T2]->(:N {name:'C'})-[:T1]->(:N {name:'D'})")
    r = rows(g, "MATCH p=(a {name:'A'})-[:T1|T2*1..6]->(d {name:'D'}) RETURN length(p), [r IN relationships(p) | type(r)]")
    return ("PASS" if r and r[0][0] == 3 else "FAIL"), f"rows={r}, expected length 3"


def e2(g):
    load(g, R_EDGES, "R")
    g.query(ST % ("A", "D") + "CALL algo.SPpaths({sourceNode:s, targetNode:t, relTypes:['R'], relDirection:'outgoing', "
            "pathCount:1, maxLen:8}) YIELD path UNWIND relationships(path) AS r SET r.cap = 99")
    caps = [x[0] for x in rows(g, "MATCH ()-[r]->() RETURN r.cap")]
    return ("PASS" if caps.count(99) == 2 else "FAIL"), f"r.cap values={caps}, expected exactly 2 x 99 (one path)"


def e3(g):
    load(g, [("A", "B", {}), ("B", "C", {})], "R")
    r = rows(g, "CALL algo.betweenness({relationshipTypes:['R'], samplingSize:10, samplingSeed:42}) "
             "YIELD node, score RETURN node.name, score")
    sc = {x[0]: x[1] for x in r}
    return ("PASS" if sc.get("B", 0) > max(sc.get("A", 0), sc.get("C", 0)) else "FAIL"), f"scores={sc}"


CHECKS = [("B1", b1), ("B1b", b1b), ("B2a", b2a), ("B2b", b2b), ("B2c", b2c), ("B3", b3),
          ("B4", b4), ("B5", b5), ("B6", b6), ("E1", e1), ("E2", e2), ("E3", e3)]

results = []
for cid, fn in CHECKS:
    g = db.select_graph("vf_" + cid.lower())
    try:
        results.append((cid, *fn(g)))
    except Exception as e:
        results.append((cid, "FAIL", f"ERROR {type(e).__name__}: {e}"))
    finally:
        try:
            g.delete()
        except Exception:
            pass

print("| Check | Result | Detail |\n|---|---|---|")
for cid, status, detail in results:
    print(f"| {cid} | {status} | {str(detail).replace('|', '/').replace(chr(10), ' ')} |")

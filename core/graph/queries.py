"""Cypher strings only (docs/03_LLD.md section 3, Q1-Q12). No execution code: callers pass the params.

The edge type (every edge is :ATTACK, see schema.py), the hop bounds and the BFS depth are written into the strings once, here, from constants.
Everything else is a $param.
"""
from core.config import MAX_HOPS
from core.graph.schema import ATTACK_PATTERN, EDGE_TYPE

BFS_DEPTH = 50  # larger than any real path, so algo.bfs is complete


def _fmt(cypher):
    # str.replace, not str.format: Cypher map literals use { } too
    return (cypher.replace("{T}", ATTACK_PATTERN).replace("{TYPES}", f"['{EDGE_TYPE}']")
            .replace("{HOPS}", str(MAX_HOPS)).replace("{DEPTH}", str(BFS_DEPTH)))


# --- Reads ---

# Q1: jewels within MAX_HOPS of $src
BLAST_RADIUS = _fmt("""
MATCH (s:Asset {id: $src})-[:{T}*1..{HOPS}]->(t:Database {crown_jewel: true})
RETURN DISTINCT t.id AS id, t.name AS name
""")

# Q2: attack paths, shortest first, top $k
ATTACK_PATHS = _fmt("""
MATCH p = (s:Asset {id: $src})-[:{T}*1..{HOPS}]->(t:Database {crown_jewel: true})
RETURN t.id AS target,
       [n IN nodes(p) | n.id]          AS node_ids,
       [r IN relationships(p) | r.eid] AS eids,
       length(p)                       AS hops
ORDER BY hops ASC
LIMIT $k
""")

# Q3: $k shortest paths from $src to $target (no path = no rows)
SHORTEST_PATHS = _fmt("""
MATCH (s:Asset {id: $src}), (t:Asset {id: $target})
CALL algo.SPpaths({sourceNode: s, targetNode: t, relTypes: {TYPES},
                   relDirection: 'outgoing', weightProp: 'w',
                   pathCount: $k, maxLen: 8})
YIELD path, pathWeight
RETURN [r IN relationships(path) | r.eid] AS eids, pathWeight AS hops
""")

# Jewels: every crown-jewel id (the sinks for Q6 and the min-cut)
JEWELS = _fmt("""
MATCH (t:Database {crown_jewel: true}) RETURN t.id AS id ORDER BY id
""")

# Q4: how many attack paths run through each edge (incident-specific choke points)
EDGE_FREQUENCY = _fmt("""
MATCH p = (s:Asset {id: $src})-[:{T}*1..{HOPS}]->(t:Database {crown_jewel: true})
WITH p LIMIT $max_paths
UNWIND relationships(p) AS r
RETURN r.eid AS eid, r.kind AS kind, count(*) AS paths_through
ORDER BY paths_through DESC
LIMIT $top
""")

# Q5: betweenness of every node (global hint only; set $n >= node count for an exact result)
BETWEENNESS = _fmt("""
CALL algo.betweenness({relationshipTypes: {TYPES}, samplingSize: $n, samplingSeed: 42})
YIELD node, score
RETURN node.id AS id, labels(node) AS labels, score
ORDER BY score DESC
LIMIT $top
""")

# Q6: max flow from $src to every crown jewel (maxFlow is a float; edge order is not creation order)
MAX_FLOW = _fmt("""
MATCH (s:Asset {id: $src})
MATCH (t:Database {crown_jewel: true})
WITH s, collect(t) AS targets
CALL algo.maxFlow({sourceNodes: [s], targetNodes: targets,
                   relationshipTypes: {TYPES}, capacityProperty: 'cap'})
YIELD edges, edgeFlows, maxFlow
RETURN [e IN edges | e.eid] AS flow_eids, edgeFlows, maxFlow
""")

# Q7: every attack edge, for the residual graph
ALL_EDGES = _fmt("""
MATCH (a)-[r:{T}]->(b)
RETURN r.eid AS eid, a.id AS u, b.id AS v, r.cap AS cap, r.kind AS kind,
       r.last_used_days AS stale_days
""")

# Q8 step 1: PROTECT_CAP = edge count + 1
PROTECT_CAP = _fmt("""
MATCH ()-[r:{T}]->() RETURN count(r) + 1 AS protect_cap
""")

# Q9: every jewel reachable from $src at any depth (every edge is an attack edge, so null = all types)
REACH_BFS = _fmt("""
MATCH (s:Asset {id: $src})
CALL algo.bfs(s, {DEPTH}, null)
YIELD nodes
RETURN [n IN nodes WHERE n:Database AND n.crown_jewel = true | n.id] AS reached_jewels
""")

# Q11: need check, one row per need that still has a path (a missing need id = broken)
NEEDS_CHECK = _fmt("""
MATCH (n:BusinessNeed)
WITH n
MATCH (a:Asset {id: n.principal_id}), (b:Asset {id: n.resource_id})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: {TYPES},
                   relDirection: 'outgoing', weightProp: 'w', pathCount: 1})
YIELD path
RETURN n.id AS need_id, count(path) > 0 AS ok
""")

# Q12: what every principal except $src can reach (run before and after a fix to find collateral)
REACH_SETS = _fmt("""
MATCH (u:User) WHERE u.id <> $src
CALL algo.bfs(u, {DEPTH}, null)
YIELD nodes
RETURN u.id AS principal,
       [n IN nodes WHERE n:Server OR n:Database | n.id] AS reaches
""")

# --- Writes: sandbox only, never the baseline ---

# Q8 step 2: for every valid need, raise one shortest path to $protect_cap and mark it protected
PROTECT_NEEDS = _fmt("""
MATCH (n:BusinessNeed {valid: true})
WITH n
MATCH (a:Asset {id: n.principal_id}), (b:Asset {id: n.resource_id})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: {TYPES},
                   relDirection: 'outgoing', weightProp: 'w', pathCount: 1})
YIELD path
UNWIND relationships(path) AS r
SET r.cap = $protect_cap, r.protected = true
RETURN n.id AS need_id, collect(r.eid) AS protected_eids
""")

# Q10: delete the edges in $eids, skipping protected ones (idempotent: a missing edge matches nothing)
APPLY_FIX = _fmt("""
UNWIND $eids AS eid
MATCH ()-[r:{T}]->()
WHERE r.eid = eid AND coalesce(r.protected, false) = false
DELETE r
RETURN count(*) AS deleted
""")

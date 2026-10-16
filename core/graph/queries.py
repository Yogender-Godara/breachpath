"""Cypher strings only (docs/03_LLD.md section 3, Q1-Q12). No execution code: callers pass the params.

The edge type (every edge is :ATTACK, see schema.py), the hop bounds and the BFS depth are written into the strings once, here, from constants.
Everything else is a $param.
"""
from core.config import MAX_HOPS
from core.graph.schema import ATTACK_PATTERN, EDGE_TYPE, NODE_LABELS, SHARED_LABEL

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

# Q2: attack paths, shortest first, top $k ($target null = every jewel)
ATTACK_PATHS = _fmt("""
MATCH p = (s:Asset {id: $src})-[:{T}*1..{HOPS}]->(t:Database {crown_jewel: true})
WHERE $target IS NULL OR t.id = $target
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

# Small helpers for the read tools: one node, its edges, one edge, counts, valid needs
NODE_INFO = """
MATCH (n:Asset {id: $id}) RETURN labels(n) AS labels, properties(n) AS props
"""
NODE_OUT = _fmt("""
MATCH (:Asset {id: $id})-[r:{T}]->(m) RETURN r.eid AS eid, r.kind AS kind, m.id AS other ORDER BY eid
""")
NODE_IN = _fmt("""
MATCH (m)-[r:{T}]->(:Asset {id: $id}) RETURN r.eid AS eid, r.kind AS kind, m.id AS other ORDER BY eid
""")
EDGE_INFO = _fmt("""
MATCH (a)-[r:{T} {eid: $eid}]->(b) RETURN r.eid AS eid, r.kind AS kind, a.id AS src, b.id AS dst, properties(r) AS props
""")
NODE_COUNT = """
MATCH (n:Asset) RETURN count(n)
"""
INVALID_NEEDS = """
MATCH (n:BusinessNeed {valid: false}) RETURN n.id AS need_id ORDER BY need_id
"""
VALID_NEEDS = """
MATCH (n:BusinessNeed {valid: true}) RETURN n.id AS need_id ORDER BY need_id
"""

# Split simulation reads (LLD 4.1): every edge with its endpoints and needs, every node's label, valid needs in full
EDGES_WITH_NEEDS = _fmt("""
MATCH (a)-[r:{T}]->(b) RETURN r.eid AS eid, a.id AS src, b.id AS dst, coalesce(r.needs, []) AS needs
""")
NODE_LABEL_ROWS = """
MATCH (n:Asset) RETURN n.id AS id, labels(n) AS labels
"""
VALID_NEEDS_FULL = """
MATCH (n:BusinessNeed {valid: true}) RETURN n.id AS need_id, n.principal_id AS principal, n.resource_id AS resource ORDER BY need_id
"""
NEEDS_BY_IDS = """
MATCH (n:BusinessNeed) WHERE n.id IN $ids RETURN n.id AS need_id, n.principal_id AS principal, n.resource_id AS resource ORDER BY need_id
"""
NODE_OUT_PROPS = _fmt("""
MATCH (:Asset {id: $id})-[r:{T}]->(m) RETURN m.id AS other, properties(r) AS props ORDER BY r.eid
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

# The users of a graph (the UI's src dropdown: a src must be a User)
USER_IDS = """
MATCH (u:User) RETURN u.id AS id ORDER BY u.id
"""

# Node rows for the UI sub-graph (GET /incidents/{id}/graph)
GRAPH_NODES = """
MATCH (n:Asset) RETURN n.id AS id, labels(n) AS labels, n.name AS name, coalesce(n.crown_jewel, false) AS crown_jewel
"""

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

# Q8 step 2: for every valid need, raise one shortest path to $protect_cap, mark it protected and record the need in r.needs
PROTECT_NEEDS = _fmt("""
MATCH (n:BusinessNeed {valid: true})
WITH n
MATCH (a:Asset {id: n.principal_id}), (b:Asset {id: n.resource_id})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: {TYPES},
                   relDirection: 'outgoing', weightProp: 'w', pathCount: 1})
YIELD path
UNWIND relationships(path) AS r
SET r.cap = $protect_cap, r.protected = true,
    r.needs = CASE WHEN n.id IN coalesce(r.needs, []) THEN coalesce(r.needs, []) ELSE coalesce(r.needs, []) + [n.id] END
RETURN n.id AS need_id, collect(r.eid) AS protected_eids
""")

# Q8 retry: also protect the 2 shortest paths of the needs in $ids (retry change "protect_second_path")
PROTECT_SECOND_PATHS = _fmt("""
MATCH (n:BusinessNeed {valid: true}) WHERE n.id IN $ids
WITH n
MATCH (a:Asset {id: n.principal_id}), (b:Asset {id: n.resource_id})
CALL algo.SPpaths({sourceNode: a, targetNode: b, relTypes: {TYPES},
                   relDirection: 'outgoing', weightProp: 'w', pathCount: 2})
YIELD path
UNWIND relationships(path) AS r
SET r.cap = $protect_cap, r.protected = true,
    r.needs = CASE WHEN n.id IN coalesce(r.needs, []) THEN coalesce(r.needs, []) ELSE coalesce(r.needs, []) + [n.id] END
RETURN n.id AS need_id, collect(DISTINCT r.eid) AS protected_eids
""")

# Q10 helper: for each eid in $eids, does the edge exist, is it protected, how stale is it? (one row per eid)
EDGE_STATE = _fmt("""
UNWIND $eids AS eid
OPTIONAL MATCH ()-[r:{T}]->()
WHERE r.eid = eid
RETURN eid, r.eid IS NOT NULL AS found, coalesce(r.protected, false) AS protected, r.last_used_days AS stale_days
""")

# Q10: delete the edges in $eids, skipping protected ones (idempotent: a missing edge matches nothing)
APPLY_FIX = _fmt("""
UNWIND $eids AS eid
MATCH ()-[r:{T}]->()
WHERE r.eid = eid AND coalesce(r.protected, false) = false
DELETE r
RETURN count(*) AS deleted
""")

# Split simulation writes (sandbox only): a node copy per label, edge create / delete, protected caps reset
CREATE_NODE = {label: f"CREATE (n:{SHARED_LABEL}:{label}) SET n += $props" for label in NODE_LABELS}
CREATE_EDGE = _fmt("""
MATCH (a:Asset {id: $src}), (b:Asset {id: $dst}) CREATE (a)-[r:{T}]->(b) SET r += $props
""")
DELETE_EDGE = _fmt("""
MATCH ()-[r:{T}]->() WHERE r.eid = $eid DELETE r
""")
PROTECT_RESET = _fmt("""
MATCH ()-[r:{T}]->() WHERE r.protected = true SET r.cap = $protect_cap RETURN count(r)
""")

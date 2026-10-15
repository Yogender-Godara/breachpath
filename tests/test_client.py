"""Needs a running FalkorDB (run inside the toolbox container); skipped when it is unreachable."""
from core.graph import client
from core.graph.schema import baseline_name

GRAPH = baseline_name("p21test")


def test_create_indexes_twice(falkordb):
    client.delete_graph(GRAPH)
    try:
        client.query(GRAPH, "CREATE (:Asset {id: 'user:x'})")  # the graph must exist before indexing
        client.create_indexes(GRAPH)
        client.create_indexes(GRAPH)  # second run must not fail

        rows = client.ro_query(GRAPH, "CALL db.indexes()")
        text = [str(r) for r in rows]
        assert any("Asset" in t and "'id'" in t for t in text)
        assert any("RELATIONSHIP" in t and "'eid'" in t for t in text)
    finally:
        client.delete_graph(GRAPH)
    assert GRAPH not in client.get_db().list_graphs()

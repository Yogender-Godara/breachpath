"""Shared test helpers (need a running FalkorDB; use them only from tests that take the falkordb fixture)."""
from contextlib import contextmanager

from core.graph import client, loader, queries
from core.graph.schema import baseline_name, sandbox_name

SCENARIOS = [f"s{i}" for i in range(1, 9)]
SAFE = [sid for sid in SCENARIOS if sid != "s5"]  # s5 has no safe cut: tests that assume one skip it


@contextmanager
def protected_copy(sid, tag):
    """Baseline copy with every valid need protected (cap = protect_cap), deleted afterwards."""
    loader.load_scenario(sid)
    sbx = sandbox_name(sid, tag)
    client.delete_graph(sbx)
    client.get_graph(baseline_name(sid)).copy(sbx)
    try:
        protect_cap = client.ro_query(sbx, queries.PROTECT_CAP)[0][0]
        client.query(sbx, queries.PROTECT_NEEDS, {"protect_cap": protect_cap})
        yield sbx
    finally:
        client.delete_graph(sbx)

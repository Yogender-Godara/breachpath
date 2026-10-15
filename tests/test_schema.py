import pytest

from core.graph import schema


def test_eight_edge_types_with_full_info():
    assert len(schema.EDGE_TYPES) == 8
    for info in schema.EDGE_TYPES.values():
        assert info["from"] and info["to"] and info["remediation"]
        assert set(info["from"] + info["to"]) <= set(schema.NODE_LABELS)


def test_every_edge_is_one_physical_type():
    assert schema.EDGE_TYPE == "ATTACK" and schema.ATTACK_PATTERN == "ATTACK"


def test_name_helpers():
    assert schema.baseline_name("s1") == "t:s1:baseline"
    assert schema.sandbox_name("s1", "abc-123") == "t:s1:sbx:abc-123"
    assert schema.sandbox_name("s1", "abc", "-split") == "t:s1:sbx:abc-split"


def test_baseline_of():
    assert schema.baseline_of(schema.sandbox_name("s2", "inc-1", "-sim-3")) == "t:s2:baseline"
    with pytest.raises(ValueError):
        schema.baseline_of("t:s2:baseline")


def test_is_sandbox():
    assert schema.is_sandbox(schema.sandbox_name("s1", "abc-123"))
    assert schema.is_sandbox("t:s1:sbx:fast-1a2b3c4d")
    assert not schema.is_sandbox(schema.baseline_name("s1"))
    assert not schema.is_sandbox("t:s1:sbx:")
    assert not schema.is_sandbox("x t:s1:sbx:abc")


@pytest.mark.parametrize("bad", ["S1", "s 1", "s:1", "", "s1/../x"])
def test_bad_ids_raise(bad):
    with pytest.raises(ValueError):
        schema.baseline_name(bad)
    with pytest.raises(ValueError):
        schema.sandbox_name("s1", bad)


def test_index_queries():
    node = [q for q in schema.INDEX_QUERIES if q.startswith("CREATE INDEX FOR (n:")]
    rel = [q for q in schema.INDEX_QUERIES if "-[r:" in q]
    assert len(node) == 3 and rel == ["CREATE INDEX FOR ()-[r:ATTACK]-() ON (r.eid)"]
    assert len(schema.INDEX_QUERIES) == 4


def test_sandbox_suffix_is_validated():
    from core.graph.schema import is_sandbox, sandbox_name
    import pytest
    assert is_sandbox(sandbox_name("s2", "inc-1", "-split"))
    with pytest.raises(ValueError):
        sandbox_name("s2", "inc-1", ":evil")

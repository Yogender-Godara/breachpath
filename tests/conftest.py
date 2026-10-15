import pytest

from core.graph import client


@pytest.fixture(scope="session")
def falkordb():
    """Skip the test (or module) when FalkorDB is unreachable, e.g. in the host venv."""
    try:
        client.get_db().list_graphs()
    except Exception as e:
        pytest.skip(f"FalkorDB unreachable: {e}")

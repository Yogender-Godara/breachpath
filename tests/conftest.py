import os

os.environ["JOBS_STREAM"] = "jobs:test"  # BEFORE any project import: the tests never touch the job stream a running worker reads ("jobs")

import pytest  # noqa: E402

from core.graph import client  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def generated_data():
    """data/generated is not in git: a fresh clone generates it here, so no manual `python -m data.scenarios --all` step is needed."""
    from data import scenarios
    if scenarios.generated_files_missing():
        scenarios.generate(scenarios.ALL_IDS)


@pytest.fixture(scope="session")
def falkordb():
    """Skip the test (or module) when FalkorDB is unreachable, e.g. in the host venv."""
    try:
        client.get_db().list_graphs()
    except Exception as e:
        pytest.skip(f"FalkorDB unreachable: {e}")

"""The job stream (docs/03_LLD.md section 8): the API adds an incident id, the worker consumes it from the group."""
import os

from redis.exceptions import ResponseError

# JOBS_STREAM: the tests use their own stream ("jobs:test", tests/conftest.py), so a running worker can never take a test's job
STREAM, GROUP = os.getenv("JOBS_STREAM", "jobs"), "workers"


def ensure_group(redis):
    """Create the consumer group (and the stream). The API calls it before XADD too, so a job added before the first worker start is not lost."""
    try:
        redis.xgroup_create(STREAM, GROUP, id="$", mkstream=True)
    except ResponseError as e:
        if "BUSYGROUP" not in str(e):  # the group already exists: fine
            raise

import time

import pytest

from core.graph import client
from core.tools import guards
from core.tools.common import GuardRefused
from orchestrator.lock import LockBusy, hold_lock

INC = "p51lock"
KEY = f"lock:inc:{INC}"
SBX = f"t:s2:sbx:{INC}"


def value(redis):
    raw = redis.get(KEY)
    return raw.decode() if isinstance(raw, bytes) else raw


@pytest.fixture
def redis(falkordb):
    r = client.get_db().connection
    r.delete(KEY)
    yield r
    r.delete(KEY)


def test_heartbeat_keeps_a_short_ttl_lock_alive(redis):
    with hold_lock(INC, ttl_ms=1000, heartbeat_s=0.25) as handle:
        time.sleep(3)  # three TTLs: without the heartbeat the key would be long gone
        assert value(redis) == handle.token and not handle.lost.is_set()
    assert redis.get(KEY) is None  # released on exit


def test_without_a_heartbeat_the_lock_expires(redis):
    with hold_lock(INC, ttl_ms=1000, heartbeat_s=10):
        time.sleep(1.5)
        assert redis.get(KEY) is None


def test_second_holder_gets_lock_busy_and_the_first_keeps_it(redis):
    with hold_lock(INC) as handle:
        with pytest.raises(LockBusy):
            with hold_lock(INC):
                pytest.fail("should not get in")
        assert value(redis) == handle.token  # the failed attempt did not touch it


def test_release_does_not_delete_a_lock_someone_else_now_holds(redis):
    with hold_lock(INC, ttl_ms=5000, heartbeat_s=0.1) as handle:
        redis.set(KEY, "someone-else", px=5000)
        assert handle.lost.wait(2)  # the next beat sees a different owner
    assert value(redis) == "someone-else"


def test_guard_passes_with_the_handle_token_while_held(redis):
    with hold_lock(INC) as handle:
        guards.check_write(SBX, INC, handle.token)
        with pytest.raises(GuardRefused):
            guards.check_write(SBX, INC, "wrong-token")
    with pytest.raises(GuardRefused):  # released: a missing lock is refused
        guards.check_write(SBX, INC, handle.token)

# first: the run's own temp directory, checked for leftovers when the session ends (tmpguard.py)
import tmpguard  # noqa: F401
from tmpguard import pytest_sessionfinish, pytest_terminal_summary  # noqa: F401

import faulthandler
import os

import pytest

# a test that hangs ends the run with every thread's stack after this long, instead of holding a CI runner until its
# 6-hour limit with nothing in the log
HANG_S = 600
_REAL_STDERR = None


def pytest_configure(config):
    # pytest captures stderr around each test; the dump goes to a copy of the real one (taken while capture is off)
    global _REAL_STDERR
    _REAL_STDERR = os.fdopen(os.dup(2), "w")


@pytest.fixture(autouse=True)
def _hang_guard():
    faulthandler.dump_traceback_later(HANG_S, exit=True, file=_REAL_STDERR)
    yield
    faulthandler.cancel_dump_traceback_later()

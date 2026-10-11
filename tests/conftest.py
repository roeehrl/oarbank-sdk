# first: the run's own temp directory, checked for leftovers when the session ends (tmpguard.py)
import tmpguard  # noqa: F401
from tmpguard import pytest_sessionfinish, pytest_terminal_summary  # noqa: F401

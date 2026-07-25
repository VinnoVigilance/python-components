"""
Global pytest configuration, loaded automatically before any test.

Its one job here is to put the project root on Python's import path so that
tests can do `from transforms.dateResolver import ...` no matter which folder
pytest is started from. Because this file lives at the project root, pytest
adds this directory to sys.path automatically -- but we make it explicit so
the behaviour is obvious to anyone reading the suite.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# --- slow tests are opt-in ---------------------------------------------------
# Tests marked `@pytest.mark.slow` (e.g. parsing the full 210-page DNFBP PDF)
# are skipped by default, including in CI, so the normal suite stays fast.
# Run them with:  pytest --runslow

def pytest_addoption(parser):
    parser.addoption(
        "--runslow",
        action="store_true",
        default=False,
        help="run tests marked as slow",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--runslow"):
        return

    skip_slow = pytest.mark.skip(reason="slow test; run with --runslow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)

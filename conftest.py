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

PROJECT_ROOT = Path(__file__).resolve().parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

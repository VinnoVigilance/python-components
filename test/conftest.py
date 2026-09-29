"""Shared fixtures for the whole test suite."""

import pytest

from repositories import jobStateRepository


@pytest.fixture(autouse=True)
def isolated_job_state(tmp_path, monkeypatch):
    """Keep hold / to-do JSON files in a temp folder, never in the real data/state/."""
    state_dir = tmp_path / "state"
    monkeypatch.setattr(jobStateRepository, "STATE_DIR", state_dir)
    return state_dir

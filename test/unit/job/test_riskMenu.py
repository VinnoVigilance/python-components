"""Verify interactive risk domain and mode selection."""

from unittest.mock import MagicMock

import pytest

from jobs import riskCategoryJob as job


@pytest.mark.parametrize("domain_pick, subject", [("1", "media"), ("2", "watchlist")])
@pytest.mark.parametrize("mode_pick, mode", [("1", "incremental"), ("2", "initial")])
def test_menu_selects_domain_before_mode(monkeypatch, domain_pick, subject, mode_pick, mode):
    from jobs import run as menu

    answers = iter([domain_pick, mode_pick])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    run = MagicMock(return_value={"status": "SUCCESS"})
    monkeypatch.setattr(job, "run_risk_job", run)
    assert menu._run_risk() == {"status": "SUCCESS"}
    run.assert_called_once_with(mode=mode, subject=subject)

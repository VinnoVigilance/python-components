"""Verify split delivery modules without executing database procedures."""

from contextlib import nullcontext
from datetime import date
from unittest.mock import MagicMock

import pytest

from services.watchlistDelivery import mediaDailyDeltaActionsGeneration as media
from services.watchlistDelivery import postIngestionSpokesDeltas as delivery
from services.watchlistDelivery import spokeSyncEngine as spokes
from services.watchlistDelivery import watchlistDailyDeltaActionsGeneration as watchlist


@pytest.mark.parametrize("last, latest", [(None, None), (date(2026, 10, 20), date(2026, 10, 20))])
def test_media_runs_when_watchlists_are_empty_or_current(monkeypatch, last, latest):
    monkeypatch.setattr(delivery, "get_watermark_dates", lambda: (
        last, latest, latest, date(2026, 10, 20), date(2026, 10, 22), date(2026, 10, 1)))
    generate = MagicMock()
    deploy = MagicMock()
    monkeypatch.setattr(delivery, "generate_media_delta_actions", generate)
    monkeypatch.setattr(delivery, "deploy_database_procedures", deploy)
    delivery.run_automated_pipeline()
    assert [call.kwargs["from_date"] for call in generate.call_args_list] == [
        date(2026, 10, 21), date(2026, 10, 22)]
    deploy.assert_not_called()


def test_media_is_attempted_before_watchlist_failure_is_propagated(monkeypatch):
    monkeypatch.setattr(delivery, "get_watermark_dates", lambda: (
        None, date(2026, 10, 20), date(2026, 10, 20),
        None, date(2026, 10, 20), date(2026, 10, 20)))
    monkeypatch.setattr(delivery, "deploy_database_procedures", MagicMock())
    monkeypatch.setattr(delivery, "generate_watchlist_delta_actions", MagicMock())
    monkeypatch.setattr(delivery, "execute_daily_sync", MagicMock(side_effect=RuntimeError("sync failed")))
    generate = MagicMock()
    monkeypatch.setattr(delivery, "generate_media_delta_actions", generate)
    with pytest.raises(RuntimeError, match="watchlist") as failure:
        delivery.run_automated_pipeline()
    assert str(failure.value.__cause__) == "sync failed"
    generate.assert_called_once_with(from_date=date(2026, 10, 20), to_date=date(2026, 10, 20))


def test_incomplete_spokes_prevent_view_refresh(monkeypatch):
    monkeypatch.setattr(delivery, "deploy_database_procedures", MagicMock())
    monkeypatch.setattr(delivery, "generate_watchlist_delta_actions", MagicMock())
    monkeypatch.setattr(delivery, "execute_daily_sync", MagicMock())
    monkeypatch.setattr(delivery, "check_spokes_finished", lambda _: False)
    refresh = MagicMock()
    monkeypatch.setattr(delivery, "_refresh_search_view", refresh)
    with pytest.raises(RuntimeError, match="incomplete"):
        delivery._run_watchlist_pipeline(None, date(2026, 10, 20), date(2026, 10, 20))
    refresh.assert_not_called()


@pytest.mark.parametrize("module, function, procedure", [
    (watchlist, "generate_watchlist_delta_actions", "generate_watchlist_daily_delta_actions"),
    (media, "generate_media_delta_actions", "generate_media_daily_delta_actions"),
])
@pytest.mark.parametrize("fails", [False, True])
def test_generators_call_correct_procedure_and_restore_pool_setting(monkeypatch, module, function, procedure, fails):
    connection = MagicMock()
    connection.autocommit = False
    cursor = connection.cursor.return_value.__enter__.return_value
    if fails:
        cursor.execute.side_effect = RuntimeError("database unavailable")
    monkeypatch.setattr(module, "get_db_connection", lambda: nullcontext(connection))
    generate = getattr(module, function)
    if fails:
        with pytest.raises(RuntimeError, match="database unavailable"):
            generate(date(2026, 10, 20), date(2026, 10, 21))
    else:
        generate(date(2026, 10, 20), date(2026, 10, 21))
        assert [call.args[1][0] for call in cursor.execute.call_args_list] == [
            date(2026, 10, 20), date(2026, 10, 21)]
    assert all(procedure in call.args[0] for call in cursor.execute.call_args_list)
    assert connection.autocommit is False


@pytest.mark.parametrize("function, args", [
    ("deploy_database_procedures", ()),
    ("execute_daily_sync", (date(2026, 10, 20),)),
    ("execute_full_rebuild", ()),
])
@pytest.mark.parametrize("fails", [False, True])
def test_spoke_wrappers_restore_pool_setting(monkeypatch, function, args, fails):
    connection = MagicMock()
    connection.autocommit = False
    if fails:
        connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("execution failed")
    monkeypatch.setattr(spokes, "get_db_connection", lambda: nullcontext(connection))
    monkeypatch.setattr(spokes, "generate_daily_sync_sql", lambda *args: "daily sql")
    monkeypatch.setattr(spokes, "generate_full_rebuild_sql", lambda *args: "rebuild sql")
    if fails:
        with pytest.raises(RuntimeError):
            getattr(spokes, function)(*args)
    else:
        getattr(spokes, function)(*args)
    assert connection.autocommit is False


@pytest.mark.parametrize("fails", [False, True])
def test_view_refresh_restores_pool_setting(monkeypatch, fails):
    connection = MagicMock()
    connection.autocommit = False
    if fails:
        connection.cursor.return_value.__enter__.return_value.execute.side_effect = RuntimeError("refresh failed")
    monkeypatch.setattr(delivery, "get_db_connection", lambda: nullcontext(connection))
    if fails:
        with pytest.raises(RuntimeError):
            delivery._refresh_search_view()
    else:
        delivery._refresh_search_view()
    assert connection.autocommit is False

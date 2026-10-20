"""Watchlist risk orchestration over core members and member deltas."""

from repositories.risk import watchlistRiskRepository
from services.risk.processing import run


def run_watchlist_risk(**options):
    return run("watchlist", watchlistRiskRepository, 802156604, **options)

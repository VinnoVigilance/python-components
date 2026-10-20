"""Media risk orchestration over core records and media deltas."""

from repositories.risk import mediaRiskRepository
from services.risk.processing import run


def run_media_risk(**options):
    return run("media", mediaRiskRepository, 802156603, **options)

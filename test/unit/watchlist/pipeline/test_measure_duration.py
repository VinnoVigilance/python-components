"""Golden test for Measures[].Duration -- the no-end markers.

Pins the date refactor (PDG-56): a non-date end marker (Ongoing, Permanent,
Until Further Notice, Indefinitely) is lifted out of Measures[].EndDate into
Measures[].Duration, leaving EndDate empty, while real end dates and lists
without markers are untouched.

Two paths are covered: the ENUM rule (EndDate word -> Duration, e.g. ADB) and
the mapping path (a dedicated source field -> Duration, e.g. WB-OTHER's Ongoing).
"""

import json
from functools import lru_cache
from pathlib import Path

import pytest

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from transforms.preProcessingEngine import PreProcessingEngine
from services.watchlistPipeline import watchlistNormalizationService as norm

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "sources"


@lru_cache(maxsize=None)
def _records(list_name):
    """Run the real full chain (preprocess -> preNorm -> map -> postNorm)."""
    config = WATCHLIST_CONFIGS[list_name]

    with open(FIXTURES / f"{list_name}_raw_sample.jsonl", encoding="utf-8") as f:
        raw = [json.loads(line) for line in f if line.strip()]

    processed = PreProcessingEngine().preprocess(
        records=raw, rules=config.get("preprocessing", [])
    )
    pre, mapper, post = norm.create_normalization_engines(config)

    return tuple(norm.normalize_record(r, config, pre, mapper, post) for r in processed)


def _marked_measures(list_name):
    """Every measure on the list that carries a Duration marker."""
    return [
        m
        for rec in _records(list_name)
        for m in (rec.get("Measures") or [])
        if m.get("Duration")
    ]


class TestMeasureDuration:

    def test_adb_enddate_marker_moves_to_duration(self):
        marked = _marked_measures("ADB-DEBARMENT-SUSPENSION")
        assert marked, "ADB sample should contain no-end markers"

        values = {m["Duration"] for m in marked}
        assert values <= {"Until Further Notice", "Indefinitely"}
        assert "Until Further Notice" in values
        # the marker is lifted OUT of the date column
        assert all(not (m.get("EndDate") or "").strip() for m in marked)

    def test_wb_other_ongoing_moves_to_duration(self):
        marked = _marked_measures("WORLD-BANK-OTHER-SANCTIONS")
        assert any(m["Duration"] == "Ongoing" for m in marked)
        assert all(not (m.get("EndDate") or "").strip() for m in marked)

    def test_list_without_markers_gets_no_duration(self):
        # DFAT has Measures with real dates and no open-ended markers.
        assert _marked_measures("DFAT") == []

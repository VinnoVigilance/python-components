"""
Golden (expected-value) tests for each source list.

Unlike test_source_conformance.py -- which checks only the *shape* of the output
so it survives routine mapping edits -- this file pins the handful of Sources[]
fields that are CONSTANT for a whole list: SourceType, DatasetCategory, ListName
and SourceName. Those are produced by `constant` handlers in mapping.xlsx, so
they never vary record-to-record and should change only when you deliberately
re-map a list.

This is the check that catches a *wrong value* in a new list's mapping -- e.g.
tagging a sanctions list with DatasetCategory "Crime", or pointing SourceName at
the wrong constant -- which the shape-only conformance test cannot see.

Onboarding a new list (the template):
  1. Drop its ~50-record sample at test/fixtures/sources/<LIST>_raw_sample.jsonl
  2. Add a line to SAMPLES below.
  3. Add the expected constants to GOLDEN below. Look them up in mapping.xlsx:
     the Sources[].SourceType / .DatasetCategory / .ListName / .SourceName rows,
     under that list's column.
  Then run:  pytest test/unit/pipeline/test_source_golden.py -k <LIST>
"""

import json
from functools import lru_cache
from pathlib import Path

import pytest

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistNormalizationService as norm

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sources"

# list_name -> committed sample file (mirrors test_source_conformance.SAMPLES).
SAMPLES = {
    "OFAC-SDN": "OFAC-SDN_raw_sample.jsonl",
    "OFAC-NON-SDN": "OFAC-NON-SDN_raw_sample.jsonl",
    "UKSL": "UKSL_raw_sample.jsonl",
    "DFAT": "DFAT_raw_sample.jsonl",
    "EU-DESIGNATED-VESSELS": "EU-DESIGNATED-VESSELS_raw_sample.jsonl",
    "EU-TRAVEL-BAN": "EU-TRAVEL-BAN_raw_sample.jsonl",
    "EU-FINANCIAL-SANCTIONS": "EU-FINANCIAL-SANCTIONS_raw_sample.jsonl",
    "UN-SANCTIONS": "UN-SANCTIONS_raw_sample.jsonl",
    "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": "ATC-DESIGNATED-TERRORIST-INDIVIDUALS_raw_sample.jsonl",
    "ATC-DESIGNATED-TERRORIST-GROUPS": "ATC-DESIGNATED-TERRORIST-GROUPS_raw_sample.jsonl",
    "DNFBP": "DNFBP_raw_sample.jsonl",
    "FBI-WANTED": "FBI-WANTED_raw_sample.jsonl",
    "DMW-RECRUITMENT-AGENCIES": "DMW-RECRUITMENT-AGENCIES_raw_sample.jsonl",
    "INTERPOL-RED-NOTICES": "INTERPOL-RED-NOTICES_raw_sample.jsonl",
}

# list_name -> the constant Sources[] fields every record of that list must carry.
# These are the human-blessed expected values; they must match the `constant`
# handlers in mapping.xlsx exactly -- sanctions lists -> "Sanctions",
# DNFBP -> "Regulatory".
#
# DatasetCategory may be a SET when a list is inherently more than one thing:
# ATC is both a terrorism designation and a targeted financial sanction, mapped
# with the `*` handler, so each record produces two Sources[] entries carrying
# "Crime" and "Sanctions" respectively. A set expects that spread across the
# entries; a plain string expects that single value on every entry.
GOLDEN = {
    "OFAC-SDN": {
        "SourceType": "Official", "DatasetCategory": "Sanctions",
        "ListName": "OFAC-SDN", "SourceName": "OFAC",
    },
    "OFAC-NON-SDN": {
        "SourceType": "Official", "DatasetCategory": "Sanctions",
        "ListName": "OFAC-NON-SDN", "SourceName": "OFAC",
    },
    "UKSL": {
        "SourceType": "Official", "DatasetCategory": "Sanctions",
        "ListName": "UKSL", "SourceName": "OFSI",
    },
    "DFAT": {
        "SourceType": "Official", "DatasetCategory": "Sanctions",
        "ListName": "DFAT", "SourceName": "DFAT",
    },
    "EU-DESIGNATED-VESSELS": {
        "SourceType": "Official", "DatasetCategory": "Sanctions",
        "ListName": "EU-DESIGNATED-VESSELS", "SourceName": "EU",
    },
    "EU-TRAVEL-BAN": {
        "SourceType": "Official", "DatasetCategory": "Sanctions",
        "ListName": "EU-TRAVEL-BAN", "SourceName": "EU",
    },
    "EU-FINANCIAL-SANCTIONS": {
        "SourceType": "Official", "DatasetCategory": "Sanctions",
        "ListName": "EU-FINANCIAL-SANCTIONS", "SourceName": "EU",
    },
    "UN-SANCTIONS": {
        "SourceType": "Official", "DatasetCategory": "Sanctions",
        "ListName": "UN-SANCTIONS", "SourceName": "UN",
    },
    "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": {
        "SourceType": "Official", "DatasetCategory": {"Crime", "Sanctions"},
        "ListName": "ATC-DESIGNATED-TERRORIST-INDIVIDUALS", "SourceName": "ATC",
    },
    "ATC-DESIGNATED-TERRORIST-GROUPS": {
        "SourceType": "Official", "DatasetCategory": {"Crime", "Sanctions"},
        "ListName": "ATC-DESIGNATED-TERRORIST-GROUPS", "SourceName": "ATC",
    },
    "DNFBP": {
        "SourceType": "Official", "DatasetCategory": "Regulatory",
        "ListName": "DNFBP", "SourceName": "AMLC",
    },
    "FBI-WANTED": {
        "SourceType": "Official", "DatasetCategory": "Law Enforcement",
        "ListName": "FBI-WANTED", "SourceName": "FBI",
    },
    "DMW-RECRUITMENT-AGENCIES": {
        "SourceType": "Official", "DatasetCategory": "Regulatory",
        "ListName": "DMW-RECRUITMENT-AGENCIES", "SourceName": "DMW",
    },
    "INTERPOL-RED-NOTICES": {
        "SourceType": "Official", "DatasetCategory": "Law Enforcement",
        "ListName": "INTERPOL-RED-NOTICES", "SourceName": "INTERPOL",
    },
}


@lru_cache(maxsize=None)
def _canonical_records(list_name):
    """Run the real normalization chain over the list's committed sample."""
    config = WATCHLIST_CONFIGS[list_name]
    pre, mapper, post = norm.create_normalization_engines(config)

    with open(FIXTURES / SAMPLES[list_name], encoding="utf-8") as f:
        raw_records = [json.loads(line) for line in f if line.strip()]

    return tuple(
        norm.normalize_record(r, config, pre, mapper, post) for r in raw_records
    )


def test_samples_and_golden_cover_the_same_lists():
    """Guard: a list can never be added to one table but forgotten in the other,
    which would silently skip its golden check."""
    assert set(SAMPLES) == set(GOLDEN), (
        f"SAMPLES vs GOLDEN mismatch: "
        f"only in SAMPLES={sorted(set(SAMPLES) - set(GOLDEN))}, "
        f"only in GOLDEN={sorted(set(GOLDEN) - set(SAMPLES))}"
    )


@pytest.mark.parametrize("list_name", list(GOLDEN))
class TestSourceGolden:

    def test_sources_constants_match_expected(self, list_name):
        expected = dict(GOLDEN[list_name])
        # DatasetCategory may be a set (a dual-natured list mapped with `*`
        # produces one Sources[] entry per value); handle it per record rather
        # than per entry.
        dataset_expected = expected.pop("DatasetCategory")
        records = _canonical_records(list_name)
        assert records, f"{list_name}: no sample records loaded"

        saw_sources = False
        for rec in records:
            sources = rec.get("Sources") or []
            for src in sources:
                saw_sources = True
                for field, want in expected.items():
                    got = src.get(field)
                    assert got == want, (
                        f"{list_name}: Sources[].{field} = {got!r}, expected "
                        f"{want!r} -- check the Sources[].{field} row under the "
                        f"'{list_name}' column in mapping.xlsx"
                    )

            if not sources:
                continue

            if isinstance(dataset_expected, (set, frozenset)):
                got = {src.get("DatasetCategory") for src in sources}
                assert got == set(dataset_expected), (
                    f"{list_name}: DatasetCategory across Sources[] = {got!r}, "
                    f"expected {set(dataset_expected)!r} -- check the `*` "
                    f"DatasetCategory row under the '{list_name}' column in "
                    f"mapping.xlsx"
                )
            else:
                for src in sources:
                    got = src.get("DatasetCategory")
                    assert got == dataset_expected, (
                        f"{list_name}: Sources[].DatasetCategory = {got!r}, "
                        f"expected {dataset_expected!r} -- check the "
                        f"Sources[].DatasetCategory row under the '{list_name}' "
                        f"column in mapping.xlsx"
                    )

        assert saw_sources, (
            f"{list_name}: no Sources[] entry was produced for any sample record "
            f"-- the mapping's 'sources' group did not populate"
        )

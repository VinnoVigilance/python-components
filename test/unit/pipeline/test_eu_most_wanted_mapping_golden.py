"""
Golden test locking the EU-MOST-WANTED mapping.

This list exercises several custom mechanics that the shape-only conformance
test cannot see:

  * Names -- one pre-norm ``split_pattern`` with NESTED named groups splits
    ``"KANYS, Renaldas"`` into full name + FirstName + LastName in one pass,
    keeping the exact ``"LAST, First"`` string.
  * Programs[] -- a ``*`` multi-branch emits TWO authorities (ENFAST and
    Europol) for the same crime.
  * Countries[] -- a ``*`` multi-branch carries "Wanted By" (name + enrichment
    code) alongside list-expanded "Nationality".
  * DateAdded / DateUpdated -- pulled out of one ``published`` string by a
    ``split_pattern`` row.
  * additionalInfo -- ``is_dangerous`` mapped to Yes/No by an ``enum`` row;
    Reward Amount concatenated with its currency symbol.
  * Reporting Phone -- multi-number strings split into one entry per number.

Input is the frozen sample fed through the real
pre-normalize -> map -> post-normalize chain (preprocessing has already run on
the sample, so ``source_record_id`` is present).
"""

import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistNormalizationService as norm

pytestmark = pytest.mark.unit

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures" / "sources" / "EU-MOST-WANTED_raw_sample.jsonl"
)

# The record every field-level assertion below is pinned to (Drupal nid).
PRIMARY_ID = "964"

_PHONE = re.compile(r"\+?\d[\d\s./-]*\d")


@lru_cache(maxsize=None)
def _records_by_id():
    """Run the real normalization chain over the frozen EU sample."""
    config = WATCHLIST_CONFIGS["EU-MOST-WANTED"]
    pre, mapper, post = norm.create_normalization_engines(config)

    with open(FIXTURE, encoding="utf-8") as f:
        raw = [json.loads(line) for line in f if line.strip()]

    canonical = [norm.normalize_record(r, config, pre, mapper, post) for r in raw]
    return {rec["EntityId"]: rec for rec in canonical}


@pytest.fixture(scope="module")
def primary():
    rec = _records_by_id().get(PRIMARY_ID)
    assert rec is not None, f"fixture is missing the pinned record {PRIMARY_ID}"
    return rec


class TestEuMostWantedMappingGolden:

    def test_entity_is_individual(self, primary):
        assert primary["EntityType"] == "Individual"
        assert primary["Gender"] == "Male"

    def test_name_split_keeps_full_and_parts(self, primary):
        # Nested-group split_pattern: full "LAST, First" preserved, parts filled.
        name = primary["Names"][0]
        assert name["NameType"] == "Primary Name"
        assert name["Name"] == "KANYS, Renaldas"
        assert name["FirstName"] == "Renaldas"
        assert name["Last Name"] == "KANYS"

    def test_birth_date_resolved_to_iso(self, primary):
        got = [(d["OriginalValue"], d["FullDate"], d["Type"]) for d in primary["Dates"]]
        assert got == [("1973-09-26T12:00:00Z", "1973-09-26", "Birth Date")]

    def test_two_authorities_for_the_same_crime(self, primary):
        # The `*` multi-branch: same crime, ENFAST and Europol as authorities.
        programs = primary["Programs"]
        assert [p["Authority"] for p in programs] == ["ENFAST", "Europol"]
        assert {p["ProgramType"] for p in programs} == {"Law Enforcement"}
        assert len({p["Program"] for p in programs}) == 1

    def test_countries_carry_wanted_by_and_nationality(self, primary):
        got = {(c["CountryType"], c["CountryName"], c["CountryCode"]) for c in primary["Countries"]}
        assert got == {
            ("Wanted By", "Lithuania", "LT"),
            ("Nationality", "Lithuanian", ""),
        }

    def test_published_split_into_added_and_updated(self, primary):
        assert primary["DateAdded"] == "September 7, 2020"
        assert primary["DateUpdated"] == "October 13, 2021"

    def test_attachment_types(self, primary):
        assert [a["Type"] for a in primary["Attachments"]] == [
            "Profile", "Photograph", "Poster", "Thumbnail",
        ]


class TestEuCrossRecordInvariants:
    """Behaviours that must hold for EVERY record in the sample."""

    def test_every_record_has_both_authorities(self):
        for eid, rec in _records_by_id().items():
            authorities = {p["Authority"] for p in rec.get("Programs", [])}
            assert authorities == {"ENFAST", "Europol"}, (
                f"{eid}: authorities were {authorities} -- check the "
                f"Programs[].Authority `*` row under the EU-MOST-WANTED column"
            )

    def test_every_name_is_split_into_parts(self):
        for eid, rec in _records_by_id().items():
            name = rec["Names"][0]
            assert name["FirstName"] and name["Last Name"], (
                f"{eid}: name {name['Name']!r} did not split into first/last -- "
                f"check the list.name split_pattern row in preNormalization.xlsx"
            )

    def test_dangerous_flag_is_yes_or_no_never_raw(self):
        for eid, rec in _records_by_id().items():
            for info in rec.get("additionalInfo", []):
                if info["Type"] == "Dangerous":
                    assert info["Value"] in {"Yes", "No"}, (
                        f"{eid}: Dangerous={info['Value']!r} -- the enum row in "
                        f"preNormalization.xlsx should map 1->Yes, 0->No"
                    )

    def test_reporting_phones_are_one_number_each(self):
        for eid, rec in _records_by_id().items():
            for info in rec.get("additionalInfo", []):
                if info["Type"] == "Reporting Phone":
                    numbers = _PHONE.findall(info["Value"])
                    assert len(numbers) == 1, (
                        f"{eid}: Reporting Phone {info['Value']!r} still packs "
                        f"{len(numbers)} numbers -- check the "
                        f"detail.reporting_phone[] split_pattern row and the "
                        f"reporting_phone list_expand in the mapping"
                    )

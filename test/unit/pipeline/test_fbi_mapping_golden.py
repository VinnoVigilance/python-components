"""
Golden test locking the FBI-WANTED mapping.

The FBI list exercises the trickiest mapping mechanics in the project, and none
of them are protected by the shape-only conformance test:

  * Identifiers -- ONE canonical array carries TWO different source fields
    (``uid`` and ``ncic``), distinguished only by their ``Note``.
  * Comments[].text -- pulled from THREE raw fields (``description * caution *
    details``) and HTML-stripped by the post-norm ``SANITIZE_HTML`` rule.
  * additionalInfo[] -- a ``*`` multi-branch of Type/Value pairs where branches
    whose value is empty (Warning/Reward/Build/... on this record) are DROPPED.
  * Attachments[] -- built from TWO ``path_expand`` sources (``images`` ->
    Photograph, ``files`` -> Document).
  * Aliases[].Alias -- de-quoted by the pre-norm ``regex_extract`` rule
    (``"Lil Kato"`` -> ``Lil Kato``).
  * Dates -- a free-text birth date resolved to an ISO ``FullDate``.

Because the sample is a FROZEN copy under test/fixtures/sources, these expected
values are stable: they change only when someone deliberately edits the FBI
mapping (or re-drops the sample), which is exactly when this test SHOULD fail.

Input is the raw FBI sample fed straight through the real
pre-normalize -> map -> post-normalize chain. FBI has no preprocessing rules, so
that reproduces the full pipeline output.
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
    / "fixtures" / "sources" / "FBI-WANTED_raw_sample.jsonl"
)

# The record every field-level assertion below is pinned to.
PRIMARY_UID = "4760d61850d74f4091a8c6e9b22b087a"

_HTML_TAG = re.compile(r"<[^>]+>")


@lru_cache(maxsize=None)
def _records_by_id():
    """Run the real normalization chain over the frozen FBI sample."""
    config = WATCHLIST_CONFIGS["FBI-WANTED"]
    pre, mapper, post = norm.create_normalization_engines(config)

    with open(FIXTURE, encoding="utf-8") as f:
        raw = [json.loads(line) for line in f if line.strip()]

    canonical = [norm.normalize_record(r, config, pre, mapper, post) for r in raw]
    return {rec["EntityId"]: rec for rec in canonical}


@pytest.fixture(scope="module")
def primary():
    rec = _records_by_id().get(PRIMARY_UID)
    assert rec is not None, f"fixture is missing the pinned record {PRIMARY_UID}"
    return rec


class TestFbiMappingGolden:

    def test_entity_is_individual(self, primary):
        assert primary["EntityType"] == "Individual"

    def test_identifiers_carry_both_uid_and_ncic(self, primary):
        # The uid*ncic dual mapping: one array, two source fields, told apart
        # by Note. Guards the Identifiers[].Number / .Note rows in mapping.xlsx.
        got = [
            (i["Type"], i["Number"], i["Note"]) for i in primary["Identifiers"]
        ]
        assert got == [
            ("Reference Number", "4760d61850d74f4091a8c6e9b22b087a", "FBI UID"),
            ("Reference Number", "W953669526", "NCIC Number"),
        ]

    def test_aliases_are_dequoted(self, primary):
        # Pre-norm regex_extract strips the wrapping quotes off '"Lil Kato"'.
        got = [(a["AliasType"], a["Alias"]) for a in primary["Aliases"]]
        assert got == [("AKA", "Lil Kato")]

    def test_additional_info_pairs_drop_empty_branches(self, primary):
        # The `*` multi-branch keeps only the branches with a real value;
        # Warning/Reward/Build/Complexion/Scars are empty here and drop out.
        assert primary["additionalInfo"] == [
            {"Type": "Race", "Value": "Black"},
            {"Type": "Hair", "Value": "Black"},
            {"Type": "Eyes", "Value": "Brown"},
            {"Type": "Weight", "Value": "180 to 190 pounds"},
            {"Type": "Height (in)", "Value": 66},
        ]

    def test_attachments_from_images_and_files(self, primary):
        # images -> Photograph, files -> Document (two path_expand sources).
        assert primary["Attachments"] == [
            {
                "Type": "Photograph",
                "URL": "https://www.fbi.gov/wanted/cei/jakodi-keshone-wilson/@@images/image/large",
            },
            {
                "Type": "Document",
                "URL": "https://www.fbi.gov/wanted/cei/jakodi-keshone-wilson/download.pdf",
            },
        ]

    def test_birth_date_is_resolved_to_iso(self, primary):
        got = [
            (d["OriginalValue"], d["FullDate"], d["Type"], d["IsApproximate"])
            for d in primary["Dates"]
        ]
        assert got == [("September 20, 2000", "2000-09-20", "Birth Date", "false")]

    def test_comments_come_from_the_three_fields_without_html(self, primary):
        # description -> Summary, caution/details -> Remarks; the caution field
        # carried <p> HTML in the raw record and must be stripped.
        types = [c["type"] for c in primary["Comments"]]
        assert types == ["Summary", "Remarks"]
        for c in primary["Comments"]:
            assert not _HTML_TAG.search(c["text"]), (
                f"HTML leaked into a comment: {c['text']!r}"
            )


class TestFbiCrossRecordInvariants:
    """Behaviours that must hold for EVERY record in the sample, not just one."""

    def test_no_html_in_any_comment(self):
        for uid, rec in _records_by_id().items():
            for c in rec.get("Comments", []):
                assert not _HTML_TAG.search(c.get("text", "")), (
                    f"{uid}: HTML survived in a comment -- check the "
                    f"SANITIZE_HTML rule in postNormalization.xlsx"
                )

    def test_no_wrapping_quotes_in_any_alias(self):
        for uid, rec in _records_by_id().items():
            for a in rec.get("Aliases", []):
                alias = a.get("Alias", "")
                assert not (alias.startswith('"') or alias.endswith('"')), (
                    f"{uid}: alias {alias!r} still wrapped in quotes -- check the "
                    f"aliases[] regex_extract rule in preNormalization.xlsx"
                )

"""Golden (expected-value) tests for each adverse-media source.

The media analog of test/unit/watchlist/pipeline/test_source_golden.py. For each
source it runs the real transform chain over a committed extracted sample --
preprocess (MediaRawRecordService) -> normalize (MediaNormalizationService:
entity_type stamp -> pre-normalization -> mapping -> post-normalization) -- and
pins the Sources[] fields that are CONSTANT for a whole dataset (SourceType,
DatasetCategory, DatasetName, SourceName) plus Publisher.Name. Those come from
`constant` handlers in mediaMapping.xlsx, so they change only on a deliberate
re-map.

PCIJ additionally proves the four `multiple` embed fields (Table/Chart/Dashboard/
Document) flow through mapping into Attachments[] with the right Type.

Onboarding a new media source:
  1. Drop its extracted sample at test/fixtures/media/<DATASET>_extracted_sample.jsonl
  2. Add a line to SAMPLES and GOLDEN below (look the constants up in the
     source's column in data/rules/mediaMapping.xlsx).
"""

import io
import json
from contextlib import redirect_stdout
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

from services.adverseMediaPipeline.mediaNormalizationService import (
    MediaNormalizationService,
)
from services.adverseMediaPipeline.mediaRawRecordService import MediaRawRecordService

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "test" / "fixtures" / "media"
MEDIA_CONFIG = ROOT / "config" / "mediaSources.yaml"

# dataset_name -> committed extracted sample file.
SAMPLES = {
    "NBI_PRESS_RELEASES": "NBI_PRESS_RELEASES_extracted_sample.jsonl",
    "AMLC_NEWS_AND_ANNOUNCEMENTS": "AMLC_NEWS_AND_ANNOUNCEMENTS_extracted_sample.jsonl",
    "PCIJ_CORRUPTION_WATCH": "PCIJ_CORRUPTION_WATCH_extracted_sample.jsonl",
}

# dataset_name -> the constant Sources[]/Publisher fields every record must carry.
# These mirror the `constant` handlers under each dataset's column in
# data/rules/mediaMapping.xlsx.
GOLDEN = {
    "NBI_PRESS_RELEASES": {
        "SourceType": "Official", "DatasetCategory": "PRESS_RELEASE",
        "DatasetName": "NBI_PRESS_RELEASES", "SourceName": "NBI",
        "PublisherName": "NBI",
    },
    "AMLC_NEWS_AND_ANNOUNCEMENTS": {
        "SourceType": "Official", "DatasetCategory": "PRESS_RELEASE",
        "DatasetName": "AMLC_NEWS_AND_ANNOUNCEMENTS", "SourceName": "AMLC",
        "PublisherName": "AMLC",
    },
    "PCIJ_CORRUPTION_WATCH": {
        "SourceType": "Official", "DatasetCategory": "News Article",
        "DatasetName": "PCIJ_CORRUPTION_WATCH", "SourceName": "PCIJ",
        "PublisherName": "PCIJ",
    },
}


@lru_cache(maxsize=None)
def _config():
    with MEDIA_CONFIG.open(encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config.get("global", {}), config.get("sources", {})


@lru_cache(maxsize=None)
def _canonical_records(dataset_name):
    """Run the real preprocess + normalization chain over the committed sample."""
    global_config, sources = _config()
    source_config = sources[dataset_name]

    with open(FIXTURES / SAMPLES[dataset_name], encoding="utf-8") as f:
        extracted = [json.loads(line) for line in f if line.strip()]

    # The service is chatty (debug prints); silence it so test output stays clean.
    with redirect_stdout(io.StringIO()):
        preprocessed = MediaRawRecordService().process(
            source_config=source_config,
            records=extracted,
        )
        service = MediaNormalizationService(
            global_config=global_config,
            source_config=source_config,
        )
        canonical = service.normalize_many(preprocessed)

    return tuple(canonical)


def test_samples_and_golden_cover_the_same_sources():
    """Guard: a source can never be added to one table but forgotten in the other."""
    assert set(SAMPLES) == set(GOLDEN), (
        f"SAMPLES vs GOLDEN mismatch: "
        f"only in SAMPLES={sorted(set(SAMPLES) - set(GOLDEN))}, "
        f"only in GOLDEN={sorted(set(GOLDEN) - set(SAMPLES))}"
    )


@pytest.mark.parametrize("dataset_name", list(GOLDEN))
class TestMediaSourceGolden:

    def test_sources_constants_match_expected(self, dataset_name):
        expected = GOLDEN[dataset_name]
        publisher_expected = expected["PublisherName"]
        source_fields = {k: v for k, v in expected.items() if k != "PublisherName"}

        records = _canonical_records(dataset_name)
        assert records, f"{dataset_name}: no sample records produced"

        saw_sources = False
        for rec in records:
            assert (rec.get("Publisher") or {}).get("Name") == publisher_expected, (
                f"{dataset_name}: Publisher.Name = "
                f"{(rec.get('Publisher') or {}).get('Name')!r}, expected "
                f"{publisher_expected!r} -- check the Publisher.Name row under the "
                f"'{dataset_name}' column in mediaMapping.xlsx"
            )
            for src in rec.get("Sources") or []:
                saw_sources = True
                for field, want in source_fields.items():
                    got = src.get(field)
                    assert got == want, (
                        f"{dataset_name}: Sources[].{field} = {got!r}, expected "
                        f"{want!r} -- check the Sources[].{field} row under the "
                        f"'{dataset_name}' column in mediaMapping.xlsx"
                    )

        assert saw_sources, (
            f"{dataset_name}: no Sources[] entry produced -- the mapping's "
            f"'sources' group did not populate"
        )


class TestPcijEmbedsBecomeAttachments:
    """The four `multiple` embed fields must survive mapping into Attachments[]."""

    def _by_title(self, needle):
        for rec in _canonical_records("PCIJ_CORRUPTION_WATCH"):
            if needle.lower() in (rec.get("Content") or {}).get("Title", "").lower():
                return rec
        raise AssertionError(f"no PCIJ sample record matching {needle!r}")

    @pytest.mark.parametrize("needle,attach_type,url_fragment", [
        ("Five Reveals from the Flood-Control", "Table", "flourish.studio"),
        ("Marcos freed up P214B", "Chart", "flourish.studio"),
        ("Billions for school infrastructure", "Dashboard", "datawrapper"),
        ("R.A. 12009", "Document", ".pdf"),
    ])
    def test_embed_field_maps_to_attachment(self, needle, attach_type, url_fragment):
        rec = self._by_title(needle)
        attachments = rec.get("Attachments") or []
        matches = [a for a in attachments if a.get("Type") == attach_type]
        assert matches, (
            f"PCIJ {needle!r}: no Attachments[] entry of Type {attach_type!r} -- "
            f"the {attach_type} embed field did not map through"
        )
        assert any(url_fragment in (a.get("URL") or "") for a in matches), (
            f"PCIJ {needle!r}: {attach_type} attachment URL missing "
            f"{url_fragment!r}: {[a.get('URL') for a in matches]}"
        )

    def test_multiple_tables_all_survive(self):
        """'Five Reveals' has 5 Flourish tables -- `multiple: true` must keep them all."""
        rec = self._by_title("Five Reveals from the Flood-Control")
        tables = [a for a in (rec.get("Attachments") or []) if a.get("Type") == "Table"]
        assert len(tables) >= 2, (
            f"expected several Table attachments from the multi-embed record, "
            f"got {len(tables)}"
        )

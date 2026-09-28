"""Golden (expected-value) tests for each adverse-media source.

The media analog of test/unit/watchlist/pipeline/test_source_golden.py. For each
source it runs the real transform chain over a committed extracted sample --
preprocess (MediaRawRecordService) -> normalize (MediaNormalizationService:
entity_type stamp -> pre-normalization -> mapping -> post-normalization) -- and
pins the Sources[] fields that are CONSTANT for a whole dataset (SourceType,
DatasetCategory, DatasetName, SourceName) plus Publisher.Name where it too is a
constant. Those come from `constant` handlers in mediaMapping.xlsx, so they
change only on a deliberate re-map. (UK_GOV's Publisher.Name is a per-record
path, so it is not pinned -- omit PublisherName from GOLDEN in that case.)

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
    "PCIJ_INVESTIGATIVE_REPORTS": "PCIJ_INVESTIGATIVE_REPORTS_extracted_sample.jsonl",
    "UK_GOV_NEWS_COMMUNICATIONS": "UK_GOV_NEWS_COMMUNICATIONS_extracted_sample.jsonl",
    "DTI_PH_FAIR_TRADE_PRESS_RELEASES": "DTI_PH_FAIR_TRADE_PRESS_RELEASES_extracted_sample.jsonl",
    "ADB_CASE_SUMMARIES": "ADB_CASE_SUMMARIES_extracted_sample.jsonl",
    "SEC_PH_ADVISORIES": "SEC_PH_ADVISORIES_extracted_sample.jsonl",
    "PTV_NEWS": "PTV_NEWS_extracted_sample.jsonl",
}

# dataset_name -> the constant Sources[]/Publisher fields every record must carry.
# These mirror the `constant` handlers under each dataset's column in
# data/rules/mediaMapping.xlsx.
GOLDEN = {
    "NBI_PRESS_RELEASES": {
        "SourceType": "Official", "DatasetCategory": "Press Release",
        "DatasetName": "NBI_PRESS_RELEASES", "SourceName": "NBI",
        "PublisherName": "NBI",
    },
    "AMLC_NEWS_AND_ANNOUNCEMENTS": {
        "SourceType": "Official", "DatasetCategory": "Press Release",
        "DatasetName": "AMLC_NEWS_AND_ANNOUNCEMENTS", "SourceName": "AMLC",
        "PublisherName": "AMLC",
    },
    "PCIJ_CORRUPTION_WATCH": {
        "SourceType": "Media", "DatasetCategory": "News Article",
        "DatasetName": "PCIJ_CORRUPTION_WATCH", "SourceName": "PCIJ",
        "PublisherName": "PCIJ",
    },
    "PCIJ_INVESTIGATIVE_REPORTS": {
        "SourceType": "Media", "DatasetCategory": "News Article",
        "DatasetName": "PCIJ_INVESTIGATIVE_REPORTS", "SourceName": "PCIJ",
        "PublisherName": "PCIJ",
    },
    # UK_GOV's Publisher.Name is a per-record `path` (the publishing org), not a
    # dataset constant, so it is not pinned here.
    "UK_GOV_NEWS_COMMUNICATIONS": {
        "SourceType": "Official", "DatasetCategory": "News Article",
        "DatasetName": "UK_GOV_NEWS_COMMUNICATIONS", "SourceName": "UK_GOV",
    },
    "DTI_PH_FAIR_TRADE_PRESS_RELEASES": {
        "SourceType": "Official", "DatasetCategory": "Press Release",
        "DatasetName": "DTI_PH_FAIR_TRADE_PRESS_RELEASES", "SourceName": "DTI_PH",
        "PublisherName": "Department of Trade and Industry (Philippines)",
    },
    "ADB_CASE_SUMMARIES": {
        "SourceType": "Official", "DatasetCategory": "Press Release",
        "DatasetName": "ADB_CASE_SUMMARIES", "SourceName": "ADB",
        "PublisherName": "Asian Development Bank",
    },
    "SEC_PH_ADVISORIES": {
        "SourceType": "Official", "DatasetCategory": "Press Release",
        "DatasetName": "SEC_PH_ADVISORIES", "SourceName": "SEC_PH",
        "PublisherName": "Securities and Exchange Commission (Philippines)",
    },
    "PTV_NEWS": {
        "SourceType": "Official", "DatasetCategory": "News Article",
        "DatasetName": "PTV_NEWS", "SourceName": "PTV",
        "PublisherName": "People's Television Network (Philippines)",
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
        publisher_expected = expected.get("PublisherName")
        source_fields = {k: v for k, v in expected.items() if k != "PublisherName"}

        records = _canonical_records(dataset_name)
        assert records, f"{dataset_name}: no sample records produced"

        saw_sources = False
        for rec in records:
            if publisher_expected is not None:
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


class TestPcijFeaturedImageAndTags:
    """FeaturedImageUrl (og:image) -> Attachments[Image], TagNames -> Metadata.Tags[]."""

    def _by_title(self, needle):
        for rec in _canonical_records("PCIJ_INVESTIGATIVE_REPORTS"):
            if needle.lower() in (rec.get("Content") or {}).get("Title", "").lower():
                return rec
        raise AssertionError(f"no PCIJ_INVESTIGATIVE_REPORTS sample record matching {needle!r}")

    def test_featured_image_becomes_image_attachment(self):
        rec = self._by_title("Have you come across pro-China propaganda")
        photos = [a for a in (rec.get("Attachments") or []) if a.get("Type") == "Image"]
        assert photos, "expected an Image attachment from FeaturedImageUrl"
        assert photos[0].get("URL"), "Image attachment has no URL"

    def test_missing_featured_image_produces_no_image_attachment(self):
        """Some older articles genuinely have no og:image -- must not fabricate one."""
        rec = self._by_title("SALN files of wannabe presidents")
        photos = [a for a in (rec.get("Attachments") or []) if a.get("Type") == "Image"]
        assert not photos, f"expected no Image attachment, got {photos}"

    def test_tag_names_become_taxonomy_tags(self):
        rec = self._by_title("Have you come across pro-China propaganda")
        tags = (rec.get("Metadata") or {}).get("Tags") or []
        assert "China" in tags, f"expected 'China' tag, got {tags}"
        assert all(isinstance(t, str) for t in tags), "Metadata.Tags[] must be flat strings"

    def test_embedded_pdf_becomes_document_attachment(self):
        """Older SALN articles embed the PDF via a raw <iframe src=*.pdf>, not the
        newer wp-block-file__embed markup -- EmbeddedPdfUrls covers that case."""
        rec = self._by_title("Duterte")
        docs = [a for a in (rec.get("Attachments") or []) if a.get("Type") == "Document"]
        assert docs, "expected a Document attachment from EmbeddedPdfUrls"
        assert ".pdf" in (docs[0].get("URL") or ""), docs[0].get("URL")


class TestDtiAttachments:
    """Featured media + body images -> Attachments[Image], body PDFs -> Attachments[Document]."""

    def _by_id(self, record_id):
        for rec in _canonical_records("DTI_PH_FAIR_TRADE_PRESS_RELEASES"):
            if str((rec.get("Sources") or [{}])[0].get("SourceRecordId")) == record_id:
                return rec
        raise AssertionError(f"no DTI sample record with SourceRecordId {record_id!r}")

    def _urls(self, rec, attach_type):
        return [a.get("URL") for a in (rec.get("Attachments") or []) if a.get("Type") == attach_type]

    def test_featured_and_body_images_become_images(self):
        photos = self._urls(self._by_id("6453"), "Image")
        assert len(photos) == 2, f"expected featured + body image, got {photos}"

    def test_body_pdf_becomes_document_attachment(self):
        docs = self._urls(self._by_id("3313"), "Document")
        assert docs and docs[0].endswith(".pdf"), f"expected a PDF Document attachment, got {docs}"

    def test_staging_domain_urls_are_excluded(self):
        for rec in _canonical_records("DTI_PH_FAIR_TRADE_PRESS_RELEASES"):
            for a in rec.get("Attachments") or []:
                assert "fteb-staging" not in (a.get("URL") or ""), a


class TestAdbCaseSummaries:
    """Each table row maps to one record: case number, two-digit-year date, entity type tag."""

    def test_every_row_becomes_a_record(self):
        assert len(_canonical_records("ADB_CASE_SUMMARIES")) == 7

    def test_case_number_is_source_record_id_and_identifier(self):
        rec = _canonical_records("ADB_CASE_SUMMARIES")[0]
        assert rec["Sources"][0]["SourceRecordId"] == "20-0223-2306"
        assert rec["Identifiers"] == [{"Type": "Source Reference", "Value": "20-0223-2306"}]

    def test_two_digit_year_resolves_to_full_date(self):
        dates = [rec["Dates"][0] for rec in _canonical_records("ADB_CASE_SUMMARIES")]
        assert dates[0]["OriginalValue"] == "23-Jun-26"
        assert dates[0]["FullDate"] == "2026-06-23"
        assert all(d["FullDate"] for d in dates)

    def test_firm_and_individual_rows_differ_by_tag(self):
        records = _canonical_records("ADB_CASE_SUMMARIES")
        assert records[0]["Metadata"]["Tags"] == ["Firm"]
        assert records[1]["Metadata"]["Tags"] == ["Individual"]
        assert records[0]["Content"]["BodyText"] == records[1]["Content"]["BodyText"]


class TestSecAdvisories:
    """Page metadata, PDF text and PDF links map into the canonical record."""

    def _by_post_id(self, post_id):
        for rec in _canonical_records("SEC_PH_ADVISORIES"):
            if rec["Sources"][0]["SourceRecordId"] == post_id:
                return rec
        raise AssertionError(f"no SEC sample record with PostId {post_id!r}")

    def test_post_id_is_source_record_id_and_identifier(self):
        rec = self._by_post_id("150359")
        assert rec["Identifiers"] == [{"Type": "Source Reference", "Value": "150359"}]

    def test_author_language_and_category(self):
        rec = self._by_post_id("150359")
        assert rec["Authors"] == [{"Name": "Michael Abrasia"}]
        assert rec["Metadata"]["Language"] == "en"
        assert rec["Metadata"]["Categories"] == ["Advisories 2026"]

    def test_multiple_categories_are_split(self):
        rec = self._by_post_id("28635")
        assert rec["Metadata"]["Categories"] == ["Advisories 2018", "notice-lcfc"]

    def test_published_and_updated_dates(self):
        dates = {d["Type"]: d["FullDate"] for d in self._by_post_id("150359")["Dates"]}
        assert dates["Published"] == "2026-09-04"

    def test_pdf_text_becomes_body(self):
        body = self._by_post_id("150359")["Content"]["BodyText"]
        assert "PUERTA FARM" in body

    def test_both_pdf_links_become_document_attachments(self):
        docs = self._by_post_id("121133")["Attachments"]
        assert len(docs) == 2
        assert all(a["Type"] == "Document" and a["URL"].endswith(".pdf") for a in docs)

    def test_scanned_pdf_keeps_every_field_but_body(self):
        rec = self._by_post_id("146421")
        assert not rec["Content"].get("BodyText")
        assert rec["Content"]["Title"]
        assert rec["Dates"] and rec["Attachments"] and rec["Authors"]


class TestPtvNews:
    """Body byline -> Authors[], featured media -> Thumbnail, body images/files -> Image/Document, terms -> Tags."""

    def _by_id(self, record_id):
        for rec in _canonical_records("PTV_NEWS"):
            if rec["Sources"][0]["SourceRecordId"] == record_id:
                return rec
        raise AssertionError(f"no PTV sample record with id {record_id!r}")

    def _urls(self, rec, attach_type):
        return [a["URL"] for a in rec["Attachments"] if a["Type"] == attach_type]

    @pytest.mark.parametrize("record_id,author", [
        (252351, "Dean Aubrey Caratiquet"),
        (252337, "Christopher Lloyd Caliwan"),
        (235295, "Anna Leah Gonzales"),
        (220346, "Gabriela Baron"),
    ])
    def test_byline_becomes_author(self, record_id, author):
        assert self._by_id(record_id)["Authors"] == [{"Name": author}]

    def test_end_of_article_credit_leaves_author_empty(self):
        assert self._by_id(70637)["Authors"] == []

    def test_featured_media_is_the_only_thumbnail(self):
        for rec in _canonical_records("PTV_NEWS"):
            assert len(self._urls(rec, "Thumbnail")) == 1, rec["Attachments"]

    def test_body_image_becomes_image(self):
        images = self._urls(self._by_id(252351), "Image")
        assert images and images[0].endswith("-1024x683.jpg"), images

    def test_body_pdf_becomes_document(self):
        docs = self._urls(self._by_id(235295), "Document")
        assert docs and docs[0].endswith(".pdf"), docs

    def test_terms_go_to_tags_not_categories(self):
        rec = self._by_id(252351)
        assert "DTI Secretary Cristina Roque" in rec["Metadata"]["Tags"]
        assert rec["Metadata"]["Categories"] == []

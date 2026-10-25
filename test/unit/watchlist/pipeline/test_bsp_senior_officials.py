"""Verify BSP extraction and canonical fields with frozen HTML and real rules."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse

from ingestion.crawler.configLoader import load_crawler_config
from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.spiders.savedHtmlSpider import SavedHtmlSpider
from ingestion.crawler.storage import CrawlerStorage
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline.watchlistNormalizationService import (
    create_normalization_engines,
    normalize_record,
)
from transforms.preProcessingEngine import PreProcessingEngine


pytestmark = pytest.mark.unit
SOURCE = "BSP_SENIOR_OFFICIALS"
ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "test" / "fixtures" / "sources"
HTML_FIXTURES = FIXTURES / "bsp_senior_officials"
BIOGRAPHY = "A complete career biography. Education and previous public service."


@pytest.fixture
def spider(tmp_path):
    config = WATCHLIST_CONFIGS[SOURCE]
    return SavedHtmlSpider(
        task=CrawlerTask(
            url=config["url"], source_name=config["source_name"], list_name=SOURCE,
        ),
        crawler_config=load_crawler_config(str(ROOT / config["source_config"])),
        storage=CrawlerStorage(
            source_name="BSP", list_name=SOURCE, base_dir=str(tmp_path),
        ),
        records=[],
    )


def _listing(spider):
    response = HtmlResponse(
        url=spider.task.url,
        body=(HTML_FIXTURES / "listing.html").read_text(encoding="utf-8"), encoding="utf-8",
    )
    return list(spider.parse(response))


@pytest.fixture(scope="module")
def canonical():
    config = WATCHLIST_CONFIGS[SOURCE]
    engines = create_normalization_engines(config)
    rows = [json.loads(line) for line in
            (FIXTURES / f"{SOURCE}_raw_sample.jsonl").read_text(encoding="utf-8").splitlines()]
    return {row["list"]["name"]: normalize_record(row, config, *engines) for row in rows}


def test_listing_keeps_all_role_groups_and_recovers_split_card_photo(spider):
    results = _listing(spider)
    requests = [row for row in results if isinstance(row, Request)]
    listing = [row for row in results if not isinstance(row, Request)]
    people = {row["list"]["name"]: row["list"] for row in listing}
    people.update({row.cb_kwargs["list_data"]["name"]: row.cb_kwargs["list_data"]
                   for row in requests})

    assert len(results) == len(people) == 6
    assert len(requests) == 3
    assert {row["category"] for row in people.values()} == {
        "Monetary Board", "Chairman of the Monetary Board and Governor",
        "Deputy Governors", "Senior Assistant Governors",
        "Assistant Governors/Executive Director II",
    }
    assert people["Split Card Official"]["photo_url"].endswith("/images/split.jpg")
    assert people["Split Card Official"]["office"] == "Financial Supervision Sub-Sector II"
    assert "Board Official" in people
    assert "Senior Official" in people


@pytest.mark.parametrize("name,filename", [
    ("Assistant Official", "profile.html"),
    ("Governor Official", "governor.html"),
])
def test_profile_layouts_keep_full_biography_and_distinct_attachment_roles(spider, name, filename):
    request = next(row for row in _listing(spider)
                   if isinstance(row, Request) and row.cb_kwargs["list_data"]["name"] == name)
    response = HtmlResponse(
        url=request.url, body=(HTML_FIXTURES / filename).read_text(encoding="utf-8"), encoding="utf-8",
    )

    record = next(spider.parse_detail(response, **request.cb_kwargs))

    assert record["detail"]["biography"].replace("\u200b", "") == BIOGRAPHY
    assert record["detail"]["profile_name"] == name.upper()
    assert record["detail"]["photo_url"] == record["list"]["photo_url"]
    assert record["attachments"] == [
        {"type": "Profile", "url": request.url},
        {"type": "Photograph", "url": record["detail"]["photo_url"]},
    ]
    assert Path(record["detail_file_path"]).read_text(encoding="utf-8") == response.text


def test_failed_profile_retains_listing_record_after_retries(spider):
    request = next(row for row in _listing(spider) if isinstance(row, Request))
    failure = SimpleNamespace(request=request, value=RuntimeError("Profile unavailable"))

    record = next(spider._handle_optional_detail_failure(failure))

    assert record["list"] == request.cb_kwargs["list_data"]
    assert record["detail"] == {}
    assert record["detail_url"] == request.url
    assert record["fetch_error"] == "Profile unavailable"
    assert record in spider.records


def test_generated_identity_survives_promotion_and_profile_url_changes():
    record = {"list": {"name": "Example Official", "office": "Original office"}}
    changed = deepcopy(record)
    changed["list"].update(name="EXAMPLE OFFICIAL", office="New office")
    changed["detail_url"] = "https://example.test/new-profile"
    other = {"list": {"name": "Another Official"}}

    rows = PreProcessingEngine().preprocess(
        [record, changed, other], WATCHLIST_CONFIGS[SOURCE]["preprocessing"],
    )

    assert rows[0]["source_record_id"] == rows[1]["source_record_id"]
    assert rows[0]["source_record_id"] != rows[2]["source_record_id"]
    assert all(row["entity_type"] == "Individual" for row in rows)


def test_mapping_keeps_three_attachment_roles_even_when_images_share_a_url(canonical):
    record = canonical["Assistant Official"]
    assert record["Attachments"] == [
        {"Type": "Thumbnail", "URL": "https://example.test/images/assistant.jpg"},
        {"Type": "Photograph", "URL": "https://example.test/images/assistant.jpg"},
        {"Type": "Profile", "URL": "https://example.test/profiles/assistant.aspx"},
    ]
    assert record["Comments"] == [{"type": "Additional Info", "text": BIOGRAPHY}]
    assert record["Occupations"][0]["Department"] == "Comptrollership Sub-Sector"
    assert record["Occupations"][0]["Note"] == "Assistant Governor | Internal Audit Office"


def test_mapping_creates_jurisdiction_without_a_country_field_in_raw(canonical):
    for record in canonical.values():
        assert record["Countries"] == [{
            "CountryType": "Jurisdiction", "CountryCode": "PH",
            "CountryName": "Philippines", "Note": "",
        }]


@pytest.mark.parametrize("name,types", [
    ("Listing Official", ["Thumbnail"]),
    ("Unavailable Official", ["Thumbnail", "Profile"]),
])
def test_missing_profile_detail_does_not_create_empty_photographs_or_comments(canonical, name, types):
    record = canonical[name]
    assert [item["Type"] for item in record["Attachments"]] == types
    assert record["Comments"] == []

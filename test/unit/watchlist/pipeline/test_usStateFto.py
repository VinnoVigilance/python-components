"""FTO source HTML through the real YAML, workbook rules, and normalizers."""

from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scrapy.http import HtmlResponse

from ingestion.crawler.models import CrawlerTask
from ingestion.crawler.spiders.savedHtmlSpider import SavedHtmlSpider
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline.watchlistNormalizationService import (
    create_normalization_engines,
    normalize_record,
)
from transforms.preProcessingEngine import PreProcessingEngine


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[4]
CONFIG = WATCHLIST_CONFIGS["US_STATE_FTO"]


@pytest.fixture(scope="module")
def records():
    extracted = []
    spider = SavedHtmlSpider(
        task=CrawlerTask(
            url=CONFIG["url"], source_name=CONFIG["source_name"],
            list_name=CONFIG["list_name"], source_config_path=CONFIG["source_config"],
        ),
        crawler_config=yaml.safe_load((ROOT / CONFIG["source_config"]).read_text()),
        storage=None,
        records=extracted,
    )
    fixture = ROOT / "test/fixtures/watchlist/us_state_fto/listing.html"
    yielded = list(spider.parse(HtmlResponse(
        url=CONFIG["url"], body=fixture.read_bytes(), encoding="utf-8",
    )))
    assert yielded == extracted
    raw = PreProcessingEngine().preprocess(deepcopy(extracted), CONFIG["preprocessing"])
    engines = create_normalization_engines(CONFIG)
    canonical = [normalize_record(record, CONFIG, *engines) for record in raw]
    return extracted, raw, {r["Names"][0]["Name"]: r for r in canonical}


def aliases(record):
    return {a["Alias"]: a for a in record["Aliases"]}


def test_only_designated_rows_are_imported(records):
    extracted, raw, canonical = records
    assert len(extracted) == len(raw) == len(canonical) == 9
    assert not any("al-Nusrah Front" in r["list"]["name"] for r in extracted)
    assert len({r["source_record_id"] for r in raw}) == 9


def test_isis_former_name_and_shared_amendment_date(records):
    row = records[2]["ISIS"]
    names = aliases(row)
    assert set(names) == {"al-Qa’ida in Iraq", "Islamic State of Iraq", "al-Hayat Media Center", "Amaq News Agency"}
    assert names["al-Qa’ida in Iraq"]["AliasType"] == "Former Name"
    assert names["al-Qa’ida in Iraq"]["Note"] == ""
    assert "January 26, 2012" in names["Islamic State of Iraq"]["Note"]
    for name in ("al-Hayat Media Center", "Amaq News Agency"):
        assert names[name]["AliasType"] == "AKA"
        assert "March 22, 2019" in names[name]["Note"]
    assert row["DateAdded"] == row["Measures"][0]["EffectiveDate"] == "2004-12-17"
    assert row["Dates"][0]["FullDate"] == "2004-12-17"


def test_abbreviation_and_multi_name_amendments(records):
    names = aliases(records[2]["Lashkar-e-Tayyiba"])
    assert len(names) == 8
    assert names["LeT"]["AliasType"] == "AKA"
    assert names["LeT"]["Note"] == ""
    for name in ("Tehrik-e-Tahafuz Qibla Awwal", "Tehrik-e-Hurmat-e-Rasool", "Al-Anfal Trust"):
        assert "June 26, 2014" in names[name]["Note"]
    assert "July 18, 2025" in names["The Resistance Front"]["Note"]


def test_aka_former_names_and_distinct_geographic_entities(records):
    canonical = records[2]
    assert aliases(canonical["al-Mulathamun Battalion"])["al-Murabitoun"]["AliasType"] == "AKA"
    sinai = canonical["ISIS-Sinai Province"]
    assert aliases(sinai)["Ansar Bayt al-Maqdis"]["AliasType"] == "Former Name"
    assert "September 30, 2015" in aliases(sinai)["ISIL Sinai Province"]["Note"]
    for place in ("Benghazi", "Darnah", "Tunisia"):
        assert canonical["Ansar al-Shari’a in " + place]["Aliases"] == []


def test_primary_name_amendment_is_preserved_without_self_alias(records):
    row = records[2]["Jaysh al-Adl"]
    assert set(aliases(row)) == {"Jundallah"}
    assert "July 2, 2019" in row["Names"][0]["Note"]


def test_source_identity_risk_and_original_text_survive_real_workbook_mapping(records):
    _, raw, canonical = records
    by_id = {r["EntityId"]: r for r in canonical.values()}
    for original in raw:
        row = by_id[original["source_record_id"]]
        assert row["EntityType"] == "Entity"
        source = row["Sources"][0]
        assert source["SourceName"] == "US_STATE"
        assert source["ListName"] == "US_STATE_FTO"
        assert source["SourceURL"] == CONFIG["url"]
        assert source["DatasetCategory"] == ["Sanctions"]
        assert source["SourceRecordId"] == original["source_record_id"]
        assert row["RiskCategory"]["RiskModelEnabled"] is True
        assert {(x["Type"], x["Value"]) for x in row["RiskCategory"]["Labels"]} == {
            ("List", "Sanctions"), ("List", "Terrorism / Terrorist Financing"),
        }
        assert row["Comments"][0]["Text"] == original["list"]["name"]
        assert row["Names"][0]["Normalized_Name"]
        assert row["Measures"][0]["Status"] == "Active"


def test_record_ids_do_not_depend_on_row_order(records):
    extracted, raw, _ = records
    reversed_records = PreProcessingEngine().preprocess(
        deepcopy(list(reversed(extracted))), CONFIG["preprocessing"],
    )
    expected = {r["list"]["name"]: r["source_record_id"] for r in raw}
    assert {r["list"]["name"]: r["source_record_id"] for r in reversed_records} == expected

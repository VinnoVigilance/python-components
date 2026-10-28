"""Verify PDG-98 coverage and canonical field ownership with frozen XML."""

from pathlib import Path

import pytest

from parsing.xmlParser import XmlParser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline.watchlistNormalizationService import create_normalization_engines, normalize_record
from transforms.preProcessingEngine import PreProcessingEngine


pytestmark = pytest.mark.unit
SOURCE = "HKPF_WANTED_PERSONS_NATIONAL_SECURITY"
FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/sources/hkpf_national_security/en.xml"


@pytest.fixture(scope="module")
def records():
    config = WATCHLIST_CONFIGS[SOURCE]
    parsed = list(XmlParser().parse(str(FIXTURE), config=config))
    processed = PreProcessingEngine().preprocess(parsed, config["preprocessing"])
    engines = create_normalization_engines(config)
    return parsed, processed, {r["id"]: normalize_record(r, config, *engines) for r in processed}


def test_complete_xml_roster_has_34_distinct_people(records):
    parsed, processed, canonical = records
    assert len(parsed) == len(processed) == len(canonical) == 34
    assert all(r["EntityType"] == "Individual" and r["Names"] for r in canonical.values())


def test_profile_details_and_urls_stay_with_the_person(records):
    row = records[2]["20240001"]
    assert row["Names"][0]["Name"] == "LAU Ka-man"
    assert row["Dates"][0]["FullDate"] == "1995-02-20"
    assert row["DateUpdated"] == "2025-12-24"
    assert row["Sources"][0]["SourceURL"].endswith("detail.html?id=20240001")
    assert {a["Type"] for a in row["Attachments"]} == {"Photograph", "Profile"}
    assert next(a["URL"] for a in row["Attachments"] if a["Type"] == "Photograph").endswith("/20240001.jpg")


def test_multiple_case_references_do_not_replace_person_identity(records):
    row = records[2]["20230012"]
    assert row["EntityId"] == "20230012"
    assert {i["Number"] for i in row["Identifiers"]} == {"NSD RN 23000245", "NSD RN 22000165"}


def test_reward_is_currency_amount_and_expiry_is_not_a_person_end_date(records):
    row = records[2]["20240001"]
    info = {i["Type"]: i["Value"] for i in row["additionalInfo"]}
    assert info["Reward"] == "HK$1,000,000"
    assert info["Reward Details"] == "Reward No. 1 of 2024 (Renewal) ; Date of Expiry: 2026-12-23"
    assert info["Status"] == "Wanted Person"
    assert {d["Type"] for d in row["Dates"]} == {"Birth Date"}
    assert row["Measures"] == []
    assert any(i["Type"] == "Reward" and i["Value"] == "HK$200,000" for i in records[2]["20250001"]["additionalInfo"])


def test_html_and_reporting_contacts_do_not_become_person_contacts(records):
    for row in records[2].values():
        assert row["Contacts"] == []
        assert all("<p" not in c["text"] and "<br" not in c["text"] for c in row["Comments"])

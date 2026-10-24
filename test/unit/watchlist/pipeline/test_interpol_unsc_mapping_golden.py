"""Pin PDG-102 field ownership using synthetic profiles and the real workbooks."""

import json
from pathlib import Path

import pytest

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline.watchlistNormalizationService import (
    create_normalization_engines,
    normalize_record,
)

pytestmark = pytest.mark.unit
SOURCE = "INTERPOL_UNSC_SPECIAL_NOTICES"
FIXTURE = (Path(__file__).resolve().parents[3] / "fixtures" / "sources"
           / f"{SOURCE}_raw_sample.jsonl")


@pytest.fixture(scope="module")
def canonical():
    config = WATCHLIST_CONFIGS[SOURCE]
    engines = create_normalization_engines(config)
    rows = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
    return {row["source_record_id"]: normalize_record(row, config, *engines) for row in rows}


def test_birth_country_and_nationality_remain_distinct(canonical):
    record = canonical["2026/10201"]
    assert {"CountryType": "Origin", "CountryCode": "SD",
            "CountryName": "Sudan", "Note": "Country of birth"} in record["Countries"]
    assert any(c["CountryType"] == "Nationality" and c["CountryCode"] == "SD"
               for c in record["Countries"])
    assert record["Places"][0]["Place"] == "Test locality"


def test_document_notes_and_dates_do_not_leak_into_un_reference(canonical):
    identifiers = canonical["2026/10201"]["Identifiers"]
    passport = next(i for i in identifiers if i["Number"] == "TEST-PASSPORT-1")
    assert passport["Type"] == "Passport"
    assert passport["IssuedDate"] == "2010-01-01"
    assert passport["ExpiryDate"] == "2030-01-01"
    assert "Citizenship: SD" in passport["Note"]
    assert "Place of issue: Test issue location" in passport["Note"]
    empty = next(i for i in identifiers if i["Number"] == "TEST-ID-2")
    assert empty["Note"] == ""
    reference = next(i for i in identifiers if i["Type"] == "Reference Number")
    assert reference["Number"] == "SDi.TEST"
    assert all(not reference[key] for key in ("IssuedDate", "ExpiryDate", "IssuingCountry", "Note"))


def test_alias_context_is_preserved_in_labeled_notes(canonical):
    alias = canonical["2026/10201"]["Aliases"][0]
    assert alias["Alias"] == "ALIAS EXAMPLE"
    for value in ("Qualification: OTHER SPELLING", "Birth date: 1970",
                  "Birthplace: Alias locality", "Birth country: SD",
                  "Original-script name:", "اسم", "تجريبي"):
        assert value in alias["Note"]


def test_connected_person_keeps_external_id_out_of_internal_id(canonical):
    relationships = canonical["2026/10201"]["Relationships"]
    linked = next(r for r in relationships if r["RelationType"] == "Connected To")
    assert linked["RelatedEntityName"] == "TEST LINKED"
    assert "2026/10202" in linked["Note"]
    assert "https://example.test/un/persons/2026-10202" in linked["Note"]
    assert all(not r["RelatedEntityID"] for row in canonical.values() for r in row["Relationships"])
    associate = next(r for r in relationships if r["RelationType"] == "Associate Of")
    assert "SDi.RELATED" in associate["Note"]
    assert "1971/02/03" in associate["Note"]


def test_only_existing_image_urls_are_mapped(canonical):
    individual = canonical["2026/10201"]
    assert {"Type": "Thumbnail", "URL": "https://example.test/un/persons/2026-10201/images/photo-1"} in individual["Attachments"]
    assert not any(a["Type"] in {"Thumbnail", "Photograph"}
                   for a in canonical["2026/10203"]["Attachments"])


def test_entity_measures_native_name_and_identifier_ownership(canonical):
    entity = canonical["2026/10203"]
    assert {m["MeasureType"] for m in entity["Measures"]} == {"Asset Freeze", "Arms Embargo"}
    assert any(n["NameType"] == "Original Script" and n["Name"] == "كيان تجريبي"
               for n in entity["Names"])
    refs = [i for i in entity["Identifiers"] if i["Type"] == "Reference Number"]
    assert len(refs) == 1 and refs[0]["Number"] == "QDe.TEST"
    assert all(not i["IssuingCountry"] and not i["IssuedDate"] for i in entity["Identifiers"])
    assert any(i["Type"] == "Registration Number" and i["Number"] == "TEST-REG-1"
               for i in entity["Identifiers"])
    assert any(a["Description"] == "financial services" for a in entity["BusinessActivities"])
    assert any(a["Type"] == "Legal Form" and a["Value"] == "Company" for a in entity["additionalInfo"])
    assert any("1977/03/30" in r["Note"] for r in entity["Relationships"])


def test_uncertain_entity_values_remain_unclassified(canonical):
    extras = [a["Value"] for a in canonical["2026/10203"]["additionalInfo"] if a["Type"] == "Other"]
    assert any("additional_informations:" in value and "1234" in value and "5678" in value for value in extras)
    assert any("from_chamber_of_commerce:" in value and "Source-provided description" in value for value in extras)

"""Verify PDG-119 case expansion and person-specific canonical fields."""

from copy import deepcopy
from pathlib import Path

import pytest

from parsing.xmlParser import XmlParser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline.watchlistNormalizationService import create_normalization_engines, normalize_record
from transforms.preProcessingEngine import PreProcessingEngine


pytestmark = pytest.mark.unit
SOURCE = "HKPF_WANTED_PERSONS_WITH_REWARD_NOTICES"
FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/sources/hkpf_reward_notices/w_en.xml"


@pytest.fixture(scope="module")
def cases():
    return list(XmlParser().parse(str(FIXTURE), config=WATCHLIST_CONFIGS[SOURCE]))


@pytest.fixture(scope="module")
def records(cases):
    config = WATCHLIST_CONFIGS[SOURCE]
    processed = PreProcessingEngine().preprocess(deepcopy(cases), config["preprocessing"])
    engines = create_normalization_engines(config)
    canonical = {r["person_name"]: normalize_record(r, config, *engines) for r in processed}
    return processed, canonical


def test_five_cases_expand_into_seven_distinct_people(cases, records):
    processed, canonical = records
    assert len(cases) == 5
    assert len(processed) == len(canonical) == len({r["source_record_id"] for r in processed}) == 7
    assert set(canonical) == {"KWOK Yau-man", "LAU Ho-yin", "CHAN Wai-lun", "LAU Ping-fai", "NG Yik-tung", "SO Kai-man", "CHAN Fok-ching"}


def test_shared_case_does_not_mix_birth_dates_or_photographs(records):
    people = records[1]
    for name, date, image in [("KWOK Yau-man", "1970-11-10", "/2018-1a.jpg"), ("LAU Ho-yin", "1976-05-30", "/2018-1b.jpg")]:
        row = people[name]
        assert row["Dates"][0]["FullDate"] == date
        assert next(a["URL"] for a in row["Attachments"] if a["Type"] == "Photograph").endswith(image)
        assert row["Sources"][0]["SourceRecordId"] == "20181009114937"
    assert people["KWOK Yau-man"]["EntityId"] != people["LAU Ho-yin"]["EntityId"]


def test_xml_and_item_order_do_not_change_identity_or_image(cases, records):
    reordered = deepcopy(list(reversed(cases)))
    for case in reordered:
        if isinstance(case.get("item"), list):
            case["item"].reverse()
    result = PreProcessingEngine().preprocess(reordered, WATCHLIST_CONFIGS[SOURCE]["preprocessing"])
    expected = {r["person_name"]: (r["source_record_id"], r["photo_url"]) for r in records[0]}
    assert {r["person_name"]: (r["source_record_id"], r["photo_url"]) for r in result} == expected


def test_narrative_only_people_have_no_invented_photo_or_birth_date(records):
    for name in ["NG Yik-tung", "SO Kai-man"]:
        row = records[1][name]
        assert row["Dates"] == []
        assert [a["Type"] for a in row["Attachments"]] == ["Profile"]


@pytest.mark.parametrize("name,month,year", [("LAU Ping-fai", "06", "1945"), ("CHAN Fok-ching", "09", "1977")])
def test_partial_birth_dates_do_not_invent_a_day(records, name, month, year):
    birth = records[1][name]["Dates"][0]
    assert (birth["Month"], birth["Year"]) == (month, year)
    assert birth["Day"] == birth["FullDate"] == ""


def test_masked_identity_and_multiple_case_references_remain_distinct(records):
    chan = records[1]["CHAN Wai-lun"]
    assert {i["Number"] for i in chan["Identifiers"] if i["Type"] == "Reference Number"} == {"MOS CCR 92702", "ST RN 03011497"}
    for row in records[1].values():
        identity = next(i for i in row["Identifiers"] if i["Type"] == "National ID")
        assert "XXX" in identity["Number"] and not identity["Number"].endswith(".")
        assert "incomplete" in identity["Note"]


def test_aliases_rewards_and_case_context_are_preserved(records):
    row = records[1]["KWOK Yau-man"]
    assert {a["Alias"] for a in row["Aliases"]} == {"Ka Shu", "Ah Ka"}
    info = {i["Type"]: i["Value"] for i in row["additionalInfo"]}
    assert info["Reward"] == "HKD$400,000"
    assert info["Reward Details"] == "Reward No. 1 of 2018 (Renewal) ; Date of Expiry: 2027-10-01"
    assert info["Status"] == "Wanted Person"
    assert row["Measures"] == row["Contacts"] == []
    assert {c["type"] for c in row["Comments"]} == {"Remarks", "Footnote"}
    assert all("<br" not in c["text"] for c in row["Comments"])


def test_partial_narrative_only_format_change_is_rejected(cases):
    case = deepcopy(next(c for c in cases if c["id"] == "20140329161339"))
    case["other"] = case["other"].replace("Male SO Kai-man,", "Male SO Kai-man;")
    with pytest.raises(ValueError, match="boundary"):
        PreProcessingEngine().preprocess([case], WATCHLIST_CONFIGS[SOURCE]["preprocessing"])

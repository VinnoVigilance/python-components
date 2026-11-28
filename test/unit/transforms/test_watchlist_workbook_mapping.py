"""Check watchlist output using the configured ingestion and workbook rules."""

from pathlib import Path
from copy import deepcopy

import pandas as pd
import pytest

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from transforms.fieldMapper import MappingEngine, load_rules
from transforms.preNormalization import PreNormalizationEngine
from transforms.preProcessingEngine import PreProcessingEngine


RULES = Path(__file__).resolve().parents[3] / "data" / "rules"
pytestmark = pytest.mark.unit


@pytest.mark.parametrize("config_key,source_name,list_name,entity_type", [
    ("BIS_DPL", "BIS", "BIS_DPL", "Individual"),
    ("BIS_DPL", "BIS", "BIS_DPL", "Entity"),
    ("SC_PH_INCUMBENT_JUSTICES", "SC_PH", "SC_PH_INCUMBENT_JUSTICES", "Individual"),
    ("SEC_US_PAUSE", "SEC_US", "SEC_US_PAUSE", "Entity"),
    ("CRIMESTOPPERS_UK_MOST_WANTED", "CRIMESTOPPERS_UK", "CRIMESTOPPERS_UK_MOST_WANTED", "Individual"),
])
def test_renamed_sources_match_config_and_workbook(config_key, source_name, list_name, entity_type):
    config = WATCHLIST_CONFIGS[config_key]
    mapper = MappingEngine(load_rules(RULES / "mapping.xlsx", config["list_name"]))
    result = mapper.map_record({"entity_type": entity_type})

    source = result["Sources"][0]
    assert source["SourceName"] == config["source_name"] == source_name
    assert source["ListName"] == config["list_name"] == list_name


@pytest.fixture(scope="module")
def pre_normalizer():
    return PreNormalizationEngine(
        pd.read_excel(RULES / "preNormalization.xlsx", keep_default_na=False),
        pd.read_excel(RULES / "sourceConfig.xlsx", keep_default_na=False),
    )


@pytest.fixture(scope="module", params=["OFAC-SDN", "OFAC-NON-SDN"])
def ofac(request):
    source = request.param
    return source, MappingEngine(load_rules(RULES / "mapping.xlsx", source))


def map_record(ofac, pre_normalizer, authorities=None):
    source, mapper = ofac
    record = {
        "generalInfo": {"entityType": {"text": "Individual"}},
        "sanctionsPrograms": {"sanctionsProgram": [{"text": "SDGT"}]},
    }
    if authorities is not None:
        record["legalAuthorities"] = {"legalAuthority": authorities}
    processed, = PreProcessingEngine().preprocess(
        [record], WATCHLIST_CONFIGS[source]["preprocessing"]
    )
    return mapper.map_record(pre_normalizer.pre_normalize_record(source, processed))


@pytest.mark.parametrize("authorities, suffix", [
    ([{"text": "Authority A"}, {"text": "Authority B"}],
     " Record legal authorities: Authority A; Authority B"),
    (None, ""),
])
def test_programme_note_uses_description_and_all_authorities(
    ofac, pre_normalizer, authorities, suffix,
):
    result = map_record(ofac, pre_normalizer, authorities)
    program, = result["Programs"]
    assert program["Program"] == "SDGT"
    assert program["Note"] == (
        "Programme description: Global terrorism sanctions under 31 CFR Part 594." + suffix
    )


def test_risk_category_keeps_list_and_programme_based_labels(ofac, pre_normalizer):
    result = map_record(ofac, pre_normalizer)
    assert result["RiskCategory"]["RiskModelEnabled"] is True
    assert {"Type": "List", "Value": "Sanctions"} in result["RiskCategory"]["Labels"]
    assert {
        "Type": "Record", "Value": "Terrorism / Terrorist Financing"
    } in result["RiskCategory"]["Labels"]


def map_workbook_record(source, raw, pre_normalizer):
    normalized = pre_normalizer.pre_normalize_record(source, raw)
    return MappingEngine(load_rules(RULES / "mapping.xlsx", source)).map_record(normalized)


def test_idb_maps_each_prohibited_practice(pre_normalizer):
    source = "IDB_SANCTIONED_ENTITIES"
    raw = {"Entity": "Firm", "Prohibited Practice": "Fraud, Corruption"}
    result = map_workbook_record(source, raw, pre_normalizer)
    assert result["Sources"][0]["DatasetCategory"] == "Regulatory"
    assert result["RiskCategory"]["RiskModelEnabled"] is True
    assert result["RiskCategory"]["Labels"] == [
        {"Type": "List", "Value": "Regulatory"},
        {"Type": "Record", "Value": "Crime-Fraud"},
        {"Type": "Record", "Value": "Crime-Corruption"},
    ]


@pytest.mark.parametrize("entity_type", ["Individual", "Entity"])
def test_dnfbp_preserves_explicit_disabled_risk_model(entity_type):
    mapper = MappingEngine(load_rules(RULES / "mapping.xlsx", "DNFBP"))
    result = mapper.map_record({"entity_type": entity_type})
    assert result["RiskCategory"]["RiskModelEnabled"] is False


@pytest.mark.parametrize("listing_details", [False, True])
def test_dbm_individual_only_keeps_office_context_without_entity_links(pre_normalizer, listing_details):
    source = "DBM_PH_GOVERNMENT_OFFICIALS_DIRECTORY"
    # This is an active gate on the committed, user-managed workbook rules.
    raw = {"printed_name": "EXAMPLE PRESIDENT", "role": "President", "record_kind": "listing",
           "source_path": ["national_government", "executive_branch", "records", 0],
           "name_source": {"source_page": 7, "text": "EXAMPLE PRESIDENT"},
           "context": [{"source_path": ["office_of_the_president"], "fields": {
               "heading": {"source_page": 7, "text": "OFFICE OF THE PRESIDENT"},
               "header_lines": [{"source_page": 7, "text": "12 Example Street"},
                                {"source_page": 7, "text": "Trunkline: (02) 8000-0000"}],
           }}]}
    if listing_details:
        raw["address_lines"] = [{"source_page": 7, "text": "34 Listing Street"}]
        raw["contact_lines"] = [
            {"source_page": 7, "text": "(02) 8000-1111 loc. 1234"},
            {"source_page": 7, "text": "official@example.gov.ph"},
        ]
    prepared = PreProcessingEngine().preprocess([raw], WATCHLIST_CONFIGS[source]["preprocessing"])
    person, = [map_workbook_record(source, row, pre_normalizer) for row in prepared]
    assert person["EntityType"] == "Individual"
    assert person["RiskCategory"] == {"RiskModelEnabled": False,
                                      "Labels": [{"Type": "List", "Value": "PEP"}]}
    assert person["Occupations"][0]["Organization"] == "OFFICE OF THE PRESIDENT"
    assert person["Occupations"][0]["OccupationTitle"] == "President"
    assert person["EntityId"] == prepared[0]["source_record_id"]
    assert person["Sources"][0]["SourceRecordId"] == person["EntityId"]
    assert person["Sources"][0]["Note"] == "PDF page: 7"
    assert not person["Relationships"]
    if listing_details:
        assert [item["Value"] for item in person["Contacts"]] == [
            "(02) 8000-1111 ext. 1234", "official@example.gov.ph",
        ]
        assert [item["FullAddress"] for item in person["Addresses"]] == ["34 Listing Street"]
        assert "Organization office address: 12 Example Street" in person["Occupations"][0]["Note"]
    else:
        assert not person["Contacts"]
        assert person["Addresses"][0]["FullAddress"] == "12 Example Street"
        assert "Shared office address" in person["Addresses"][0]["Note"]
        assert "12 Example Street" not in person["Occupations"][0]["Note"]
    assert "Organization phone: (02) 8000-0000" in person["Occupations"][0]["Note"]
    from transforms.postNormalization import PostNormalizationEngine
    post = PostNormalizationEngine(pd.read_excel(RULES / "postNormalization.xlsx"), WATCHLIST_CONFIGS[source])
    for record in (person,):
        expected = deepcopy(record)
        canonical = post.post_normalize_record(record)
        assert canonical["RiskCategory"] == expected["RiskCategory"]
        assert canonical["EntityId"] == expected["EntityId"]
        assert canonical["Relationships"] == expected["Relationships"]


def test_dfat_keeps_description_and_instrument_with_each_programme(pre_normalizer):
    result = map_workbook_record("DFAT", {
        "Type": "Individual",
        "ProgrammeDesignations": [
            {"Committees": "Autonomous (Russia)", "instrument": "Russia instrument"},
            {"Committees": "Autonomous (Ukraine)", "instrument": "Ukraine instrument"},
        ],
    }, pre_normalizer)
    assert [(p["Program"], p["Note"]) for p in result["Programs"]] == [
        ("Autonomous (Russia)", "Programme description: Australian autonomous sanctions concerning Russia. Designation instrument: Russia instrument"),
        ("Autonomous (Ukraine)", "Programme description: Australian autonomous sanctions concerning Ukraine. Designation instrument: Ukraine instrument"),
    ]


def test_canada_enriches_bilingual_country_label(pre_normalizer):
    result = map_workbook_record("CANADA_CONSOLIDATED_SANCTIONS", {
        "entity_type": "Individual",
        "Country-Pays": "Russia / Russie",
    }, pre_normalizer)
    program, = result["Programs"]
    assert program["Program"] == "Russia"
    assert program["Note"] == "Programme description: Canadian sanctions concerning Russia."


@pytest.mark.parametrize("source, raw, code, description", [
    ("UKSL", {"IndividualEntityShip": "Individual", "RegimeName":
              "The Afghanistan (Sanctions) (EU Exit) Regulations 2020"},
     "The Afghanistan (Sanctions) (EU Exit) Regulations 2020", "UK sanctions concerning Afghanistan."),
    ("UN-SANCTIONS", {"_root_tag": "INDIVIDUAL", "UN_LIST_TYPE": "CAR"},
     "CAR", "UN sanctions concerning the Central African Republic."),
    ("SECO-SANCTIONS", {"Target type": "ORGANISATION", "Sanctions program": "Belarus"},
     "Belarus", "Swiss sanctions concerning Belarus."),
    ("EU-FINANCIAL-SANCTIONS", {"subjectType": {"code": "person"},
                               "regulation": {"programme": "AFG"}},
     "AFG", "EU sanctions concerning Afghanistan and the Taliban."),
    ("ATC-DESIGNATED-TERRORIST-INDIVIDUALS",
     {"entity_type": "Individual", "list": {"category": "ATTG"}},
     "ATTG", "Philippine Anti-Terrorism Council designations under Resolution 43 (2023)."),
    ("ATC-DESIGNATED-TERRORIST-GROUPS",
     {"entity_type": "Entity", "list": {"category": "CTG"}},
     "CTG", "Philippine Anti-Terrorism Council designation category for communist terrorist groups."),
    ("INTERPOL_UNSC_SPECIAL_NOTICES",
     {"list": {"record_type": "Individual"}, "detail": {"un_resolution": "1533"}},
     "1533", "UN Security Council resolution 1533 committee reference for sanctions concerning the Democratic Republic of the Congo."),
])
def test_programme_description_reaches_the_mapped_note(
    pre_normalizer, source, raw, code, description,
):
    result = map_workbook_record(source, raw, pre_normalizer)
    program, = result["Programs"]
    assert program["Program"] == code
    assert program["Note"] == f"Programme description: {description}"
    if source == "INTERPOL_UNSC_SPECIAL_NOTICES":
        assert result["Sources"][0]["DatasetCategory"] == "Sanctions"

from copy import deepcopy

import pytest

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from transforms.preProcessingEngine import PreProcessingEngine


pytestmark = pytest.mark.unit
RULES = WATCHLIST_CONFIGS["DBM_PH_GOVERNMENT_OFFICIALS_DIRECTORY"]["preprocessing"]


def line(text, top=100, page=1):
    return {"text": text, "source_page": page, "top": top}


def listing(**values):
    return {
        "record_kind": "listing",
        "source_path": ["national_government", "executive_branch", "records", 0],
        **values,
    }


def prepare(record):
    engine = PreProcessingEngine()
    prepared = engine.prepare_dbm_listing(record, {})
    return engine.generate_composite_id(prepared, RULES[0]["config"]["person_id"])


@pytest.mark.parametrize("text,numbers", [
    ("Trunk Line: (02) 8952-5700; 8931-8207", ["(02) 8952-5700", "8931-8207"]),
    ("(02) 8521-0306; 8521-0307", ["(02) 8521-0306", "8521-0307"]),
    ("Hotlines: (02) 8722-1144; 8722-1155", ["(02) 8722-1144", "8722-1155"]),
    ("TL: 929-1753", ["929-1753"]),
    ("(02) 8817-5001 TL; 8816-4941 F", ["(02) 8817-5001", "8816-4941"]),
])
def test_office_header_keeps_contact_fragments_out_of_postal_address(text,numbers):
    result = {"address_text": None, "phones": [], "emails": [], "websites": [], "contact_unparsed": []}
    PreProcessingEngine()._dbm_office_contacts([line("12 Example Street", 10), line(text, 20)], result)
    assert result["address_text"] == "12 Example Street"
    assert [p["number"] for p in result["phones"]] == numbers
    assert result["addresses"] == [{"text": "12 Example Street", "label": ""}]
    assert not result["contact_unparsed"]


def test_office_header_preserves_separate_main_and_extension_addresses():
    result = {"address_text": None, "phones": [], "emails": [], "websites": [], "contact_unparsed": []}
    PreProcessingEngine()._dbm_office_contacts([
        line("Main Office: 32/F The Mega Tower,", 10), line("Mandaluyong City", 20),
        line("Extension: 12 Escriva Drive, Pasig City", 30),
        line("Trunkline: (02) 8631-0945 to 68", 40),
    ], result)
    assert result["addresses"] == [
        {"text": "32/F The Mega Tower, Mandaluyong City", "label": "Main Office"},
        {"text": "12 Escriva Drive, Pasig City", "label": "Extension"},
    ]


def test_locality_qualified_header_phone_is_not_a_postal_address():
    result = {"address_text": None, "phones": [], "emails": [], "websites": [], "contact_unparsed": []}
    PreProcessingEngine()._dbm_office_contacts([
        line("La Vista del Mar, Zamboanga City", 10),
        line("Manila: (032) 8829-1787 F", 20),
    ], result)
    assert result["addresses"] == [{"text": "La Vista del Mar, Zamboanga City", "label": ""}]
    assert result["phones"] == [{"number": "(032) 8829-1787", "extension": None, "kind": "fax", "label": "Manila"}]


def test_unit_number_range_is_preserved_as_address_not_telephone():
    result = {"address_text": None, "phones": [], "emails": [], "websites": [], "contact_unparsed": []}
    PreProcessingEngine()._dbm_office_contacts([line("Unit 1501-1502 Ayala Life-FGU Center")], result)
    assert not result["phones"]
    assert result["addresses"][0]["text"] == "Unit 1501-1502 Ayala Life-FGU Center"


def test_dataset_emits_only_named_individuals_and_preserves_office_context():
    context = [{"source_path": ["departments", 0], "fields": {
        "heading": line("DEPARTMENT OF EXAMPLE"),
        "header_lines": [line("Trunkline: (02) 8000-0000")],
    }}]
    raw = [listing(printed_name="FIRST OFFICIAL", role="Secretary", context=context),
           listing(printed_name="SECOND OFFICIAL", role="Undersecretary", context=context),
           listing(printed_name="VACANT", vacant=True, context=context),
           listing(record_kind="office_contact", heading=line("CONTACT CENTER"), context=context)]
    original = deepcopy(raw)
    output = PreProcessingEngine().preprocess(raw, RULES)
    assert len(output) == 2
    for person, source in zip(output, raw):
        assert person["entity_type"] == "Individual"
        assert person["source_record_id"] == prepare(source)["source_record_id"]
        assert person["source_evidence"] == source
        assert not person.get("relationships")
        assert not person["phones"]
        assert "Organization phone: (02) 8000-0000" in person["occupation_note"]
    assert raw == original


def test_individual_keeps_printed_institution_listing_contacts():
    raw = listing(printed_name="EXAMPLE OFFICIAL", role="President",
                  source_path=["corporations_and_colleges", "records", 0],
                  office_lines=[line("EXAMPLE UNIVERSITY")],
                  address_lines=[line("12 Example Street")], contact_lines=[line("office@example.edu")])
    person, = PreProcessingEngine().preprocess([raw], RULES)
    assert person["organization_name"] == "EXAMPLE UNIVERSITY"
    assert person["emails"] == ["office@example.edu"]
    assert person["address_text"] == "12 Example Street"
    assert person["source_evidence"] == raw


def test_related_agency_name_remains_occupation_context():
    raw = listing(printed_name="EXAMPLE OFFICIAL", context=[{
        "source_path": ["department_rosters", "related_rosters", 0],
        "fields": {"heading": line("NATIONAL ECONOMIC"),
                   "heading_continuation_lines": [line("AND DEVELOPMENT AUTHORITY")]},
    }])
    person, = PreProcessingEngine().preprocess([raw], RULES)
    assert person["organization_name"] == "NATIONAL ECONOMIC AND DEVELOPMENT AUTHORITY"


def test_institution_wrapped_address_is_retained_as_occupation_context():
    raw = listing(printed_name="EXAMPLE OFFICIAL", context=[{
        "source_path": ["office"], "fields": {
            "heading": line("OFFICE OF EXAMPLE"),
            "header_lines": [line("(20th CONGRESS)", 5),
                             line("11/F Robinson’s Cybergate Plaza, EDSA", 10),
                             line("cor. Pioneer St., Mandaluyong City", 20),
                             line("Trunkline: (02) 8532-5942", 30)],
        },
    }])
    person, = PreProcessingEngine().preprocess([raw], RULES)
    details = person["organization_details"]
    assert details["address_text"] == "11/F Robinson’s Cybergate Plaza, EDSA cor. Pioneer St., Mandaluyong City"
    assert details["phones"][0]["number"] == "(02) 8532-5942"
    assert details["contact_unparsed"] == ["(20th CONGRESS)"]
    assert not person["phones"]


def test_hierarchy_joins_department_heading_and_uses_only_active_group():
    record = listing(
        printed_name="REYNALDO R. CANCIO",
        role_lines=[line("Assistant Secretary")],
        office_lines=[line("Policy and Planning Group")],
        context=[{"source_path": ["departments", 3], "fields": {
            "heading": line("DEPARTMENT OF ECONOMY, PLANNING,"),
            "heading_continuation_lines": [line("AND DEVELOPMENT")],
            "group_headings": [line("REGIONAL OFFICES"), line("ATTACHED AGENCIES")],
        }}],
    )
    original = deepcopy(record)
    result = prepare(record)
    assert result["department_name"] == "DEPARTMENT OF ECONOMY, PLANNING, AND DEVELOPMENT"
    assert result["role_text"] == "Assistant Secretary"
    assert result["hierarchy"][-1] == {"kind": "office", "name": "Policy and Planning Group"}
    assert not any(item["name"] in {"REGIONAL OFFICES", "ATTACHED AGENCIES"} for item in result["hierarchy"])
    assert result["source_evidence"] == record == original
    result["source_evidence"]["context"][0]["fields"]["heading"]["text"] = "changed"
    assert record == original


def test_configured_composite_id_tracks_listing_identity_not_evidence_or_contacts():
    config = WATCHLIST_CONFIGS["DBM_PH_GOVERNMENT_OFFICIALS_DIRECTORY"]
    record = listing(
        printed_name="EXAMPLE OFFICIAL", role="Undersecretary",
        contact_lines=[line("(02) 8000-0000")],
    )
    original = prepare(record)
    assert config["external_id_path"] == "source_record_id"
    assert original["source_record_id"].startswith("DBM_PH-")
    assert original["source_record_id"] == prepare(deepcopy(record))["source_record_id"]
    changed = deepcopy(record)
    changed["contact_lines"] = [line("(02) 8999-9999", page=40)]
    changed["source_path"][-1] = 9
    assert original["source_record_id"] == prepare(changed)["source_record_id"]
    changed["role"] = "Secretary"
    assert original["source_record_id"] != prepare(changed)["source_record_id"]


def test_assignment_id_ignores_typography_and_generic_directory_categories():
    record = listing(printed_name="MARIO VITTORIO A. MARIÑO", role="Director",
                     office_lines=[line("Public Affairs Service")], context=[{
                         "source_path": ["departments", 0],
                         "fields": {"heading": line("DEPARTMENT OF EXAMPLE")},
                     }], group_source=line("SERVICES"))
    changed = deepcopy(record)
    changed.update(printed_name="  mario vittorio a.  marin\u0303o  ",
                   group_source=line("PUBLIC ASSISTANCE DESK/EXTERNAL RELATIONS OFFICE"))
    changed["context"][0]["fields"]["heading"]["text"] = " Department   of Example "
    changed["office_lines"] = [line("public affairs service", page=99)]
    assert prepare(record)["source_record_id"] == prepare(changed)["source_record_id"]


@pytest.mark.parametrize("name", ["Jocelyn P. Tulfo", "Tulfo, Jocelyn P.", "Hon. Jocelyn P. Tulfo", "Jocelyn P Tulfo"])
def test_identity_normalizes_explicit_name_order_and_honorific_without_changing_printed_name(name):
    raw = listing(printed_name=name, role="Chairperson")
    person = prepare(raw)
    assert person["source_record_id"] == prepare(listing(printed_name="Jocelyn P. Tulfo", role="Chairperson"))["source_record_id"]
    assert person["name_text"] == name
    assert person["source_evidence"]["printed_name"] == name


@pytest.mark.parametrize("name", ["Juan Cruz Jr.", "Cruz, Juan Jr.", "Cruz, Juan, Jr."])
def test_identity_keeps_name_suffix_after_surname(name):
    assert prepare(listing(printed_name=name))["source_record_id"] == prepare(listing(printed_name="Juan Cruz Jr."))["source_record_id"]


@pytest.mark.parametrize("field,first,second", [
    ("role", "Secretary", "Undersecretary"),
    ("office_lines", [line("Treaties and Legal Affairs")], [line("Bids and Awards Committee")]),
    ("committee_lines", [line("Accounts")], [line("Finance")]),
    ("location_lines", [line("Argentina Buenos Aires")], [line("Brazil Brasilia")]),
    ("province", "Province A", "Province B"),
    ("municipality", "Town A", "Town B"),
    ("qualifier", "(Caretaker)", None),
])
def test_same_person_distinct_assignments_have_distinct_ids(field, first, second):
    a = listing(printed_name="SAME OFFICIAL", role="Director", **{field: first}) if field != "role" else listing(printed_name="SAME OFFICIAL", role=first)
    b = deepcopy(a)
    b[field] = second
    rows = PreProcessingEngine().preprocess([a, b], RULES)
    assert len(rows) == 2
    assert rows[0]["source_record_id"] != rows[1]["source_record_id"]


def test_house_districts_differ_but_ordinal_spelling_does_not():
    raw = listing(printed_name="ROMUALDEZ, FERDINAND MARTIN G.",
                  role="District Representative", qualifier="(Caretaker)", printed_district="1st",
                  source_path=["national_government", "legislative_branch", "house_of_representatives", "records", 0],
                  context=[{"source_path": ["places", 0], "fields": {"printed_place": "Palawan"}}])
    same = deepcopy(raw)
    same["printed_district"] = "1st District"
    different = deepcopy(raw)
    different["printed_district"] = "3rd"
    assert prepare(raw)["source_record_id"] == prepare(same)["source_record_id"]
    assert prepare(raw)["source_record_id"] != prepare(different)["source_record_id"]
    assert "District: 1st" in prepare(raw)["occupation_note"]


def test_party_list_and_court_division_distinguish_assignments():
    for context_field, first, second in [("printed_label", "ALONA", "AMIN"), ("heading", "First Division", "Second Division")]:
        a = listing(printed_name="SAME OFFICIAL", role="Member", context=[{
            "source_path": ["courts", 0, "groups", 0], "fields": {context_field: first},
        }], office_lines=[line("Office of the Clerk of Court")])
        b = deepcopy(a)
        b["context"][0]["fields"][context_field] = second
        assert prepare(a)["source_record_id"] != prepare(b)["source_record_id"]


def test_repeated_assignment_coalesces_contacts_and_preserves_every_source_row():
    first = listing(printed_name="EXAMPLE OFFICIAL", role="Director",
                    office_lines=[line("Example Service")], contact_lines=[line("first@example.gov.ph", page=1)])
    second = deepcopy(first)
    second["source_path"][-1] = 9
    second["contact_lines"] = [line("second@example.gov.ph", page=2)]
    rows = PreProcessingEngine().preprocess([first, second], RULES)
    assert len(rows) == 1
    assert rows[0]["emails"] == ["first@example.gov.ph", "second@example.gov.ph"]
    assert rows[0]["source_evidence_rows"] == [first, second]
    assert rows[0]["source_note"] == "PDF pages: 1, 2"
    assert len(rows[0]["contacts"]) == 2


@pytest.mark.parametrize("reverse", [False, True])
def test_repeated_assignment_prefers_explicit_address_over_inherited_header(reverse):
    first = listing(printed_name="EXAMPLE OFFICIAL", role="Director", office_lines=[line("Example Service")],
                    context=[{"source_path": ["departments", 0], "fields": {
                        "heading": line("DEPARTMENT OF EXAMPLE"), "header_lines": [line("12 Parent Street")],
                    }}])
    second = deepcopy(first)
    second["address_lines"] = [line("34 Listing Street")]
    rows = PreProcessingEngine().preprocess([second, first] if reverse else [first, second], RULES)
    assert len(rows) == 1
    assert [value["text"] for value in rows[0]["addresses"]] == ["34 Listing Street"]
    assert "Organization office address: 12 Parent Street" in rows[0]["occupation_note"]
    assert len(rows[0]["source_evidence_rows"]) == 2


def test_lgu_identity_uses_province_and_town_without_administrative_region():
    raw = listing(printed_name="EXAMPLE OFFICIAL", role="Municipal Mayor", province="Province A",
                  municipality="Town A", region="Region I",
                  source_path=["local_government_units", "municipal_governments", "records", 0])
    changed = deepcopy(raw)
    changed["region"] = "Region II"
    assert prepare(raw)["source_record_id"] == prepare(changed)["source_record_id"]
    changed["province"] = "Province B"
    assert prepare(raw)["source_record_id"] != prepare(changed)["source_record_id"]


def test_senate_role_is_inferred_only_for_the_roster():
    raw = listing(printed_name="EXAMPLE SENATOR", source_path=["national_government", "legislative_branch", "senate", "roster", "records", 0])
    assert prepare(raw)["role_text"] == "Senator"
    raw["source_path"][3] = "secretariat"
    assert prepare(raw)["role_text"] is None


def test_institution_header_belongs_to_occupation_and_keeps_contact_ownership():
    record = listing(printed_name="EXAMPLE OFFICIAL", role="President", context=[{
        "source_path": ["national_government", "executive_branch", "president"],
        "fields": {
            "heading": line("OFFICE OF THE PRESIDENT"),
            "header_lines": [
                line("Malacañang Palace Compound, New Executive Bldg.", 10),
                line("J.P. Laurel St., San Miguel, Manila", 20),
                line("Trunkline: (02) 8249-8310", 30),
                line("www.op-proper.gov.ph", 40),
            ],
        },
    }])
    result = prepare(record)
    assert result["organization_name"] == "OFFICE OF THE PRESIDENT"
    details = result["organization_details"]
    assert details["address_text"] == "Malacañang Palace Compound, New Executive Bldg. J.P. Laurel St., San Miguel, Manila"
    assert details["phones"][0]["number"] == "(02) 8249-8310"
    assert details["websites"] == ["www.op-proper.gov.ph"]
    assert result["phones"] == result["websites"] == []
    assert result["address_text"] is None
    assert "Organization office address:" not in result["occupation_note"]
    assert result["addresses"] == [{
        "text": details["address_text"], "note": "Shared office address — OFFICE OF THE PRESIDENT",
    }]
    assert result["source_evidence"] == record
    changed = deepcopy(record)
    changed["context"][0]["fields"]["header_lines"] = []
    assert prepare(changed)["source_record_id"] == result["source_record_id"]


def test_organization_header_does_not_transfer_parent_contacts_to_attached_organization():
    record = listing(
        printed_name="EXAMPLE OFFICIAL", office_lines=[line("ATTACHED CORPORATION")],
        source_path=["corporations_and_colleges", "records", 0],
        context=[{"source_path": ["departments", 0], "fields": {
            "heading": line("DEPARTMENT OF EXAMPLE"),
            "header_lines": [line("Trunkline: (02) 8000-0000")],
        }}],
    )
    result = prepare(record)
    assert result["organization_name"] == "ATTACHED CORPORATION"
    assert result["organization_details"] is None
    assert "8000-0000" not in result["occupation_note"]


def test_listing_address_wins_and_shared_header_address_remains_in_occupation_note():
    result = prepare(listing(printed_name="EXAMPLE OFFICIAL", address_lines=[line("34 Regional Street")],
                            context=[{"source_path": ["departments", 0], "fields": {
                                "heading": line("DEPARTMENT OF EXAMPLE"),
                                "header_lines": [line("12 Headquarters Street")],
                            }}]))
    assert result["addresses"] == [{"text": "34 Regional Street", "note": "Office address printed for this listing"}]
    assert "Organization office address: 12 Headquarters Street" in result["occupation_note"]


@pytest.mark.parametrize("header_phones,expected", [
    (["Trunkline: (02) 8000-0000"], "(02) 8000-0000 ext. 8410"),
    (["Trunkline: (02) 8000-0000; 8000-0001"], "ext. 8410"),
    ([], "ext. 8410"),
])
def test_extension_uses_only_one_applicable_shared_header_number(header_phones, expected):
    raw = listing(printed_name="EXAMPLE OFFICIAL", contact_lines=[line("loc. 8410")],
                  context=[{"source_path": ["office"], "fields": {
                      "heading": line("OFFICE OF EXAMPLE"),
                      "header_lines": [line(text) for text in header_phones],
                  }}])
    result = prepare(raw)
    assert result["contacts"][0]["value"] == expected
    assert "Printed type:" not in result["contacts"][0]["note"]
    assert result["phones"] == [{"number": None, "extension": "8410", "kind": "phone"}]
    assert result["source_evidence"] == raw


def test_chamber_header_is_available_to_other_subsections_of_the_same_edition():
    roster = listing(printed_name="SENATOR", source_path=["senate", "roster", "records", 0], context=[{
        "source_path": ["senate", "roster"], "fields": {
            "heading": line("SENATE OF THE PHILIPPINES"),
            "header_lines": [line("12 Senate Street"), line("(02) 8552-6601")],
        },
    }])
    secretariat = listing(printed_name="SECRETARY", contact_lines=[line("loc. 6501")],
                          source_path=["senate", "secretariat", "records", 0])
    first, second = PreProcessingEngine().preprocess([roster, secretariat], RULES)
    assert second["addresses"] == first["addresses"]
    assert second["contacts"][0]["value"] == "(02) 8552-6601 ext. 6501"
    assert second["organization_details"]["source_path"] == ["senate", "roster"]
    assert second["source_evidence"] == secretariat


def test_source_note_collects_listing_and_used_header_pages_without_sibling_inventory():
    raw = listing(printed_name="EXAMPLE OFFICIAL", name_source=line("EXAMPLE OFFICIAL", page=8),
                  contact_lines=[line("loc. 8410", page=9)],
                  context=[{"source_path": ["departments", 0], "fields": {
                      "heading": line("DEPARTMENT OF EXAMPLE", page=7),
                      "header_lines": [line("12 Example Street", page=7)],
                      "group_headings": [line("OTHER GROUP", page=50)],
                  }}])
    result = prepare(raw)
    assert result["source_pages"] == [7, 8, 9]
    assert result["source_note"] == "PDF pages: 7, 8, 9"


def test_posting_retains_parent_department_and_multiline_role_without_nationality():
    record = listing(
        printed_name="H.E. GRACE T. CRUZ-FABELLA",
        role_lines=[line("Ambassador Extraordinary"), line("and Plenipotentiary")],
        location_lines=[line("ARGENTINA"), line("Buenos Aires")],
        context=[{"source_path": ["column_rosters", 0], "fields": {
            "parent_heading": line("DEPARTMENT OF FOREIGN AFFAIRS"),
            "heading": line("FOREIGN SERVICE POSTS"),
        }}],
        contact_lines=[
            line("(+54-11) 4554-4015; 4554-9194 F"),
            line("buenosaires.pe@dfa.gov.ph"),
            line("www.buenosairespe.dfa.gov.ph"),
        ],
    )
    result = prepare(record)
    assert result["department_name"] == "DEPARTMENT OF FOREIGN AFFAIRS"
    assert result["role_text"] == "Ambassador Extraordinary and Plenipotentiary"
    assert result["hierarchy"][-1] == {"kind": "posting", "name": "ARGENTINA Buenos Aires"}
    assert "nationality" not in result
    assert result["phones"] == [
        {"number": "(+54-11) 4554-4015", "extension": None, "kind": "phone"},
        {"number": "4554-9194", "extension": None, "kind": "fax"},
    ]
    assert result["emails"] == ["buenosaires.pe@dfa.gov.ph"]
    assert result["websites"] == ["www.buenosairespe.dfa.gov.ph"]


def test_phone_extensions_pair_by_source_position_and_extension_only_stays_separate():
    result = prepare(listing(
        printed_name="Tulfo, Jocelyn P.",
        phone_lines=[line("(02) 8931-5388", 100), line("(02) 8931-6317", 110)],
        extension_lines=[line("7234", 110), line("7395", 100)],
        contact_lines=[line("loc. 21421", 120)],
    ))
    assert result["name_text"] == "Tulfo, Jocelyn P."
    assert result["phones"] == [
        {"number": "(02) 8931-5388", "extension": "7395", "kind": "phone"},
        {"number": "(02) 8931-6317", "extension": "7234", "kind": "phone"},
        {"number": None, "extension": "21421", "kind": "phone"},
    ]


def test_missing_job_title_keeps_person_and_printed_service_unit():
    result = prepare(listing(
        printed_name="ROSALINDA P. PINEDA",
        office_lines=[line("Information and Publication Service")],
        role_lines=[],
    ))
    assert result["is_person_record"]
    assert result["role_text"] is None
    assert result["office_text"] == "Information and Publication Service"


def test_committee_chair_and_court_shared_role_use_source_context():
    chair = prepare(listing(
        record_kind="committee",
        chair_source=line("PANFILO M. LACSON"),
        committee_lines=[line("Finance")],
    ))
    assert chair["name_text"] == "PANFILO M. LACSON"
    assert chair["role_text"] == "Chairperson"
    judge = prepare(listing(
        printed_name="EXAMPLE JUSTICE",
        context=[{"source_path": ["courts", 0, "groups", 0], "fields": {
            "heading": line("ASSOCIATE JUSTICES"), "shared_role": "Associate Justice",
        }}],
    ))
    assert judge["role_text"] == "Associate Justice"


@pytest.mark.parametrize("record", [
    listing(printed_name="VACANT", vacant=True),
    listing(printed_name=None, status="vacant", record_kind="committee"),
    listing(record_kind="office_contact", heading=line("CONTACT CENTER")),
    listing(printed_name=None, office_lines=[line("Office Management")]),
])
def test_nonperson_source_rows_remain_available_with_evidence(record):
    result = prepare(record)
    assert not result["is_person_record"]
    assert result["source_evidence"] == record


def test_contacts_keep_unknown_text_and_compound_domain_intact():
    result = prepare(listing(
        printed_name="MARIÑO",
        contact_lines=[
            line("web.example.org.sg"),
            line("speaker@house,gov.ph"),
            line("310 San Rafael St., Malacañang Complex"),
            line("(02) 8000-0000 local 101–122"),
        ],
    ))
    assert result["name_text"] == "MARIÑO"
    assert result["websites"] == ["web.example.org.sg"]
    assert result["contact_unparsed"] == ["speaker@house,gov.ph"]
    assert result["address_text"] == "310 San Rafael St., Malacañang Complex"
    assert result["phones"] == [{"number": "(02) 8000-0000", "extension": "101–122", "kind": "phone"}]


@pytest.mark.parametrize("page, top, merged", [(1, 110, True), (2, 110, False), (1, 140, False)])
def test_email_fragments_join_only_on_adjacent_lines_of_same_page(page, top, merged):
    result = prepare(listing(
        printed_name="EXAMPLE OFFICIAL",
        contact_lines=[line("office@", 100), line("example.gov.ph", top, page)],
    ))
    assert result["emails"] == (["office@example.gov.ph"] if merged else [])
    assert result["source_evidence"]["contact_lines"][0]["text"] == "office@"


@pytest.mark.parametrize("text, number, extension", [
    ("(02) 8771-9PCC (722) TL", "(02) 8771-9PCC (722)", None),
    ("1-DTI (1-384)", "1-DTI (1-384)", None),
    ("Hotline: 1326", "1326", None),
    ("(033) 337-0149 loc. 1014, 1008. 1016", "(033) 337-0149", "1014, 1008. 1016"),
    ("(045) 606-8110 loc. loc. 201", "(045) 606-8110", "201"),
])
def test_printed_phone_patterns_preserve_numbers_and_extension_notation(text, number, extension):
    record = listing(printed_name="EXAMPLE OFFICIAL", contact_lines=[line(text)])
    result = prepare(record)
    assert result["phones"] == [{"number": number, "extension": extension, "kind": "phone"}]
    assert not result["contact_unparsed"]
    assert result["source_evidence"] == record


@pytest.mark.parametrize("text, expected", [
    ("www. melbournepcg.dfa.gov.ph", "www.melbournepcg.dfa.gov.ph"),
    ("www,telavivpe.dfa.gov.ph", "www.telavivpe.dfa.gov.ph"),
])
def test_www_separator_cleanup_keeps_printed_evidence(text, expected):
    record = listing(printed_name="EXAMPLE OFFICIAL", contact_lines=[line(text)])
    result = prepare(record)
    assert result["websites"] == [expected]
    assert not result["contact_unparsed"]
    assert result["source_evidence"] == record


def test_mixed_email_spacing_is_fixed_but_double_at_address_is_not_partially_accepted():
    result = prepare(listing(
        printed_name="EXAMPLE OFFICIAL",
        contact_lines=[
            line("FTEB@dti.gov.ph; MarimelDancelPorciuncula @dti.gov.ph"),
            line("oas@reintegration@dmw.gov.ph"),
        ],
    ))
    assert result["emails"] == ["FTEB@dti.gov.ph", "MarimelDancelPorciuncula@dti.gov.ph"]
    assert result["contact_unparsed"] == ["oas@reintegration@dmw.gov.ph"]


def test_comma_separated_emails_require_every_address_to_be_complete():
    result = prepare(listing(
        printed_name="EXAMPLE OFFICIAL",
        contact_lines=[
            line("first@example.org, second@example.org"),
            line("broken@example,org"),
        ],
    ))
    assert result["emails"] == ["first@example.org", "second@example.org"]
    assert result["contact_unparsed"] == ["broken@example,org"]


def test_address_fragments_from_contact_fields_rejoin_in_printed_order():
    record = listing(
        printed_name="EXAMPLE OFFICIAL",
        address_lines=[line("San Miguel, Manila", 110)],
        contact_lines=[
            line("310 San Rafael St., Malacañang Complex,", 100),
            line("(02) 8734-2120", 120),
        ],
    )
    result = prepare(record)
    assert result["address_text"] == "310 San Rafael St., Malacañang Complex, San Miguel, Manila"
    assert result["phones"][0]["number"] == "(02) 8734-2120"
    assert not result["contact_unparsed"]
    assert result["source_evidence"] == record


def test_postal_locality_follows_existing_address_without_becoming_a_phone():
    result = prepare(listing(
        printed_name="EXAMPLE OFFICIAL",
        address_lines=[line("National Arts Center, Mt. Makiling College,", 100)],
        contact_lines=[line("4031 Los Baños, Laguna", 110)],
    ))
    assert result["address_text"].endswith("College, 4031 Los Baños, Laguna")
    assert not result["phones"] and not result["contact_unparsed"]


@pytest.mark.parametrize("text", [
    "oas@reintegration@dmw.gov.ph",
    "president@localhost",
    "(072) 603-31 l 7",
])
def test_ambiguous_printed_values_remain_unparsed_without_invented_corrections(text):
    result = prepare(listing(printed_name="EXAMPLE OFFICIAL", contact_lines=[line(text)]))
    assert result["contact_unparsed"] == [text]
    assert not result["emails"] and not result["phones"]

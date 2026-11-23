from copy import deepcopy

import pytest

from parsing.dbm.records import iter_listings
from parsing.parserFactory import create_parser


pytestmark = pytest.mark.unit


@pytest.mark.parametrize("new_person", [True, False])
def test_mission_heading_below_name_reparents_only_a_new_representative(monkeypatch, new_person):
    from parsing.dbm import representatives as reader

    def words(text, is_bold=False):
        return [{"text": text, "fontname": "Example-Bold" if is_bold else "Example-Regular"}] if text else []

    def row(top, left, right, name=False):
        return ({"source_page": 282, "top": top}, words(left), words(right, name))

    lines = [row(100, "ISRAEL", "FIRST PERSON", True),
             row(110, "Consulate of Israel (Cebu)", "Consul"),
             row(120, "12 Cebu Street", "")]
    if new_person:
        lines.append(row(140, "", "SECOND PERSON", True))
    lines += [row(150, "Consulate of Israel (Davao)", "Consul" if new_person else ""),
              row(160, "34 Davao Street", "")]
    monkeypatch.setattr(reader, "find_sections", lambda _: (282, 282, 282))
    monkeypatch.setattr(reader, "representative_columns", lambda *args: [(282, lines)])
    result = reader.read_missions("unused.pdf")
    assert not result["issues"]
    if new_person:
        first, second = result["missions"]
        assert [p["printed_name"] for p in first["representatives"]] == ["FIRST PERSON"]
        assert [p["printed_name"] for p in second["representatives"]] == ["SECOND PERSON"]
        assert second["printed_mission"] == "Consulate of Israel (Davao)"
        assert first["mission_lines"][-1]["text"] == "12 Cebu Street"
    else:
        mission, = result["missions"]
        assert len(mission["representatives"]) == 1
        assert any(line["text"] == "Consulate of Israel (Davao)" for line in mission["mission_lines"])


def test_wrapped_mission_name_is_not_address_text(monkeypatch):
    from parsing.dbm import representatives as reader

    def words(text, font="Regular"):
        return [{"text": text, "fontname": font}] if text else []
    lines = [({"source_page": 1, "top": 100}, words("UNITED KINGDOM"), words("EXAMPLE PERSON", "Bold")),
             ({"source_page": 1, "top": 110}, words("Embassy of the United Kingdom"), words("Ambassador")),
             ({"source_page": 1, "top": 120}, words("of Great Britain and Northern Ireland"), []),
             ({"source_page": 1, "top": 130}, words("12 Example Road"), [])]
    monkeypatch.setattr(reader, "find_sections", lambda _: (1, 2, 1))
    monkeypatch.setattr(reader, "representative_columns", lambda *args: [(1, lines)])
    result = reader.read_missions("unused.pdf")
    mission, = result["missions"]
    assert mission["printed_mission"].endswith("of Great Britain and Northern Ireland")
    assert [line["text"] for line in mission["mission_lines"]] == ["12 Example Road"]
    assert mission["mission_name_continuation_lines"][0]["source_page"] == 1


def test_mission_alias_below_same_line_heading_does_not_create_empty_office(monkeypatch):
    from parsing.dbm import representatives as reader

    def words(text, bold=False):
        return [{"text": text, "fontname": "Bold" if bold else "Regular"}] if text else []
    lines = [({"source_page": 1, "top": 100}, words("AUSTRIA"), words("FIRST PERSON", True)),
             ({"source_page": 1, "top": 110}, words("Consulate of Austria (Cebu)"), words("Consul")),
             ({"source_page": 1, "top": 140}, words("Consulate of Austria (Davao)"), words("SECOND PERSON", True)),
             ({"source_page": 1, "top": 150}, words("Austrian Honorary Consulate"), words("Consul")),
             ({"source_page": 1, "top": 160}, words("12 Example Street"), [])]
    monkeypatch.setattr(reader, "find_sections", lambda _: (1, 1, 1))
    monkeypatch.setattr(reader, "representative_columns", lambda *args: [(1, lines)])
    result = reader.read_missions("unused.pdf")
    assert not result["issues"]
    first, second = result["missions"]
    assert second["printed_mission"] == "Consulate of Austria (Davao)"
    assert second["representatives"][0]["printed_name"] == "SECOND PERSON"


@pytest.fixture
def directory():
    return {
        "source": "directory.pdf",
        "departments": [{
            "heading": {"text": "DEPARTMENT OF FOREIGN AFFAIRS", "source_page": 50},
            "posts": [{
                "heading": {"text": "FOREIGN SERVICE POSTS", "source_page": 52},
                "records": [{
                    "printed_name": "GRACE CRUZ-FABELLA",
                    "role_lines": [{"text": "Ambassador", "source_page": 52}],
                    "contact_lines": [{"text": "(+54-11) 4554-4015", "source_page": 52}],
                }],
            }],
            "office_contacts": [{
                "heading": {"text": "CONTACT CENTER", "source_page": 53},
                "contact_lines": [{"text": "help@example.org", "source_page": 53}],
            }],
            "issues": [{"reason": "Review source", "source_page": 52}],
        }],
    }


def test_nested_listing_retains_department_and_section_evidence(directory):
    listing, office = list(iter_listings(directory))
    assert listing["source_path"] == ["departments", 0, "posts", 0, "records", 0]
    assert listing["printed_name"] == "GRACE CRUZ-FABELLA"
    assert "source_file" not in listing
    department = listing["context"][0]["fields"]
    section = listing["context"][1]["fields"]
    assert all("source" not in entry["fields"] for entry in listing["context"])
    assert department["heading"]["text"] == "DEPARTMENT OF FOREIGN AFFAIRS"
    assert department["issues"] == [{"reason": "Review source", "source_page": 52}]
    assert section["heading"]["text"] == "FOREIGN SERVICE POSTS"
    assert office["record_kind"] == "office_contact"
    assert "printed_name" not in office


def test_output_records_do_not_share_mutable_context_or_modify_directory(directory):
    original = deepcopy(directory)
    listing, office = list(iter_listings(directory))
    listing["context"][0]["fields"]["heading"]["text"] = "changed"
    listing["role_lines"][0]["text"] = "changed"
    assert office["context"][0]["fields"]["heading"]["text"] == "DEPARTMENT OF FOREIGN AFFAIRS"
    assert directory == original


def test_vacancy_and_unlisted_slots_are_preserved():
    rows = list(iter_listings({
        "source": "directory.pdf",
        "vacancies": [{"province": "Example", "role": "Governor"}],
        "unlisted": [{"province": "Other", "role": "Vice Governor"}],
    }))
    assert [row["record_kind"] for row in rows] == ["vacancy", "unlisted"]
    assert all("printed_name" not in row for row in rows)


def test_committee_kind_uses_listing_structure_in_both_chambers():
    senate, house = list(iter_listings({
        "source": "directory.pdf",
        "senate": {"committees": {"records": [{
            "committee_lines": [{"text": "Finance"}],
            "chair_source": {"text": "Example Senator"},
        }]}},
        "house": {"committees": [{
            "committee_lines": [{"text": "Finance"}],
            "printed_name": "Example Representative",
        }]},
    }))
    assert senate["record_kind"] == house["record_kind"] == "committee"
    assert senate["chair_source"]["text"] == "Example Senator"
    assert house["printed_name"] == "Example Representative"


@pytest.mark.parametrize("gap, second_person, expected_unassigned", [
    (9.6, False, 0),
    (30, False, 1),
    (9.6, True, 1),
])
def test_extra_phone_and_extension_require_one_adjacent_listing(
    monkeypatch, gap, second_person, expected_unassigned
):
    from parsing.dbm import house

    monkeypatch.setattr(house, "party_columns", lambda rows: (False, (150, 300, 380)))

    def words(top, *cells):
        return [{"text": text, "x0": x, "top": top} for x, text in cells]

    rows = [(173, words(100, (45, "ACT-CIS"), (161, "Tulfo,"),
                        (200, "Jocelyn"), (302, "(02)"), (325, "8931-5388"),
                        (383, "7395")))]
    if second_person:
        rows.append((173, words(110, (161, "Other,"), (205, "Person"),
                                 (302, "(02)"), (325, "8000-0000"), (383, "7000"))))
    top = rows[-1][1][0]["top"] + gap
    rows.append((173, words(top, (302, "(02)"), (325, "8931-6317"), (383, "7234"))))
    result = {"parties": [], "issues": []}
    house.read_party_lists(rows, result)
    party, = result["parties"]
    assert len(party["unassigned_contacts"]) == expected_unassigned
    if not expected_unassigned:
        record, = party["records"]
        assert [line["text"] for line in record["phone_lines"]] == [
            "(02) 8931-5388", "(02) 8931-6317",
        ]
        assert [line["text"] for line in record["extension_lines"]] == ["7395", "7234"]


@pytest.mark.parametrize("directory, message", [
    ({"source": "directory.pdf", "records": []}, "no source listings"),
    ({"source": "directory.pdf", "records": ["invalid"]}, "listing object"),
    ({"source": "directory.pdf", "records": [{"context": []}]}, "metadata conflicts"),
])
def test_invalid_listing_structure_fails_explicitly(directory, message):
    with pytest.raises(ValueError, match=message):
        list(iter_listings(directory))


@pytest.mark.parametrize("mode", ["dbm_directory", " DBM_DIRECTORY "])
def test_configured_pdf_mode_returns_listings_through_factory(tmp_path, monkeypatch, directory, mode):
    import parsing.dbm.parser as dbm_parser

    pdf = tmp_path / "directory.pdf"
    pdf.write_bytes(b"%PDF-")
    paths = []
    def extract(path):
        paths.append(path)
        return directory
    monkeypatch.setattr(dbm_parser, "extract_directory", extract)
    config = {"parser": {"mode": mode}}
    records = create_parser("pdf").parse(pdf, config)
    assert len(records) == 2
    assert records[0]["printed_name"] == "GRACE CRUZ-FABELLA"
    assert records[1]["record_kind"] == "office_contact"
    assert paths == [pdf]
    assert records[0]["context"][0]["fields"]["issues"] == directory["departments"][0]["issues"]


def test_missing_pdf_fails_before_dbm_extraction(tmp_path, monkeypatch):
    import parsing.dbm.parser as dbm_parser

    def unexpected_extract(path):
        pytest.fail("Missing input must not reach directory extraction")
    monkeypatch.setattr(dbm_parser, "extract_directory", unexpected_extract)
    config = {"parser": {"mode": "dbm_directory"}}
    with pytest.raises(FileNotFoundError):
        create_parser("pdf").parse(tmp_path / "missing.pdf", config)


@pytest.mark.parametrize("failure", ["empty", "section_error"])
def test_pipeline_rejects_empty_or_failed_directory_extraction(tmp_path, monkeypatch, failure):
    import parsing.dbm.parser as dbm_parser

    pdf = tmp_path / "directory.pdf"
    pdf.write_bytes(b"%PDF-")
    def extract(path):
        if failure == "section_error":
            raise ValueError("national_government / executive_branch: unreadable section")
        return {"source": str(path), "records": []}
    monkeypatch.setattr(dbm_parser, "extract_directory", extract)
    message = "unreadable section" if failure == "section_error" else "no source listings"
    with pytest.raises(ValueError, match=message):
        create_parser("pdf").parse(pdf, {"parser": {"mode": "dbm_directory"}})

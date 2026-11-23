"""METI uses existing rule handlers and mapping, without a custom handler."""

import json
from copy import deepcopy
from pathlib import Path

import pandas as pd
import pytest

from transforms.fieldMapper import MappingEngine, Rule
from transforms.preNormalization import HANDLERS, PreNormalizationEngine

pytestmark = pytest.mark.unit
SOURCE = "METI_FOREIGN_END_USER_LIST"
NAME = "企業名､組織名\nCompany or Organization"
COUNTRY = "国名、地域名\nCountry or Region"
ALIASES = "別名\nAlso Known As"
WMD = "懸念区分\nType of WMD"
CW = "通常兵器\nConventional Weapons"
FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "fixtures/meti_generic_rules.json").read_text())


def prepare(**fields):
    raw = {NAME: "Example (EX)", COUNTRY: "中国\nPeople's Republic of China",
           ALIASES: "", WMD: "", CW: "", "entity_type": "Entity", "source_record_id": "example"}
    raw.update(fields)
    original = deepcopy(raw)
    engine = PreNormalizationEngine(
        pd.DataFrame(FIXTURE["prenormalization"]),
        pd.DataFrame([{"source": SOURCE, "entity_field": "entity_type"}]),
    )
    normalized = engine.pre_normalize_record(SOURCE, raw)
    assert raw == original
    assert all(normalized[key] == value for key, value in raw.items())
    return MappingEngine([Rule(**row) for row in FIXTURE["mapping"]]).map_record(normalized)


def test_uses_only_existing_handlers():
    assert "meti_fields" not in HANDLERS
    assert {r["normalization_type"] for r in FIXTURE["prenormalization"]} <= {
        "regex_extract", "regex_extract_all", "first_non_empty", "split_pattern", "enum", "enum_add_field",
    }


def test_native_name_and_nested_parentheses_preserve_all_names():
    mapped = prepare(**{NAME: "Example (EX)\n（例子(中國)公司）"})
    assert mapped["Names"][0]["Name"] == "Example"
    assert [a["Alias"] for a in mapped["Aliases"]] == ["例子(中國)公司", "EX"]
    assert mapped["Countries"][0]["CountryName"] == "People's Republic of China"


def test_multiple_abbreviations_and_suffix_are_not_lost():
    mapped = prepare(**{NAME: "Example Institute（EI） (EI 14), DAE"})
    assert mapped["Names"][0]["Name"] == "Example Institute , DAE"
    assert [a["Alias"] for a in mapped["Aliases"]] == ["EI", "EI 14"]


@pytest.mark.parametrize("qualifier", ["China", "HK", "Pvt", "Private", "Pyongyang branch"])
def test_qualifiers_remain_in_primary_name(qualifier):
    name = f"Example ({qualifier}) Limited"
    mapped = prepare(**{NAME: name})
    assert mapped["Names"][0]["Name"] == name
    assert mapped["Aliases"] == []


def test_native_only_name_is_kept_with_note():
    mapped = prepare(**{NAME: "セソン技術貿易"})
    assert mapped["Names"][0]["Name"] == "セソン技術貿易"
    assert "No English/Latin-script" in mapped["Names"][0]["Note"]


@pytest.mark.parametrize("aliases,expected", [
    ("・Example Trading,\nLLC・Other Name", ["Example Trading,\nLLC", "Other Name"]),
    ("Bulletless Alias", ["Bulletless Alias"]),
    (" ", []),
    (" ・First・Second", ["First", "Second"]),
])
def test_alias_separation_keeps_wrapped_and_bulletless_values(aliases, expected):
    mapped = prepare(**{NAME: "Example", ALIASES: aliases})
    assert [a["Alias"] for a in mapped["Aliases"]] == expected


def test_each_wmd_code_and_conventional_marker_gets_its_own_program():
    mapped = prepare(**{WMD: "生物、化学、ミサイル、核\nB,C,M,Ｎ", CW: "通常兵器\nCW"})
    assert [p["Program"] for p in mapped["Programs"]] == [
        f"Foreign End User List: {code}" for code in ("B", "C", "M", "N", "CW")
    ]
    assert [p["Note"] for p in mapped["Programs"]] == [
        f"Program description: {description}" for description in (
            "Biological weapons", "Chemical weapons", "Missiles", "Nuclear weapons", "Conventional weapons",
        )
    ]
    assert mapped["RiskCategory"] == {
        "RiskModelEnabled": True, "Labels": [{"Type": "List", "Value": "Regulatory"}],
    }


def test_conventional_only_does_not_invent_wmd_programs():
    assert [p["Program"] for p in prepare(**{CW: "通常兵器 CW"})["Programs"]] == ["Foreign End User List: CW"]
    assert prepare()["Programs"] == []

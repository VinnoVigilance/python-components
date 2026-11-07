"""Replay the acquired 2022 PDF through the real parser and workbook rules."""
from copy import deepcopy
from pathlib import Path

import pytest

from parsing.pdfParser import PdfParser
from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistNormalizationService as norm
from transforms.preProcessingEngine import PreProcessingEngine

pytestmark = pytest.mark.unit
KEY = "PNP_MOST_WANTED_WITH_REWARDS_CRIMINALITY_2022"
PDF = Path(__file__).resolve().parents[3] / "fixtures/parsing/pnp_rewards_criminality_2022.pdf"


@pytest.fixture(scope="module")
def source():
    config = WATCHLIST_CONFIGS[KEY]
    extracted = PdfParser().parse(PDF, config)
    processed = PreProcessingEngine().preprocess(deepcopy(extracted), config["preprocessing"])
    pre, mapper, post = norm.create_normalization_engines(config)
    canonical = [norm.normalize_record(row, config, pre, mapper, post) for row in processed]
    return extracted, processed, canonical


def info(record):
    return {item["Type"]: item["Value"] for item in record["additionalInfo"]}


def test_complete_document_reaches_canonical_output(source):
    extracted, processed, canonical = source
    assert len(extracted) == len(processed) == len(canonical) == 840
    assert {row["source_page"] for row in extracted} == set(range(1, 98))
    assert len({row["EntityId"] for row in canonical}) == 840
    assert sum(bool(row["Names"]) for row in canonical) == 839
    assert sum(bool(row["Aliases"]) for row in canonical) == 367
    for row in canonical:
        assert row["EntityType"] == "Individual"
        assert row["Sources"][0]["SourceName"] == "PNP"
        assert row["Sources"][0]["ListName"] == KEY
        assert row["Sources"][0]["DatasetCategory"] == "Law Enforcement"
        assert info(row)["List Reference Date"] == "13 October 2022"
        assert not row["Addresses"]
        assert not row["Countries"]
        assert all(item["Value"] for item in row["additionalInfo"])


@pytest.mark.parametrize("position,aliases", [
    (6, ["Boy Muslim", "Israel Iran"]),
    (12, ["Engr Corpuz", "Taba", "Joven"]),
    (28, ["LUPIN", "ENOT", "RICO", "STAN", "GIMA", "JINMOY"]),
    (36, ["FRANK", "BANG", "POKTONG", "MORGAN", "HENRY"]),
    (227, ["SINAG", "TINA", "CHELO"]),
    (571, ["Tony", "Openg", "Sander", "Ponce", "Poncing", "Duma"]),
])
def test_real_spaces_and_wrapped_alias_words_survive_mapping(source, position, aliases):
    assert [a["Alias"] for a in source[2][position - 1]["Aliases"]] == aliases


def test_original_layout_remains_available_and_search_uses_repaired_alias(source):
    extracted, _, canonical = source
    assert extracted[11]["aliases_layout_raw"] == "Engr\nCorpuz/Taba/Jove\nn"
    assert extracted[11]["aliases_raw"] == "Engr Corpuz/Taba/Joven"
    henry = next(alias for alias in canonical[35]["Aliases"] if alias["Alias"] == "HENRY")
    assert henry["Normalized_Alias"] == "henry"
    assert henry["Search_Tokens"] == ["henry"]


def test_case_reference_wraps_are_rejoined_without_removing_real_separators(source):
    extracted, _, canonical = source
    assert "Q-10-\n162652-66" in extracted[50]["case_numbers_layout_raw"]
    assert info(canonical[50])["Case Reference"] == "Q-09-162148-72; Q-09-162216-31; Q-10-162652-66"
    assert info(canonical[0])["Case Reference"] == "L-2854, L-2855, L-2856, L-2857, L-2858 & L-2859"


def test_source_omissions_and_unusual_name_are_not_invented_or_corrected(source):
    canonical = source[2]
    nonoy = canonical[623]
    assert nonoy["Names"] == []
    assert [a["Alias"] for a in nonoy["Aliases"]] == ["Nonoy"]
    assert "Primary name is blank" in info(nonoy)["Identity Note"]
    assert "Case Reference" not in info(canonical[15])
    assert "Issuing Court" not in info(canonical[15])
    assert "Issuing Court" not in info(canonical[466])  # Source prints a dash.
    assert canonical[116]["Names"][0]["Name"] == "SAPTULA, Dexson Adam 0"
    assert canonical[14]["Names"][0]["Name"] == "MONTAÑER, Osmeña"


def test_improved_display_values_do_not_change_existing_record_identity(source):
    extracted, processed, _ = source
    rules = WATCHLIST_CONFIGS[KEY]["preprocessing"]
    changed = deepcopy(extracted[11])
    changed["aliases_raw"] = "Another extracted spelling"
    changed["case_numbers_raw"] = "Corrected case reference"
    result = PreProcessingEngine().preprocess([changed], rules)[0]
    assert result["unique_id"] == processed[11]["unique_id"]


def test_complete_repaired_fields_reconcile_with_raw_source(source):
    extracted, _, canonical = source
    pairs = {"offenses_raw": "Offense", "reward_raw": "Reward",
             "issuing_court_raw": "Issuing Court", "dilg_mc_raw": "DILG Memorandum Circular Number",
             "case_numbers_raw": "Case Reference", "region_raw": "Region as Listed"}
    for raw, final in zip(extracted, canonical):
        values = info(final)
        for field, target in pairs.items():
            expected = " ".join(raw[field].split())
            if expected in {"", "-"}:
                assert target not in values
            else:
                assert values[target] == expected


@pytest.mark.parametrize("validation,error", [
    ({"page_pattern": r"(?m)^Thursday, (?P<date>.*) Page \d+ of 97$",
      "expected_page_fields": {"date": "14 October 2022"}}, "unexpected date"),
    ({"summary_pattern": r"\((?P<record_count>84)0 detail records\)"}, "does not reconcile"),
])
def test_changed_document_metadata_or_summary_stops_extraction(validation, error):
    config = deepcopy(WATCHLIST_CONFIGS[KEY])
    config["parser"]["validation"] = validation
    with pytest.raises(ValueError, match=error):
        PdfParser().parse(PDF, config)

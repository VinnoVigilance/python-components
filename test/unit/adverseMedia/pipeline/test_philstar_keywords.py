from pathlib import Path

import pytest
from openpyxl import load_workbook

from transforms.preNormalization import RegexExtractAllHandler


pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[4]


def test_philstar_keywords_keep_every_term_without_commas():
    sheet = load_workbook(
        ROOT / "data/rules/mediaPreNormalization.xlsx", read_only=True, data_only=True,
    ).active
    rules = [
        row for row in sheet.iter_rows(values_only=True)
        if row[0] == "PHILSTAR_NEWS" and row[2] == "KeywordText"
    ]
    assert len(rules) == 1
    result = RegexExtractAllHandler().normalize(
        "atong ang,department of justice,doj,manila,missing sabungeros,warrant of arrest",
        rules[0][4],
    )
    assert result == [
        "atong ang", "department of justice", "doj", "manila",
        "missing sabungeros", "warrant of arrest",
    ]

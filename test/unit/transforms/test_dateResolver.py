"""
Unit tests for transforms/dateResolver.py

The date resolver is pure logic (no database, no files, no network), which
makes it the ideal place to have a lot of small, precise tests. Each test
below states one behaviour the resolver promises and checks it exactly, so
that if a future change breaks that promise, the failure names the exact
rule that broke.

Run just this file:      pytest test/unit/transforms/test_dateResolver.py -v
Run the whole unit tier: pytest -m unit
"""

import pytest

from transforms.dateResolver import (
    build_row,
    clean_text,
    disbelieved_year,
    expand_range,
    parse_date_string,
    read_approximate,
    read_day,
    read_month,
    read_year,
    resolve_dates,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# read_year: a 4-digit Gregorian year inside the believable window, else ""
# ---------------------------------------------------------------------------

class TestReadYear:
    def test_accepts_normal_year(self):
        assert read_year("1965") == "1965"

    def test_accepts_window_edges(self):
        assert read_year("1850") == "1850"   # MIN_YEAR
        assert read_year("2200") == "2200"   # MAX_YEAR

    def test_rejects_year_before_window(self):
        # 1800 is below MIN_YEAR (1850) -- usually a mis-read, not a real DOB
        assert read_year("1800") == ""

    def test_rejects_hijri_looking_year(self):
        # 1402 is a real number from another calendar, not a Gregorian year
        assert read_year("1402") == ""

    def test_rejects_non_four_digit(self):
        assert read_year("65") == ""
        assert read_year("19650") == ""
        assert read_year("abcd") == ""

    def test_rejects_empty(self):
        assert read_year("") == ""
        assert read_year(None) == ""


# ---------------------------------------------------------------------------
# disbelieved_year: a 4-digit number that cannot be a Gregorian year
# ---------------------------------------------------------------------------

class TestDisbelievedYear:
    def test_foreign_calendar_year_is_disbelieved(self):
        assert disbelieved_year("1402") is True

    def test_real_year_is_believed(self):
        assert disbelieved_year("1965") is False

    def test_placeholder_is_not_a_disbelieved_year(self):
        # "19yy" is an unknown marker, not a foreign number
        assert disbelieved_year("19yy") is False


# ---------------------------------------------------------------------------
# read_month / read_day: names, numbers, and "unknown" placeholders
# ---------------------------------------------------------------------------

class TestReadMonth:
    @pytest.mark.parametrize("value,expected", [
        ("jan", "01"),
        ("January", "01"),
        ("March", "03"),
        ("Sep.", "09"),   # trailing dot is tolerated
        ("3", "03"),
        ("12", "12"),
    ])
    def test_valid_months(self, value, expected):
        assert read_month(value) == expected

    @pytest.mark.parametrize("value", ["13", "0", "mm", "", "foo"])
    def test_invalid_or_placeholder_months(self, value):
        assert read_month(value) == ""


class TestReadDay:
    @pytest.mark.parametrize("value,expected", [
        ("5", "05"),
        ("05", "05"),
        ("31", "31"),
    ])
    def test_valid_days(self, value, expected):
        assert read_day(value) == expected

    @pytest.mark.parametrize("value", ["32", "0", "dd", "", "xx"])
    def test_invalid_or_placeholder_days(self, value):
        assert read_day(value) == ""


# ---------------------------------------------------------------------------
# parse_date_string: read one date written in any of the supported shapes
# ---------------------------------------------------------------------------

class TestParseDateString:
    def test_iso_full_date(self):
        assert parse_date_string("1965-03-29") == ("1965", "03", "29", False)

    def test_slashed_dmy(self):
        # 30/01/1972 read day-first (default DMY)
        assert parse_date_string("30/01/1972") == ("1972", "01", "30", False)

    def test_day_month_name_year(self):
        assert parse_date_string("31 Jul 1990") == ("1990", "07", "31", False)

    def test_month_name_day_year(self):
        assert parse_date_string("July 31, 1990") == ("1990", "07", "31", False)

    def test_month_name_and_year_only(self):
        assert parse_date_string("August 1961") == ("1961", "08", "", False)

    def test_year_only(self):
        assert parse_date_string("1971") == ("1971", "", "", False)

    def test_placeholder_parts_keep_only_the_year(self):
        # "dd/mm/1957" -- day and month are unknown markers, year survives
        assert parse_date_string("dd/mm/1957") == ("1957", "", "", False)

    def test_approx_word_sets_the_flag(self):
        assert parse_date_string("circa 1963") == ("1963", "", "", True)

    def test_foreign_calendar_year_yields_nothing(self):
        # 1402 cannot be Gregorian, so the whole date is rejected
        assert parse_date_string("1402") is None

    def test_empty_input(self):
        assert parse_date_string("") is None
        assert parse_date_string(None) is None


# ---------------------------------------------------------------------------
# read_approximate: read the flag the ENUM_NORMALIZE rule already settled
# ---------------------------------------------------------------------------

class TestReadApproximate:
    def test_reads_settled_true(self):
        # ENUM_NORMALIZE has already mapped the source word to "true"
        assert read_approximate("true") == "true"

    def test_reads_settled_false(self):
        assert read_approximate("false") == "false"

    def test_empty_flag_is_exact(self):
        assert read_approximate("") == "false"

    def test_range_forces_true_over_the_flag(self):
        # A resolver-derived range wins even when the source flag says exact,
        # so OFAC's "1955 to 1957" (isApproximate=false) still reads approximate
        assert read_approximate("false", from_text=True) == "true"


# ---------------------------------------------------------------------------
# expand_range: "1945-1950" becomes one row per year, unless it is too wide
# ---------------------------------------------------------------------------

class TestExpandRange:
    def test_between_and(self):
        years, span = expand_range("between 1945 and 1950")
        assert years == ["1945", "1946", "1947", "1948", "1949", "1950"]
        assert span  # the matched span text is reported back

    def test_from_to(self):
        years, _ = expand_range("From Year: 1957 To Year: 1959")
        assert years == ["1957", "1958", "1959"]

    def test_span_too_wide_is_not_a_date(self):
        # 1090-2011 is 921 years -- no birth date spans that, so no years
        years, span = expand_range("1090-2011")
        assert years == []
        assert span == "1090-2011"

    def test_no_range_present(self):
        assert expand_range("1945") == ([], "")


# ---------------------------------------------------------------------------
# build_row: FullDate is only filled when the date is complete
# ---------------------------------------------------------------------------

class TestBuildRow:
    def test_complete_date_gets_full_date(self):
        row = build_row("1980", "01", "15", "Birth", False, "")
        assert row["FullDate"] == "1980-01-15"
        assert row["IsApproximate"] == "false"

    def test_partial_date_leaves_full_date_empty(self):
        row = build_row("1980", "", "", "Birth", False, "")
        assert row["FullDate"] == ""
        assert row["Year"] == "1980"

    def test_approximate_flag_is_rendered_as_text(self):
        row = build_row("1980", "01", "15", "Birth", True, "")
        assert row["IsApproximate"] == "true"


# ---------------------------------------------------------------------------
# resolve_dates: the public entry point -- rows in, normalised rows out
# ---------------------------------------------------------------------------

class TestResolveDates:
    def test_resolves_separate_day_month_year_parts(self):
        rows = [{"Year": "1980", "Month": "jan", "Day": "15", "Type": "Birth"}]
        result = resolve_dates(rows)

        assert len(result) == 1
        assert result[0]["FullDate"] == "1980-01-15"
        assert result[0]["Type"] == "Birth"

    def test_deduplicates_identical_rows(self):
        rows = [
            {"Year": "1980", "Month": "01", "Day": "15", "Type": "Birth"},
            {"Year": "1980", "Month": "01", "Day": "15", "Type": "Birth"},
        ]
        assert len(resolve_dates(rows)) == 1

    def test_non_list_input_is_safe(self):
        assert resolve_dates("not a list") == []


# ---------------------------------------------------------------------------
# clean_text: Excel noise is stripped
# ---------------------------------------------------------------------------

class TestCleanText:
    def test_strips_excel_carriage_return_marker(self):
        cleaned = clean_text("2020_x000D_extra")
        assert "_x000D_" not in cleaned
        assert "2020" in cleaned

    def test_joins_list_values(self):
        assert clean_text(["a", "b"]) == "a, b"

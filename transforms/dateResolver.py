"""
Turns the many shapes a source can publish a date in into normalised rows.

The contract is one rule for every list:

    one raw date row in  ->  a list of normalised date rows out (0, 1 or many)

A range becomes one row per year, a cell holding several dates becomes one
row per date, and a row we cannot read at all becomes no rows rather than a
row full of noise.

FullDate always holds a complete ISO date or nothing at all, so a partial
date never reaches the database looking like a whole one.
"""

import re


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Sources mark an unknown part of a date rather than leaving it out,
# for example UKSL publishes "dd/mm/1957"
PLACEHOLDERS = {"dd", "mm", "yy", "yyyy", "d", "m", "xx", "??", "--", "00", "0"}

CALENDARS = {
    "GREGORIAN", "ISLAMIC", "HIJRI", "PERSIAN",
    "SOLAR", "JULIAN", "BUDDHIST",
}

APPROX_WORDS = ("approx", "circa", "about", "around", "est.", "possibly")

# Words this run has already reported as missing from the sheet, so each
# unknown approximate value is printed once rather than once per record.
_UNKNOWN_APPROX_SEEN = set()

# A range in free text. UN writes "From Year: 1973 To Year: 1974",
# other sources write "between 1945 and 1950" or "1945-1950".
RANGE_PATTERNS = [
    r"(?i)from\s*(?:year)?\s*:?\s*(\d{4})\s*(?:to|till|until|through|-|–)\s*"
    r"(?:year)?\s*:?\s*(\d{4})",
    r"(?i)between\s+(\d{4})\s+and\s+(\d{4})",
    # Any two years joined by a range word or dash, so "1957 till 1959"
    # expands the same way "from 1957 to 1959" does
    r"(?i)\b(\d{4})\s*(?:-|–|—|to|till|until|through)\s*(\d{4})\b",
]

# A birth date is never uncertain across more than half a century, so a
# wider span is something else wearing a range's clothes: a reference
# number, or a period someone was active. Those are recorded as text
# rather than turned into years nobody published.
MAX_RANGE_YEARS = 50

# Words and marks that join the two ends of a range. "and" is left out
# because a list reads the same way, as in "1968, 1969 and 1970".
RANGE_CONNECTOR = re.compile(
    r"^\s*(?:-|–|—|to|till|until|through)\s*$",
    re.IGNORECASE,
)

# Nobody on a sanctions list was born before this, so a four digit year
# below it is not a Gregorian date. It is usually a Hijri year that the
# source published without saying so: those run around 1300 to 1450.
# The oldest genuine year across every list is 1922.
MIN_YEAR = 1850
MAX_YEAR = 2200

# Used to pull a date back out of text that also carries other words,
# for example UN writes "Nov. 1973" alongside its own labels.
# Order matters: the most complete shape gets first claim on the text.
TOKEN_PATTERNS = [
    r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}",
    r"[0-9a-zA-Z?]{1,4}[/.\-][0-9a-zA-Z?]{1,4}[/.\-][0-9a-zA-Z?]{2,4}",
    r"\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}",
    r"[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}",
    r"[A-Za-z]{3,9}\.?\s+\d{4}",
    r"\d{1,2}[/.\-]\d{4}",
    r"\d{4}",
]


# The UN mapping builds its note by gluing labels onto FROM_YEAR and
# TO_YEAR. By the time a note is stored those years are rows of their
# own, so the labels are duplication whether or not they carry a value.
RANGE_LABEL = re.compile(r"(?i)\b(?:from|to)\s+year\s*:?\s*\d{0,4}")


def clean_text(value):
    """Excel hands us carriage returns as _x000D_ and rows can hold lists."""
    if isinstance(value, list):
        return ", ".join(
            clean_text(item) for item in value if str(item).strip()
        )

    text = str(value or "").replace("_x000D_", " ")
    text = re.sub(r"[\r\n\t]+", ", ", text)

    return re.sub(r"\s+", " ", text).strip()


def clean_note(value):
    """Drop generated scaffolding, keep whatever the source itself wrote."""
    text = RANGE_LABEL.sub(" ", clean_text(value))

    return re.sub(r"\s+", " ", text).strip(" ,;:-")


def first_value(value):
    """A mapped field can arrive as a list when the source repeats it."""
    if isinstance(value, list):
        for item in value:
            if str(item).strip():
                return item

        return ""

    return value


# =========================================================
# VALUE READERS
# =========================================================

def is_placeholder(token):
    return str(token).strip().lower() in PLACEHOLDERS


def read_year(value):
    text = str(value or "").strip()

    if not re.fullmatch(r"\d{4}", text):
        return ""

    if not MIN_YEAR <= int(text) <= MAX_YEAR:
        return ""

    return text


def disbelieved_year(value):
    """
    A four digit number that cannot be a Gregorian year.

    Distinct from a year we simply cannot read: "19yy" is a placeholder
    meaning unknown, and the day and month around it are still usable,
    whereas "1402" is a real number from another calendar and nothing
    beside it can be trusted.
    """
    text = str(value or "").strip()

    return bool(re.fullmatch(r"\d{4}", text)) and not read_year(text)


def read_month(value):
    text = str(value or "").strip().lower().rstrip(".")

    if not text or is_placeholder(text):
        return ""

    if text in MONTHS:
        return f"{MONTHS[text]:02d}"

    if text.isdigit() and 1 <= int(text) <= 12:
        return f"{int(text):02d}"

    return ""


def read_day(value):
    text = str(value or "").strip()

    if not text or is_placeholder(text):
        return ""

    if text.isdigit() and 1 <= int(text) <= 31:
        return f"{int(text):02d}"

    return ""


def read_calendar(note):
    """
    EU maps calendarType into Note, so a note that is nothing but a
    calendar name is metadata rather than something to read as a date.
    """
    text = str(note or "").strip()

    if text.upper() in CALENDARS:
        return text.upper(), ""

    return "", text


def load_approx_vocab(prenorm_df):
    """
    Build the approximate/exact vocabulary from preNormalization.xlsx.

    A single general row (source '*', normalization_type 'approximate')
    carries the whole list as ``word=true|word=false|...``, so the words a
    source uses for an uncertain date live in the sheet a data analyst can
    edit, not in this code. Returns {word_lower: 'true'|'false'}, or {} when
    no such row is present (callers then fall back to the constants above).
    """
    vocab = {}

    if prenorm_df is None:
        return vocab

    try:
        rows = prenorm_df[
            prenorm_df["normalization_type"].astype(str).str.strip()
            == "approximate"
        ]
    except Exception:
        return vocab

    for _, row in rows.iterrows():
        rule = str(row.get("normalization_rule", "") or "")

        for pair in rule.split("|"):
            if "=" not in pair:
                continue

            word, result = pair.split("=", 1)
            word = word.strip().lower()
            result = result.strip().lower()

            if word and result in {"true", "false"}:
                vocab[word] = result

    return vocab


def read_approximate(value, from_text=False, vocab=None):
    # A date the resolver itself judged approximate -- it came from a range,
    # from several candidate years, or from words like "circa" -- is
    # approximate whatever the source's own flag says. OFAC, for one, marks a
    # "1955 to 1957" birth range with isApproximate=false (it tracks range-ness
    # in a separate isDateRange field), so the range detection has to win.
    if from_text:
        return "true"

    text = str(value or "").strip().lower()

    # No source flag and not derived-approximate: treat as exact.
    if not text:
        return "false"

    # The approximate/exact word list lives only in preNormalization.xlsx now.
    if vocab and text in vocab:
        return vocab[text]

    # A non-empty word the sheet does not list: print it once so it can be
    # added to the 'approximate' row, and fall back to exact.
    if text not in _UNKNOWN_APPROX_SEEN:
        _UNKNOWN_APPROX_SEEN.add(text)
        print(
            "[dateResolver] approximate value not in preNormalization "
            f"list: {text!r} (defaulted to false)"
        )

    return "false"


# =========================================================
# DATE STRINGS
# =========================================================

def strip_time(text):
    return re.sub(r"\s+\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\s*$", "", text)


def strip_approx_words(text):
    return re.sub(
        r"(?i)\b(approximately|approx\.?|circa|about|around|possibly)\b",
        "",
        text,
    ).strip(" ,")


def order_day_month(first, second, date_order):
    """
    Decide which of two numbers is the day and which is the month.

    The values win over the configured order when only one reading is
    possible, so a source labelled MDY that publishes 24/08 is still
    read as 24 August rather than discarded.
    """
    day_first = read_day(first)
    month_first = read_month(first)
    day_second = read_day(second)
    month_second = read_month(second)

    # Only one arrangement can be true
    if not month_first and month_second:
        return day_first, month_second

    if not month_second and month_first:
        return day_second, month_first

    if str(date_order).upper() == "MDY":
        return day_second, month_first

    return day_first, month_second


def parse_date_string(text, date_order="DMY"):
    """
    Read a single date written as text.

    Returns (year, month, day, approximate) with month and day possibly
    empty, or None when there is no date here at all.
    """
    raw = str(text or "").strip()

    if not raw:
        return None

    approximate = any(word in raw.lower() for word in APPROX_WORDS)

    cleaned = strip_approx_words(strip_time(raw))

    if not cleaned:
        return None

    def result(year, month, day):
        # A four digit year we cannot believe means the whole date is in
        # some other calendar, so its day and month are not Gregorian
        # either and none of it can be kept
        if disbelieved_year(year):
            return None

        return read_year(year), month, day, approximate

    # 1965-03-29
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", cleaned)

    if match:
        year, month, day = match.groups()
        return result(year, read_month(month), read_day(day))

    # 30/01/1972, dd/mm/1957, dd/09/1958, 15/08/19yy
    match = re.fullmatch(
        r"([0-9a-zA-Z?]{1,4})[/.\-]([0-9a-zA-Z?]{1,4})[/.\-]([0-9a-zA-Z?]{2,4})",
        cleaned,
    )

    if match:
        first, second, year = match.groups()
        day, month = order_day_month(first, second, date_order)
        return result(year, month, day)

    # 31 Jul 1990
    match = re.fullmatch(
        r"(\d{1,2})\s+([A-Za-z]{3,9})\.?\s+(\d{4})", cleaned
    )

    if match:
        day, month, year = match.groups()
        return result(year, read_month(month), read_day(day))

    # July 31, 1990
    match = re.fullmatch(
        r"([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", cleaned
    )

    if match:
        month, day, year = match.groups()
        return result(year, read_month(month), read_day(day))

    # August 1961
    match = re.fullmatch(r"([A-Za-z]{3,9})\.?\s+(\d{4})", cleaned)

    if match:
        month, year = match.groups()

        if read_month(month):
            return result(year, read_month(month), "")

    # 09/1958
    match = re.fullmatch(r"(\d{1,2})[/.\-](\d{4})", cleaned)

    if match:
        month, year = match.groups()
        return result(year, read_month(month), "")

    # 1971
    match = re.fullmatch(r"(\d{4})", cleaned)

    if match:
        return result(match.group(1), "", "")

    return None


def expand_range(text):
    """
    A span of years becomes one year per row, so either end still matches.

    Returns (years, span). years is empty when a span was found but is
    too wide to be a date, and the caller keeps the span as text instead.
    """
    for pattern in RANGE_PATTERNS:
        match = re.search(pattern, str(text or ""))

        if not match:
            continue

        # Read the ends as plain numbers rather than as believable years,
        # so a span like "1090-2011" is still recognised as a span and
        # suppressed below instead of leaking one of its ends as a date
        first, second = match.group(1), match.group(2)

        if not (first.isdigit() and second.isdigit()):
            continue

        start, end = int(first), int(second)

        if start > end:
            start, end = end, start

        span = match.group(0).strip()

        if end - start > MAX_RANGE_YEARS:
            return [], span

        years = [
            read_year(year)
            for year in range(start, end + 1)
            if read_year(year)
        ]

        if not years:
            return [], ""

        return years, span

    return [], ""


def split_fragments(text):
    parts = re.split(r"\s*[,;]\s*|\s+and\s+", str(text or ""))
    return [part.strip() for part in parts if part.strip()]


def has_content(parsed):
    """A year, or a day and month together, is worth keeping."""
    if not parsed:
        return False

    year, month, day, _ = parsed

    return bool(year or (month and day))


def scan_date_tokens(text, date_order="DMY"):
    """
    Pull dates out of text that carries other words around them.

    Longer shapes claim their span first, so "Nov. 1973" is read as a
    month and a year rather than as a bare year.
    """
    results = []
    claimed = []

    for pattern in TOKEN_PATTERNS:
        for match in re.finditer(pattern, text):
            start, end = match.span()

            if any(start < done_end and end > done_start
                   for done_start, done_end in claimed):
                continue

            parsed = parse_date_string(match.group(), date_order)

            if not has_content(parsed):
                continue

            claimed.append((start, end))
            results.append((start, end, parsed))

    results.sort(key=lambda item: item[0])

    # Two dates joined by "to" or a dash are the ends of one uncertain
    # date rather than two confirmed ones, so neither is exact
    is_range = any(
        RANGE_CONNECTOR.match(text[results[i][1]:results[i + 1][0]])
        for i in range(len(results) - 1)
    )

    if is_range:
        return [
            (year, month, day, True)
            for _, _, (year, month, day, _) in results
        ]

    return [parsed for _, _, parsed in results]


def parse_text_dates(text, date_order="DMY"):
    """
    Read free text that may hold a range, several dates, or one date.

    DFAT publishes "Approximately 1963, 30/01/1972" in a single cell, so
    each fragment is read on its own and keeps its own approximation.
    """
    text = clean_text(text)

    if not text:
        return []

    years, span = expand_range(text)

    if years:
        return [(year, "", "", True) for year in years]

    # A span too wide to be a date must not fall through to the readers
    # below, which would pick up one of its ends as a real year
    if span:
        return []

    results = []

    for fragment in split_fragments(text):
        parsed = parse_date_string(fragment, date_order)

        if has_content(parsed):
            results.append(parsed)

    # Nothing read cleanly, so look for a date sitting inside the words
    if not results:
        results = scan_date_tokens(text, date_order)

    # Several candidate dates in one field -- a comma-separated list of years
    # like "1945, 1946, 1947" or a couple of alternatives -- means the real
    # date is uncertain, so none of them is exact. Mark every one approximate.
    if len(results) > 1:
        results = [
            (year, month, day, True)
            for year, month, day, _ in results
        ]

    return results


# =========================================================
# ROW RESOLUTION
# =========================================================

def build_row(year, month, day, date_type, approximate, note, original_value=""):
    full_date = ""

    # Only a whole date earns the FullDate column
    if year and month and day:
        full_date = f"{year}-{month}-{day}"

    return {
        # The raw date exactly as the source published it, kept beside the
        # parsed parts so a whole date and the string it came from both survive.
        "OriginalValue": clean_text(original_value),
        "FullDate": full_date,
        "Day": day,
        "Month": month,
        "Year": year,
        "Type": date_type,
        "IsApproximate": "true" if approximate else "false",
        "Note": clean_note(note),
    }


def resolve_row(row, date_order="DMY", approx_vocab=None):
    """Turn one mapped date row into a list of normalised rows."""
    if not isinstance(row, dict):
        return []

    date_type = clean_text(first_value(row.get("Type") or row.get("type")))
    calendar, note = read_calendar(clean_text(row.get("Note") or row.get("note")))

    source_approx = first_value(row.get("IsApproximate"))

    # A non Gregorian row carries its parts in that calendar, so reading
    # them as Gregorian would store a year that never existed
    foreign_calendar = bool(calendar) and calendar != "GREGORIAN"

    # OriginalValue is the raw date the source published, and the single field
    # a list needs to map. Until a list is migrated to it, fall back to the
    # older FullDate field so the raw string is still captured and parsed.
    original_value = clean_text(
        first_value(
            row.get("OriginalValue")
            or row.get("FullDate")
            or row.get("date_full")
        )
    )

    # 1. a complete date the source already gave us
    if original_value:
        parsed = parse_date_string(original_value, date_order)

        if parsed and (parsed[0] or (parsed[1] and parsed[2])):
            year, month, day, approx = parsed

            return [build_row(
                year, month, day, date_type,
                read_approximate(source_approx, approx, approx_vocab) == "true",
                note,
                original_value,
            )]

    # 2. the parts the source gave us
    if not foreign_calendar:
        year = read_year(first_value(row.get("Year") or row.get("year")))
        month = read_month(first_value(row.get("Month")))
        day = read_day(first_value(row.get("Day")))

        if year or (month and day):
            return [build_row(
                year, month, day, date_type,
                read_approximate(source_approx, vocab=approx_vocab) == "true",
                note,
                original_value,
            )]

    # 3. whatever the free text holds (OriginalValue first, then Note)
    parsed_notes = parse_text_dates(original_value or note, date_order)

    if parsed_notes:
        return [
            build_row(
                year, month, day, date_type,
                read_approximate(source_approx, approx, approx_vocab) == "true",
                note,
                original_value,
            )
            for year, month, day, approx in parsed_notes
        ]

    # 4. a span too wide to be a birth date, kept as text so the record
    #    survives without inventing years the source never published
    _, wide_span = expand_range(original_value or note)

    if wide_span:
        return [build_row(
            "", "", "", date_type,
            True,
            note or wide_span,
            original_value,
        )]

    # A date published only in another calendar produces nothing. Its
    # numbers are meaningless read as Gregorian, and carrying them in a
    # note would still leave a year like 1402 in the data.
    return []


def resolve_dates(rows, date_order="DMY", approx_vocab=None):
    """Resolve every date row on a record and drop duplicates."""
    if not isinstance(rows, list):
        return []

    resolved = []
    seen = set()

    for row in rows:
        for item in resolve_row(row, date_order, approx_vocab):
            key = (
                item["FullDate"], item["Day"], item["Month"],
                item["Year"], item["Type"], item["Note"],
            )

            if key in seen:
                continue

            seen.add(key)
            resolved.append(item)

    return resolved

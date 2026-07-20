# The date pipeline

How dates get from a published watchlist into the common schema.

Code: [`transforms/dateResolver.py`](../transforms/dateResolver.py)
Invoked from: [`transforms/postNormalization.py`](../transforms/postNormalization.py) via the
`DATE_NORMALIZATION` rule in `data/rules/postNormalization.xlsx` (priority 4).

---

## Where it sits

```
parse -> map (fieldMapper) -> Dates[] rows -> postNormalization -> resolved Dates[]
                                                  |
                                                  +-- DATE_NORMALIZATION (priority 4)
                                                  |     -> dateResolver.resolve_dates()
                                                  +-- DEDUPLICATE_ALL_ARRAYS (priority 100)
```

Two separate responsibilities:

- **The mapper** pulls whatever a source publishes into `Dates[]` rows, using the paths in
  `mapping.xlsx`. It knows nothing about date formats.
- **The resolver** turns those raw rows into clean ones. It knows nothing about source paths.

Adding a new list means describing where its dates live in the Excel. No new code.

---

## Why the old handler was replaced

It assumed the date always lived in a text blob:

```python
raw_text = item.get("Note") or item.get("note") or item.get("date_full")
           or item.get("Year") or item.get("year") or ""
if not raw_text:
    continue
```

Three fatal properties:

1. **`Note` came first**, so it shadowed everything else. EU's `Note` holds `"GREGORIAN"`
   (the calendar type), so no date was ever found and **every EU row was dropped**. UN's
   `Note` holds label text, which shadowed a perfectly good `Year`.
2. **It never read `FullDate`, `Day` or `Month`.** Those fields did not exist when it was
   written.
3. **No regex match meant the row vanished**, with no fallback to the structured fields
   sitting right beside it.

Result before the rewrite: UN 947 -> 68 rows, EU 3,844 -> 0 rows.

---

## The two contracts

> **One raw date row in -> a list of clean date rows out (0, 1 or many).**

A range becomes several rows. A cell holding three dates becomes three rows. A row with no
date becomes zero rows. Every list obeys the same rule.

> **`FullDate` holds a complete ISO date (`YYYY-MM-DD`) or nothing.**

Never partial, never placeholder text. If a date is incomplete, the known parts go in
`Year`/`Month`/`Day` and the original goes in `Note`. Nothing downstream has to guess
whether `FullDate` can be trusted.

`Dates` is an array on the person. Extra dates are extra objects **in that array** — the
person record is never duplicated.

```json
{
  "EntityId": "6908002",
  "Dates": [
    {"FullDate": "1965-12-28", "Day": "28", "Month": "12", "Year": "1965", ...},
    {"FullDate": "1965-12-29", "Day": "29", "Month": "12", "Year": "1965", ...}
  ]
}
```

---

## How one row is resolved

### Step 0 — context

- **Calendar** — if `Note` is *exactly* a calendar name (`GREGORIAN`, `ISLAMIC`, `HIJRI`,
  `PERSIAN`, `SOLAR`, `JULIAN`, `BUDDHIST`), it is metadata, not a note. It is pulled out
  and `Note` becomes empty.
- **Date order** — `DMY` or `MDY`, from the source config.

Values arriving as lists are flattened, and Excel artifacts (`_x000D_`, newlines, tabs)
become separators.

### Step 1 — trust `FullDate` if it parses

`1965-03-29 00:00:00` -> time stripped -> `Year 1965, Month 03, Day 29`,
`FullDate = 1965-03-29`.

If it parses only partially (`dd/mm/1957`), the known parts are kept, `FullDate` stays
empty, and the original string is preserved in `Note`.

### Step 2 — otherwise use the components

`Year` / `Month` / `Day`, each validated independently:

| Field | Accepted |
|---|---|
| `Year` | 4 digits, **1850–2200** |
| `Month` | 1–12, or a month name (`Nov`, `November`) |
| `Day` | 1–31 |

The 1850 floor is a calendar guard, not just a sanity check. Nobody on a sanctions list was
born before it — the oldest genuine year across every source is **1922** — so a four digit
year below it is not Gregorian. In practice it is a Hijri year the source published without
saying so, and those currently run around 1300–1450.

**A year we disbelieve rejects the whole date**, not just the year. If `24/06/1402` were
read as "day 24, month 06, unknown year", the day and month would still be Hijri. So the
entire value is discarded.

This is deliberately different from a year we simply cannot *read*. `15/08/19yy` keeps
`Day 15, Month 08` — `19yy` is a placeholder meaning "unknown", and the parts beside it are
still Gregorian. `1402` is a real number from another calendar, and nothing beside it can be
trusted.

This validation is the safeguard against mapping drift. When OFAC's mapping was shifted by
one row and `Year` contained `"Organization Established Date"`, it was rejected rather than
written to the database — and because the field came out *empty rather than wrong*, the
mapping bug stayed visible.

**Skipped entirely when the calendar is not Gregorian.**

### Step 3 — otherwise read the text

Three passes:

1. **Range?** `From Year: 1973 To Year: 1974`, `between 1945 and 1950`, `1945-1950`
   -> one row per year, all marked approximate.
2. **Several dates?** Split on commas, semicolons and the word "and". Each fragment parsed
   on its own, **keeping its own approximation**. This is what makes
   `"Approximately 1963, 30/01/1972"` work — one fuzzy year plus one exact date, which a
   single row cannot represent.
3. **Date buried in prose?** Scan for date-shaped tokens anywhere in the string. Longer
   shapes claim their span first, so `"Nov. 1973 From Year: To Year: "` yields
   `Month 11, Year 1973` rather than a bare year.

### Step 4 — a span too wide to be a date

Over `MAX_RANGE_YEARS` (50), emit one row with **no date numbers** and the original text in
the `Note`. See "The range cap" below.

### Step 5 — non-Gregorian dates produce nothing

If the calendar is not Gregorian and no Gregorian date was found, **no row is emitted**.

The numbers are meaningless read as Gregorian, and keeping them in a note would still leave
a year like `1402` sitting in the data. A date that cannot be expressed in the schema's
calendar is not recorded at all.

Where a source supplies both — EU publishes `birthdate: 1982-04-19` alongside
`year: 1402` — step 1 takes the Gregorian date and the row is kept normally.

### Finally

**The note is cleaned.** The UN mapping builds its note by gluing labels onto `FROM_YEAR`
and `TO_YEAR`:

```
INDIVIDUAL_DATE_OF_BIRTH.NOTE | "From Year: " | FROM_YEAR | "To Year: " | TO_YEAR
       ^ the source's own note                ^ generated scaffolding
```

By the time a note is stored those years are rows of their own, so the scaffolding is
duplication whether or not it carried a value. It is stripped, keeping whatever the source
itself wrote:

| Note before | Note after |
|---|---|
| `From Year:  To Year: ` | *(empty)* |
| `Nov. 1973 From Year:  To Year: ` | `Nov. 1973` |
| `From Year: 1973 To Year: 1974` | *(empty)* — both years are rows already |
| `Approximately From Year: 1966 To Year: 1967` | `Approximately` |

Effect on UN: **947 notes carrying scaffolding -> 934 empty, 32 real source content**
(`Approximately`, `August 1961`, `Between Aug. and Sep. 1977`, `from false passport`, …).
No rows are lost — the counts are identical before and after.

Duplicates are then dropped on `(FullDate, Day, Month, Year, Type, Note)`.

---

## Value shapes handled

| Input | Year | Month | Day | FullDate |
|---|---|---|---|---|
| `1965-03-29 00:00:00` | 1965 | 03 | 29 | `1965-03-29` |
| `30/01/1972` (DMY) | 1972 | 01 | 30 | `1972-01-30` |
| `06/05/2026` (MDY) | 2026 | 06 | 05 | `2026-06-05` |
| `31 Jul 1990` | 1990 | 07 | 31 | `1990-07-31` |
| `July 31, 1990` | 1990 | 07 | 31 | `1990-07-31` |
| `August 1961` | 1961 | 08 | — | *(empty)* |
| `09/1958` | 1958 | 09 | — | *(empty)* |
| `1971` | 1971 | — | — | *(empty)* |
| `dd/09/1958` | 1958 | 09 | — | *(empty)* |
| `dd/mm/1957` | 1957 | — | — | *(empty)* |
| `15/08/19yy` | — | 08 | 15 | *(empty)* |

**Placeholders** (`dd`, `mm`, `yy`, `xx`, `??`, `00`) mean "the source is telling us this
part is unknown". They become empty, never the literal text `"dd"`.

**List values** — DFAT's merge step produces `Note: ["1963", "1965", "1955"]`. Flattened
before parsing -> three rows.

---

## Date order

Set per source in `pipelines/watchlistConfigs.py`:

```python
"date_order": "DMY",   # or "MDY"
```

| Source | Evidence | Order |
|---|---|---|
| UKSL | publishes `dd/mm/1963` literally | DMY |
| UN-SANCTIONS | `24/06/1980` | DMY |
| DFAT | `24/08/1962`, `30/01/1972` | DMY |
| DNFBP | `03/31/2027` | **MDY** |
| all others | no ambiguous values observed | DMY |

**The value overrides the config when only one reading is possible.** Under an `MDY`
config, `24/08/1962` is still read as 24 August, because there is no month 24. This is a
typo-catcher: a wrong `date_order` degrades instead of silently corrupting thousands of
dates. It only helps when a number exceeds 12 — for `06/05/2026` both readings are valid,
so the config decides.

---

## The range cap

`MAX_RANGE_YEARS = 50`.

The cap is a **plausibility test**, not a row-count limit. Evidence from the data:

- Real UN birth ranges (`FROM_YEAR`/`TO_YEAR`) are **1 to 6 years**. Nothing wider exists.
- Wider spans found in text are **not dates**: `1090-2011` (a reference number),
  `between 1994 and 2016` and `from 1993 to 2012` (periods someone was *active*).

So past a certain width the thing almost certainly is not a birth date, and expanding it
would manufacture birth years that were never published.

| Span | Behaviour |
|---|---|
| <= 50 years | one row per year, all approximate |
| > 50 years | one row, **no year asserted**, original text kept in `Note` |

Emitting the two endpoints for a wide span was rejected: for `1090-2011` that would store a
birth year of **1090**. Emitting nothing at all would silently lose the fact that something
date-shaped was there. Keeping the row with no numbers preserves both — an analyst sees the
text, the matching engine gets nothing false to match on.

Range detection reads the two ends as **plain numbers**, not as believable years. If it used
the 1850 floor, `1090-2011` would fail to register as a span at all, fall through to the
token scanner, and leak `2011` as a birth year — worse than the problem it was guarding
against.

## Ranges are approximate

A range is uncertain whether it is written in years or in full dates, so **every row from a
range is marked `IsApproximate: true`**, including `1980-05-01 till 1980-05-13`.

A range is detected by the connector *between* two parsed dates: `to`, `till`, `until`,
`through`, `-`, `–`. The word **"and" is deliberately excluded**, because a list reads
identically — `"1968, 1969 and 1970"` is three separate dates, not a range. (`between X and
Y` is still recognised, by its own pattern.)

All spellings expand identically, so every year in the span becomes a row:

```
1957 till 1959   between 1957 and 1959   from 1957 to 1959
1957-1959        1957 until 1959         1957 through 1959
   -> all give three rows: 1957, 1958, 1959
```

ISO dates are not mistaken for ranges — `1965-03-29` stays one date, because the segments
after the dash are not four digits.

---

## Per-list behaviour

| List | Where dates come from | What the resolver does | Order |
|---|---|---|---|
| **UN-SANCTIONS** | `FullDate<-DATE`, `Year<-YEAR`, `IsApproximate<-TYPE_OF_DATE`, `Note<-`concat | Step 1 for the 554 with a real DATE; step 2 for year-only; step 3 recovers `Nov. 1973` and expands FROM/TO ranges. `EXACT`/`APPROXIMATELY`/`BETWEEN` -> true/false | DMY |
| **EU-FINANCIAL** | `FullDate<-birthdate.birthdate`, `Day`/`Month`/`Year`, `IsApproximate<-circa`, `Note<-calendarType` | `Note` recognised as calendar metadata. Step 1 for the 3,202 complete; step 2 for year-only; Islamic via step 5 | DMY |
| **UKSL** | `Year<-DOB[]` (the whole string) | `Year` rejects `dd/mm/1963`, falls to step 3 which parses the placeholder format | DMY |
| **DFAT** | `Note<-Date of Birth` (often a list, often multi-date) | Flatten -> split fragments -> one row per date, each with its own approximation | DMY |
| **DNFBP** | `Note<-"UPDATING OF REGISTRATION IS REQUIRED ON"` | Step 3, US format | **MDY** |
| **OFAC-SDN / NON-SDN** | `Year<-valueDate.fromDateBegin` via `conditional_path` | That field holds full dates (`1990-07-31`); `Year` validation rejects it, step 3 parses it properly | DMY |
| **EU-TRAVEL-BAN** | `Year<-year` | Step 2 | DMY |
| **ATC x2** | `Note<-atc_birth_date` | Step 3 | DMY |

---

## What it deliberately refuses

Verified across all sources — every dropped row was checked to contain no date:

| Dropped | Count | Why correct |
|---|---|---|
| `"GREGORIAN"` alone | 140 EU | calendar metadata, no date attached |
| `"From Year:  To Year: "` alone | 49 UN | label text with nothing in it |
| `"30-35 years old"` | 1 UN | an **age**, not a date |
| `"10/061962"` | 1 DFAT | malformed source value; the year guard refuses to invent `0619` |

Rejecting a bad parse is a feature. Producing year `0619` would be worse than producing
nothing.

---

## Results

| Source | Before | After |
|---|---|---|
| UN-SANCTIONS | 68 | **896** |
| EU-FINANCIAL-SANCTIONS | 0 | **3,943** |
| DFAT | 2,413 | **2,942** |
| all sources | — | **0 invalid values** |
| all sources | — | **0 dropped rows containing a real Gregorian date** |

Resolved years span **1922–2028** across all dated rows, with nothing below the 1850 floor.

---

## Known limits

1. **No Hijri conversion.** Non-Gregorian dates are discarded rather than converted, so a
   person whose birth date exists *only* in the Hijri calendar ends up with no date row.
   That is 4 EU records. Where a source supplies the Gregorian equivalent too — 1 EU record,
   and the UN entries for the same people — the date is kept normally.

   The same individual appears across three lists: UN DATAID `2962519` and the DFAT record
   both carry `24/06/1402` alongside `19/04/1982`. All three now resolve to 1982 only.

   Building real conversion would recover the 4 records. It needs a Hijri library and is its
   own task.
3. **Ranges expand to one row per year**, which favours screening recall but loses the fact
   that it *was* a range. Revisit if the matching engine wants a span.
4. **The `concat_path` mapping still generates the scaffolding.** The resolver strips it on
   the way out, so stored notes are clean, but `mapping.xlsx` still builds
   `"From Year: " | FROM_YEAR | …` and the raw mapped rows still carry it. Fixing it at
   source means the `concat_path` label-pairing change.
5. **UKSL mapping points `DOB[]` at `Year`.** Works via the step 3 fallback, but
   `Dates[].FullDate` would be the honest target.
6. **Two-digit years are not guessed.** `19yy` yields no year rather than assuming 1900s.

---

## Testing

The resolver is pure functions with no pipeline imports, so any case can be run directly:

```python
from transforms.dateResolver import resolve_row

resolve_row({"Note": "Approximately 1963, 30/01/1972"}, "DMY")
# -> [{"Year": "1963", "IsApproximate": "true",  ...},
#     {"FullDate": "1972-01-30", "Day": "30", "Month": "01", "Year": "1972",
#      "IsApproximate": "false", ...}]
```

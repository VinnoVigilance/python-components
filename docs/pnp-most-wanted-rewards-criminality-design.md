# PNP criminality reward snapshot (2022)

List key: `PNP_MOST_WANTED_WITH_REWARDS_CRIMINALITY_2022`.
Source: Philippine National Police (PNP). Category: Official / Law Enforcement.

## Source and reporting period

Original PDF:
https://www.spd.ncrpo.pnp.gov.ph/wp-content/uploads/2024/02/AsOfOct13_MWPs-with-Reward-criminality_1665621706.pdf

The document is dated **13 October 2022** on all 97 pages and reports **840 detail records**. The `/2024/02/` upload directory does not establish the reporting year. Acquisition folders record download time separately.

The committed fixture is `test/fixtures/parsing/pnp_rewards_criminality_2022.pdf` (1,260,023 bytes). Its SHA-256 fingerprint is:
`63d69478fd81de1ec4ef84afe8c6120f0072df310ef13f9b0de2b52958d3092e`.

This is a historical snapshot. It does not establish current wanted status, current reward availability, or an annual publication cadence. Register future reporting years separately after inspecting their actual sources.

## Pipeline and reuse

`Acquire PDF -> extract -> preprocess -> pre-normalize -> map -> post-normalize -> persist`

- Acquisition reuses the existing browser collector with `BYPASS`, `stealth_browser`, and navigation-based `save_pdf`. Direct requests returned HTTP 403; browser acquisition succeeded.
- Extraction uses the opt-in `labeled_blocks` mode in `parsing/pdfParser.py`. The source configuration identifies labels and logical columns without absolute page coordinates.
- Preprocessing reuses `set_constant_field` and `generate_composite_id`. No new preprocessing handler remains.
- No source-specific pre-normalization or post-normalization rule is needed. Alias splitting uses existing mapping `explode` with `aliases_raw::/`.
- Existing post-normalization generates normalized names, alias search tokens, and phonetic keys.
- Existing persistence requires source/list database lookup rows; registration SQL is supplied separately.

## Label-relative extraction

A person starts at a `NAME` label. The next start, or the configured footer/summary marker, ends that person's block. Within each block, complete label phrases locate fields in their configured logical columns. Their actual positions determine column order, field order, and value boundaries. Label text height determines geometric tolerances; configuration contains no fixed page coordinates or page dimensions.

A source may configure `label_font_pattern` to distinguish label words from the same words appearing in values. Missing or repeated labels, unaligned label columns, overlapping fields, and side-by-side record starts fail explicitly instead of silently omitting data.

The optional `validation` object can require page metadata and reconcile extraction with a document summary. A summary pattern must contain a named `record_count` capture group. These checks are not mandatory for every source. This source checks its date footer and its reported total. The existing pipeline minimum-count guard is also set to 840.

Supported layout family: vertically repeated records, labels followed by values in aligned columns, single-line labels, and records contained on one page. Multiline values are supported. Side-by-side cards, wrapped labels, labels above values, and records spanning pages require a different extraction strategy. Existing table, text, OCR, and image-card modes retain their behavior.

PRO9 remains on its existing dynamic card-border mode; it is not forced through the vertical-record mode.

## Field-specific whitespace preservation

A PDF's visible line wrapping can split a word without storing a space. Reading layout text and flattening its newlines would turn `Jove` / `n` into `Jove n`, while some other line breaks really do separate words, such as `Israel` / `Iran`.

The optional per-field setting `text_mode: stored_characters` reads the existing library's character objects within the located field, retaining spaces actually stored in the PDF. Only aliases and criminal case references enable it here. `layout_field` separately retains the multiline representation as evidence:

| Field used by mapping | Preserved layout field |
| --- | --- |
| `aliases_raw` | `aliases_layout_raw` |
| `case_numbers_raw` | `case_numbers_layout_raw` |

Stored and layout extraction must contain identical non-whitespace characters in the same order. A mismatch stops parsing. This guard does not establish that arbitrary PDFs store spaces correctly, so the setting remains opt-in for inspected sources.

The inspected snapshot produces `Joven`, `JINMOY`, `POKTONG`, `HENRY`, `CHELO`, and `Sander` without inserting spaces inside those words. Legitimate spaces such as `Israel Iran` remain. Five canonical records have repaired aliases, and 82 have repaired case-reference spacing, compared with the earlier layout-only extraction. Case punctuation and separators remain intact.

## Mapping and incomplete source data

| Raw field | Canonical destination |
| --- | --- |
| `name_raw` | `Names[].Name`, with a Primary Name type only when populated |
| `aliases_raw` | `Aliases[]`, split at `/` by mapping |
| `offenses_raw` | additionalInfo: Offense |
| `reward_raw` | additionalInfo: Reward |
| `issuing_court_raw` | additionalInfo: Issuing Court |
| `dilg_mc_raw` | additionalInfo: DILG Memorandum Circular Number |
| `case_numbers_raw` | additionalInfo: Case Reference |
| `region_raw` | additionalInfo: Region as Listed |
| `reference_date_raw` | additionalInfo: List Reference Date |
| `identity_note` | additionalInfo: Identity Note, only when the primary name is blank |

The source country is not an individual's nationality, and region is not a residential address. Rewards remain as printed; no currency is inferred from an unlabeled field. Missing values and a court printed as `-` are omitted by existing mapping cleanup.

The final output contains 839 populated primary-name arrays and 367 populated alias arrays. One record on page 73 has no primary name and only the alias Nonoy. It retains an empty Names array, the listed alias, and an explicit identity note. `SAPTULA, Dexson Adam 0` retains its source-printed trailing zero. Neither issue is silently corrected.

## Identity, scheduling, and versioning

Composite-ID inputs are `name_raw`, `aliases_layout_raw`, `dilg_mc_raw`, and `region_raw`, with prefix `PNP-REWARDS-CRIMINALITY-2022`. The preserved alias layout keeps existing identities stable when display extraction improves. All 840 IDs are unique in this snapshot. Source changes to those identity inputs can still change IDs because the publisher supplies no permanent person identifier.

`every_12_months` is a supported rolling recheck interval for this historical URL, not a promise of annual discovery. `continuous` versioning applies within this dated list, allowing a corrected edition to update membership. Another reporting year requires its own key.

## Workbook and database registration

- `sourceConfig.xlsx`: list key, entity field `entity_type`, external ID field `unique_id`.
- `mapping.xlsx`: new TYPE/value columns for this exact key.
- `pickLists.xlsx`: list registration under PNP and missing additional-information vocabulary.
- `preNormalization.xlsx` and `postNormalization.xlsx`: unchanged.

`scripts/sql/pnp-rewards-criminality-2022.sql` registers the PNP source if absent and this dated list. It preserves existing metadata on reruns and writes no member data. Run it explicitly against the intended database before production ingestion. It has not been executed against a live database as part of this work.

## Running and replaying

From the repository root:

```powershell
vv-env/Scripts/python.exe -m scripts.watchlist.stage_ingest PNP_MOST_WANTED_WITH_REWARDS_CRIMINALITY_2022
vv-env/Scripts/python.exe -m scripts.watchlist.run_pipeline_no_db PNP_MOST_WANTED_WITH_REWARDS_CRIMINALITY_2022
```

For offline replay, add `--source-file test/fixtures/parsing/pnp_rewards_criminality_2022.pdf` to the second command. Final output is `data/final/PNP_MOST_WANTED_WITH_REWARDS_CRIMINALITY_2022_final.jsonl`. The stage harness writes intermediate artifacts under ignored data directories. Excel paste exports are local review aids, not application inputs.

## Verification

Generic parser tests use small generated PDFs to cover shifted layouts, reordered configuration, genuine spaces versus word wraps, multiline values, optional count validation, field-name collisions, and rejection of missing labels, ambiguous labels, invalid required values, reordered character streams, and side-by-side records.

Source regression tests parse the full committed PDF and run the actual workbook normalization chain. They cover all 840 records, unique identities, corrected aliases and search tokens, case references, the alias-only record, the trailing zero, Unicode names, missing values, and date/count validation failures. A curated 48-record preprocessed sample also participates in the existing source golden and conformance suites.

The original PDF and parser fixtures allow replay without network or database access. Database persistence and production indexing are outside these checks.

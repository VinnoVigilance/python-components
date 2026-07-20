# Branch changes: `refactor/xml-multiple-root-tags`

Summary of everything changed on this branch, grouped by concern. The date
work has its own document: [`date-pipeline.md`](date-pipeline.md).

---

## 1. XML parser — [`parsing/xmlParser.py`](../parsing/xmlParser.py)

**Multiple root tags.** `run_xml_ingestion` and `parse` accept a list of root
tags and a `config`, so one source can pull several record types from one file
(UN publishes `INDIVIDUAL` and `ENTITY` in the same document). Tag resolution
was duplicated in both methods and is now one helper, `resolve_root_tags`.

**Exact tag match instead of suffix match.** Matching was
`elem.tag.endswith(tag)`, which would also match `sanctionEntity` when looking
for `ENTITY`. It is now `elem.tag.split("}")[-1] == tag` (`matches_root_tag`) —
strips the XML namespace, then compares exactly.

**Streaming memory.** The old code only called `elem.clear()` on matched
elements, so every other node stayed in memory for the whole parse — the entire
document on a large file. `release()` now clears the matched element and drops
already-processed siblings. On the 6,002-record EU file, elements retained in
memory dropped from ~6,027 to ~163.

**Comment safety.** XML comments carry a callable tag rather than a string. The
old code called `.split()` on it and crashed the whole parse. `elem_to_dict`
now filters out non-string tags, so a stray `<!-- comment -->` no longer breaks
a file.

---

## 2. Source auto-detection — [`utils/vvAnalystApp.py`](../utils/vvAnalystApp.py)

The Streamlit tool guesses which source an uploaded file belongs to. It used a
hardcoded pattern dictionary that still said `"UN": "UN"` after the config key
became `UN-SANCTIONS`, so UN files silently detected as nothing and parsed to
zero records.

`detect_source_for_file` now derives the names from the configs themselves
(`source_aliases` reads the config key, `source_name`, `list_name`, and an
optional `filename_aliases` list). Longest match wins, and a tie is only
accepted when the tied sources agree on root tags. The detection result is now
shown in the UI — a hit reports the source and its root tags, a miss warns
instead of silently falling back to `Designation`.

Config side: `filename_aliases` added to UKSL, EU-TRAVEL-BAN, UN-SANCTIONS and
EU-FINANCIAL so their real download filenames still match.

---

## 3. Field mapper — [`transforms/fieldMapper.py`](../transforms/fieldMapper.py)

**Placeholder values treated as empty.** A source's word for "nothing" —
`UNKNOWN`, `N/A`, `NA`, `NONE`, `NULL`, `-`, `--` — used to flow straight into
the output, where concatenated fields read like `"Baghdad UNKNOWN"` and a
country came out as the literal `"UNKNOWN"`. `drop_placeholder` converts these
to a missing value inside `resolve_in_context` and `resolve_all_in_context`, so
the existing "keep the default" guards handle them. Effect on real data: 1,342
concatenated strings cleaned, 1,557 all-placeholder POB rows removed, 86
`UNKNOWN` countries emptied.

> Note left in the code: `pickLists.xlsx` defines `Unknown` as a legitimate
> value for `Gender` and `Measures.Status`. No source produces it there today,
> but if one ever does it will need an exception.

**`resolve_in_context` restored to returning `None`.** An earlier edit made it
return `""` for a missing path, which silently disabled the `if value is not
None` guards that protect schema defaults (a missing value could overwrite a
`[]` with `""`). Reverted after confirming zero output change across ~40,000
records.

**`concat_path` literal support** (already on the branch) lets a mapping cell
mix quoted constants with paths, e.g. `"Low quality name:"| isLowQuality`.

---

## 4. Post-normalization — [`transforms/postNormalization.py`](../transforms/postNormalization.py)

`date_normalization_handler` went from ~165 lines of inline regex to 12 lines
that call the new resolver. Handlers now take an optional `config`, and
`PostNormalizationEngine` accepts a per-source config so the date order reaches
the resolver. See [`date-pipeline.md`](date-pipeline.md) for the resolver
itself.

---

## 5. Date resolver — [`transforms/dateResolver.py`](../transforms/dateResolver.py) (new)

The whole date normalisation engine. Fully documented in
[`date-pipeline.md`](date-pipeline.md).

---

## 6. Configuration

- [`pipelines/watchlistConfigs.py`](../pipelines/watchlistConfigs.py) —
  `date_order` (`DMY`/`MDY`) on all 11 sources; `filename_aliases`; the
  `UN` -> `UN-SANCTIONS` split and `EU-FINANCIAL-SANCTIONS` entry.
- [`pipelines/watchlistPipline.py`](../pipelines/watchlistPipline.py) — passes
  the source config into `PostNormalizationEngine`.
- [`transforms/preProcessingEngine.py`](../transforms/preProcessingEngine.py) —
  removed a dead `_resolve_value` method that called a non-existent
  `_get_nested_value` and would have crashed if ever invoked.

## 7. Rules workbooks

- `data/rules/mapping.xlsx` — OFAC-NON-SDN, DFAT and EU Measures/Dates
  alignment fixes; `Note` labelling for name quality; per-source date columns.
- `data/rules/pickLists.xlsx` — lookup-list edits.

## 8. Regenerated outputs

`data/final/*.jsonl` and `data/raw/*.jsonl` are re-runs of the pipeline with the
changes above. They are data, not code.

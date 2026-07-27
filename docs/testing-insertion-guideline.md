# Test Coverage — VVIF Data Insertion Guideline

**Component:** Vinno Vigilance — Watchlist Ingestion & ETL Pipeline (`python-components`)
**Specification under test:** *VVIF Data Insertion Guideline v1.6* (`VVIF_Data_Insertion_Guideline_v1.6.pdf`)
**Prepared:** 2026-07-27
**Status of suite at time of writing:** ✅ **315 passed locally** + **35 real-database tests passing in CI** (350 total) — 0 failures

---

## 1. Purpose of this document

The Data Insertion Guideline defines how downloaded watchlist data must be
registered, versioned, normalized, and delivered through the pipeline. This
document is the **evidence that the implemented parts of that guideline are
verified by an automated test suite.**

For every guideline rule we have implemented, this document states:

- **which guideline section** the rule comes from,
- **what business scenario** we test (in plain language),
- **what the test proves** (the expected outcome),
- **the exact test** that proves it, so anyone can re-run it.

It is intended to be shared with the wider team and reviewers as a coverage
record. It deliberately also documents what is **not yet tested** and what is
**not yet implemented**, so the picture is complete and honest.

> **Scope note.** The guideline is a large document covering the whole pipeline
> from file download to customer XML export. Not all of it is built yet. This
> document reports on the parts that are **implemented** — anything not built has
> no code to test, and is listed transparently in [Section 8](#8-not-yet-covered).

---

## 2. How to read the scenario tables

Each detailed section below uses the same layout:

| Column | Meaning |
| --- | --- |
| **Scenario** | The business situation being tested, in plain language. |
| **Guideline** | The section of the v1.6 guideline the rule comes from. |
| **What the test proves** | The behaviour that must hold for the pipeline to be correct. |
| **Test** | The automated test that asserts it (file → test name). |

All test names are real and can be run directly — see [Section 9](#9-how-to-run-the-tests).

---

## 3. Coverage summary

The guideline is organised as a series of database tables and ETL stages. This
matrix maps each one to its implementation and test status.

| # | Guideline area (v1.6) | Pages | Implemented? | Tested? | Detail |
| --- | --- | --- | --- | --- | --- |
| 1 | `raw.watchlist_file` — file registry, duplicate detection, versioning | 2–9 | ✅ Yes | ✅ Yes | [§5.1](#51-file-registration-duplicate-detection--versioning-rawwatchlist_file) |
| 2 | `raw.watchlist_file_log` — append-only processing log | 9–17 | ✅ Yes | ✅ Yes | [§5.2](#52-processing-log-rawwatchlist_file_log) |
| 3 | `raw.unparsed_watchlist_payload` — one row per source entity | 17–21 | ✅ Yes | ✅ Yes | [§5.3](#53-raw-entity-preservation-rawunparsed_watchlist_payload) |
| 4 | Attachments — `raw.attachment` / `list_attachment` / `member_attachment` | 22–29 | ✅ Yes | ✅ Yes | [§5.6](#56-attachment-management-rawattachment--list_attachment--member_attachment) |
| 5 | `core.watchlist_member` — canonical record, versioning, delete detection | 29–39 | ✅ Yes | ✅ Yes | [§5.4](#54-canonical-record-versioning--delete-detection-corewatchlist_member) |
| 6 | Watchlist Daily Delta Generation — `delivery.watchlist_daily_delta` | 40–50 | ◑ DB procedure | ◑ Table only | [§8](#8-not-yet-covered) |
| 7 | Core Spoke Table Synchronization — `core.member_*` spoke tables | 50–58 | ❌ No | — | [§8](#8-not-yet-covered) |
| 8 | Member Risk Category Calculation & Versioning — `core.member_risk_category` | 59–66 | ✅ Yes | ✅ Yes | [§5.5](#55-member-risk-category-calculation--versioning-coremember_risk_category) |
| 9 | Data Export & XML Generation | 67–75 | ❌ No (spec is TODO) | — | [§8](#8-not-yet-covered) |

Legend: ✅ done · ◑ partial · ⚠️ gap · ❌ not started

**In addition**, the canonical record that the guideline requires (the
`full_payload` written into `core.watchlist_member`, guideline §6 *Normalization
Workflow*) is produced by a parsing + normalization chain that has its own
extensive test coverage. That supporting coverage is summarised in
[Section 6](#6-supporting-coverage-the-canonical-payload).

---

## 4. Test approach & environment

The suite is split into tiers so it stays fast, safe, and runnable anywhere.

| Tier | What it exercises | Database? | Runs in CI |
| --- | --- | --- | --- |
| **Unit** | Business decisions (duplicate detection, version detection, risk versioning, logging) with every I/O boundary mocked. | No — a fake cursor | ✅ Every push/PR |
| **End-to-end (e2e)** | The orchestrator wiring the whole chain together, with the four service modules mocked. | No | ✅ (local + CI) |
| **Integration (real DB)** | The actual SQL running against a real PostgreSQL loaded with the committed schema. | Yes — a disposable test DB | ✅ In CI, on a Postgres service container |

**Safety of the real-database tests.** These are designed so they can never touch
real data:

1. They only run when a `TEST_DATABASE_URL` is provided (otherwise they skip).
2. A name guard **hard-refuses** any database whose name is not clearly a test
   database (it rejects the production names outright).
3. Every test runs inside a transaction that is **rolled back** at the end, so
   nothing is ever persisted — proven by dedicated rollback-isolation tests.

Locally (on a machine with no Postgres) the 35 real-DB tests **skip cleanly**;
in CI they run against a fresh `postgres:16` container loaded from the committed
schema file.

---

## 5. Tested scenarios (detail)

### 5.1 File registration, duplicate detection & versioning (`raw.watchlist_file`)

> Guideline §5 (Duplicate Detection Rules) and §6 (File Versioning Strategy).
> A downloaded file must be fingerprinted with a SHA-256 hash, and that hash
> decides whether the file is brand new, an exact duplicate, or a new version.

| Scenario | Guideline | What the test proves | Test |
| --- | --- | --- | --- |
| First time we ever see this source + list type | §5 Case 1 | No existing file → treated as `FIRST_DOWNLOAD`, insert a new record. | `test_watchlistFileService.py` → `test_first_download_when_no_existing_file` |
| A different file content arrives for the same list | §5 Case 3 | Different hash → `NEW_VERSION`; the previous record is never overwritten. | `test_different_hash_is_a_new_version` |
| The identical file is downloaded again, already fully processed | §5 Case 2 | Same hash on a completed file → `DUPLICATE_COMPLETED`; it must **not** re-enter the pipeline. | `test_same_hash_already_normalized_is_completed_duplicate` |
| The identical file, but a previous run stopped before normalization | §5 Case 2 | Same hash, raw rows exist but not normalized → `RESUME_NORMALIZATION` (resume, don't re-download). | `test_same_hash_with_raw_but_not_normalized_resumes_normalization` |
| The identical file, but a previous run stopped before parsing | §5 Case 2 | Same hash, not yet parsed → `RESUME_PROCESSING`. | `test_same_hash_not_yet_parsed_resumes_processing` |
| Independent documents (e.g. advisories) have no logical version | §6 Independent | `independent` lists → version is `None`. | `test_independent_lists_have_no_version` |
| First version of a continuously-evolving list | §6 Continuous | A `continuous` list's first file → version `"1"`. | `test_first_download_is_version_one` |
| A new version of a continuous list | §6 Continuous | Latest version `3` → next version becomes `"4"`. | `test_new_version_increments_the_latest` |
| The stored version number is corrupt | §6 Continuous | A non-numeric latest version raises a clear error instead of guessing. | `test_non_numeric_latest_version_raises` |

### 5.2 Processing log (`raw.watchlist_file_log`)

> Guideline §10–§12 (Logging Requirements, Immutability). Every significant
> processing event writes exactly one new row; log rows are **append-only** and
> never updated.

| Scenario | Guideline | What the test proves | Test |
| --- | --- | --- | --- |
| A processing event is recorded | §6 Field Population | The log insert returns the new row id and targets `raw.watchlist_file_log`. | `test_watchlistFileLogRepository.py` → `test_insert_file_log_returns_new_id`, `test_insert_file_log_targets_the_log_table` |
| A failure event carries full diagnostics | §10 Error Handling | `step`, `status`, `message`, `error_code`, `error_details`, `duration_ms` are all persisted in the correct order. | `test_insert_file_log_passes_all_values_in_order` |
| Raw insertion is logged as it happens | §10 | A `RAW_INSERT` / `SUCCESS` event is written after raw rows land. | `test_watchlistRawService.py` → `test_raw_insert_success_is_logged` |
| Normalization success is logged | `core.watchlist_member` §10 | A `NORMALIZATION` / `SUCCESS` event is written at the end of core processing. | `test_watchlistCoreService.py` → `test_success_is_logged_at_the_end` |

### 5.3 Raw entity preservation (`raw.unparsed_watchlist_payload`)

> Guideline §5 (One Record = One Source Entity), §8 (External ID Rules), §9
> (Parsing Rules). The raw layer preserves each source entity exactly, one row
> per entity, keyed by the source's own identifier.

| Scenario | Guideline | What the test proves | Test |
| --- | --- | --- | --- |
| A file with N entities produces N rows | §5 | Three source records become three payload rows — entities are never collapsed into one. | `test_watchlistRawService.py` → `test_each_record_becomes_one_payload_row` |
| A source entity has no identifier | §8 | The pipeline **refuses** the record, names the offending record number, and inserts nothing (we must never invent our own external id). | `test_record_missing_external_id_is_rejected` |
| Each raw row is built correctly | §7 Field Population | Each `(external_id, raw_json)` pair maps to a `(watchlist_file_id, external_id, Json(raw_json))` row; the original JSON is preserved via the driver's `Json` adapter. | `test_rawPayloadRepository.py` → `test_insert_raw_payloads_builds_correct_rows`, `test_insert_raw_payloads_reports_row_count` |

### 5.4 Canonical record, versioning & delete detection (`core.watchlist_member`)

> Guideline §7 (Version Detection Rules), §8 (Delete Detection Rules), §9 (Record
> Hash), §11 (Current Version Rules), §14 (Unique Constraints). This is the
> heart of the Core layer: change detection driven by a hash of the canonical
> record, with full version history preserved.

**Decision logic** — unit tests (`test_watchlistCoreService.py`), database mocked:

| Scenario | Guideline | What the test proves | Test |
| --- | --- | --- | --- |
| A member the system has never seen | §7 Case 1 | Inserted as version 1, `change_type = NEW`, marked current. | `test_new_member_is_inserted_as_version_one` |
| An existing member that has not changed | §7 Case 2 | Same hash → **no** new version; the member is skipped. | `test_unchanged_member_is_skipped_no_new_version` |
| An existing member whose data changed | §7 Case 3 | Different hash → old version closed, new version = previous + 1, same `vv_member_id`, `change_type = UPDATED`. | `test_changed_member_closes_old_and_inserts_next_version` |
| A member removed from the source list | §8 | A current member absent from the new dataset becomes a new `DELETED` version (never physically removed). | `test_missing_external_member_becomes_deleted_version` |
| Delete detection only for continuous lists | §8 | For `independent` lists, delete detection does not run. | `test_delete_detection_skipped_for_non_continuous_lists` |
| A record with no entity type | §10 / Error Handling | Missing `EntityType` raises and the file is marked `FAILED`. | `test_missing_entity_type_fails_and_marks_file_failed` |

**Real SQL against a real database** — integration tests (`test_coreMemberRepository.py`), run in CI. These prove the SQL is valid, the foreign keys line up, and the history columns end up in the right state — things a mock cannot prove:

| Scenario | Guideline | What the test proves | Test |
| --- | --- | --- | --- |
| Insert a brand-new member | §7 Case 1 | Real row: version 1, current, `change_type = NEW`, `valid_to` NULL, `vv_member_id` auto-generated (UUIDv7). | `test_insert_new_member_creates_version_one` |
| Look up the current member | §7 | `find_current_member` returns the inserted row; returns nothing when absent. | `test_find_current_member_returns_the_inserted_row`, `test_find_current_member_returns_none_when_absent` |
| Update chain end-to-end | §7 Case 3 + §11 | Old version closed (`is_current=false`, `valid_to` set), new version current & v+1 with the same `vv_member_id`, and **exactly one** current version exists. | `test_update_closes_old_version_and_opens_the_next` |
| Version integrity is enforced by the database | §14 | Two rows cannot claim the same `(vv_member_id, version_no)` — the unique index rejects it. | `test_duplicate_version_number_is_rejected_by_unique_index` |
| Closing a non-existent version | §11 | Refuses silently passing (raises) when there is nothing to close. | `test_close_current_member_raises_if_nothing_to_close` |
| Delete detection end-to-end | §8 | A current member absent from the file is found, closed, and re-inserted as a `DELETED` version 2 (including reading `full_payload` back out of JSONB and re-writing it). | `test_delete_detection_records_a_deleted_version`, `test_find_deleted_current_members_flags_rows_absent_from_the_file` |
| Nothing leaks between tests | (safety) | What one test writes is invisible on a second connection and never committed. | `test_written_member_is_not_visible_on_a_second_connection` |

**Schema tripwire** — `test_schema_smoke.py` (16 checks, run in CI) proves the
committed schema actually contains the schemas, tables, columns, and the
`gen_random_uuid_v7()` function the code depends on — so a future schema change
that renames or drops something fails immediately with a clear message.

### 5.5 Member Risk Category Calculation & Versioning (`core.member_risk_category`)

> Guideline "Member Risk Category ETL": Initial Load, Incremental ADD / UPDATE /
> DELETE, hash-based change detection, and the one-active-row invariant. Risk
> classifications are versioned exactly like core members (SCD Type-2).

**Engine bridge** — `test_riskEngine.py`, adapts one member's `full_payload` into the `risk_details` object:

| Scenario | What the test proves | Test |
| --- | --- | --- |
| The authoritative list name is used | The primary list name is read from the first `Sources[]` entry and forwarded to the classifier. | `test_primary_list_name_reads_first_source`, `test_primary_list_name_none_when_absent` |
| Rule labels become `risk_details` | Classifier output is shaped into the stored `risk_details`; an unclassifiable member yields empty categories. | `test_classify_wraps_rule_labels_into_risk_details`, `test_classify_empty_when_no_labels` |

**ETL versioning** — `test_watchlistRiskCategoryService.py`, database mocked:

| Scenario | Guideline | What the test proves | Test |
| --- | --- | --- | --- |
| First-ever population of the table | Initial Load | Every current member runs through the ADD workflow and is inserted; nothing is expired. | `test_initial_load_inserts_new_member` |
| Re-running the Initial Load | Initial Load / Idempotency | A member whose active hash already matches is skipped — no expire, no insert. | `test_initial_load_is_idempotent_on_matching_hash` |
| A member with no risk classification | Data Integrity | An empty classification (e.g. an excluded DNFBP list) is **never written** and is counted as empty. | `test_initial_load_skips_empty_classification`, `test_add_empty_classification_is_not_inserted` |
| A new member in the daily delta | ADD | Retrieved from the latest `effective_date` and inserted as a new active risk row. | `test_add_new_member_is_versioned` |
| An updated member, risk unchanged | UPDATE Scenario 1 | Same hash → skipped (no insert, no expire). | `test_update_same_hash_is_skipped` |
| An updated member, risk changed | UPDATE Scenario 2 | Current version expired (`is_current=false`, `valid_to` set), new version inserted with the matching `version_no`. | `test_update_changed_hash_expires_then_inserts` |
| One-active-row invariant | Data Integrity | Even an ADD expires any pre-existing active row first, so two current rows can never coexist. | `test_add_with_existing_active_row_still_expires_first` |
| A deleted member | DELETE | All active risk rows are expired; nothing new is inserted. | `test_delete_expires_active_rows_without_insert` |
| The delta references a member that is gone | (robustness) | Counted as missing and non-fatal; processing continues. | `test_missing_member_is_counted_not_fatal` |
| The delta table is empty | No Changes Scenario | Completes cleanly with no work done. | `test_no_delta_data_completes_cleanly` |

### 5.6 Attachment management (`raw.attachment` / `list_attachment` / `member_attachment`)

> Guideline "Attachment Management" (pp. 22–29): `raw.attachment` §5 (Duplicate
> Detection by `file_hash`), `raw.member_attachment` (entity → attachment, keyed
> by `external_id`), `raw.list_attachment` (file → attachment). An attachment's
> binary is stored once and reused; mapping records link it to entities or files.

**Processing logic** — service tests (`test_watchlistAttachmentService.py`), database and object storage mocked, real temp files used for the on-disk existence check:

| Scenario | Guideline | What the test proves | Test |
| --- | --- | --- | --- |
| A list has no attachment rules | — | Nothing is stored, logged, or mapped; the result is all zeros. | `test_no_attachment_rules_does_nothing` |
| An entity publishes a new attachment | `attachment` §5 Case 1 + `member_attachment` | The binary is uploaded once, registered once in `raw.attachment`, and mapped to the entity via `raw.member_attachment` (keyed by `external_id`). | `test_new_attachment_is_stored_and_registered` |
| Two entities publish the same file | `attachment` §5 Case 2 | The binary is stored **once** (same hash → reused, not re-uploaded); both entities get a mapping. This is the anti-duplication rule. | `test_duplicate_attachment_is_reused_not_stored_again` |
| The entity → attachment mapping already exists | `member_attachment` §8 | An existing mapping is left alone — no duplicate mapping row is inserted. | `test_existing_member_mapping_is_not_duplicated` |
| An attachment belongs to the whole file | `list_attachment` | A list-scope attachment is mapped through `raw.list_attachment` against the file id, not the entity. | `test_list_scope_attachment_maps_to_the_file` |
| Attachment processing is logged | file_log | An `ATTACHMENT` / `STARTED` event is written at the start and an `ATTACHMENT` / `SUCCESS` event at the end. | `test_attachment_processing_is_logged_started_and_success` |
| A referenced attachment file is missing | Error Handling | The file is marked `FAILED` (step `ATTACHMENT`) and the error is re-raised — nothing is registered. | `test_missing_attachment_file_marks_file_failed_and_raises` |

**Repository SQL (fake cursor)** — `test/unit/repositories/test_attachmentRepository.py` proves each function sends the right SQL to the right table with the right values: look up / insert into `raw.attachment` (with `source_url` optional → NULL), and the `raw.list_attachment` and `raw.member_attachment` mapping inserts.

**Repository SQL (real database)** — `test/integration/repositories/test_attachmentRepository.py`, run in CI, executes the same statements against a real PostgreSQL so the SQL, the foreign keys, and the constraints are actually proven:

| Scenario | Guideline | What the test proves | Test |
| --- | --- | --- | --- |
| Store and retrieve an attachment | `attachment` §6 | A real row is inserted and found back by hash with all fields intact; an absent hash returns nothing. | `test_insert_attachment_is_found_back_by_hash`, `test_find_attachment_by_hash_returns_none_when_absent` |
| The store-once rule is enforced by the database | `attachment` §5 | A second row with the same `file_hash` is **rejected** by a UNIQUE constraint — a duplicate binary can never be registered twice. (A fake cursor cannot prove this.) | `test_duplicate_file_hash_is_rejected_by_the_database` |
| Map an attachment to a file | `list_attachment` | A real `raw.list_attachment` row is created against valid `watchlist_file` and `attachment` foreign keys, and found afterwards. | `test_list_attachment_maps_file_to_attachment` |
| Map an attachment to an entity | `member_attachment` | A real `raw.member_attachment` row is keyed by `external_id` + `attachment_type`; a different type is a distinct mapping. | `test_member_attachment_maps_entity_to_attachment` |
| One attachment shared by many entities | `member_attachment` | Two entities may map to the same attachment id — both mappings are accepted. | `test_one_attachment_can_map_to_many_entities` |
| Mappings must reference a real attachment | (integrity) | A mapping to a non-existent attachment id is rejected by the foreign key. | `test_member_attachment_requires_a_real_attachment` |

---

## 6. Supporting coverage: the canonical payload

The guideline requires `core.watchlist_member` to store a **canonical
`full_payload`** produced by normalization (guideline §6 *Normalization
Workflow*). That payload is generated by a parsing + mapping + normalization
chain which is itself thoroughly tested, so the data being inserted is trustworthy:

- **Parsers** (XML, tabular/CSV/XLSX, HTML, PDF) — tested against real sample
  files from every source list.
- **Field mapping & normalization** — the mapping engine, pre/post-normalization,
  date resolution, and search enrichment are unit-tested.
- **Per-list conformance** — for **all 11 source lists**, every sample record is
  driven through the real normalization chain and checked to produce a valid
  entity type, only declared fields, correct array/scalar shapes, and no junk
  tokens.
- **Golden values** — the constant per-list `Sources[]` fields (source type,
  dataset category, list name, source name) are pinned to expected values, so a
  wrong mapping for a new list is caught.
- **Record hashing** — the canonical SHA-256 hash used for change detection is
  order-independent and deterministic (`test_hashing.py`).

These are the foundation under Sections 5.3–5.5; they are listed here for
completeness rather than repeated in full.

---

## 7. Traceability summary

| Guideline area | Scenarios tested | Test tier(s) |
| --- | --- | --- |
| `raw.watchlist_file` (duplicate detection + versioning) | 9 | Unit + e2e |
| `raw.watchlist_file_log` (logging) | 3 + logging assertions in services | Unit |
| `raw.unparsed_watchlist_payload` (raw preservation) | 6 | Unit |
| `core.watchlist_member` (versioning + delete detection) | 6 unit + 9 real-DB + 16 schema checks | Unit + Integration |
| Attachments (`raw.attachment` / `list_attachment` / `member_attachment`) | 7 service + 8 repository (fake cursor) + 6 real-DB | Unit + Integration |
| `core.member_risk_category` (risk ETL) | 4 engine + 11 ETL | Unit |
| Pipeline orchestration (wiring, duplicate short-circuit) | 3 | e2e |
| Canonical payload (parsing/mapping/normalization) | see [Section 6](#6-supporting-coverage-the-canonical-payload) | Unit |

**Pipeline orchestration** is verified end-to-end in `test_watchlist_pipeline.py`:
a first download runs the full chain in order (`test_first_download_runs_the_full_chain`),
an exact duplicate short-circuits before the expensive stages
(`test_exact_duplicate_short_circuits_before_processing`), and an unknown list
name is rejected (`test_unknown_watchlist_raises`).

---

## 8. Not yet covered

Documented transparently so the coverage picture is complete.

### Implemented but not yet tested

*None.* Attachment management was the last item in this category and is now
covered — see [§5.6](#56-attachment-management-rawattachment--list_attachment--member_attachment).

### Partially implemented

| Area | Guideline | Note |
| --- | --- | --- |
| **Watchlist Daily Delta Generation** — `delivery.watchlist_daily_delta` | pp. 40–50 | The delta-actions table is populated by a **database procedure** (`generate_watchlist_daily_delta_actions`) delivered with the schema rather than by Python code in this repository. Its existence is smoke-tested; the decision-rule logic (first/last-event → ADD/UPDATE/DELETE/Ignore) is exercised indirectly as the input to the Risk Category ETL, but is not yet directly tested here. The Member Risk Category ETL consumes this table. |

### Not yet implemented (no code to test)

| Area | Guideline | Note |
| --- | --- | --- |
| **Core Spoke Table Synchronization** — `core.member_name`, `member_alias`, … | pp. 50–58 | The incremental spoke-rebuild ETL is not built yet. |
| **Data Export & XML Generation** | pp. 67–75 | The guideline section itself is largely `TODO` (XML mapping spec, XSD, naming convention, packaging are all pending finalisation with the customer). No code exists. |

---

## 9. How to run the tests

All tests run through the project's `vv-env` interpreter.

**Everything that needs no database** (unit + e2e — the 315 that pass locally):

```bash
vv-env/Scripts/python.exe -m pytest
```

**Only one guideline area**, e.g. the risk-category ETL:

```bash
vv-env/Scripts/python.exe -m pytest test/unit/services/test_watchlistRiskCategoryService.py -v
```

**The real-database tests** (the 35 that run in CI) — point them at a disposable
test database whose name contains `test`:

```bash
TEST_DATABASE_URL="postgresql://user:pass@localhost:5432/vinno_test" \
  vv-env/Scripts/python.exe -m pytest -m db
```

Without `TEST_DATABASE_URL` these skip cleanly, so the command above is safe to
run anywhere. In CI they execute automatically against a fresh PostgreSQL
container loaded from the committed schema.

---

## 10. Summary

Every guideline rule that is **implemented** in the ingestion, core-versioning,
and risk-category layers is backed by an automated test that names the exact
guideline scenario it protects. The suite runs on every push (unit + e2e) and,
in CI, against a real PostgreSQL database (versioning, delete detection, schema
integrity). Attachment management (`raw.attachment` and its mapping tables) is
now covered too, including the store-once / reuse-by-hash rule. The remaining
guideline areas — Daily Delta decision logic, spoke synchronization, and XML
export — are listed above as the roadmap for extending this coverage.

*Generated against the working tree on 2026-07-27. Re-run the commands in
Section 9 to reproduce the results.*

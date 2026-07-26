# Database schema

The DDL the pipeline runs against lives here. It is the **DBA-owned source of
truth**, committed so that both the integration tests and CI can create a real
(throwaway) database with the exact tables the code expects.

```
db/schema/vigilance_core_standard_v2_phase1.sql   the full schema (schemas, extensions,
                                                  functions, tables, indexes)
```

## Load it into a scratch database

```bash
createdb vinno_vigilance_test
psql "postgresql://postgres:pw@localhost:5432/vinno_vigilance_test" \
    -f db/schema/vigilance_core_standard_v2_phase1.sql
```

CI does the same automatically (see `.github/workflows/tests.yml`, the
`database-tests` job) against a disposable Postgres service container.

## When does the schema change vs. the mapping?

These are two different layers — they change on very different cadences:

* **`data/rules/mapping.xlsx`** maps each source's fields onto the canonical VV
  record (`Names[]`, `Aliases[]`, `Identifiers[]`, `Dates[]`, …). It changes
  **often** — every time a source adds a field or we map one differently.

* **`db/schema/*.sql`** is the shape of the tables in Postgres. It changes
  **rarely**.

The whole canonical record is stored verbatim in
`core.watchlist_member.full_payload` (a `jsonb` column). Because JSONB is
schemaless, **adding or editing mapping rows that target an existing canonical
section does not require any schema change** — the new data just lands inside
`full_payload`, and (for the sections that have a dedicated table:
`member_name`, `member_alias`, `member_identifier`, `member_date`,
`member_country`, `member_address`, `member_relationship`, `member_program`,
`member_contact`) into that existing table.

You only touch the schema when you introduce a **brand-new structural concept**
that needs its own typed, indexed, queryable table/column — e.g. deciding to
promote a canonical section that today lives only inside `full_payload` into its
own table for matching. Day-to-day mapping edits never need a migration.

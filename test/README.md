# Test suite

This folder holds the automated tests for the pipeline, plus the CI robot that
runs them on GitHub. If you have never used `pytest`, read this once — it is
short.

## The two things working together

1. **pytest** — the tool that runs tests on your machine.
2. **GitHub Actions** (`.github/workflows/tests.yml`) — a robot that runs the
   tests automatically on GitHub every time you push or open a pull request. If
   a test fails, you get a red ✗ and an email. No GitLab needed.

## Running tests yourself

Use the project's virtual environment (`vv-env`) so the real dependencies are
present:

```bash
# one-time: install the test tools into the venv
vv-env/Scripts/python.exe -m pip install -r requirements-dev.txt

# run everything that is safe to run anywhere (no real DB / network)
vv-env/Scripts/python.exe -m pytest -m "not db and not network and not e2e"

# run only the fast pure-logic tests
vv-env/Scripts/python.exe -m pytest -m unit

# see which lines of your code no test touches yet (coverage)
vv-env/Scripts/python.exe -m pytest --cov
```

(On macOS/Linux the interpreter is `vv-env/bin/python`.)

## How a test works (the whole idea in 4 lines)

```python
def test_year_only():
    # call your real code with a known input ...
    result = parse_date_string("1971")
    # ... and assert the exact answer you expect.
    assert result == ("1971", "", "", False)
```

If the real code ever stops returning that, the test fails and prints the file,
the test name, what it expected, and what it actually got.

> The `[ 42%]` next to each test is just a **progress bar** through the run —
> not a score. It counts up to 100% as tests finish.

## Folder layout — it mirrors the pipeline

```
test/
  unit/                fast tests. No real DB, no network, no live files.
    transforms/        date resolver ...
    utils/             hashing ...
    repositories/      DB code tested with a FAKE cursor (a mock)
    parsing/           parser factory ...
    ingestion/         downloader (network mocked out)
  integration/         tests that use a real database (opt-in, see below)
    conftest.py        the safe DB harness
    test_db_harness.py proves the harness + rollback safety
  fixtures/            small sample data files for tests (add as needed)
```

Each `test/...` path lines up with a real package in the project, so it is
always obvious where a new test belongs.

## Markers (grouping / tiers)

| marker         | meaning                                              | runs in CI? |
|----------------|------------------------------------------------------|-------------|
| `unit`         | pure logic or mocked I/O. Fast. Always safe.         | yes         |
| `integration`  | touches a real database or service.                  | no (yet)    |
| `db`           | needs a running PostgreSQL test database.            | no (yet)    |
| `network`      | needs live internet (source websites).               | no          |
| `e2e`          | full pipeline end-to-end.                            | no          |

Run one group: `pytest -m unit`. Skip groups: `pytest -m "not db and not network"`.

## Testing the database SAFELY

There are two complementary ways the DB code is tested, and **neither can harm
your real data**:

**1. Mocked (always on).** In `test/unit/repositories/` the database functions
are given a *fake* cursor. Nothing connects; we just verify the code sends the
right SQL with the right values. These run on the CI robot with no database.

**2. Real database (opt-in, isolated).** `test/integration/` can run the code
against a real PostgreSQL, protected by three layers (see
`test/integration/conftest.py`):

  - **Opt-in** — nothing connects unless you set `TEST_DATABASE_URL`. A normal
    `pytest` run never touches any database.
  - **Name guard** — the target database name must contain `test`, and known
    production names are hard-refused. A mistyped connection string cannot hit
    production.
  - **Rollback** — every test runs in a transaction that is always rolled back.
    Even the test database is left exactly as found.

To run them locally against a scratch database:

```bash
# create a throwaway DB that has your schema, then:
TEST_DATABASE_URL="postgresql://postgres:pw@localhost:5432/vinno_vigilance_test" \
    vv-env/Scripts/python.exe -m pytest -m db
```

## How to add a test for a new step

1. Find the module to cover (e.g. `transforms/fieldMapper.py`).
2. Create `test/unit/transforms/test_fieldMapper.py`.
3. If it needs a sample input file, put a **small** one under `test/fixtures/`
   and load it (never download in a unit test).
4. Write `test_*` functions that call the real code and `assert` the result.
5. Tag the module at the top: `pytestmark = pytest.mark.unit`.
6. Run `pytest -m unit` — green means it works, red names what broke.

## Roadmap (what is done, what is next)

Done:
- Pure logic: `dateResolver`, `hashing`.
- DB code (mocked): `watchlistFileLogRepository`, `rawPayloadRepository`.
- Parser factory; downloader model + delegation.
- Safe real-DB harness with rollback + name guard.
- CI robot running all of the above.

Next:
- More transforms: `fieldMapper`, `preNormalization`, `postNormalization`,
  `searchEnrichment`.
- Real parser tests with small committed fixtures (convert the old interactive
  `test/parsing/test_xml_parser.py`, then delete it from the `--ignore` list in
  `pyproject.toml`).
- Risk: `ruleMatcher`, `configLoader`.
- Commit a schema/migration file so the real-DB job can be enabled in CI (the
  second job in `.github/workflows/tests.yml` is scaffolded and commented).
- End-to-end test of `run_watchlist_pipeline` with the downloader and DB mocked.

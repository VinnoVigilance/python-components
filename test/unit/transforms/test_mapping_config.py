"""
Structural validation of data/rules/mapping.xlsx.

The mapping file changes frequently, so this test deliberately does NOT check
what any specific field maps to (that would break on every intentional edit).
Instead it checks the *shape* of the file -- the invariants that must hold no
matter how the mappings themselves change:

  * every declared Field Type is one the engine understands
  * every Entity Category is one the engine actually applies
  * every source's handler ("... TYPE") is a real handler name, so a typo like
    "conditonal_path" is caught instead of silently dropping data
  * rows grouped together target the same array (a group cannot span two arrays)
  * every configured source actually loads at least one rule

If you add or change mappings, these stay green as long as the file is
well-formed. They only fail when a row is genuinely malformed.

This mirrors the risk-config validation in risk/configLoader.py.
"""

from pathlib import Path

import pandas as pd
import pytest

from transforms.fieldMapper import HANDLERS, load_rules

pytestmark = pytest.mark.unit

MAPPING_FILE = Path(__file__).resolve().parents[2].parent / "data" / "rules" / "mapping.xlsx"

# Field Type values transforms/fieldMapper.default_value() understands.
KNOWN_FIELD_TYPES = {"string", "array", "object", "dict", "list", "json", "raw"}

# Entity categories detect_entity_type() can actually produce; a rule tagged
# with anything else would never be applied.
KNOWN_ENTITY_CATEGORIES = {"Individual", "Entity", "Vessel", "Aircraft"}

# Valid base handler names come straight from the engine, so adding a new
# handler to fieldMapper.HANDLERS automatically makes it valid here too.
KNOWN_HANDLERS = set(HANDLERS)


@pytest.fixture(scope="module")
def mapping_df():
    assert MAPPING_FILE.exists(), f"mapping file not found: {MAPPING_FILE}"
    return pd.read_excel(MAPPING_FILE)


def _source_names(df):
    """Each source contributes a "<name> TYPE" column and a "<name>" column."""
    return [c[:-len(" TYPE")] for c in df.columns if c.endswith(" TYPE")]


def test_field_types_are_known(mapping_df):
    bad = {
        str(v).strip().lower()
        for v in mapping_df["Field Type"].dropna().unique()
        if str(v).strip().lower() not in KNOWN_FIELD_TYPES
    }
    assert not bad, f"Unknown Field Type value(s) in mapping.xlsx: {sorted(bad)}"


def test_entity_categories_are_known(mapping_df):
    bad = {
        str(v).strip()
        for v in mapping_df["Entity Category"].dropna().unique()
        if str(v).strip() not in KNOWN_ENTITY_CATEGORIES
    }
    assert not bad, f"Unknown Entity Category value(s): {sorted(bad)}"


def test_every_source_type_is_a_known_handler(mapping_df):
    """A source_type may be several base handlers joined by '*' (the multi-value
    syntax). Every part must be a real handler, else that rule silently drops."""
    type_cols = [c for c in mapping_df.columns if c.endswith(" TYPE")]

    unknown = {}
    for col in type_cols:
        for raw in mapping_df[col].dropna():
            for part in str(raw).split("*"):
                token = part.strip().lower()
                if token and token not in KNOWN_HANDLERS:
                    unknown.setdefault(col, set()).add(token)

    assert not unknown, (
        "Unknown handler name(s) in mapping.xlsx "
        f"(typos silently drop data): { {k: sorted(v) for k, v in unknown.items()} }"
    )


def test_group_rows_target_a_single_array(mapping_df):
    """All rows sharing a Group must write into the same array root, e.g. every
    'sources' row targets Sources[...]. A group spanning two arrays is a bug."""
    rows = mapping_df[mapping_df["Group"].notna() & mapping_df["Field"].notna()]

    inconsistent = {}
    for group, group_rows in rows.groupby("Group"):
        roots = {
            str(field).split("[]")[0].strip()
            for field in group_rows["Field"]
        }
        if len(roots) > 1:
            inconsistent[str(group)] = sorted(roots)

    assert not inconsistent, f"Group(s) targeting multiple array roots: {inconsistent}"


def test_every_source_loads_at_least_one_rule(mapping_df):
    """Using the REAL loader: each configured source must yield >0 rules, so a
    renamed or emptied source column is caught."""
    empty_sources = [
        name for name in _source_names(mapping_df)
        if len(load_rules(str(MAPPING_FILE), name)) == 0
    ]
    assert not empty_sources, f"Source(s) that load zero mapping rules: {empty_sources}"

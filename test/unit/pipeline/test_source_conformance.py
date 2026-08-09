"""
Per-list conformance tests: run real sample records through the REAL
normalization chain (pre-normalize -> map -> post-normalize, using the actual
Excel rule files) and check the output obeys the contract -- WITHOUT pinning
exact values, so intentional mapping edits don't break these.

Why this shape of test (given the mapping changes often):
  * We do NOT assert "OFAC field X = value Y" (that would break on every edit).
  * We DO assert the output *conforms* to what the mapping declares and what the
    rest of the pipeline requires:
      - every record normalizes without error
      - EntityType is present and valid (the core service rejects a record
        without it)
      - every top-level field the output produces is a field the mapping
        actually declares (catches a typo'd / wrong target)
      - array fields are arrays and scalar fields are scalars (matches the
        mapping's Field Type)
      - no placeholder junk ("N/A", "UNKNOWN", ...) leaks into the output

Samples live in test/fixtures/sources/<LIST>_raw_sample.jsonl (~50 real records
each, cut from the real downloads). Add a list by dropping in its sample and
adding a line to SAMPLES.
"""

import json
from functools import lru_cache
from pathlib import Path

import pytest

from pipelines.watchlistConfigs import WATCHLIST_CONFIGS
from services.watchlistPipeline import watchlistNormalizationService as norm
from transforms.fieldMapper import load_rules

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAPPING_FILE = PROJECT_ROOT / "data" / "rules" / "mapping.xlsx"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "sources"

# list_name -> committed sample file (one per source list).
SAMPLES = {
    "OFAC-SDN": "OFAC-SDN_raw_sample.jsonl",
    "OFAC-NON-SDN": "OFAC-NON-SDN_raw_sample.jsonl",
    "UKSL": "UKSL_raw_sample.jsonl",
    "DFAT": "DFAT_raw_sample.jsonl",
    "EU-DESIGNATED-VESSELS": "EU-DESIGNATED-VESSELS_raw_sample.jsonl",
    "EU-TRAVEL-BAN": "EU-TRAVEL-BAN_raw_sample.jsonl",
    "EU-FINANCIAL-SANCTIONS": "EU-FINANCIAL-SANCTIONS_raw_sample.jsonl",
    "UN-SANCTIONS": "UN-SANCTIONS_raw_sample.jsonl",
    "ATC-DESIGNATED-TERRORIST-INDIVIDUALS": "ATC-DESIGNATED-TERRORIST-INDIVIDUALS_raw_sample.jsonl",
    "ATC-DESIGNATED-TERRORIST-GROUPS": "ATC-DESIGNATED-TERRORIST-GROUPS_raw_sample.jsonl",
    "DNFBP": "DNFBP_raw_sample.jsonl",
    "FBI-WANTED": "FBI-WANTED_raw_sample.jsonl",
}

VALID_ENTITY_TYPES = {"Individual", "Entity", "Vessel"}

# Tokens that are never legitimate real data, so they must never appear in the
# canonical output. NOTE: "NA" is deliberately NOT here -- it is Namibia's ISO
# country code, a real value. Sources that mean "not available" use "N/A".
JUNK_TOKENS = {"N/A", "#REF!"}


@lru_cache(maxsize=None)
def _schema(list_name):
    """Top-level canonical fields the mapping declares, and whether each is an
    array. Derived from mapping.xlsx, so it tracks the mapping automatically."""
    roots = {}
    for rule in load_rules(str(MAPPING_FILE), list_name):
        root = rule.target_path.split("[]")[0].split(".")[0].strip()
        if not root:
            continue
        roots[root] = roots.get(root, False) or ("[]" in rule.target_path)
    return roots


@lru_cache(maxsize=None)
def _canonical_records(list_name):
    """Run the real normalization chain over the list's sample records."""
    config = WATCHLIST_CONFIGS[list_name]
    pre, mapper, post = norm.create_normalization_engines(config)

    with open(FIXTURES / SAMPLES[list_name], encoding="utf-8") as f:
        raw_records = [json.loads(line) for line in f if line.strip()]

    return tuple(
        norm.normalize_record(r, config, pre, mapper, post) for r in raw_records
    )


@pytest.mark.parametrize("list_name", list(SAMPLES))
class TestSourceConformance:

    def test_every_record_normalizes_with_valid_entity_type(self, list_name):
        records = _canonical_records(list_name)
        assert len(records) > 0

        for rec in records:
            entity_type = str(rec.get("EntityType", "")).strip()
            # The core service raises if EntityType is missing -- enforce it here.
            assert entity_type, "canonical record is missing EntityType"
            assert entity_type in VALID_ENTITY_TYPES, (
                f"unexpected EntityType: {entity_type!r}"
            )

    def test_output_fields_are_declared_in_the_mapping(self, list_name):
        allowed = set(_schema(list_name))

        for rec in _canonical_records(list_name):
            undeclared = set(rec) - allowed
            assert not undeclared, (
                f"{list_name}: output has field(s) not declared in mapping.xlsx: "
                f"{sorted(undeclared)}"
            )

    def test_field_shapes_match_mapping(self, list_name):
        # Both directions now hold: array-declared fields are lists, and
        # scalar-declared fields are NOT lists (the mapping engine enforces the
        # declared Field Type via coerce_to_field_type, which is what fixed the
        # UN DateUpdated case where a scalar field used to become a 2-element
        # array).
        schema = _schema(list_name)

        for rec in _canonical_records(list_name):
            for key, value in rec.items():
                if key not in schema:
                    continue
                if schema[key]:
                    assert isinstance(value, list), (
                        f"{list_name}.{key} is declared as an array but the "
                        f"output is {type(value).__name__}"
                    )
                else:
                    assert not isinstance(value, list), (
                        f"{list_name}.{key} is declared as a scalar but the "
                        f"output is a list: {value!r}"
                    )

    def test_no_junk_tokens_leak_into_output(self, list_name):
        def walk(value):
            if isinstance(value, str):
                return value.strip().upper() in {t.upper() for t in JUNK_TOKENS}
            if isinstance(value, dict):
                return any(walk(v) for v in value.values())
            if isinstance(value, list):
                return any(walk(v) for v in value)
            return False

        for rec in _canonical_records(list_name):
            assert not walk(rec), (
                f"{list_name}: a junk token {JUNK_TOKENS} leaked into output"
            )

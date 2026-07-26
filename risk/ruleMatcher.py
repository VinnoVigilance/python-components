"""
Rule matcher for the Risk Category Engine (deterministic layers 1 + 2).

Given a mapped record and a loaded RiskConfig, this produces the record's
``RiskCategories[]`` from:

  Layer 1 - Base label (provenance)
      Every included list contributes a base label. The top-level Category is
      taken from the record's Sources[].DatasetCategory - the value the mapping
      now stamps on every record - so the mapping is the single source of truth
      for what a list "is". The SubCategory, Confidence and the in/out-of-scope
      decision still come from ListScope. When a record carries no
      DatasetCategory (older data, or a source that omits Sources[]) or one the
      risk taxonomy does not know, the ListScope DefaultCategory is used as a
      fallback, so the base label is never lost. This is the backbone: present
      and clean for every included record.

  Layer 2 - Rules
      Field-match rules add further labels on top of the base
      (e.g. Program == 'NPWMD' -> Crime/Proliferation).

Contributions are appended and then de-duplicated by (Category, SubCategory):
duplicates are merged, keeping the highest confidence and the union of the
evidence and contributing sources. Nothing overwrites anything.

Records whose lists are all excluded (e.g. DNFBP) are deliberately left with an
empty ``RiskCategories`` - that is a decision, not a failure. This layer never
invents a "benign"/"low risk" label; the LLM layer that runs afterwards fills
in subcategories from prose and may add an ``Undetermined`` marker where an
included record genuinely could not be classified.
"""

import json
import re
from copy import deepcopy

from risk.configLoader import RiskConfig, load_risk_config, _norm


# ---------------------------------------------------------------------------
# Field resolvers: MatchField name -> the list of raw values to test in a record
# ---------------------------------------------------------------------------

def _collect(record, array_key, item_key):
    values = []
    for item in record.get(array_key) or []:
        if isinstance(item, dict):
            values.append(item.get(item_key))
    return values


def _dataset_categories_by_list(record) -> dict:
    """Map each source list name -> the DatasetCategory stamped on it in the
    record's Sources[].

    This is the provenance-driven top-level category the mapping now writes onto
    every record; Layer 1 prefers it over the ListScope DefaultCategory.
    """
    out = {}
    for item in record.get("Sources") or []:
        if isinstance(item, dict):
            ln = _norm(item.get("ListName"))
            dc = _norm(item.get("DatasetCategory"))
            if ln and dc:
                out[ln] = dc
    return out


FIELD_RESOLVERS = {
    "ListName": lambda r: _collect(r, "Sources", "ListName"),
    "Program": lambda r: _collect(r, "Programs", "Program"),
    "ProgramType": lambda r: _collect(r, "Programs", "ProgramType"),
    "MeasureType": lambda r: _collect(r, "Measures", "MeasureType"),
    "Comment": lambda r: _collect(r, "Comments", "text"),
}


def _match_value(value, match_type: str, target: str) -> bool:
    """Case-insensitive, whitespace-normalized match of one value against a rule."""
    v = _norm(value)
    t = _norm(target)
    if v is None or t is None:
        return False

    vl, tl = v.lower(), t.lower()
    if match_type == "exact":
        return vl == tl
    if match_type == "contains":
        return tl in vl
    if match_type == "regex":
        try:
            return re.search(target, v, re.IGNORECASE) is not None
        except re.error:
            return False
    return False


def _match_rule(record, rule):
    """Return the (normalized) value that fired the rule, or None if no match."""
    resolver = FIELD_RESOLVERS.get(rule.match_field)
    if resolver is None:
        return None
    for value in resolver(record):
        if _match_value(value, rule.match_type, rule.match_value):
            return _norm(value)
    return None


# ---------------------------------------------------------------------------
# Merge / dedup
# ---------------------------------------------------------------------------

def _merge_contributions(contributions: list) -> list:
    """Collapse contributions sharing (Category, SubCategory) into one entry."""
    merged = {}
    for c in contributions:
        if not c["category"] and not c["subcategory"]:
            continue
        key = (c["category"], c["subcategory"])
        conf = c["confidence"] if c["confidence"] is not None else 0.0

        ind = c.get("indicator")  # rule contributions have none; LLM ones may
        if key not in merged:
            merged[key] = {
                "Category": c["category"],
                "SubCategory": c["subcategory"],
                "Indicators": [ind] if ind else [],
                "Confidence": conf,
                "Method": [c["method"]],
                "Evidence": [c["evidence"]],
                "Sources": [c["source"]] if c["source"] else [],
            }
        else:
            e = merged[key]
            e["Confidence"] = max(e["Confidence"], conf)
            if ind and ind not in e["Indicators"]:
                e["Indicators"].append(ind)
            if c["method"] not in e["Method"]:
                e["Method"].append(c["method"])
            if c["evidence"] not in e["Evidence"]:
                e["Evidence"].append(c["evidence"])
            if c["source"] and c["source"] not in e["Sources"]:
                e["Sources"].append(c["source"])

    out = []
    for e in merged.values():
        e["Method"] = "+".join(sorted(set(e["Method"])))
        out.append(e)

    out.sort(key=lambda x: (-x["Confidence"], x["Category"] or "", x["SubCategory"] or ""))
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class RuleMatcher:
    """Applies the deterministic ListScope + Rules layers to records."""

    def __init__(self, config: RiskConfig):
        self.config = config

    def _record_list_names(self, record) -> list:
        names = [_norm(v) for v in FIELD_RESOLVERS["ListName"](record)]
        return [n for n in names if n]

    def classify_record(self, record, list_name=None) -> dict:
        """Classify one record.

        ``list_name`` is the authoritative list identity supplied by the
        pipeline (which always knows the source it is processing). It is used
        in addition to any ListName found inside ``Sources[]`` - important
        because some sources (e.g. ATC) do not populate ``Sources[]`` on every
        record, so the in-record ListName cannot be relied on alone.
        """
        entity = deepcopy(record)
        list_names = self._record_list_names(record)

        hinted = _norm(list_name)
        if hinted and hinted not in list_names:
            list_names.append(hinted)

        included = [n for n in list_names if self.config.is_included(n)]

        # No included source -> deliberately out of scope (e.g. DNFBP).
        if not included:
            entity["RiskCategories"] = []
            return entity

        contributions = []
        dataset_cats = _dataset_categories_by_list(record)

        # Layer 1: base label, one per included source. Top-level Category comes
        # from the record's Sources[].DatasetCategory; SubCategory / Confidence /
        # inclusion come from ListScope. Fall back to the ListScope
        # DefaultCategory when the record carries no (known) DatasetCategory.
        for name in included:
            default = self.config.default_label(name)
            if not default:
                continue
            base_cat, sub, conf = default

            override = dataset_cats.get(name)
            if override and override in self.config.categories:
                cat = override
                evidence = f"ListName={name} DatasetCategory={cat}"
            else:
                cat = base_cat
                evidence = f"ListName={name}"

            # Never emit an orphaned pair: if overriding the category leaves the
            # ListScope subcategory under a different parent, drop the subcategory.
            if sub is not None:
                meta = self.config.subcategories.get(sub)
                if not meta or meta.get("parent") != cat:
                    sub = None

            contributions.append({
                "category": cat,
                "subcategory": sub,
                "confidence": conf,
                "method": "listscope",
                "evidence": evidence,
                "source": name,
            })

        # Layer 2: rules (priority order preserved from the loader).
        for rule in self.config.rules:
            applies = rule.applies_to_list is None or rule.applies_to_list in list_names
            if not applies:
                continue
            matched = _match_rule(record, rule)
            if matched is None:
                continue
            source = rule.applies_to_list
            if source is None:
                source = included[0] if len(included) == 1 else ", ".join(included)
            contributions.append({
                "category": rule.add_category,
                "subcategory": rule.add_subcategory,
                "confidence": rule.confidence,
                "method": "rule",
                "evidence": f"{rule.match_field}={matched} ({rule.rule_id})",
                "source": source,
            })

        entity["RiskCategories"] = _merge_contributions(contributions)
        return entity

    def run(self, records, list_name=None) -> list:
        return [self.classify_record(r, list_name) for r in records]


def load_jsonl_safe(path):
    """Load JSONL, skipping blank lines, git conflict markers and bad rows.

    Returns (records, skipped) so callers can see how many lines were dropped.
    """
    records, skipped = [], 0
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith(("<<<<<<<", "=======", ">>>>>>>")):
                skipped += 1
                continue
            try:
                records.append(json.loads(s))
            except json.JSONDecodeError:
                skipped += 1
    return records, skipped


if __name__ == "__main__":
    import collections
    import glob
    import os

    cfg = load_risk_config()
    matcher = RuleMatcher(cfg)

    print("Rule-matcher dry run over data/final/*.jsonl\n")
    for path in sorted(glob.glob("data/final/*_final.jsonl")):
        src = os.path.basename(path).replace("_final.jsonl", "")
        records, skipped = load_jsonl_safe(path)
        # The pipeline knows the source it is running; here we derive it from
        # the filename and pass it as the authoritative list identity.
        classified = matcher.run(records, list_name=src)

        labels = collections.Counter()
        empty = 0
        for rec in classified:
            rc = rec["RiskCategories"]
            if not rc:
                empty += 1
            for entry in rc:
                labels[(entry["Category"], entry["SubCategory"])] += 1

        note = f" (skipped {skipped} conflict/bad lines)" if skipped else ""
        print(f"=== {src}: {len(records)} records, {empty} with no category{note}")
        for (cat, sub), n in labels.most_common():
            print(f"      {n:6}  {cat} / {sub}")

    # Show one fully-classified example so the output shape is visible.
    print("\n--- sample classified record (first OFAC-SDN with >1 label) ---")
    recs, _ = load_jsonl_safe("data/final/OFAC-SDN_final.jsonl")
    for rec in matcher.run(recs):
        if len(rec["RiskCategories"]) > 1:
            print(json.dumps(rec["RiskCategories"], ensure_ascii=False, indent=2))
            break

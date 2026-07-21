"""
Observability for the rule-based Risk Category Engine.

Runs the deterministic matcher over the mapped data and reports what happened:

  * per source: how many records were classified, how many got no category
  * category / subcategory distribution
  * method breakdown (base label from ListScope vs. added by a rule)
  * per-rule hit counts - how many records each rule fired on (dead rules show 0)
  * a few fully-traced sample records, so you can see *why* each label was applied

This is the audit view for task 6 ("validate generated risk tags and document
classification results"). It writes a human-readable report to
data/reports/risk_rule_report.txt and prints the same to the console.

Usage:
    ./vv-env/Scripts/python.exe -m risk.reporter
"""

import collections
import glob
import json
import os
from pathlib import Path

from risk.configLoader import load_risk_config
from risk.ruleMatcher import RuleMatcher, load_jsonl_safe

ROOT_DIR = Path(__file__).resolve().parent.parent
FINAL_DIR = ROOT_DIR / "data" / "final"
REPORT_PATH = ROOT_DIR / "data" / "reports" / "risk_rule_report.txt"


class _Tee:
    """Write to console and collect lines for the report file."""

    def __init__(self):
        self.lines = []

    def __call__(self, text=""):
        print(text)
        self.lines.append(text)

    def save(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")


def _source_name(path):
    return os.path.basename(path).replace("_final.jsonl", "")


def generate_report(config, final_dir=FINAL_DIR, sample_per_source=2):
    out = _Tee()
    matcher = RuleMatcher(config)

    # Aggregates across every source.
    grand_total = 0
    grand_empty = 0
    rule_hits = collections.Counter()          # rule_id -> records it fired on
    method_totals = collections.Counter()      # method string -> label count
    all_labels = collections.Counter()

    out("=" * 72)
    out("RISK CATEGORY ENGINE - rule-based classification report")
    out("=" * 72)

    for path in sorted(glob.glob(str(final_dir / "*_final.jsonl"))):
        src = _source_name(path)
        records, skipped = load_jsonl_safe(path)
        classified = matcher.run(records, list_name=src)

        total = len(records)
        empty = 0
        labels = collections.Counter()
        methods = collections.Counter()
        src_rule_hits = collections.Counter()
        samples = []

        for rec in classified:
            rc = rec["RiskCategories"]
            if not rc:
                empty += 1
                continue
            for entry in rc:
                labels[(entry["Category"], entry["SubCategory"])] += 1
                methods[entry["Method"]] += 1
                method_totals[entry["Method"]] += 1
                # Attribute rule hits from the evidence trail (e.g. "... (R05)").
                for ev in entry["Evidence"]:
                    if "(" in ev and ev.rstrip().endswith(")"):
                        rid = ev[ev.rfind("(") + 1: ev.rfind(")")]
                        src_rule_hits[rid] += 1
                        rule_hits[rid] += 1
            if len(rec["RiskCategories"]) > 1 and len(samples) < sample_per_source:
                samples.append(rec)

        grand_total += total
        grand_empty += empty
        for k, v in labels.items():
            all_labels[k] += v

        note = f"  (skipped {skipped} bad lines)" if skipped else ""
        out("")
        out("-" * 72)
        classified_n = total - empty
        pct = (100.0 * classified_n / total) if total else 0.0
        out(f"{src}{note}")
        out(f"  records            : {total}")
        out(f"  with a category    : {classified_n} ({pct:.1f}%)")
        out(f"  no category        : {empty}"
            + ("   [excluded list - expected]" if empty == total and total else ""))

        if labels:
            out("  category distribution:")
            for (cat, sub), n in labels.most_common():
                out(f"      {n:7}  {cat} / {sub}")
            out("  by method:")
            for method, n in methods.most_common():
                out(f"      {n:7}  {method}")
        if src_rule_hits:
            out("  rules fired:")
            for rid, n in src_rule_hits.most_common():
                out(f"      {n:7}  {rid}")

        for rec in samples:
            eid = rec.get("EntityId", "?")
            out(f"  trace  EntityId={eid}:")
            for entry in rec["RiskCategories"]:
                conf = entry["Confidence"]
                ev = "; ".join(entry["Evidence"])
                out(f"      {entry['Category']}/{entry['SubCategory']}"
                    f"  conf={conf}  via {entry['Method']}  <- {ev}")

    # Dead-rule check: rules in the config that never fired.
    fired = set(rule_hits)
    defined = {r.rule_id for r in config.rules}
    dead = sorted(defined - fired)

    out("")
    out("=" * 72)
    out("OVERALL")
    out("=" * 72)
    out(f"  total records       : {grand_total}")
    out(f"  with a category     : {grand_total - grand_empty}")
    out(f"  no category         : {grand_empty}")
    out("  label totals:")
    for (cat, sub), n in all_labels.most_common():
        out(f"      {n:7}  {cat} / {sub}")
    out("  method totals:")
    for method, n in method_totals.most_common():
        out(f"      {n:7}  {method}")
    out("  rule hit totals:")
    for r in config.rules:
        out(f"      {rule_hits.get(r.rule_id, 0):7}  {r.rule_id}: "
            f"{r.match_field} {r.match_type} '{r.match_value}' "
            f"-> {r.add_category}/{r.add_subcategory}")
    if dead:
        out(f"  [!] rules that never fired: {', '.join(dead)}")
    else:
        out("  [OK] every rule fired at least once")

    out.save(REPORT_PATH)
    out("")
    out(f"Report written to: {REPORT_PATH}")


if __name__ == "__main__":
    cfg = load_risk_config()
    generate_report(cfg)

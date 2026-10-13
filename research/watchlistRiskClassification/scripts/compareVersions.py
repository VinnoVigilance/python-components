"""Compare checker versions on the same blind-labelled records (v5 check pool, random 300 and held-out samples, all 15
labels under rules v2): per-label precision and recall of what the model adds beyond the lookup. Design numbers only."""
import glob
import sys
from pathlib import Path

import pandas as pd

from buildCheckPoolV5 import record_predictions
from experimentKit import wilson
from scoreFresh import LABELS, lookup_labels
from scoreV5 import read_blind

RESEARCH = Path(__file__).resolve().parents[1]
BLIND = RESEARCH / "cache" / "blind"
FILES = {"v4": "checker_answers.csv", "v5": "checker_answers_v5.csv", "v6": "checker_answers_v6.csv"}


def main(versions):
    b = read_blind(sorted(glob.glob(str(BLIND / "out_pool5_*.csv"))) + [RESEARCH / "data" / "fresh_blind_labels_v2.csv"])
    g = b.merge(lookup_labels(set(b["record_key"])), how="left").fillna({"lookup": 0})
    for v in versions:
        g = g.merge(record_predictions(FILES[v]).rename(columns={"system": v}), how="left").fillna({v: 0})
    g = g[g["lookup"] == 0]
    rows = []
    for label in LABELS + ["all 15"]:
        s = g if label == "all 15" else g[g["label"] == label]
        row = {"label": label, "true": int(s["blind"].sum())}
        for v in versions:
            tp, n = int((s[v] * s["blind"]).sum()), int(s[v].sum())
            p, r = tp / max(n, 1), tp / max(s["blind"].sum(), 1)
            row.update({f"{v} P": p, f"{v} R": r, f"{v} F1": 2 * p * r / max(p + r, 1e-9)})
        rows.append(row)
    out = pd.DataFrame(rows).set_index("label")
    out.to_csv(RESEARCH / "results" / "versions_design_comparison.csv")
    pd.set_option("display.width", 250)
    print(f"{g['record_key'].nunique()} records")
    print(out.round(3).to_string())


if __name__ == "__main__":
    main(sys.argv[1:] or ["v4", "v5", "v6"])

"""Compare teachers with the v5 questions on the design records (all 15 labels blind-labelled: old check pool,
random 300, held-out samples without DILG): per-label precision and recall, lookup excluded."""
import sys
from pathlib import Path

import pandas as pd

from buildCheckPoolV5 import record_predictions
from experimentKit import wilson
from scoreFresh import LABELS, blind_labels, lookup_labels

RESEARCH = Path(__file__).resolve().parents[1]
FRESH = RESEARCH / "cache" / "fresh"


def main(tags):
    blind = blind_labels()
    blind = blind[~blind["record_key"].str.startswith("DILG-LOCAL-OFFICIALS")]
    keys = set(blind["record_key"])
    g = blind.merge(lookup_labels(keys), how="left").fillna({"lookup": 0})
    files = {"qwen4b": "checker_answers_v5.csv", **{t: f"teacher_{t}_answers_v5_design.csv" for t in tags}}
    for name, f in files.items():
        g = g.merge(record_predictions(f).rename(columns={"system": name}), how="left").fillna({name: 0})
    g = g[g["lookup"] == 0]
    rows = []
    for label in LABELS + ["all"]:
        s = g if label == "all" else g[g["label"] == label]
        row = {"label": label, "blind yes": int(s["blind"].sum())}
        for name in files:
            tp, n = int((s[name] * s["blind"]).sum()), int(s[name].sum())
            row.update({f"{name} yes": n, f"{name} P": tp / max(n, 1), f"{name} low": wilson(tp, n)[0],
                        f"{name} R": tp / max(s["blind"].sum(), 1)})
        rows.append(row)
    out = pd.DataFrame(rows).set_index("label")
    out.to_csv(RESEARCH / "results" / "teachers_v5_design.csv")
    pd.set_option("display.width", 250)
    print(f"{g['record_key'].nunique()} records, (record, label) pairs not given by the lookup: {len(g)}")
    print(out.round(3).to_string())


if __name__ == "__main__":
    main(sys.argv[1:])

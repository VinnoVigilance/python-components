"""Score a v5 student like the frozen checker v5: precision on the v5 check pool, recall on the random 300 (rules v2
labels), agreement with the teacher, and CPU speed with 4 threads."""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from evalStudent import record_yes
from experimentKit import wilson
from scoreFresh import LABELS, MIN_CASES, TARGET, lookup_labels
from scoreV5 import read_blind
from student import Student, pieces

RESEARCH = Path(__file__).resolve().parents[1]
FRESH = RESEARCH / "cache" / "fresh"


def main(name):
    q = pieces("v5")
    q = q[q["split"].isin(["check_pool_v5", "random300", "heldout_list"])].copy()
    s = Student(RESEARCH / "cache" / f"student_{name}", device="cuda:0")
    q["student"] = s.p_yes(q["label"].values, q["text"].values, batch_size=128)
    agree = ((q["student"] >= 0.5) == (q["target"] >= 0.5)).mean()
    stu = record_yes(q, "student")

    pool = pd.read_csv(FRESH / "scored_pool_v5.csv").merge(stu, how="left").fillna({"student": 0})
    rows = []
    for label in LABELS:
        p = pool[pool["label"] == label]
        kept = p[p["student"] == 1]
        k, n = int(kept["blind"].sum()), len(kept)
        lo = wilson(k, n)[0] if n else np.nan
        rows.append({"label": label, "teacher yes checked": len(p), "student keeps": n,
                     "precision": k / n if n else np.nan, "95% low": lo,
                     "decision": "auto-apply" if n and lo >= TARGET else "review queue",
                     "correct dropped": int(((p["student"] == 0) & (p["blind"] == 1)).sum())})
    table = pd.DataFrame(rows).set_index("label")

    test = read_blind([RESEARCH / "data" / "fresh_blind_labels_v2.csv"])
    split = pd.read_csv(FRESH / "split_v5.csv", dtype=str, keep_default_na=False).set_index("record_key")["split"]
    g = test.merge(lookup_labels(set(test["record_key"])), how="left").fillna({"lookup": 0})
    g = g.merge(stu, how="left").fillna({"student": 0})
    g["system"] = g["student"].astype(int) | g["lookup"].astype(int)
    r = g[g["record_key"].map(split) == "random300"]
    tp = int((r["system"] & r["blind"]).sum())
    recall = {label: (lambda pos: pos["system"].mean() if len(pos) >= MIN_CASES else np.nan)(
        r[(r["label"] == label) & (r["blind"] == 1)]) for label in LABELS}

    cpu = Student(RESEARCH / "cache" / f"student_{name}", device="cpu", threads=4)
    sample = q.sample(500, random_state=0)
    t0 = time.time()
    cpu.p_yes(sample["label"].values, sample["text"].values, batch_size=32)
    per_piece = (time.time() - t0) / len(sample)
    all_pieces = pieces("v5")

    table.to_csv(RESEARCH / "results" / f"student_{name}_precision.csv")
    pd.set_option("display.width", 250)
    print(f"agreement with the teacher on test pieces: {agree:.3f}")
    print(table.round(3).to_string())
    print(f"random 300: precision {tp / r['system'].sum():.3f}, recall {tp / r['blind'].sum():.3f}; "
          f"PEP recall {recall['PEP']:.3f}, Terrorism recall {recall['Terrorism / Terrorist Financing']:.3f}")
    print(f"CPU, 4 threads: {per_piece * 1000:.1f} ms per piece; "
          f"{len(all_pieces) / all_pieces['record_key'].nunique():.2f} pieces per flagged record")


if __name__ == "__main__":
    main(sys.argv[1])

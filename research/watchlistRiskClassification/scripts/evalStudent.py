"""Score a trained student like the frozen checker on the fresh test splits, and time it on the CPU with 4 threads."""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from scoreFresh import LABELS, POOL_MIN, TARGET, blind_labels, sampled_for_label
from student import VICTIM, Student, pieces
from experimentKit import SEGMENT_OF, wilson

RESEARCH = Path(__file__).resolve().parents[1]
FRESH = RESEARCH / "cache" / "fresh"


def record_yes(q, col):
    """(record_key, label) = 1 where any piece scores >= 0.5 and the record's victim score is < 0.5."""
    victims = set(q.loc[(q["label"] == VICTIM) & (q[col] >= 0.5), "record_key"])
    yes = q[(q["label"] != VICTIM) & (q[col] >= 0.5) & ~q["record_key"].isin(victims)]
    return yes[["record_key", "label"]].drop_duplicates().assign(**{col: 1})


def main(name):
    s = Student(RESEARCH / "cache" / f"student_{name}", device="cuda:0")
    q = pieces()
    q = q[q["split"].isin(["check_pool", "random300", "heldout_list"])].copy()
    q["student"] = s.p_yes(q["label"].values, q["text"].values, batch_size=128)
    piece_agree = ((q["student"] >= 0.5) == (q["target"] >= 0.5)).mean()

    split = pd.read_csv(FRESH / "split_final.csv", dtype=str, keep_default_na=False)
    preds = pd.read_csv(FRESH / "record_predictions.csv", dtype={"record_key": str})
    pool = sampled_for_label(split, preds).merge(blind_labels(), how="left")
    stu = record_yes(q, "student")
    pool = pool.merge(stu, how="left").fillna({"student": 0})
    pool["segment"] = pool["record_key"].str.split(":").str[0].map(SEGMENT_OF)
    rows = []
    for label in LABELS:
        p = pool[pool["label"] == label]
        kept = p[p["student"] == 1]
        k, n = int(kept["blind"].sum()), len(kept)
        lo = wilson(k, n)[0]
        rows.append({"label": label, "teacher yes checked": len(p), "student keeps": n,
                     "student precision": k / n if n else np.nan, "95% low": lo,
                     "decision": "auto-apply" if n and lo >= TARGET else "review queue",
                     "teacher-correct the student dropped": int(((p["student"] == 0) & (p["blind"] == 1)).sum())})
    table = pd.DataFrame(rows).set_index("label")

    g = pd.read_csv(FRESH / "scored_random300_heldout.csv").merge(stu, how="left").fillna({"student": 0})
    g["student system"] = g["student"].astype(int) | g["lookup"]
    r = g[g["split"] == "random300"]
    tp = int((r["student system"] & r["blind"]).sum())
    r300 = {"student system yes": int(r["student system"].sum()), "blind yes": int(r["blind"].sum()), "both": tp,
            "precision": tp / max(r["student system"].sum(), 1), "recall": tp / max(r["blind"].sum(), 1),
            "teacher precision": int((r["system"] & r["blind"]).sum()) / max(r["system"].sum(), 1),
            "teacher recall": int((r["system"] & r["blind"]).sum()) / max(r["blind"].sum(), 1)}

    cpu = Student(RESEARCH / "cache" / f"student_{name}", device="cpu", threads=4)
    sample = q.sample(500, random_state=0)
    t0 = time.time()
    cpu.p_yes(sample["label"].values, sample["text"].values, batch_size=32)
    per_piece = (time.time() - t0) / len(sample)

    table.to_csv(RESEARCH / "results" / f"student_{name}_precision.csv")
    pd.set_option("display.width", 250)
    print(f"piece-level agreement with the teacher on test splits: {piece_agree:.3f}")
    print(table.round(3).to_string())
    print(pd.Series(r300).round(3).to_string())
    print(f"CPU, 4 threads: {per_piece * 1000:.1f} ms per piece ({1 / per_piece:.0f} pieces/s); "
          f"fresh export averages {len(pieces()) / 18431:.2f} pieces per record")


if __name__ == "__main__":
    main(sys.argv[1])

"""Score the v6 student against its teacher (checker v6) on the 1,178 blind-labelled design records the student never
trained on (v5 check pool, random 300, held-out lists): per-label precision and recall of what the AI adds beyond the
lookup, agreement with the teacher, and CPU speed. Design numbers only."""
import glob
import sys
import time
from pathlib import Path

import pandas as pd

from buildCheckPoolV5 import record_predictions
from evalStudent import record_yes
from scoreFresh import LABELS, lookup_labels
from scoreV5 import read_blind
from student import Student, pieces

RESEARCH = Path(__file__).resolve().parents[1]
BLIND = RESEARCH / "cache" / "blind"


def scores(g, col):
    out = {}
    for label in LABELS + ["all 15"]:
        s = g if label == "all 15" else g[g["label"] == label]
        tp, n, pos = int((s[col] * s["blind"]).sum()), int(s[col].sum()), int(s["blind"].sum())
        p, r = tp / max(n, 1), tp / max(pos, 1)
        out[label] = {"true": pos, f"{col} yes": n, f"{col} P": p, f"{col} R": r, f"{col} F1": 2 * p * r / max(p + r, 1e-9)}
    return pd.DataFrame(out).T


def main(name, threads):
    q = pieces("v6")
    q = q[q["split"].isin(["check_pool_v5", "random300", "heldout_list"])].copy()
    s = Student(RESEARCH / "cache" / f"student_{name}", device="cuda:0")
    q["student"] = s.p_yes(q["label"].values, q["text"].values, batch_size=128)
    agree = ((q["student"] >= 0.5) == (q["target"] >= 0.5)).mean()

    b = read_blind(sorted(glob.glob(str(BLIND / "out_pool5_*.csv"))) + [RESEARCH / "data" / "fresh_blind_labels_v2.csv"])
    g = b.merge(lookup_labels(set(b["record_key"])), how="left").fillna({"lookup": 0})
    g = g.merge(record_predictions("checker_answers_v6.csv").rename(columns={"system": "teacher"}), how="left")
    g = g.merge(record_yes(q, "student"), how="left").fillna({"teacher": 0, "student": 0})
    g = g[g["lookup"] == 0]
    t, st = scores(g, "teacher"), scores(g, "student")
    table = pd.concat([t, st.drop(columns="true")], axis=1)
    table.to_csv(RESEARCH / "results" / f"student_{name}_vs_teacher_v6.csv")

    cpu = Student(RESEARCH / "cache" / f"student_{name}", device="cpu", threads=threads)
    sample = q.sample(300, random_state=0)
    t0 = time.time()
    cpu.p_yes(sample["label"].values, sample["text"].values, batch_size=32)
    per_piece = (time.time() - t0) / len(sample)
    all_pieces = pieces("v6")
    per_record = len(all_pieces) / all_pieces["record_key"].nunique()

    pd.set_option("display.width", 250)
    print(f"{g['record_key'].nunique()} records; piece agreement with the teacher {agree:.3f}")
    print(table.round(3).to_string())
    print(f"CPU, {threads} threads: {per_piece * 1000:.1f} ms per piece, {per_record:.2f} pieces per flagged record "
          f"({time.time() - t0:.0f} s test)")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 10)

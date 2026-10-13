"""Compare two teachers on the dev 900 against the reviewed reference: per-label precision / recall of labels added
beyond the lookup, agreement, and precision where both teachers say yes."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from experimentKit import Data, label_tiers, wilson

RESEARCH = Path(__file__).resolve().parents[1]
CACHE = RESEARCH / "cache"
FILES = {"qwen4b": ("checker_pyes_v4.csv", "checker_role_v4.csv"),
         "granite": ("teacher_granite_pyes_v4.csv", "teacher_granite_role_v4.csv"),
         "qwen8b": ("teacher_qwen8b_pyes_v4.csv", "teacher_qwen8b_role_v4.csv")}


def predictions(d, name):
    """Record x label 0/1: any flagged unit with P(yes) >= 0.5, victims cleared (frozen v4 rule)."""
    answers, role = (pd.read_csv(CACHE / f) for f in FILES[name])
    S = np.zeros(d.Y.shape)
    for r, label, p in answers[["record", "label", "p_yes"]].itertuples(index=False):
        j = d.labels.index(label)
        S[r, j] = max(S[r, j], p)
    S[role.loc[role["p_victim"] >= 0.5, "record"].values] = 0
    return (S >= 0.5).astype(int)


def reviewed_reference(d):
    review = pd.read_csv(RESEARCH / "data" / "reference_review.csv", encoding="utf-8-sig")
    key = {k: i for i, k in enumerate(d.df["record_key"])}
    YR = d.Y.copy()
    for k, label, v in review[["record_key", "label", "reviewed"]].itertuples(index=False):
        YR[key[k], d.labels.index(label)] = v
    return YR


def score(added, truth, need):
    good, n = int((added & truth).sum()), int(added.sum())
    return {"added": n, "precision": good / max(n, 1), "95% low": wilson(good, n)[0], "recall": good / max(need, 1)}


def main(a="qwen4b", b="granite"):
    d = Data()
    tiers = label_tiers(d)
    scope = [i for i in range(len(d.labels)) if tiers[i] != "lookup only" and i not in d.rare]
    YR, L = reviewed_reference(d), d.L
    A, B = predictions(d, a), predictions(d, b)
    rows = []
    for i in list(scope) + ["all"]:
        cols = scope if i == "all" else [i]
        la, lb = A[:, cols] & (1 - L[:, cols]), B[:, cols] & (1 - L[:, cols])
        truth, need = YR[:, cols], int((YR[:, cols] & (1 - L[:, cols])).sum())
        row = {"label": "all 15" if i == "all" else d.short[i], "needed": need}
        row.update({f"{a} {k}": v for k, v in score(la, truth, need).items()})
        row.update({f"{b} {k}": v for k, v in score(lb, truth, need).items()})
        flagged = (la | lb).sum()
        row["agree when either says yes"] = (la & lb).sum() / max(flagged, 1)
        row.update({f"both yes {k}": v for k, v in score(la & lb, truth, need).items()})
        rows.append(row)
    out = pd.DataFrame(rows).set_index("label")
    out.to_csv(RESEARCH / "results" / f"teachers_dev_{a}_vs_{b}.csv")
    pd.set_option("display.width", 250)
    print(out.round(3).to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])

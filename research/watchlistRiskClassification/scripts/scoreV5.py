"""Precision proof for the frozen checker v5 on its own check pool (drawn from never-inspected training records):
per label and per label x list type, Wilson rule; plus recall on the random 300 and held-out samples (rules v2 labels)."""
import glob
from pathlib import Path

import pandas as pd

from buildCheckPoolV5 import record_predictions
from experimentKit import SEGMENT_OF, wilson
from scoreFresh import LABELS, MIN_CASES, TARGET, lookup_labels

RESEARCH = Path(__file__).resolve().parents[1]
FRESH, BLIND = RESEARCH / "cache" / "fresh", RESEARCH / "cache" / "blind"


def read_blind(files):
    b = pd.concat([pd.read_csv(f, dtype=str) for f in files]).drop_duplicates(["record_key", "label"])
    b["blind"] = (b["answer"].str.strip().str.lower() == "yes").astype(int)
    return b[["record_key", "label", "blind"]]


def main():
    pool_blind = read_blind(sorted(glob.glob(str(BLIND / "out_pool5_*.csv"))))
    manifest = pd.read_csv(BLIND / "manifest_pool5.csv")
    assert set(manifest["record_key"]) == set(pool_blind["record_key"])
    assert (pool_blind.groupby("record_key").size() == 15).all()
    pool = pd.read_csv(FRESH / "check_pool_v5_draw.csv").merge(pool_blind, how="left")
    pool["segment"] = pool["record_key"].str.split(":").str[0].map(SEGMENT_OF)
    rows, cells = [], []
    for label in LABELS:
        p = pool[pool["label"] == label]
        k, n = int(p["blind"].sum()), len(p)
        lo = wilson(k, n)[0] if n else float("nan")
        rows.append({"label": label, "n": n, "correct": k, "precision": k / n if n else float("nan"), "95% low": lo,
                     "decision": "auto-apply" if n and lo >= TARGET else "review queue"})
        for seg, s in p.groupby("segment"):
            k, n = int(s["blind"].sum()), len(s)
            lo = wilson(k, n)[0]
            cells.append({"label": label, "list type": seg, "n": n, "correct": k, "precision": k / n, "95% low": lo,
                          "decision": "auto-apply" if lo >= TARGET else "review queue"})
    prec, cells = pd.DataFrame(rows).set_index("label"), pd.DataFrame(cells)

    test = read_blind([RESEARCH / "data" / "fresh_blind_labels_v2.csv"])
    split = pd.read_csv(FRESH / "split_v5.csv", dtype=str, keep_default_na=False).set_index("record_key")["split"]
    g = test.merge(lookup_labels(set(test["record_key"])), how="left").fillna({"lookup": 0})
    g = g.merge(record_predictions("checker_answers_v5.csv"), how="left").fillna({"system": 0})
    g["system"] = g["system"].astype(int) | g["lookup"].astype(int)
    g["split"], g["list"] = g["record_key"].map(split), g["record_key"].str.split(":").str[0]
    rec = []
    for label in LABELS:
        pos = g[(g["split"] == "random300") & (g["label"] == label) & (g["blind"] == 1)]
        r = {"label": label, "blind yes": len(pos)}
        if len(pos) >= MIN_CASES:
            lo, hi = wilson(int(pos["system"].sum()), len(pos))
            r.update({"recall": pos["system"].mean(), "95% low": lo, "95% high": hi})
        rec.append(r)
    rec = pd.DataFrame(rec).set_index("label")
    lists = []
    for (sp, lst), s in g.groupby(["split", "list"]):
        if sp != "heldout_list":
            continue
        tp = int((s["system"] & s["blind"]).sum())
        lists.append({"list": lst, "records": s["record_key"].nunique(), "system yes": int(s["system"].sum()),
                      "blind yes": int(s["blind"].sum()), "precision": tp / max(s["system"].sum(), 1),
                      "recall": tp / max(s["blind"].sum(), 1)})
    r300 = g[g["split"] == "random300"]
    tp = int((r300["system"] & r300["blind"]).sum())

    res = RESEARCH / "results"
    prec.to_csv(res / "v5_precision_by_label.csv")
    cells.to_csv(res / "v5_precision_by_label_and_list_type.csv", index=False)
    rec.to_csv(res / "v5_recall_random300.csv")
    pd.DataFrame(lists).to_csv(res / "v5_heldout_lists.csv", index=False)
    pool.to_csv(FRESH / "scored_pool_v5.csv", index=False)
    pd.set_option("display.width", 250)
    print(prec.round(3).to_string())
    print(cells[cells["n"] >= 10].round(3).to_string(index=False))
    print(rec.round(3).to_string())
    print(f"random 300 overall: precision {tp / r300['system'].sum():.3f}, recall {tp / r300['blind'].sum():.3f}")
    print(pd.DataFrame(lists).round(3).to_string(index=False))


if __name__ == "__main__":
    main()

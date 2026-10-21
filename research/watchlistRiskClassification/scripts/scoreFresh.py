"""Score the frozen checker on fresh records against the blind labels: precision per label (check pool, Wilson rule),
recall on the random 300, precision and recall on the held-out lists."""
import glob
import json
from pathlib import Path

import pandas as pd

from experimentKit import SEGMENT_OF, wilson
from lookupRules import record_rule_labels

RESEARCH = Path(__file__).resolve().parents[1]
FRESH = RESEARCH / "cache" / "fresh"
BLIND = RESEARCH / "cache" / "blind"
LABELS = json.load(open(RESEARCH / "results" / "frozen_versions.json", encoding="utf-8"))["scope_labels"]
POOL_MAX, POOL_MIN, SEED, TARGET, MIN_CASES = 100, 60, 42, 0.90, 10


def blind_labels():
    """Blind yes/no per (record_key, label) for the random 300, held-out lists and the check pool."""
    parts = [pd.read_csv(RESEARCH / "data" / "fresh_blind_labels.csv", dtype=str)]
    parts += [pd.read_csv(f, dtype=str) for f in sorted(glob.glob(str(BLIND / "out_pool_*.csv")))]
    b = pd.concat(parts).drop_duplicates(["record_key", "label"])
    b["blind"] = (b["answer"].str.strip().str.lower() == "yes").astype(int)
    return b[["record_key", "label", "blind", "evidence"]]


def sampled_for_label(split, preds):
    """Recreate which pool records were drawn for which label (same order and seed as buildCheckPool.py)."""
    first = pd.read_csv(FRESH / "split.csv", dtype=str, keep_default_na=False)
    rest_yes = preds[preds["record_key"].isin(set(first.loc[first["split"] == "rest", "record_key"]))]
    out = []
    for label in LABELS:
        candidates = rest_yes.loc[rest_yes["label"] == label, "record_key"].drop_duplicates()
        size = len(candidates) if len(candidates) < POOL_MIN else POOL_MAX if len(candidates) >= 2 * POOL_MAX else POOL_MIN
        out += [{"record_key": k, "label": label} for k in candidates.sample(size, random_state=SEED)]
    return pd.DataFrame(out)


def lookup_labels(keys):
    """Lookup-rule labels for fresh records, from the export payload."""
    x = pd.read_csv(FRESH / "server_export_2026-10-12.csv", dtype=str, keep_default_na=False)
    x["record_key"] = x["list_name"] + ":" + x["entity_id"]
    x = x[x["record_key"].isin(keys)]
    rows = [{"record_key": k, "label": label} for k, lst, p in zip(x["record_key"], x["list_name"], x["payload"])
            for label in record_rule_labels(lst, json.loads(p)) if label in LABELS]
    return pd.DataFrame(rows, columns=["record_key", "label"]).drop_duplicates().assign(lookup=1)


def grid(keys, preds, blind, lookup):
    """One row per (record, label) for the given records: checker, lookup, system (= either) and blind answer."""
    g = pd.MultiIndex.from_product([sorted(keys), LABELS], names=["record_key", "label"]).to_frame(index=False)
    g = g.merge(preds.rename(columns={"system": "checker"}), how="left").merge(lookup, how="left").merge(blind, how="left")
    g[["checker", "lookup"]] = g[["checker", "lookup"]].fillna(0).astype(int)
    g["system"] = g["checker"] | g["lookup"]
    return g


def interval(k, n):
    lo, hi = wilson(k, n)
    return {"n": n, "correct": k, "value": k / n if n else float("nan"), "95% low": lo, "95% high": hi}


def precision_table(pool, segments):
    """Checker precision per label on the records drawn for it, and the auto-apply decision (Wilson low >= 0.90)."""
    rows = []
    for label in LABELS:
        p = pool[pool["label"] == label]
        r = {"label": label, **interval(int(p["blind"].sum()), len(p))}
        r["decision"] = "auto-apply" if r["95% low"] >= TARGET else "review queue"
        for seg in segments:
            s = p[p["segment"] == seg]
            r[f"{seg} n"], r[f"{seg} P"] = len(s), s["blind"].mean() if len(s) else float("nan")
            r[f"{seg} low"] = wilson(int(s["blind"].sum()), len(s))[0]
        rows.append(r)
    return pd.DataFrame(rows).set_index("label")


def recall_table(g):
    """System and checker recall per label where the blind labeller found at least MIN_CASES cases."""
    rows = []
    for label in LABELS:
        pos = g[(g["label"] == label) & (g["blind"] == 1)]
        r = {"label": label, "blind yes": len(pos), "system yes": int(g.loc[g["label"] == label, "system"].sum())}
        if len(pos) >= MIN_CASES:
            r.update({f"system {k}": v for k, v in interval(int(pos["system"].sum()), len(pos)).items() if k != "n"})
            r["checker-only recall"] = pos["checker"].mean()
        rows.append(r)
    return pd.DataFrame(rows).set_index("label")


def pr(g):
    tp = int((g["system"] & g["blind"]).sum())
    return {"system yes": int(g["system"].sum()), "blind yes": int(g["blind"].sum()), "both yes": tp,
            "precision": tp / max(g["system"].sum(), 1), "recall": tp / max(g["blind"].sum(), 1)}


def main():
    split = pd.read_csv(FRESH / "split_final.csv", dtype=str, keep_default_na=False)
    preds = pd.read_csv(FRESH / "record_predictions.csv", dtype={"record_key": str})
    blind = blind_labels()
    seg = split.set_index("record_key")["list_name"].map(SEGMENT_OF)

    pool = sampled_for_label(split, preds).merge(blind, how="left")
    missing = pool["blind"].isna().sum()
    pool = pool.dropna(subset=["blind"]).assign(segment=lambda x: x["record_key"].map(seg))
    segments = [s for s in dict.fromkeys(SEGMENT_OF.values()) if (pool["segment"] == s).any()]
    prec = precision_table(pool, segments)

    keys = set(split.loc[split["split"].isin(["random300", "heldout_list"]), "record_key"]) & set(blind["record_key"])
    g = grid(keys, preds, blind, lookup_labels(keys))
    g["split"], g["list_name"] = g["record_key"].map(split.set_index("record_key")["split"]), g["record_key"].str.split(":").str[0]
    r300, held = g[g["split"] == "random300"], g[g["split"] == "heldout_list"]
    rec = recall_table(r300)
    held_rows = pd.DataFrame([{"list": lst, "records": s["record_key"].nunique(), **pr(s)} for lst, s in held.groupby("list_name")])

    res = RESEARCH / "results"
    prec.to_csv(res / "fresh_precision_by_label.csv")
    rec.to_csv(res / "fresh_recall_random300.csv")
    held_rows.to_csv(res / "fresh_heldout_lists.csv", index=False)
    g.to_csv(FRESH / "scored_random300_heldout.csv", index=False)
    pool.to_csv(FRESH / "scored_pool.csv", index=False)
    pd.set_option("display.width", 250)
    print(f"pool rows without blind label yet: {missing}")
    print(prec[["n", "correct", "value", "95% low", "decision"]].round(3).to_string())
    print(prec.filter(regex=" n$| P$").round(2).to_string())
    print(rec.round(3).to_string())
    print(pd.Series(pr(r300)).round(3).to_string())
    print(held_rows.round(3).to_string())


if __name__ == "__main__":
    main()

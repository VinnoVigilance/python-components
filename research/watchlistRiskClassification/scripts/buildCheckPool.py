"""Reserve the precision check pool (checker 'yes' records per label) from the fresh 'rest' split; the rest is training."""
import json
from pathlib import Path

import pandas as pd

RESEARCH = Path(__file__).resolve().parents[1]
FRESH = RESEARCH / "cache" / "fresh"
LABELS = json.load(open(RESEARCH / "results" / "frozen_versions.json", encoding="utf-8"))["scope_labels"]
POOL_MAX, POOL_MIN, SEED = 100, 60, 42


def record_predictions():
    """Per fresh record and label: 1 if any flagged piece has P(yes) >= 0.5 and the record is not judged a victim."""
    answers = pd.read_csv(FRESH / "checker_answers.csv", dtype={"record_key": str})
    role = pd.read_csv(FRESH / "checker_role.csv", dtype={"record_key": str})
    victims = set(role.loc[role["p_victim"] >= 0.5, "record_key"])
    yes = answers[(answers["p_yes"] >= 0.5) & ~answers["record_key"].isin(victims)]
    return yes.groupby(["record_key", "label"]).size().gt(0).astype(int).rename("system").reset_index()


def build():
    split = pd.read_csv(FRESH / "split.csv", dtype=str, keep_default_na=False)
    preds = record_predictions()
    rest_keys = set(split.loc[split["split"] == "rest", "record_key"])
    rest_yes = preds[preds["record_key"].isin(rest_keys)]
    pooled, counts = set(), []
    for label in LABELS:
        candidates = rest_yes.loc[rest_yes["label"] == label, "record_key"].drop_duplicates()
        size = len(candidates) if len(candidates) < POOL_MIN else POOL_MAX if len(candidates) >= 2 * POOL_MAX else POOL_MIN
        take = candidates.sample(size, random_state=SEED)
        pooled |= set(take)
        counts.append({"label": label, "checker yes in rest": len(candidates), "reserved for check pool": len(take),
                       "below 60": len(take) < POOL_MIN})
    split.loc[split["record_key"].isin(pooled), "split"] = "check_pool"
    pool_people = set(split.loc[split["split"] == "check_pool", "entity_id"])
    split.loc[(split["split"] == "rest") & split["entity_id"].isin(pool_people), "split"] = "excluded_copy"
    split.loc[split["split"] == "rest", "split"] = "training"
    split.to_csv(FRESH / "split_final.csv", index=False, encoding="utf-8")
    preds.to_csv(FRESH / "record_predictions.csv", index=False)
    return split, preds, pd.DataFrame(counts).set_index("label")


def count_tables(split, preds):
    """Records per list and split, and checker-yes records per label and split."""
    per_list = pd.crosstab(split["list_name"], split["split"], margins=True, margins_name="total")
    lab = preds.merge(split[["record_key", "split"]], on="record_key")
    per_label = pd.crosstab(lab["label"], lab["split"], margins=True, margins_name="total")
    return per_list, per_label


if __name__ == "__main__":
    s, p, c = build()
    per_list, per_label = count_tables(s, p)
    c.to_csv(RESEARCH / "results" / "fresh_check_pool_counts.csv")
    per_list.to_csv(RESEARCH / "results" / "fresh_split_counts_by_list.csv")
    per_label.to_csv(RESEARCH / "results" / "fresh_split_counts_by_label.csv")
    print(c.to_string()); print(per_list.to_string()); print(per_label.to_string())

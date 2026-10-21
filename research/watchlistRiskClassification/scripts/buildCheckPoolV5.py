"""Reserve a new precision check pool for the frozen checker v5 from the training records (never inspected), with the
same 60 / 100 / all rule; the pool and its people's copies leave training. Writes blind batches for the pool."""
import json
from pathlib import Path

import pandas as pd

from buildCheckPool import POOL_MAX, POOL_MIN

RESEARCH = Path(__file__).resolve().parents[1]
FRESH, BLIND = RESEARCH / "cache" / "fresh", RESEARCH / "cache" / "blind"
LABELS = json.load(open(RESEARCH / "results" / "frozen_versions.json", encoding="utf-8"))["scope_labels"]
SEED, BATCH = 2026, 50


def record_predictions(answers_file):
    """Per record and label: 1 if any flagged piece has P(yes) >= 0.5 and the record is not judged a victim."""
    answers = pd.read_csv(FRESH / answers_file, dtype={"record_key": str})
    role = pd.read_csv(FRESH / "checker_role.csv", dtype={"record_key": str})
    victims = set(role.loc[role["p_victim"] >= 0.5, "record_key"])
    yes = answers[(answers["p_yes"] >= 0.5) & ~answers["record_key"].isin(victims)]
    return yes[["record_key", "label"]].drop_duplicates().assign(system=1)


def build():
    split = pd.read_csv(FRESH / "split_final.csv", dtype=str, keep_default_na=False)
    preds = record_predictions("checker_answers_v5.csv")
    train_yes = preds[preds["record_key"].isin(set(split.loc[split["split"] == "training", "record_key"]))]
    drawn, counts = [], []
    for label in LABELS:
        candidates = train_yes.loc[train_yes["label"] == label, "record_key"].drop_duplicates().sort_values()
        size = len(candidates) if len(candidates) < POOL_MIN else POOL_MAX if len(candidates) >= 2 * POOL_MAX else POOL_MIN
        take = candidates.sample(size, random_state=SEED)
        drawn += [{"record_key": k, "label": label} for k in take]
        counts.append({"label": label, "v5 yes in training": len(candidates), "reserved": size, "below 60": size < POOL_MIN})
    drawn = pd.DataFrame(drawn)
    keys = set(drawn["record_key"])
    split.loc[split["record_key"].isin(keys), "split"] = "check_pool_v5"
    people = set(split.loc[split["split"] == "check_pool_v5", "entity_id"])
    split.loc[(split["split"] == "training") & split["entity_id"].isin(people), "split"] = "excluded_copy"
    split.to_csv(FRESH / "split_v5.csv", index=False, encoding="utf-8")
    drawn.to_csv(FRESH / "check_pool_v5_draw.csv", index=False)
    preds.to_csv(FRESH / "record_predictions_v5.csv", index=False)

    pool = split[split["split"] == "check_pool_v5"].sample(frac=1, random_state=SEED)
    manifest = []
    for b in range(0, len(pool), BATCH):
        name = f"pool5_{b // BATCH + 1:02d}"
        with open(BLIND / f"{name}.jsonl", "w", encoding="utf-8") as out:
            for key, text in zip(pool["record_key"].iloc[b:b + BATCH], pool["text"].iloc[b:b + BATCH]):
                out.write(json.dumps({"record_key": key, "text": text}, ensure_ascii=False) + "\n")
                manifest.append({"record_key": key, "batch": name, "purpose": "v5_check_pool"})
    pd.DataFrame(manifest).to_csv(BLIND / "manifest_pool5.csv", index=False)
    counts = pd.DataFrame(counts).set_index("label")
    counts.to_csv(RESEARCH / "results" / "fresh_check_pool_v5_counts.csv")
    print(counts.to_string())
    print(split["split"].value_counts().to_string())
    print(f"{len(pool)} pool records in {(len(pool) + BATCH - 1) // BATCH} blind batches")


if __name__ == "__main__":
    build()

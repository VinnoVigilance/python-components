"""Split the server export by person into dev overlap, held-out lists, a random 300 test set and the remaining pool."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from watchlistText import render

RESEARCH = Path(__file__).resolve().parents[1]
EXPORT = RESEARCH / "cache" / "fresh" / "server_export_2026-10-12.csv"
OUT = RESEARCH / "cache" / "fresh" / "split.csv"
HELD_OUT_LISTS = ["EU-MOST-WANTED", "DFAT", "AFDB-DEBARRED-ENTITIES", "DILG-LOCAL-OFFICIALS"]
RANDOM_N = 300
SEED = 42


def build():
    """Assign every exported row to dev900 / heldout_list / random300 / excluded_copy / rest."""
    x = pd.read_csv(EXPORT, dtype=str, keep_default_na=False)
    x["record_key"] = x["list_name"] + ":" + x["entity_id"]
    x["list_size"] = x["list_size"].astype(int)
    x["text"] = [render(json.loads(p), name) for p, name in zip(x["payload"], x["list_name"])]
    dev_keys = {json.loads(l)["record_key"] for l in open(RESEARCH / "data" / "dataset_900.jsonl", encoding="utf-8")}
    dev_people = {k.split(":", 1)[1] for k in dev_keys}

    x["split"] = "rest"
    x.loc[x["entity_id"].isin(dev_people), "split"] = "dev900"
    x.loc[(x["split"] == "rest") & x["list_name"].isin(HELD_OUT_LISTS), "split"] = "heldout_list"
    held_people = set(x.loc[x["split"] == "heldout_list", "entity_id"])
    x.loc[(x["split"] == "rest") & x["entity_id"].isin(held_people), "split"] = "excluded_copy"

    rest = x[x["split"] == "rest"]
    exported = rest.groupby("list_name")["list_name"].transform("size")
    weight = rest["list_size"] / exported
    picked = rest.sample(RANDOM_N, weights=weight, random_state=SEED).index
    x.loc[picked, "split"] = "random300"
    random_people = set(x.loc[picked, "entity_id"])
    x.loc[(x["split"] == "rest") & x["entity_id"].isin(random_people), "split"] = "excluded_copy"

    x.drop(columns=["payload"]).to_csv(OUT, index=False, encoding="utf-8")
    return x


if __name__ == "__main__":
    s = build()
    print(s["split"].value_counts().to_string())
    print(pd.crosstab(s["list_name"], s["split"]).to_string())

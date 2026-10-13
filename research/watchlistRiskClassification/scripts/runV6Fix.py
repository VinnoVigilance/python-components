"""Re-ask only the labels whose v6 notes were reworded (Fraud, Organized Crime) and replace their answers in the v6 files.
The first v6 answers are kept as checker_answers_v6a.csv / checker_pyes_v6a.csv."""
import sys
import time

import pandas as pd

from experimentKit import Data
from runV6 import CACHE, FRESH, ORDER, answer, build

LABELS = sys.argv[1:] or ["Crime-Fraud", "Crime-Organized Crime"]


def redo(q, old, key):
    q = answer(q[q["label"].isin(LABELS)], "fix_" + key)
    old = old.set_index([key, "unit_index", "label"])
    old.loc[q.set_index([key, "unit_index", "label"]).index, "p_yes"] = q.set_index([key, "unit_index", "label"])["p_yes"]
    return old.reset_index()


def main():
    t0 = time.time()
    split = pd.read_csv(FRESH / "split.csv", dtype=str, keep_default_na=False)
    split = split[split["split"].isin(ORDER)]
    old = pd.read_csv(FRESH / "checker_answers_v6a.csv")
    new = redo(build(split["record_key"], split["text"], split["list_name"]), old, "record_key")
    new[old.columns].to_csv(FRESH / "checker_answers_v6.csv", index=False, encoding="utf-8")
    print(f"fresh done in {time.time() - t0:.0f} s", flush=True)
    d = Data()
    q = build(d.df["record_key"], d.df["text"], d.df["source"])
    q["record"] = q["record_key"].map({k: i for i, k in enumerate(d.df["record_key"])})
    old = pd.read_csv(CACHE / "checker_pyes_v6a.csv")
    redo(q, old, "record")[old.columns].to_csv(CACHE / "checker_pyes_v6.csv", index=False)
    print(f"all done in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()

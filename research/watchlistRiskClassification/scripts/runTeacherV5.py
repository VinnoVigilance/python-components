"""Ask another teacher model the v5 questions on design data only (old check pool, random 300, held-out samples) and
the dev 900, to compare with Qwen3-4B before v5 is frozen. Resumable."""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from checker import Checker
from experimentKit import Data
from runTeacher import MODELS
from runV5 import build

RESEARCH = Path(__file__).resolve().parents[1]
CACHE, FRESH = RESEARCH / "cache", RESEARCH / "cache" / "fresh"


def ask(checker, q, name):
    partial = CACHE / f"{name}.partial.npy"
    done, t0 = (list(np.load(partial)) if partial.exists() else []), time.time()
    for s in range(len(done), len(q), 500):
        done += checker.p_yes(q["prompt"].iloc[s:s + 500].tolist())
        np.save(partial, np.array(done))
        (CACHE / f"{name}_progress.txt").write_text(f"{len(done)} / {len(q)} questions, {time.time() - t0:.0f} s")
    q = q.drop(columns=["prompt"]).assign(p_yes=done)
    partial.unlink()
    return q, time.time() - t0


def main(tag):
    checker = Checker(MODELS[tag])
    split = pd.read_csv(FRESH / "split_final.csv", dtype=str, keep_default_na=False)
    blind = set(pd.read_csv(RESEARCH / "data" / "fresh_blind_labels.csv", dtype=str)["record_key"])
    design = split[(split["split"] == "check_pool") | split["record_key"].isin(blind)]
    q, secs = ask(checker, build(design["record_key"], design["text"], design["list_name"]), f"teacher_{tag}_v5_design")
    q.to_csv(FRESH / f"teacher_{tag}_answers_v5_design.csv", index=False, encoding="utf-8")
    print(f"design: {len(q)} questions in {secs:.0f} s", flush=True)
    d = Data()
    q, secs = ask(checker, build(d.df["record_key"], d.df["text"], d.df["source"]), f"teacher_{tag}_v5_dev")
    q["record"] = q["record_key"].map({k: i for i, k in enumerate(d.df["record_key"])})
    q[["record", "unit_index", "label", "p_yes"]].to_csv(CACHE / f"teacher_{tag}_pyes_v5.csv", index=False)
    print(f"dev: {len(q)} questions in {secs:.0f} s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])

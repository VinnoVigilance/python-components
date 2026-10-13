"""Checker v6 on the fresh records and the dev 900: v6 highlighter (boilerplate and 'fighting the conduct' words skipped)
and v6 questions; Qwen3-8B answers the labels in EIGHT_B, Qwen3-4B the rest. Resumable; the GPU pauses when hot."""
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from checker import Checker, label_definitions, questions
from experimentKit import Data
from highlighter import EXTRA_TRIGGERS, TAXONOMY_TRIGGERS, compile_triggers, flag_units, split_units

RESEARCH = Path(__file__).resolve().parents[1]
CACHE, FRESH = RESEARCH / "cache", RESEARCH / "cache" / "fresh"
SCOPE = json.load(open(RESEARCH / "results" / "frozen_versions.json", encoding="utf-8"))["scope_labels"]
ORDER = ["random300", "heldout_list", "rest"]
MODELS = {"Qwen/Qwen3-4B-Instruct-2507": None, "Qwen/Qwen3-8B": ["Crime-Fraud", "Judicial / Court"]}


def build(keys, texts, sources):
    pick = lambda lexicon: {k: v for k, v in lexicon.items() if k in SCOPE}
    triggers, defs, rows = compile_triggers(pick(TAXONOMY_TRIGGERS), pick(EXTRA_TRIGGERS)), label_definitions(), []
    for key, text, source in zip(keys, texts, sources):
        units = split_units(text)
        flags = flag_units(units, triggers, source=source, version="v6")
        for (k, label, _), prompt in zip(flags, questions(units, flags, defs, version="v6")):
            rows.append({"record_key": key, "unit_index": k, "label": label, "field": units[k][0], "unit": units[k][1],
                         "prompt": prompt})
    return pd.DataFrame(rows)


def answer(q, name):
    """Ask every question with the model chosen for its label, saving progress per model."""
    q = q.copy()
    q["p_yes"] = np.nan
    eight = MODELS["Qwen/Qwen3-8B"]
    for model_id, mask in [("Qwen/Qwen3-4B-Instruct-2507", ~q["label"].isin(eight)), ("Qwen/Qwen3-8B", q["label"].isin(eight))]:
        todo = q.loc[mask, "prompt"].tolist()
        tag = hashlib.sha256((name + model_id + str(len(todo))).encode()).hexdigest()[:12]
        partial = CACHE / f"v6_{name}_{tag}.partial.npy"
        done = list(np.load(partial)) if partial.exists() else []
        if len(done) < len(todo):
            checker, t0 = Checker(model_id), time.time()
            for s in range(len(done), len(todo), 1000):
                done += checker.p_yes(todo[s:s + 1000])
                np.save(partial, np.array(done))
                (CACHE / "v6_progress.txt").write_text(f"{name} {model_id}: {len(done)} / {len(todo)}, {time.time() - t0:.0f} s")
            del checker
        q.loc[mask, "p_yes"] = done
        partial.unlink(missing_ok=True)
    return q.drop(columns=["prompt"])


def main():
    split = pd.read_csv(FRESH / "split.csv", dtype=str, keep_default_na=False)
    split = split[split["split"].isin(ORDER)]
    t0 = time.time()
    answer(build(split["record_key"], split["text"], split["list_name"]), "fresh").to_csv(
        FRESH / "checker_answers_v6.csv", index=False, encoding="utf-8")
    print(f"fresh done in {time.time() - t0:.0f} s", flush=True)
    d = Data()
    q = answer(build(d.df["record_key"], d.df["text"], d.df["source"]), "dev")
    q["record"] = q["record_key"].map({k: i for i, k in enumerate(d.df["record_key"])})
    q[["record", "unit_index", "label", "p_yes"]].to_csv(CACHE / "checker_pyes_v6.csv", index=False)
    print(f"all done in {time.time() - t0:.0f} s", flush=True)


if __name__ == "__main__":
    main()

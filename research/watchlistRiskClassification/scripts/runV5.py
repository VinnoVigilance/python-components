"""Checker v5 on the fresh records and the dev 900: v5 highlighter (boilerplate skipped) and v5 notes. Questions whose
wording did not change reuse the v4 answer; only labels with new notes are asked again (resumable)."""
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from checker import MODEL_ID, NOTES, NOTES_V5, Checker, label_definitions, questions
from experimentKit import Data, label_tiers
from highlighter import EXTRA_TRIGGERS, TAXONOMY_TRIGGERS, compile_triggers, flag_units, split_units

RESEARCH = Path(__file__).resolve().parents[1]
CACHE, FRESH = RESEARCH / "cache", RESEARCH / "cache" / "fresh"
SCOPE = json.load(open(RESEARCH / "results" / "frozen_versions.json", encoding="utf-8"))["scope_labels"]
CHANGED = sorted(k for k in NOTES_V5 if NOTES_V5[k] != NOTES.get(k))
ORDER = ["random300", "heldout_list", "rest"]


def build(keys, texts, sources):
    pick = lambda lexicon: {k: v for k, v in lexicon.items() if k in SCOPE}
    triggers, defs, rows = compile_triggers(pick(TAXONOMY_TRIGGERS), pick(EXTRA_TRIGGERS)), label_definitions(), []
    for key, text, source in zip(keys, texts, sources):
        units = split_units(text)
        flags = flag_units(units, triggers, source=source)
        for (k, label, _), prompt in zip(flags, questions(units, flags, defs, version="v5")):
            rows.append({"record_key": key, "unit_index": k, "label": label, "field": units[k][0], "unit": units[k][1],
                         "prompt": prompt})
    return pd.DataFrame(rows)


def answer(q, old, name, checker, ask_labels=CHANGED):
    """Reuse earlier answers for unchanged labels; ask the checker for `ask_labels`, saving progress."""
    q = q.merge(old, how="left", on=["record_key", "unit_index", "label"])
    ask = q["label"].isin(ask_labels)
    q.loc[ask, "p_yes"] = np.nan
    assert q.loc[~ask, "p_yes"].notna().all()
    tag = hashlib.sha256(("|".join(sorted(ask_labels)) + checker.model_id).encode()).hexdigest()[:12]
    partial = CACHE / f"v5_{name}_{tag}.partial.npy"
    done = list(np.load(partial)) if partial.exists() else []
    todo, t0 = q.loc[ask, "prompt"].tolist(), time.time()
    for s in range(len(done), len(todo), 1000):
        done += checker.p_yes(todo[s:s + 1000])
        np.save(partial, np.array(done))
        (CACHE / "v5_progress.txt").write_text(f"{name}: {len(done)} / {len(todo)} questions, {time.time() - t0:.0f} s")
    q.loc[ask, "p_yes"] = done
    partial.unlink(missing_ok=True)
    return q.drop(columns=["prompt"]), len(todo), time.time() - t0


def main(ask_labels=CHANGED, model_id=MODEL_ID):
    """With `ask_labels` narrower than CHANGED, the other labels reuse the previous v5 answers."""
    checker = Checker(model_id)
    split = pd.read_csv(FRESH / "split.csv", dtype=str, keep_default_na=False)
    split = split[split["split"].isin(ORDER)]
    previous = FRESH / ("checker_answers_v5.csv" if ask_labels != CHANGED else "checker_answers.csv")
    old = pd.read_csv(previous, dtype={"record_key": str})[["record_key", "unit_index", "label", "p_yes"]]
    q, n, secs = answer(build(split["record_key"], split["text"], split["list_name"]), old, "fresh", checker, ask_labels)
    q.to_csv(FRESH / "checker_answers_v5.csv", index=False, encoding="utf-8")
    print(f"fresh: {len(q)} v5 questions, {n} asked again in {secs:.0f} s", flush=True)

    d = Data()
    keys = d.df["record_key"]
    previous = CACHE / ("checker_pyes_v5.csv" if ask_labels != CHANGED else "checker_pyes_v4.csv")
    old = pd.read_csv(previous).assign(record_key=lambda x: keys.iloc[x["record"]].values)
    q, n, secs = answer(build(keys, d.df["text"], d.df["source"]), old.drop(columns=["record"]), "dev", checker, ask_labels)
    q["record"] = q["record_key"].map({k: i for i, k in enumerate(keys)})
    q[["record", "unit_index", "label", "p_yes"]].to_csv(CACHE / "checker_pyes_v5.csv", index=False)
    print(f"dev: {len(q)} v5 questions, {n} asked again in {secs:.0f} s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1].split(",") if len(sys.argv) > 1 else CHANGED, *sys.argv[2:3])

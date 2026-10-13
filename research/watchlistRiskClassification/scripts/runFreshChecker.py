"""Run the frozen highlighter + checker over the fresh split (random300, held-out lists, rest), resumable."""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from checker import Checker, label_definitions, questions, role_questions
from highlighter import EXTRA_TRIGGERS, TAXONOMY_TRIGGERS, compile_triggers, flag_units, split_units

RESEARCH = Path(__file__).resolve().parents[1]
FRESH = RESEARCH / "cache" / "fresh"
FROZEN = json.load(open(RESEARCH / "results" / "frozen_versions.json", encoding="utf-8"))
ORDER = ["random300", "heldout_list", "rest"]
CHUNK = 2000


def build_questions():
    """Flag the fresh records and build one question per flagged (unit, label), in split order."""
    split = pd.read_csv(FRESH / "split.csv", dtype=str, keep_default_na=False)
    split = split[split["split"].isin(ORDER)].copy()
    split["order"] = split["split"].map(ORDER.index)
    split = split.sort_values(["order", "list_name", "record_key"])
    scope = set(FROZEN["scope_labels"])
    pick = lambda lexicon: {k: v for k, v in lexicon.items() if k in scope}
    triggers = compile_triggers(pick(TAXONOMY_TRIGGERS), pick(EXTRA_TRIGGERS))
    definitions = label_definitions()
    rows = []
    for key, text in zip(split["record_key"], split["text"]):
        units = split_units(text)
        flags = flag_units(units, triggers)
        for (k, label, hits), prompt in zip(flags, questions(units, flags, definitions)):
            rows.append({"record_key": key, "unit_index": k, "label": label, "field": units[k][0], "unit": units[k][1],
                         "trigger_words": ", ".join(hits), "prompt": prompt})
    return split, pd.DataFrame(rows)


def main():
    split, q = build_questions()
    print(f"{len(split)} records, {q['record_key'].nunique()} with flags, {len(q)} questions", flush=True)
    partial, progress = FRESH / "checker_pyes.partial.npy", FRESH / "checker_progress.txt"
    done = list(np.load(partial)) if partial.exists() else []
    checker, t0 = Checker(), time.time()
    for s in range(len(done), len(q), CHUNK):
        done += checker.p_yes(q["prompt"].iloc[s:s + CHUNK].tolist())
        np.save(partial, np.array(done))
        progress.write_text(f"{len(done)} / {len(q)} questions, {time.time() - t0:.0f} s")
    q["p_yes"] = done
    q.drop(columns=["prompt"]).to_csv(FRESH / "checker_answers.csv", index=False, encoding="utf-8")
    flagged = split[split["record_key"].isin(set(q["record_key"]))]
    p_victim = checker.p_yes(role_questions(flagged["text"].str.replace(r"^Source list:.*\n", "", regex=True).tolist()))
    pd.DataFrame({"record_key": flagged["record_key"], "p_victim": p_victim}).to_csv(FRESH / "checker_role.csv", index=False)
    progress.write_text(f"done: {len(q)} questions + {len(flagged)} role questions, {time.time() - t0:.0f} s")
    print("done", flush=True)


if __name__ == "__main__":
    main()

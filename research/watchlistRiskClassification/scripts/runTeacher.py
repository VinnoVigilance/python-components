"""Ask a second teacher model the frozen v4 checker questions on the dev 900 (same highlighter flags), resumable."""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from checker import Checker, label_definitions, questions, role_questions
from experimentKit import Data, label_tiers
from highlighter import EXTRA_TRIGGERS, TAXONOMY_TRIGGERS, compile_triggers, flag_units, split_units

RESEARCH = Path(__file__).resolve().parents[1]
CACHE = RESEARCH / "cache"
MODELS = {"granite": "ibm-granite/granite-3.3-8b-instruct", "qwen8b": "Qwen/Qwen3-8B"}


def dev_questions():
    """The same (record, unit, label) questions as notebook 04 at v4."""
    d = Data()
    tiers = label_tiers(d)
    scope = [i for i in range(len(d.labels)) if tiers[i] != "lookup only" and i not in d.rare]
    pick = lambda lexicon: {k: v for k, v in lexicon.items() if d.labels.index(k) in scope}
    triggers = compile_triggers(pick(TAXONOMY_TRIGGERS), pick(EXTRA_TRIGGERS))
    defs = label_definitions()
    units = [split_units(t) for t in d.df["text"]]
    flags = [flag_units(u, triggers) for u in units]
    rows = pd.DataFrame([{"record": r, "unit_index": k, "label": label} for r, fl in enumerate(flags) for k, label, _ in fl])
    rows["prompt"] = [p for r, fl in enumerate(flags) for p in questions(units[r], fl, defs)]
    return d, rows


def main(tag):
    d, rows = dev_questions()
    out, role_out = CACHE / f"teacher_{tag}_pyes_v4.csv", CACHE / f"teacher_{tag}_role_v4.csv"
    partial, progress = CACHE / f"teacher_{tag}_pyes_v4.partial.npy", CACHE / f"teacher_{tag}_progress.txt"
    done = list(np.load(partial)) if partial.exists() else []
    checker, t0 = Checker(MODELS[tag]), time.time()
    for s in range(len(done), len(rows), 500):
        done += checker.p_yes(rows["prompt"].iloc[s:s + 500].tolist())
        np.save(partial, np.array(done))
        progress.write_text(f"{len(done)} / {len(rows)} questions, {time.time() - t0:.0f} s")
    rows["p_yes"] = done
    rows[["record", "unit_index", "label", "p_yes"]].to_csv(out, index=False)
    flagged = sorted(rows["record"].unique())
    p_victim = checker.p_yes(role_questions(d.df["text_noname"].iloc[flagged].tolist()))
    pd.DataFrame({"record": flagged, "p_victim": p_victim}).to_csv(role_out, index=False)
    progress.write_text(f"done: {len(rows)} questions + {len(flagged)} role questions, {time.time() - t0:.0f} s")
    partial.unlink()


if __name__ == "__main__":
    main(sys.argv[1])

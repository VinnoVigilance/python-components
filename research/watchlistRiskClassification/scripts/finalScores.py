"""Per-label precision, recall and F1 of the whole system (lookup rules + model) on the dev 900, for the v5 teacher and
the v5 student, with how many correct labels came from the rules and how many from the model; plus the v5 precision on
the untouched check pool."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

import compareTeachers as ct
from experimentKit import Data, label_tiers, wilson
from highlighter import EXTRA_TRIGGERS, TAXONOMY_TRIGGERS, compile_triggers, flag_units, split_units
from student import CONTEXT, RECORD_CHARS, VICTIM, Student

RESEARCH = Path(__file__).resolve().parents[1]
PEP_LISTS = {"DILG-LOCAL-OFFICIALS", "COMELEC-2025-SENATORS"}


def student_dev(d, scope, name="e5small_v5", version="v5"):
    """Record x label 0/1 from the student on the dev 900 (v5 or v6 highlighter, victim rule)."""
    pick = lambda lexicon: {k: v for k, v in lexicon.items() if d.labels.index(k) in scope}
    triggers, rows = compile_triggers(pick(TAXONOMY_TRIGGERS), pick(EXTRA_TRIGGERS)), []
    for r, (text, source) in enumerate(zip(d.df["text"], d.df["source"])):
        units = split_units(text)
        flags = flag_units(units, triggers, source=source, version=version)
        for k, label, _ in flags:
            before = units[k - 1][1][-CONTEXT:] if k else ""
            after = units[k + 1][1][:CONTEXT] if k + 1 < len(units) else ""
            rows.append({"record": r, "label": label, "text": f"{units[k][0]}: {units[k][1]} || {before} [...] {after}"})
        if flags:
            rows.append({"record": r, "label": VICTIM, "text": d.df["text_noname"].iloc[r][:RECORD_CHARS]})
    q = pd.DataFrame(rows)
    q["p"] = Student(RESEARCH / "cache" / f"student_{name}", device="cuda:0").p_yes(q["label"].values, q["text"].values, 128)
    P = np.zeros(d.Y.shape, dtype=int)
    victims = set(q.loc[(q["label"] == VICTIM) & (q["p"] >= 0.5), "record"])
    for r, label, p in q[q["label"] != VICTIM][["record", "label", "p"]].itertuples(index=False):
        if p >= 0.5 and r not in victims:
            P[r, d.labels.index(label)] = 1
    return P


def main(version="v5"):
    d = Data()
    tiers = label_tiers(d)
    scope = [i for i in range(len(d.labels)) if tiers[i] != "lookup only" and i not in d.rare]
    YR, L = ct.reviewed_reference(d), d.L.copy()
    pep, lists = d.labels.index("PEP"), d.df["source"].isin(PEP_LISTS).values
    YR[lists, pep], L[lists, pep] = 1, 1
    ct.FILES[version] = (f"checker_pyes_{version}.csv", "checker_role_v4.csv")
    models = {"teacher (Qwen)": ct.predictions(d, version),
              "student (e5-small)": student_dev(d, scope, f"e5small_{version}", version)}
    proof = pd.read_csv(RESEARCH / "results" / "v5_precision_by_label.csv", index_col=0)
    rows = []
    for i in range(len(d.labels)):
        y, l = YR[:, i], L[:, i]
        row = {"label": d.labels[i], "group": "rules only" if tiers[i] == "lookup only" else
               ("set aside" if i in d.rare else "rules + AI"), "true labels (dev 900)": int(y.sum()),
               "found by rules": int((l & y).sum())}
        for name, P in models.items():
            s = l | (P[:, i] if i in scope else 0)
            tp, fp, fn = int((s & y).sum()), int((s & (1 - y)).sum()), int(((1 - s) & y).sum())
            p, r = tp / max(tp + fp, 1), tp / max(tp + fn, 1)
            row.update({f"{name} P": p, f"{name} R": r, f"{name} F1": 2 * p * r / max(p + r, 1e-9),
                        f"{name} found by AI": int(((s & y) & (1 - l)).sum())})
        name = d.labels[i]
        row["untouched precision (v5)"] = proof["precision"].get(name, np.nan)
        rows.append(row)
    out = pd.DataFrame(rows).set_index("label")
    out.to_csv(RESEARCH / "results" / ("final_scores_by_label.csv" if version == "v5" else f"final_scores_by_label_{version}.csv"))
    pd.set_option("display.width", 300)
    print(out.round(2).to_string())


if __name__ == "__main__":
    main(*sys.argv[1:])

# %% [markdown]
# # 04 — The checker: deciding each highlighted piece in context
#
# **Question:** if a language model reads each piece the highlighter flagged, together with its neighbouring text and the
# label's definition from the taxonomy, does it keep the true labels and drop the wrong flags — on **every** list,
# including lists no model was trained on?
#
# ```
# record → lookup (codes) → highlighter (units + trigger words, notebook 03) → CHECKER (this notebook) → final labels
# ```
#
# | Step | What | Status |
# |---|---|---|
# | 4a / 4b | Whole-record models (notebooks 01, 02) — best on new lists: ML-part F1 0.55, precision 0.46 | done |
# | B | Highlighter (notebook 03) — flags 94.8% of the text layer's labels, 48% of flags right | done |
# | **C** | **Checker (this notebook)** | **now** |
#
# **The checker** (`scripts/checker.py`): `Qwen/Qwen3-4B-Instruct-2507` (open licence, Apache-2.0), loaded in 4-bit on the
# laptop's RTX 4070 — **no data leaves the machine**. For every flagged (unit, label) pair it gets one question:
#
# * the label's **definition, coverage and boundary** (taxonomy column C, English);
# * the **field** the unit comes from (Remarks, Occupations, …);
# * the **unit** itself, plus the unit before and after it as context;
# * the question: does this text support assigning the label to the listed person or entity?
#
# It answers yes or no. We read the **probability of "yes"** from the model's first answer word (one model pass, no text
# generation), so every answer is a score between 0 and 1. A record gets a label when **any** of its flagged units scores
# ≥ 0.5.
#
# **Nothing is trained on our labels.** The model never saw these records or lists, so there is no seen / unseen split:
# every list is "new" to it. The 0.5 cut-off is fixed in advance. (A second view picks a cut-off per label from the
# *other* unseen-list folds, to show what tuning could add.)
#
# **Scope:** the same 15 labels as notebook 03. The 5 labels not covered yet and the 3 lookup-only labels are excluded.
#
# **Caveat:** the reference labels were made by two LLM labellers (agreed + decided disputes), so a "wrong" answer may
# sometimes be a reference mistake. The human answer sheet from notebook 03 is the independent check.

# %%
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

pd.set_option("display.max_colwidth", 120, "display.width", 220, "display.precision", 3)

RESEARCH = Path.cwd().parent
sys.path.insert(0, str(RESEARCH / "scripts"))
from experimentKit import Data, crossfit_predict, label_tiers, ml_part, prf, wilson
from highlighter import EXTRA_TRIGGERS, TAXONOMY_TRIGGERS, compile_triggers, flag_units, split_units
from checker import MODEL_ID, PROMPT_VERSION, Checker, label_definitions, questions, role_questions

CACHE = RESEARCH / "cache"
d = Data()
df, Y, L = d.df, d.Y, d.L
TIERS = label_tiers(d)
SCOPE = [i for i in range(len(d.labels)) if TIERS[i] != "lookup only" and i not in d.rare]
NEED = Y & (1 - L)
in_scope = lambda lexicon: {k: v for k, v in lexicon.items() if d.labels.index(k) in SCOPE}
TRIGGERS = compile_triggers(in_scope(TAXONOMY_TRIGGERS), in_scope(EXTRA_TRIGGERS))
DEFS = label_definitions()

UNITS = [split_units(t) for t in df["text"]]
FLAGS = [flag_units(u, TRIGGERS) for u in UNITS]
ROWS = pd.DataFrame([{"record": r, "unit_index": k, "label": label, "field": UNITS[r][k][0], "unit": UNITS[r][k][1],
                      "trigger words": ", ".join(hits)}
                     for r, fl in enumerate(FLAGS) for k, label, hits in fl])
ROWS["prompt"] = [p for r, fl in enumerate(FLAGS) for p in questions(UNITS[r], fl, DEFS)]
print(f"{len(ROWS):,} questions for {ROWS['record'].nunique()} records; model: {MODEL_ID}")
print("\nexample question:\n" + "-" * 100 + "\n" + ROWS.loc[ROWS["label"] == "Crime-Corruption", "prompt"].iloc[0] + "\n" + "-" * 100)

# %% [markdown]
# ## 1. Run the checker
# Answers are cached in `cache/checker_pyes.csv`, so re-running the notebook does not re-ask the model.

# %%
cache_file = CACHE / f"checker_pyes_{PROMPT_VERSION}.csv"
if cache_file.exists() and len(pd.read_csv(cache_file)) == len(ROWS):
    ROWS["p_yes"] = pd.read_csv(cache_file)["p_yes"].values
    SECONDS = float(open(CACHE / f"checker_seconds_{PROMPT_VERSION}.txt").read())
else:
    checker = Checker()
    partial, progress = CACHE / f"checker_pyes_{PROMPT_VERSION}.partial.npy", CACHE / "checker_progress.txt"
    done = list(np.load(partial)) if partial.exists() else []
    t0 = time.time()
    for s in range(len(done), len(ROWS), 500):
        done += checker.p_yes(ROWS["prompt"].iloc[s:s + 500].tolist())
        np.save(partial, np.array(done))
        progress.write_text(f"{len(done)} / {len(ROWS)} questions, {time.time() - t0:.0f} s")
    SECONDS = time.time() - t0
    ROWS["p_yes"] = done
    ROWS[["record", "unit_index", "label", "p_yes"]].to_csv(cache_file, index=False)
    open(CACHE / f"checker_seconds_{PROMPT_VERSION}.txt", "w").write(str(SECONDS))
    partial.unlink()
print(f"{len(ROWS):,} questions in {SECONDS:.0f} s on the RTX 4070 ({len(ROWS) / SECONDS:.1f} questions per second)")
print(f"share answered yes (≥ 0.5): {(ROWS['p_yes'] >= 0.5).mean():.1%}")

# %% [markdown]
# **Victim check (from v3).** One extra question per record that has flags: is the listed person a victim or missing
# person rather than a suspect? If yes (≥ 0.5), the record gets no text-layer labels.

# %%
FLAGGED = sorted(ROWS["record"].unique())
role_file = CACHE / f"checker_role_{PROMPT_VERSION}.csv"
if PROMPT_VERSION < "v3":
    P_VICTIM = pd.Series(0.0, index=FLAGGED)
elif role_file.exists():
    P_VICTIM = pd.read_csv(role_file, index_col="record")["p_victim"]
else:
    try:
        checker
    except NameError:
        checker = Checker()
    P_VICTIM = pd.Series(checker.p_yes(role_questions(df["text_noname"].iloc[FLAGGED].tolist())), index=FLAGGED, name="p_victim")
    P_VICTIM.rename_axis("record").to_csv(role_file)
VICTIMS = P_VICTIM.index[P_VICTIM >= 0.5]
print(f"records judged victim / missing person: {len(VICTIMS)} of {len(FLAGGED)}")
df.loc[VICTIMS, ["source", "text_noname"]].assign(text_noname=lambda x: x["text_noname"].str[:160])

# %% [markdown]
# ## 2. From unit answers to record labels
# Record score for a label = the highest P(yes) among its flagged units (0 if no unit was flagged).

# %%
S = np.zeros(Y.shape)
for r, label, p in ROWS[["record", "label", "p_yes"]].itertuples(index=False):
    j = d.labels.index(label)
    S[r, j] = max(S[r, j], p)
S[VICTIMS] = 0

FOLDS = d.unseen_folds()
P_fixed = (S >= 0.5).astype(int)
P_tuned = np.zeros_like(Y)
P_tuned[:, SCOPE] = crossfit_predict(S[:, SCOPE], Y[:, SCOPE], FOLDS)
P_flags = (S > 0).astype(int)

nb02 = np.load(CACHE / "nb02_predictions.npz")
APPROACHES = {
    "TF-IDF + LR (notebook 01/02), unseen lists": nb02["B0 char TF-IDF + LR | unseen"],
    "best whole-record model: TF-IDF + e5-base (notebook 02), unseen lists": nb02["ENS B0 + E2 mE5-base | unseen"],
    "highlighter only (every flag = yes)": P_flags,
    "highlighter + checker, cut-off 0.5 (no tuning)": P_fixed,
    "highlighter + checker, cut-off per label tuned on other folds": P_tuned,
}

def view(P):
    pr, rc, f1 = ml_part(Y, P, L, SCOPE)
    sys_ = P | L
    return {"ML part P": pr, "ML part R": rc, "ML part F1": f1,
            "macro-F1, 15 labels (lookup ∪ model)": np.mean([prf(Y[:, i], sys_[:, i])[2] for i in SCOPE]),
            "labels added": int((P[:, SCOPE] & (1 - L[:, SCOPE])).sum()),
            "added but wrong": int((P[:, SCOPE] & (1 - L[:, SCOPE]) & (1 - Y[:, SCOPE])).sum())}

card = pd.DataFrame({k: view(P) for k, P in APPROACHES.items()}).T
card.to_csv(RESEARCH / "results" / "checker_score_card.csv")
card

# %% [markdown]
# **Reading — score card (15 labels, label instances the lookup misses; original reference)**
#
# * Question **v2** (`PROMPT_VERSION`): adds the taxonomy's sample indicators, "victims, officials fighting the conduct
#   and list authorities do not count", "one event can carry several labels", and a PEP note on non-state armed groups.
#   v1 → v2: precision 0.706 → **0.733**, recall 0.867 → **0.875**, wrong labels added 220 → 195.
# * **Highlighter + checker beats every whole-record model:** ML-part F1 **0.798** vs 0.594 for the best notebook 02 model
#   on unseen lists (precision 0.733 vs 0.518, recall 0.875 vs 0.697).
# * The checker removes 69% of the highlighter's wrong flags (636 → 195) and keeps 92% of its correct ones.
# * A per-label tuned cut-off is no better (F1 0.795) than the fixed 0.5 — no labelled data is needed to set it.
# * v2 was written after looking at v1's errors on these same records, so its gain here is slightly optimistic.

# %% [markdown]
# ## 2b. Scores against the reviewed reference
# Every case where the checker and the reference disagree was reviewed against the taxonomy definitions
# (`data/reference_review.csv`: decision `keep` / `add` / `remove` with a reason per case). A random sample of 40 cases
# where both said yes was also reviewed: all 40 were correct, so cases where both are wrong are rare.
# Only the reference changes here; the checker's answers are the same.

# %%
review = pd.read_csv(RESEARCH / "data" / "reference_review.csv", encoding="utf-8-sig")
print(review["decision"].value_counts().to_dict())
key = {k: i for i, k in enumerate(df["record_key"])}
YR = Y.copy()
for k, label, v in review[["record_key", "label", "reviewed"]].itertuples(index=False):
    YR[key[k], d.labels.index(label)] = v

def reviewed_view(P):
    pr, rc, f1 = ml_part(YR, P, L, SCOPE)
    w, s = (df["segment"] == "Wanted lists").values, (df["segment"] == "Sanctions & terror lists").values
    return {"ML part P": pr, "ML part R": rc, "ML part F1": f1,
            "wanted lists P": ml_part(YR[w], P[w], L[w], SCOPE)[0], "sanctions lists P": ml_part(YR[s], P[s], L[s], SCOPE)[0]}

reviewed_card = pd.DataFrame({k: reviewed_view(APPROACHES[k]) for k in (
    "best whole-record model: TF-IDF + e5-base (notebook 02), unseen lists", "highlighter + checker, cut-off 0.5 (no tuning)")}).T
reviewed_card.to_csv(RESEARCH / "results" / "checker_score_card_reviewed.csv")
reviewed_card

# %%
rows = []
for i in SCOPE:
    added = P_fixed[:, i] & (1 - L[:, i])
    good, n = int((added & YR[:, i]).sum()), int(added.sum())
    need = int((YR[:, i] & (1 - L[:, i])).sum())
    lo, _ = wilson(good, n)
    rows.append({"label": d.short[i], "labels added": n, "precision": good / max(n, 1), "precision, 95% lower bound": lo,
                 "recall": int((added & YR[:, i]).sum()) / max(need, 1), "needed": need})
reviewed_per_label = pd.DataFrame(rows).set_index("label").sort_values("precision", ascending=False)
reviewed_per_label.to_csv(RESEARCH / "results" / "checker_per_label_reviewed.csv")
reviewed_per_label

# %% [markdown]
# **Reading — reviewed reference**
#
# * Review of the 239 disagreements: the reference was right in **176** (checker wrong), missed the label in **60**, and had
#   a wrong label in **3** (Proliferation for explosive attacks called "weapons of mass destruction" in US law).
#   Typical reference gaps: Iranian nuclear-programme designations → Proliferation; court charges / probation /
#   hearings → Judicial; killing and attacks on civilians → Violent Crime (alongside Human Rights).
# * **Against the reviewed reference: precision 0.815, recall 0.891, F1 0.851** (best whole-record model: 0.540 / 0.664).
#   Wanted lists precision **0.920**; sanctions lists **0.714**.
# * Per label: Fraud 1.00, Corruption 0.94, Theft 0.94, Money Laundering 0.93 are at or above 90%; Cybercrime 0.89,
#   Judicial 0.87, Violent Crime 0.86 are close; **PEP 0.59** and **Terrorism 0.52** are far below.
# * The checker's remaining mistakes on those two are systematic: election **candidates** and the **listing authority**
#   ("Australian Government Department of Foreign Affairs") called PEP; **Taliban** roles without any reported terrorist
#   act called Terrorism; and **victims** ("Homicide Victim", "Missing Persons") called Violent Crime.
# * Caveat: the review was done by Claude against the taxonomy, not by a human; reasons are in the file for spot-checking.

# %% [markdown]
# ## 3. Per label (cut-off 0.5)

# %%
rows = []
for i in SCOPE:
    need = NEED[:, i]
    out = {}
    for name, P in (("best whole-record", APPROACHES["best whole-record model: TF-IDF + e5-base (notebook 02), unseen lists"]),
                    ("checker", P_fixed)):
        pr, rc, f1 = ml_part(Y, P, L, [i])
        out[(name, "P")], out[(name, "R")], out[(name, "F1")] = pr, rc, f1
    found = int((need & P_fixed[:, i]).sum())
    lo, hi = wilson(found, int(need.sum()))
    out[("checker", "R 95% CI")] = f"{lo:.2f}–{hi:.2f}"
    out[("", "needed")], out[("", "tier")] = int(need.sum()), TIERS[i]
    rows.append(pd.Series(out, name=d.short[i]))
per_label = pd.DataFrame(rows)[[("", "tier"), ("", "needed"), ("best whole-record", "P"), ("best whole-record", "R"),
                                ("best whole-record", "F1"), ("checker", "P"), ("checker", "R"), ("checker", "F1"),
                                ("checker", "R 95% CI")]]
per_label.to_csv(RESEARCH / "results" / "checker_per_label.csv")
per_label.sort_values(("", "needed"), ascending=False)

# %% [markdown]
# **Reading — per label (original reference)**
#
# * Large gains over the best whole-record model: Theft F1 0.38 → **0.85**, Money Laundering 0.71 → **0.95**, Fraud
#   0.83 → **0.90**, Judicial 0.73 → **0.88**, Corruption 0.42 → **0.85**, Tax 0.45 → **0.85**, Trafficking 0.74 → **0.88**.
# * Weak: **Terrorism** (precision 0.36), **Proliferation** (0.46), **PEP** (0.53).

# %% [markdown]
# ## 4. Per segment and list (cut-off 0.5)

# %%
seg = []
for key in ("segment", "source"):
    for g, idx in df.groupby(key).groups.items():
        idx = np.asarray(idx)
        if NEED[np.ix_(idx, SCOPE)].sum() < 10:
            continue
        b = ml_part(Y[idx], APPROACHES["best whole-record model: TF-IDF + e5-base (notebook 02), unseen lists"][idx], L[idx], SCOPE)
        c = ml_part(Y[idx], P_fixed[idx], L[idx], SCOPE)
        seg.append({"level": key, "group": g, "needed": int(NEED[np.ix_(idx, SCOPE)].sum()),
                    "best whole-record F1": b[2], "checker P": c[0], "checker R": c[1], "checker F1": c[2]})
seg = pd.DataFrame(seg).set_index(["level", "group"])
seg.sort_values("needed", ascending=False)

# %% [markdown]
# **Reading — segments and lists (original reference)**
#
# * **Wanted lists: F1 0.893** (precision 0.881) vs 0.656 for the best whole-record model.
# * **Sanctions & terror lists: F1 0.678** (precision 0.572) vs 0.514 — the weak point (DFAT 0.46, UN 0.44, EU financial
#   0.45, SECO 0.50). Section 2b shows how much of this is reference gaps.

# %% [markdown]
# ## 5. What the checker gets right and wrong — real examples
# For a few cases the model is also asked for a one-sentence reason (text generation, slower), to show the evidence a
# reviewer would see.

# %%
ROWS["truth"] = [Y[r, d.labels.index(l)] for r, l in zip(ROWS["record"], ROWS["label"])]
ROWS["from lookup"] = [L[r, d.labels.index(l)] for r, l in zip(ROWS["record"], ROWS["label"])]
judge = ROWS[ROWS["from lookup"] == 0]
best_unit = judge.loc[judge.groupby(["record", "label"])["p_yes"].idxmax()]
cases = {
    "kept, correct": best_unit[(best_unit["p_yes"] >= 0.5) & (best_unit["truth"] == 1)],
    "dropped, correct": best_unit[(best_unit["p_yes"] < 0.5) & (best_unit["truth"] == 0)],
    "kept, but the label is not in the reference": best_unit[(best_unit["p_yes"] >= 0.5) & (best_unit["truth"] == 0)],
    "dropped, but the label is in the reference": best_unit[(best_unit["p_yes"] < 0.5) & (best_unit["truth"] == 1)],
}
print({k: len(v) for k, v in cases.items()})
examples = pd.concat({k: v.sample(min(4, len(v)), random_state=1) for k, v in cases.items()})
examples["label"] = examples["label"].map(lambda l: d.short[d.labels.index(l)])
examples[["label", "field", "unit", "p_yes"]]

# %%
try:
    checker
except NameError:
    checker = Checker()
reasons = examples.groupby(level=0, group_keys=False).head(2)
for (case, idx), row in reasons.iterrows():
    print(f"[{case}] {row['label']} | P(yes)={row['p_yes']:.2f} | {row['unit'][:150]}")
    print("   model:", checker.explain(ROWS.loc[idx, "prompt"]), "\n")

# %% [markdown]
# **Reading — examples**
#
# * Against the original reference: 534 kept correctly, 441 dropped correctly, 195 kept but not in the reference, 44
#   dropped although in the reference. Section 2b reviews every one of these 239 disagreements.
# * **The model's written reasons are still not trustworthy** for audit: the decision uses the score, and the evidence
#   shown to a reviewer is the flagged sentence itself.

# %% [markdown]
# ## 6. Speed and cost

# %%
per_q = SECONDS / len(ROWS)
cost = pd.Series({
    "questions per 1,000 records": len(ROWS) / len(df) * 1000,
    "seconds per question (RTX 4070, 4-bit, batches of 4)": per_q,
    "minutes per 1,000 records": len(ROWS) / len(df) * 1000 * per_q / 60,
    "hours for 300k records, no cache": 300 * len(ROWS) / len(df) * 1000 * per_q / 3600,
    "hours for 300k records, cache of identical (unit, label) pairs": 300 * ROWS[["unit", "label"]].drop_duplicates().shape[0] / len(df) * 1000 * per_q / 3600,
})
cost.round(2)

# %% [markdown]
# **Reading — speed and cost**
#
# * v2 questions are longer (indicators added): **0.15 s per question** on the RTX 4070 → ≈ **9.4 minutes per 1,000 records**;
#   300k records ≈ 47 h without a cache, ≈ 27 h reusing answers for identical (unit, label) pairs.
# * On the laptop **CPU** the same model takes ≈ 24 s per question (measured on 16 questions) — ≈ 25 hours per 1,000
#   records, so a CPU-only server needs a smaller model (see conclusions).
#
# ## 7. Conclusions
#
# 1. **Highlighter + checker is the right design:** against the reviewed reference, precision **0.82**, recall **0.89**,
#    F1 **0.85**, vs 0.54 / 0.66 / 0.60 for the best whole-record model — with no training on our labels.
# 2. **Wanted lists already meet a 90% precision target (0.92); sanctions lists do not (0.71).**
# 3. 4 labels are at ≥ 90% precision (Fraud, Corruption, Theft, Money Laundering); PEP (0.59) and Terrorism (0.52) pull
#    the total down with systematic, fixable mistakes (candidates, list authorities, Taliban roles, victims).
# 4. Decisions come from the score; evidence is the flagged sentence (this model's written reasons are unreliable).
# 5. Cost: ≈ 9 min per 1,000 records on the laptop GPU; unusable on CPU (≈ 25 h per 1,000) → a CPU server needs a small
#    distilled model.
# 6. Next for the 90% target: PEP / Terrorism question fixes, a larger checker model, and a check on fresh records
#    (fixes made after seeing these errors make scores on these 900 records optimistic).

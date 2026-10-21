# %% [markdown]
# # 03 — The highlighter: evidence units + trigger words
#
# **Question:** can a simple, list-agnostic step find (almost) every place in a record where a text-layer label is
# evidenced, while sending only a few short pieces of text to the expensive decider (LLM / small model)?
#
# | Step | What | Status |
# |---|---|---|
# | 4a | Traditional ML (notebook 01) | done |
# | 4b | Encoders (notebook 02) — whole-record models fail on new lists through **precision** | done |
# | **B** | **Highlighter (this notebook): split records into units, flag candidates with trigger words** | **now** |
# | C | Decider: LLM reads each candidate unit with the label definition | next |
#
# ```
# record → L1 lookup (codes)
#        → L2 split into evidence units (lines, items, charge phrases, sentences; ID fields dropped)
#        → L3 trigger words flag candidate (unit, label) pairs        ← measured here: must not miss (recall)
#        → L4 decider says yes / no per (unit, label)                 ← Part C: must be right (precision)
# ```
#
# **Why units:** notebook 02 showed that models reading the whole record learn each list's style and add wrong labels
# on new lists. A unit (*"TAX EVASION"*, *"He personally took part in brutal beatings"*) carries no list style, and
# every final label can point to the sentence that justifies it.
#
# **How it is scored.** Our labels are per **record**, not per unit. So:
# * **candidate recall** = of the label instances the lookup misses (the text layer's job), the share whose record has
#   at least one unit flagged for that label. Goal ≈ 100%: whatever is not flagged can never be found later.
# * **flag precision** = of the (record, label) pairs flagged beyond the lookup, the share that are true labels.
#   Low is acceptable here — the decider removes wrong flags — but it sets the decider's workload.
# * **workload** = flagged (unit, label) questions per record, and the share of the record text the decider must read.
#
# Trigger words do not depend on which lists were in training, so their scores are the same on seen and unseen lists.
# The only part that learns from data (section 5) is tested on the unseen-list folds.
#
# **Scope: 15 labels.** Left out: the 3 lookup-only labels (Sanctions, Regulatory, Law Enforcement) and the
# **5 labels not covered yet** — RCA (1 example), State-Owned (4), Securities (5), Environmental (7), Jurisdiction Risk (0).
# Too few examples to build or measure anything; no lookup rule gives them either. They stay in the taxonomy as future
# labels; their trigger words are kept in `highlighter.py` but not used here. Section 2 shows them for information only.

# %%
import collections
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

pd.set_option("display.max_colwidth", 110, "display.width", 220, "display.precision", 3)

RESEARCH = Path.cwd().parent
sys.path.insert(0, str(RESEARCH / "scripts"))
from experimentKit import SEGMENT_OF, Data, label_tiers, wilson
from highlighter import EXTRA_TRIGGERS, TAXONOMY_TRIGGERS, compile_triggers, flag_units, fold, split_units

d = Data()
df, Y, L = d.df, d.Y, d.L
TIERS = label_tiers(d)
SCOPE = [i for i in range(len(d.labels)) if TIERS[i] != "lookup only" and i not in d.rare]
NOT_COVERED = [i for i in d.rare if Y[:, i].sum()]
NEED = Y & (1 - L)
in_scope = lambda lexicon, cols=SCOPE: {k: v for k, v in lexicon.items() if d.labels.index(k) in cols}
print(f"labels in scope ({len(SCOPE)}):", ", ".join(d.short[i] for i in SCOPE))
print("not covered yet:", ", ".join(f"{d.short[i]} ({Y[:, i].sum()})" for i in d.rare))
print(f"label instances the lookup misses on the {len(SCOPE)} labels: {int(NEED[:, SCOPE].sum())}")

# %% [markdown]
# ## 1. Evidence units
# Rules (in `scripts/highlighter.py`, `split_units`):
# * one record line = one field (`Programs:`, `Remarks:`, …); lines without a field name continue the previous field;
# * `Source list` and `Entity type` lines and ID fields (numbers, IDs, website, e-mail, phone, dates) are dropped;
# * split at `||` (items), `;` (parts and charge lists) and sentence ends; pieces without a 3-letter word are dropped.

# %%
UNITS = [split_units(t) for t in df["text"]]
n_units = np.array([len(u) for u in UNITS])
all_units = [u for us in UNITS for _, u in us]
print(f"units per record: median {np.median(n_units):.0f}, mean {n_units.mean():.1f}, max {n_units.max()}")
print(f"units in 900 records: {len(all_units):,}; distinct: {len(set(all_units)):,} ({len(set(all_units)) / len(all_units):.0%})")
seg_units = pd.DataFrame({"segment": df["segment"], "units": n_units}).groupby("segment")["units"].describe()[["count", "mean", "50%", "max"]]
seg_units

# %%
example = df.index[df["source"] == "INTERPOL-RED-NOTICES"][0]
print(df.loc[example, "text"], "\n\n→ units:")
for field, unit in UNITS[example]:
    print(f"  [{field}] {unit}")

# %% [markdown]
# **Reading — units**
#
# * A record splits into a median of **10 units** (mean 10.9; the longest UK sanctions narrative gives 147).
# * Only **34% of the 9,848 units are distinct**: standard phrases repeat ("Asset freeze", "Law Enforcement", charge names,
#   repeated measure texts). Repeats can be answered once and cached.
# * Business-register (DNFBP) records are almost all ID fields, so they give almost no units (median 0) — correct,
#   they carry no conduct text.
# * The Interpol example shows the ideal case: the charge *"Murder"* becomes its own unit.

# %% [markdown]
# ## 2. Trigger words
# Two lists, kept separate so the result stays honest:
#
# | List | Source | Written how |
# |---|---|---|
# | **v1 taxonomy** | the taxonomy's *Sample Indicators* (column E of `VV_Risk_Labels_Final_reviewed.xlsx`) reduced to their key words | from the taxonomy only, before looking at results |
# | **v2 conduct words** | general conduct words, official titles, family words and common Spanish / French / German / Portuguese charge words | from domain knowledge; written after reading some records, so its score is slightly optimistic |
#
# Matching: lower-case, accents removed (*Evasión* → *evasion*), hyphens as spaces, **whole words**; a trailing `*`
# allows word endings (`launder*` → *laundered, laundering*). `tax` matches *tax* but not *taxi*.

# %%
print({d.short[d.labels.index(k)]: len(v) for k, v in TAXONOMY_TRIGGERS.items()})
print({d.short[d.labels.index(k)]: len(v) for k, v in EXTRA_TRIGGERS.items()})
print("v1 Tax Crime:", TAXONOMY_TRIGGERS["Crime-Tax Crime"])
print("v2 Tax Crime:", EXTRA_TRIGGERS["Crime-Tax Crime"])

# %%
def run(triggers, rows=None):
    """Flag every record; returns record×label flags, questions per record, flagged characters per record, raw flags."""
    rows = range(len(df)) if rows is None else rows
    F, Q, C, raw = np.zeros_like(Y), np.zeros(len(df)), np.zeros(len(df)), {}
    for r in rows:
        fl = flag_units(UNITS[r], triggers)
        raw[r] = fl
        Q[r] = len(fl)
        C[r] = sum(len(UNITS[r][k][1]) for k in {k for k, _, _ in fl})
        for _, label, _ in fl:
            F[r, d.labels.index(label)] = 1
    return F, Q, C, raw

def summary(F, Q, C, cols=SCOPE):
    hit, need = int((NEED[:, cols] & F[:, cols]).sum()), int(NEED[:, cols].sum())
    added = F[:, cols] & (1 - L[:, cols])
    good = int((added & Y[:, cols]).sum())
    text_chars = df["text_noname"].str.len().values
    return {"candidate recall": hit / need, "found / needed": f"{hit} / {need}", "flag precision": good / max(int(added.sum()), 1),
            "questions per record": Q.mean(), "records with ≥1 flag": (Q > 0).mean(),
            "share of text to read": C.sum() / text_chars.sum()}

V1 = compile_triggers(in_scope(TAXONOMY_TRIGGERS))
V12 = compile_triggers(in_scope(TAXONOMY_TRIGGERS), in_scope(EXTRA_TRIGGERS))
RUNS = {"v1 taxonomy": run(V1), "v1 + v2 conduct words": run(V12)}
pd.DataFrame({k: summary(*v[:3]) for k, v in RUNS.items()}).T

# %%
def per_label(F):
    rows = []
    for i in SCOPE:
        need, hit = int(NEED[:, i].sum()), int((NEED[:, i] & F[:, i]).sum())
        lo, hi = wilson(hit, need)
        added = F[:, i] & (1 - L[:, i])
        rows.append({"label": d.short[i], "tier": TIERS[i], "needed": need, "found": hit,
                     "candidate recall": hit / need if need else np.nan, "95% CI": f"{lo:.2f}–{hi:.2f}" if need else "",
                     "flag precision": (added & Y[:, i]).sum() / max(added.sum(), 1)})
    return pd.DataFrame(rows).set_index("label")

pl = pd.concat({"v1": per_label(RUNS["v1 taxonomy"][0])[["found", "candidate recall"]],
                "v1 + v2": per_label(RUNS["v1 + v2 conduct words"][0])}, axis=1)
pl.to_csv(RESEARCH / "results" / "highlighter_per_label.csv")
pl.sort_values(("v1 + v2", "needed"), ascending=False)

# %%
F_rare = run(compile_triggers(in_scope(TAXONOMY_TRIGGERS, NOT_COVERED), in_scope(EXTRA_TRIGGERS, NOT_COVERED)))[0]
print("For information only — labels not covered yet (not in any score above):")
pd.DataFrame([{"label": d.short[i], "examples": int(Y[:, i].sum()), "found by v1 + v2": int((NEED[:, i] & F_rare[:, i]).sum())}
              for i in NOT_COVERED]).set_index("label")

# %% [markdown]
# **Reading — trigger words (15 labels)**
#
# * **v1 (taxonomy only)** finds **74.4%** of the label instances the lookup misses (454 of 610), and 74% of its flags are right.
#   The taxonomy indicators are written as examples ("murder allegation"), not as search words, so they miss many
#   phrasings — PEP only 34%, Organized Crime 35%, Judicial 66%.
# * **v1 + v2** finds **94.8%** (578 of 610). 12 of 15 labels reach ≥ 97%; Judicial, Fraud, Corruption, Human Rights,
#   Tax Crime, Terrorism, Proliferation and Cybercrime are at 100%.
# * The price is precision: 48% of flags are right (PEP 22%, Terrorism 27%). That is the decider's job — e.g.
#   *"Head of the Main Department for Combating Organised Crime and Corruption"* contains *corruption* but is not
#   Corruption (see the answer sheet in section 7).
# * **Weak spots:** PEP 76% (51 of 67), Organized Crime 84% (31 of 37), Violent Crime 94% (89 of 95).
# * Workload: **3.9 questions per record**; 27% of records get no flag at all; the decider reads about half of the text
#   (49% of characters), split into short pieces.
# * The 4 labels not covered yet are shown below for information only (not part of any score).

# %% [markdown]
# ## 3. By segment and by list
# Lists differ in style; a list-agnostic step should work everywhere. Segments with fewer than 5 needed instances are noisy.

# %%
F12, Q12, C12, RAW12 = RUNS["v1 + v2 conduct words"]
by = []
for key in ("segment", "source"):
    for g, idx in df.groupby(key).groups.items():
        idx = np.asarray(idx)
        need = int(NEED[np.ix_(idx, SCOPE)].sum())
        if need == 0:
            continue
        by.append({"level": key, "group": g, "records": len(idx), "needed": need,
                   "candidate recall": (NEED[np.ix_(idx, SCOPE)] & F12[np.ix_(idx, SCOPE)]).sum() / need,
                   "questions per record": Q12[idx].mean()})
by = pd.DataFrame(by).set_index(["level", "group"])
by.loc["segment"].sort_values("needed", ascending=False)

# %%
by.loc["source"].query("needed >= 5").sort_values("candidate recall")

# %% [markdown]
# **Reading — segments and lists**
#
# * **Wanted lists 96.3%** (377 needed), **sanctions & terror lists 91.8%** (219), regulatory 100% (14).
# * Weakest lists: EU financial sanctions 79% and EU travel ban 81% — almost all their misses are PEP (senior roles
#   written as titles, see section 4). UKSL is the most expensive list (13 questions per record: long narratives repeated
#   under each measure).
# * No list falls below 79%, so the step does not depend on one list's writing style.

# %% [markdown]
# ## 4. What is still missed (v1 + v2)
# Every label instance the lookup misses and no unit flags, with the record's units. These are the cases the
# decider can never see.

# %%
miss = []
for r in range(len(df)):
    for i in SCOPE:
        if NEED[r, i] and not F12[r, i]:
            miss.append({"source": df.loc[r, "source"], "missed label": d.short[i],
                         "units": " | ".join(u for _, u in UNITS[r])[:300]})
miss = pd.DataFrame(miss)
print(miss["missed label"].value_counts().to_dict())
miss.sort_values("missed label")

# %% [markdown]
# **Reading — the 32 misses**
#
# * **PEP (16)** — senior roles written as titles: *Commander of the Air Force, IRGC*, *Brigadier General*, *Chairman of
#   the State Security Committee*, *Member of the State Duma*, *Director, Office for Safeguarding National Security*.
#   This is a job-title problem, best solved by a dedicated official-title rule (rank + organisation words), not more crime words.
# * **Violent Crime (6)** — Interpol charges in other languages or legal wording: *VIOLACION* (rape), *trois meurtres*
#   (plural not matched), *Intentionally injures the person of another*, *Illegal deprivation of liberty*, *brutal beatings*.
# * **Organized Crime (6)** — *CRIMINAL ORGANIZED GROUP*, *committed by organized group*, *criminal armed group*,
#   *within the organization*: word order differs from the triggers.
# * **Others (4):** Theft *misappriopration* (a typo), Money Laundering *transferring criminal property*, Forgery
#   *knock-offs and fakes*, Trafficking *distribute amphetamine*.
# * These words are **not** added here: adding words after seeing the misses would inflate the score. They go into the
#   next lexicon version, which must be judged on the human-checked test set.

# %% [markdown]
# ## 5. Can words learned from data close the gap? (unseen-list folds)
# For each unseen-list fold, extra trigger words are learned **from the training lists only**: every word and word pair
# that occurs in ≥ 3 training records with the label, from ≥ 2 different lists, and in ≥ p of all training records
# that contain it. They are added to v1 + v2 and the held-out lists are flagged. Two strictness levels, p = 0.7 and 0.5.

# %%
WORD = re.compile(r"[a-z]{3,}")
def terms(units):
    words = set()
    for _, u in units:
        w = WORD.findall(fold(u))
        words |= set(w) | {a + " " + b for a, b in zip(w, w[1:])}
    return words

TERMS = [terms(u) for u in UNITS]
FOLDS, SRC = d.unseen_folds(), df["source"].values

def learn(train, p):
    extra = {}
    for i in SCOPE:
        n, pos, lists = collections.Counter(), collections.Counter(), collections.defaultdict(set)
        for r in train:
            for t in TERMS[r]:
                n[t] += 1
                if Y[r, i]:
                    pos[t] += 1
                    lists[t].add(SRC[r])
        found = [t for t in pos if pos[t] >= 3 and len(lists[t]) >= 2 and pos[t] / n[t] >= p]
        if found:
            extra[d.labels[i]] = found
    return extra

learned = {}
for p in (0.7, 0.5):
    F, Q, C = np.zeros_like(Y), np.zeros(len(df)), np.zeros(len(df))
    sizes = []
    for f in range(5):
        extra = learn(np.flatnonzero(FOLDS != f), p)
        sizes.append(sum(map(len, extra.values())))
        Ff, Qf, Cf, _ = run(compile_triggers(in_scope(TAXONOMY_TRIGGERS), in_scope(EXTRA_TRIGGERS), extra),
                            np.flatnonzero(FOLDS == f))
        te = FOLDS == f
        F[te], Q[te], C[te] = Ff[te], Qf[te], Cf[te]
    learned[f"+ v3 learned, p ≥ {p}"] = {**summary(F, Q, C), "learned words per fold (mean)": np.mean(sizes)}
    if p == 0.7:
        example_extra = extra
pd.DataFrame({"v1 + v2 conduct words": summary(F12, Q12, C12), **learned}).T

# %%
print("examples of learned Tax Crime / Theft words (last fold, p ≥ 0.7):")
for k in ("Crime-Tax Crime", "Crime-Theft & Property Crime"):
    print(" ", d.short[d.labels.index(k)], sorted(example_extra.get(k, []))[:25])

# %% [markdown]
# **Reading — learned words**
#
# * Learning words from the training lists adds a little recall on the held-out lists: 94.8% → **96.4%** (p ≥ 0.7) or
#   **97.7%** (p ≥ 0.5).
# * The cost is high: questions per record 3.9 → 6.5 → 10.9, flag precision 0.48 → 0.29 → 0.19, and ~930–1,360 learned
#   words per fold.
# * The learned words are mostly noise that co-occurs with a label in the training lists: for Theft *"alias"*, *"build"*,
#   *"having"*, *"the most"*, *"and also"* next to genuine ones (*robbery*, *embezzlement*, *receiving stolen*).
# * **Decision: keep v1 + v2 without learned words.** +1.6 points of recall do not justify +67% decider work and an
#   unreadable word list. The misses are better closed with targeted rules (section 4).

# %% [markdown]
# ## 6. Workload for the decider and scale-up
# The decider (Part C) can be asked once per **flagged unit** (with all its candidate labels) or once per
# **(unit, label)** pair. Identical units repeat across records (charge phrases, standard sentences), so their answer
# can be cached and reused.

# %%
flagged_units = [(UNITS[r][k][1], label) for r, fl in RAW12.items() for k, label, _ in fl]
unit_calls = sum(len({k for k, _, _ in fl}) for fl in RAW12.values())
work = pd.Series({
    "(unit, label) questions": len(flagged_units),
    "(unit, label) questions, distinct (cached)": len(set(flagged_units)),
    "flagged units (one call each, all candidate labels)": unit_calls,
    "flagged units, distinct (cached)": len({u for u, _ in flagged_units}),
    "records with no flag (no call at all)": int((Q12 == 0).sum()),
}, name="900 records")
per_1000 = (work / len(df) * 1000).round(0).rename("per 1,000 records")
pd.concat([work, per_1000], axis=1)

# %% [markdown]
# **Reading — workload**
#
# * Per 1,000 records: **3,888 (unit, label) questions**, or **2,872 unit calls** if the decider gets each flagged unit with
#   all its candidate labels at once. With a cache of identical units: 2,266 questions / **1,619 calls**.
# * 270 of every 1,000 records need no call at all (only the lookup).
# * On 300k records this is ≈ 860k unit calls without a cache (2.9 per record); the cache share will be higher at that
#   scale because standard phrases repeat more, but that was not measured here. Time = calls × seconds per call,
#   measured in Part C on the chosen model and machine.

# %% [markdown]
# ## 7. Answer sheet for spot-checking
# A stratified sample of flagged (unit, label) pairs — up to 8 per label, at most 2 per list per label — saved to
# `results/highlighter_answer_sheet.csv`. For each row, answer: **does this unit, on its own, show this label?**
# (yes / no / unsure). The record's labels are deliberately left out so they do not steer the answer. The answers
# become the first test set for the decider in Part C.

# %%
rng = np.random.default_rng(42)
rows = [{"record_key": df.loc[r, "record_key"], "source": df.loc[r, "source"], "field": UNITS[r][k][0],
         "unit": UNITS[r][k][1], "label": d.short[d.labels.index(label)], "trigger words": ", ".join(hits)}
        for r, fl in RAW12.items() for k, label, hits in fl if not L[r, d.labels.index(label)]]
pool = pd.DataFrame(rows).drop_duplicates(["unit", "label"])
pool = pool.sample(frac=1, random_state=42)
sheet = (pool.groupby(["label", "source"], group_keys=False).head(2)
             .groupby("label", group_keys=False).head(8)
             .sort_values(["label", "source"]).reset_index(drop=True))
sheet.insert(0, "sheet_id", range(1, len(sheet) + 1))
sheet["your answer (yes/no/unsure)"] = ""
SHEET = RESEARCH / "results" / "highlighter_answer_sheet.csv"
answered = SHEET.exists() and (pd.read_csv(SHEET, encoding="utf-8-sig", keep_default_na=False)
                                .iloc[:, -1].str.strip() != "").any()
if answered:
    print("answer sheet already has answers — not overwritten")
else:
    sheet.to_csv(SHEET, index=False, encoding="utf-8-sig")
print(f"{len(sheet)} rows, {sheet['label'].nunique()} labels")
sheet.head(12)

# %% [markdown]
# **Reading — answer sheet**
#
# * 120 flagged (unit, label) pairs covering the 15 labels; the first rows already show the decider's job:
#   *"Head of the Main Department for Combating Organised Crime and Corruption"* contains *corruption* but describes an
#   anti-corruption role, while *"Receipt of Kickbacks for Patient Referrals"* is real Corruption evidence.
# * The notebook never overwrites a sheet that already has answers.
#
# ## 8. Conclusions
#
# 1. **The highlighter works and is list-agnostic:** trigger words v1 + v2 put **94.8%** of the text layer's label
#    instances (15 labels) in front of the decider (wanted lists 96%, sanctions 92%, no list below 79%), at
#    **3.9 questions per record**.
# 2. The taxonomy indicators alone (v1) are not enough (74.4%); conduct words, titles and non-English charge words are needed.
# 3. Words learned from training lists add little recall (+1.6 points) for much more work — not used.
# 4. **Remaining gap (32 of 610):** mostly PEP job titles (16), foreign / legal-language violence charges (6) and
#    word-order variants of organized crime (6) → next lexicon version + an official-title rule, judged on the human-checked test set.
# 5. Flags are 48% precise, so the **decider (Part C)** must remove most flags reliably — that is where bank-grade
#    precision has to come from. Its workload: ~2,900 unit calls per 1,000 records (~1,600 with a cache).
# 6. **Not covered yet:** RCA, State-Owned, Securities, Environmental, Jurisdiction Risk — too few examples (0–7) and no
#    lookup rule; kept in the taxonomy as future labels, excluded from every score.
# 7. Next: fill the answer sheet (120 rows) → first test set for the decider.

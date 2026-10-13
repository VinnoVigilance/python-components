# PDG-84 — session report part 2, RTX 4070 laptop, 2026-10-12 / 13

Follows `TO_GPU_SESSION_2026-10-12.md` (decisions from the original laptop session) and part 1
(`SESSION_2026-10-12_RTX4070.md`). Nothing was committed on this laptop. Personal data from the server export stays in
the git-ignored `cache/`. Sections 1–10 cover the frozen checker **v4**; sections 11–18 cover **v5**, built from what
v4 got wrong and proved on a second, untouched set of records.

## 0. Summary in plain words

**v4 (sections 1–10).**

* We froze the checker (v4) **before** looking at any fresh record, then measured it on fresh records from the server
  export (18,431 rows) with labels from a **blind** labeller that never saw the checker's answers.
* On fresh data **one label passed the 90% proof: Cybercrime** (60 of 60). Most losses came from **structural
  mistakes**: programme names, a legal paragraph on one US list, judges and prosecutors read as defendants.
* The blind labels are reliable: a second blind labeller agreed with the first on **96.3%** of the disputed cases.

**v5 (sections 11–18).**

* **Your decisions (13 Oct):** local and regional officials (governors, mayors, vice-mayors, council members) **are PEP**
  (AMLC "substantial authority" test, applied to every country). Corruption and Tax stay in the review queue, and every
  label can be promoted later from logged reviewer decisions (section 17).
* **v5** = the highlighter skips standard text that is never evidence + clearer rule notes for 6 labels. Qwen3-8B
  answers Fraud and Judicial, Qwen3-4B the rest. Frozen on 13 Oct, then proved on **701 new records nobody had looked
  at**.
* **Proof on the untouched records:** precision rose sharply. Violent Crime 0.64 → **0.94**, Proliferation
  0.82 → **0.95**, Terrorism 0.89 → **0.94**, Judicial 0.87 → **0.92**, Cybercrime **0.97**. **Human Rights proves
  ≥ 90%** (59 of 60, lower bound 0.911) and auto-applies. The other labels are close but 60–100 checks cannot *prove*
  90% at these rates, so they stay in the review queue. **Fraud fell to 0.75**: my Qwen3-8B choice for Fraud did not
  hold on new data (section 14).
* **CPU student (e5-small) for the server:** about **30 ms per piece, ~1.6 hours per 100,000 records** on 4 threads.
  It keeps the teacher's quality and is a little stricter. The second student, **mDeBERTa, is ruled out**: its
  training collapsed and it needs 2.4 s per piece on the CPU.
* **This export is used up for proof:** for most crime labels no untouched "yes" records are left. From here the
  proof has to come from reviewer decisions in production (section 17).

## 1. What was agreed (recorded here as required)

From `TO_GPU_SESSION_2026-10-12.md` and the later approval (user + original laptop session):

1. Design accepted; **headline numbers only from fresh records**. The 900 are the dev set.
2. Auto-apply a label only where the **Wilson 95% lower bound ≥ 0.90 on fresh data**, per label and per list type;
   otherwise the review queue. The 5 rare labels are out of scope.
3. All reviewing done by Claude, **blind**: versions frozen first; a separate subagent sees only the record text, the
   taxonomy (columns A, C, E) and `data/labelling_rules_pdg84.md`; a second blind pass on disagreements; ~150 "both said
   no" pairs on the dev 900.
4. **Approved addition — precision:** per label, take about 60 fresh records where the system said "yes" (not from the
   900, not from training), blind-check them and apply the Wilson rule. Fewer than 60 → use all and state the number.
5. **Approved addition — recall:** keep the random 300. Report recall per label only where there are enough cases, with
   a 95% interval. For rare labels rely on the "both said no" check and the per-list onboarding check, and say so.
6. **Split plan for the export:** remove the 900 → hold out 4 lists → draw the random 300 → reserve a precision check
   pool (per label up to 60–100 checker-"yes" records, all of them if fewer than 60; excluded from training) → the rest
   is training. Counts reported **before training** (section 3).

## 2. What was frozen

`results/frozen_versions.json` (written before any fresh record was labelled or scored):

| Part | Frozen version |
|---|---|
| Checker | Qwen3-4B-Instruct-2507, 4-bit NF4, questions **v4**, cut-off 0.5, victim rule, batch 4 |
| Highlighter | trigger lists v1 + v2; PEP not asked about the `Programs` field |
| Labelling rules | `data/labelling_rules_pdg84.md` (6 rules, each with a positive and a negative example) |
| Blind instructions | `data/blind_labelling_instructions.md` |

Each file is pinned by a hash. No part was changed after fresh labelling started.

## 3. The fresh data and how it was split (counts before training)

Server export `cache/fresh/server_export_2026-10-12.csv` (SQL in `sql/teacherSample.sql`, EntityId tie-breaker):
**18,431 rows, 29 lists, 17,460 distinct people.** 971 people appear on more than one list, so **every split is by
person**: when one copy of a person is in a test set, their copies on other lists are set aside ("excluded copy") and
never used for training.

Scripts: `scripts/buildFreshSplit.py` → `cache/fresh/split.csv`; `scripts/buildCheckPool.py` → `split_final.csv`.

### 3.1 Records per split

| Split | Records | What it is for |
|---|---:|---|
| dev 900 overlap | 414 | people already in the 900 — removed |
| held-out lists | 2,985 | EU-MOST-WANTED (only 27 left), DFAT, AFDB-DEBARRED-ENTITIES, DILG-LOCAL-OFFICIALS — never trained on |
| random 300 | 300 | random draw weighted by list size → recall and real-world frequencies |
| check pool | 908 | checker-"yes" records reserved for the precision proof |
| excluded copy | 376 | other-list copies of people in a test set |
| **training** | **13,448** | teacher / student training |
| total | 18,431 | |

### 3.2 Records per list

| List | training | check pool | random 300 | held-out | dev overlap | excluded | total |
|---|---:|---:|---:|---:|---:|---:|---:|
| ADB-DEBARMENT-SUSPENSION | 991 | 0 | 9 | 0 | 0 | 0 | 1,000 |
| AFDB-DEBARRED-ENTITIES | 0 | 0 | 0 | 989 | 11 | 0 | 1,000 |
| ATC-DESIGNATED-TERRORIST-GROUPS | 23 | 0 | 0 | 0 | 5 | 0 | 28 |
| ATC-DESIGNATED-TERRORIST-INDIVIDUALS | 49 | 0 | 0 | 0 | 15 | 0 | 64 |
| CFTC-RED-LIST | 265 | 0 | 0 | 0 | 18 | 0 | 283 |
| CIA-WORLD-LEADERS-HISTORICAL | 866 | 48 | 84 | 0 | 2 | 0 | 1,000 |
| COMELEC-2025-SENATORS | 43 | 2 | 0 | 0 | 14 | 7 | 66 |
| DFAT | 0 | 0 | 0 | 974 | 26 | 0 | 1,000 |
| DILG-LOCAL-OFFICIALS | 0 | 0 | 0 | 995 | 5 | 0 | 1,000 |
| DMW-RECRUITMENT-AGENCIES | 982 | 0 | 13 | 0 | 5 | 0 | 1,000 |
| DNFBP | 948 | 0 | 49 | 0 | 3 | 0 | 1,000 |
| EU-DESIGNATED-VESSELS | 665 | 0 | 1 | 0 | 7 | 0 | 673 |
| EU-FINANCIAL-SANCTIONS | 841 | 8 | 15 | 0 | 14 | 122 | 1,000 |
| EU-MOST-WANTED | 0 | 0 | 0 | 27 | 21 | 0 | 48 |
| EU-TRAVEL-BAN | 810 | 16 | 11 | 0 | 20 | 143 | 1,000 |
| FBI-WANTED | 703 | 246 | 2 | 0 | 49 | 0 | 1,000 |
| GPPB-BLACKLISTED-ENTITIES | 130 | 0 | 0 | 0 | 20 | 0 | 150 |
| INTERPOL-RED-NOTICES | 811 | 169 | 20 | 0 | 0 | 0 | 1,000 |
| NCA-MOST-WANTED | 15 | 10 | 0 | 0 | 14 | 0 | 39 |
| OFAC-NON-SDN | 444 | 2 | 1 | 0 | 22 | 12 | 481 |
| OFAC-SDN | 843 | 18 | 49 | 0 | 10 | 80 | 1,000 |
| PH-HOUSE-MEMBERS | 282 | 24 | 0 | 0 | 18 | 0 | 324 |
| SECO-SANCTIONS | 875 | 81 | 23 | 0 | 9 | 12 | 1,000 |
| UKSL | 877 | 90 | 20 | 0 | 13 | 0 | 1,000 |
| UN-SANCTIONS | 891 | 72 | 0 | 0 | 37 | 0 | 1,000 |
| US-MARSHALS-PROFILED-FUGITIVES | 121 | 53 | 1 | 0 | 37 | 0 | 212 |
| US-STATE-TERRORIST-EXCLUSION | 13 | 44 | 0 | 0 | 2 | 0 | 59 |
| WORLD-BANK-DEBARRED | 960 | 25 | 2 | 0 | 13 | 0 | 1,000 |
| WORLD-BANK-OTHER-SANCTIONS | 0 | 0 | 0 | 0 | 4 | 0 | 4 |
| **total** | **13,448** | **908** | **300** | **2,985** | **414** | **376** | **18,431** |

### 3.3 Check pool per label (the precision proof)

Rule (`buildCheckPool.py`): **100** records if the label has ≥ 200 checker-"yes" records, **60** if it has 60–199,
**all** if fewer than 60. (We first tried 100 for every label; that left no training "yes" examples for Forgery,
Corruption and Tax, so the rule became 60 / 100.)

| Label | checker "yes" in the pool source | reserved | note |
|---|---:|---:|---|
| PEP | 2,055 | 100 | |
| Crime-Corruption | 32 | **32 (all)** | below 60 — cannot reach the proof unless 32 of 32 are right; review queue |
| Crime-Fraud | 198 | 60 | |
| Crime-Money Laundering | 117 | 60 | |
| Crime-Trafficking & Smuggling | 339 | 100 | |
| Crime-Cybercrime | 118 | 60 | |
| Crime-Violent Crime | 836 | 100 | |
| Crime-Theft & Property Crime | 149 | 60 | |
| Crime-Forgery & Counterfeiting | 86 | 60 | |
| Crime-Tax Crime | 8 | **8 (all)** | below 60 — cannot reach the proof; review queue |
| Crime-Organized Crime | 149 | 60 | |
| Terrorism / Terrorist Financing | 628 | 100 | |
| Proliferation / Proliferation Financing | 130 | 60 | |
| Human Rights / Atrocities | 200 | 100 | |
| Judicial / Court | 489 | 100 | |

One pool record can count for several labels, so 908 records cover 1,060 label checks.

### 3.4 Checker-"yes" answers per label and split

These are what the frozen checker said (or the lookup for the test splits), not verified truth.

| Label | training | check pool | random 300 | held-out | excluded |
|---|---:|---:|---:|---:|---:|
| PEP | 1,852 | 197 | 97 | 1,132 | 6 |
| Crime-Corruption | **0** | 32 | 0 | 0 | 0 |
| Crime-Fraud | 90 | 108 | 1 | 0 | 0 |
| Crime-Money Laundering | 24 | 93 | 0 | 0 | 0 |
| Crime-Trafficking & Smuggling | 184 | 154 | 4 | 15 | 1 |
| Crime-Cybercrime | 33 | 85 | 0 | 0 | 0 |
| Crime-Violent Crime | 590 | 246 | 11 | 25 | 0 |
| Crime-Theft & Property Crime | 63 | 86 | 3 | 4 | 0 |
| Crime-Forgery & Counterfeiting | 17 | 69 | 0 | 0 | 0 |
| Crime-Tax Crime | **0** | 8 | 0 | 1 | 0 |
| Crime-Organized Crime | 54 | 94 | 1 | 9 | 1 |
| Terrorism / Terrorist Financing | 467 | 159 | 8 | 54 | 2 |
| Proliferation / Proliferation Financing | 65 | 65 | 1 | 12 | 0 |
| Human Rights / Atrocities | 85 | 115 | 3 | 8 | 0 |
| Judicial / Court | 256 | 232 | 4 | 43 | 1 |

**Consequence for the student:** Corruption and Tax have **no "yes" training examples** left in this export, so the
student cannot learn them from it. They stay in the review queue anyway. Money Laundering (24), Forgery (17) and
Cybercrime (33) are thin; the 900 dev records can add examples for training only (never for scoring).

## 4. How the fresh checking was done

1. The frozen checker ran on all fresh records outside the dev overlap (`scripts/runFreshChecker.py`): 29,941 questions
   plus 8,826 victim questions in 5,384 s on the RTX 4070 (GPU peak 81 °C).
2. The blind labeller (separate subagents, 50 records per batch) labelled all 15 labels for: the random 300, ~50 records
   per held-out list (EU-MOST-WANTED: all 27), and all 908 check-pool records. Each "yes" needs an exact quote from the
   text; every batch was checked for 15 rows per record.
3. **Second blind pass:** every record where the checker and the first blind label disagreed (273 label decisions on
   246 records; DILG left out, see 8.1) was labelled again from scratch by new subagents. They did not know which label
   was disputed.
4. Scoring: `scripts/scoreFresh.py` → `results/fresh_precision_by_label.csv`, `fresh_recall_random300.csv`,
   `fresh_heldout_lists.csv`.

## 5. Results on the dev 900 (development numbers — NOT headline numbers)

Checker v4 against the reviewed reference (`data/reference_review.csv`), labels added beyond the lookup:

| | Precision | Recall |
|---|---:|---:|
| v2 | 0.815 | 0.891 |
| v3 | 0.831 | 0.879 |
| **v4 (frozen)** | **0.843** | **0.843** |
| v4, wanted lists | 0.943 | |
| v4, sanctions lists | 0.754 | |

* "Both said no" check on the dev 900: the blind labeller found 5 real labels in 1,050 (record, label) pairs where
  neither the checker nor the reference had one → **hidden miss rate 0.5% (95% CI 0.2–1.1%)**. Blind labels agreed with
  the reviewed reference on 99.3%.
* Per label, dev precision was high for most crime labels (Fraud 1.00, Money Laundering 0.95, Violent 0.94) and weak
  for PEP (0.60) and Terrorism (0.70).

## 6. Results on fresh records (headline numbers)

### 6.1 Precision per label — the auto-apply decision

Check pool, first blind pass. "95% low" = Wilson lower bound; auto-apply needs ≥ 0.90.

| Label | checked | correct | precision | 95% low | decision |
|---|---:|---:|---:|---:|---|
| **Crime-Cybercrime** | 60 | 60 | 1.000 | **0.940** | **auto-apply** |
| Human Rights / Atrocities | 100 | 93 | 0.930 | 0.863 | review queue |
| Crime-Fraud | 60 | 56 | 0.933 | 0.841 | review queue |
| Crime-Theft & Property Crime | 60 | 55 | 0.917 | 0.819 | review queue |
| PEP | 100 | 90 | 0.900 | 0.826 | review queue |
| Crime-Corruption | 32 (all) | 29 | 0.906 | 0.758 | review queue |
| Terrorism / Terrorist Financing | 100 | 89 | 0.890 | 0.814 | review queue |
| Judicial / Court | 100 | 87 | 0.870 | 0.790 | review queue |
| Crime-Money Laundering | 60 | 51 | 0.850 | 0.739 | review queue |
| Crime-Organized Crime | 60 | 49 | 0.817 | 0.701 | review queue |
| Proliferation / Proliferation Financing | 60 | 49 | 0.817 | 0.701 | review queue |
| Crime-Trafficking & Smuggling | 100 | 66 | 0.660 | 0.563 | review queue |
| Crime-Violent Crime | 100 | 64 | 0.640 | 0.542 | review queue |
| Crime-Tax Crime | 8 (all) | 5 | 0.625 | 0.306 | review queue |
| Crime-Forgery & Counterfeiting | 60 | 20 | 0.333 | 0.227 | review queue |

**Why "0.93 correct" is still "review queue":** with 100 checked answers and 93 right, the true rate could plausibly be
as low as 0.86. To *prove* 90% with 60 checks you need about 59–60 right; with 100 checks, about 96 right.

### 6.2 Precision per label and list type

Same check pool, split by the list's segment. Only cells with at least one checked record are shown; ✔ = passes.

| Label | Sanctions & terror lists | Wanted lists | Officials & candidates | Regulatory / debarment |
|---|---|---|---|---|
| PEP | 27/35 = 0.77 (low 0.61) | — | 63/65 = 0.97 (low **0.895**) | — |
| Corruption | 13/14 = 0.93 (0.69) | 11/12 = 0.92 (0.65) | — | 5/6 (0.44) |
| Fraud | — | 36/40 = 0.90 (0.77) | — | 20/20 = 1.00 (0.84) |
| Money Laundering | 1/4 | 50/56 = 0.89 (0.79) | — | — |
| Trafficking | 9/20 = 0.45 | 57/71 = 0.80 (0.70) | 0/9 | — |
| Cybercrime | 6/6 (0.61) | **54/54 = 1.00 (0.934) ✔** | — | — |
| Violent Crime | 10/22 = 0.45 | 54/78 = 0.69 (0.58) | — | — |
| Theft | 3/3 | 52/57 = 0.91 (0.81) | — | — |
| Forgery | **0/40** | 20/20 = 1.00 (0.84) | — | — |
| Tax Crime | 1/4 | 4/4 | — | — |
| Organized Crime | 12/13 = 0.92 (0.67) | 37/47 = 0.79 (0.65) | — | — |
| Terrorism | 41/52 = 0.79 (0.66) | **48/48 = 1.00 (0.926) ✔** | — | — |
| Proliferation | 45/49 = 0.92 (0.81) | 4/11 = 0.36 | — | — |
| Human Rights | **89/91 = 0.98 (0.923) ✔** | 4/9 = 0.44 | — | — |
| Judicial | 11/23 = 0.48 | **76/77 = 0.99 (0.930) ✔** | — | — |

These cells come from a random sample per label, so each cell is a random sample of that label within that list type;
small cells have wide intervals.

### 6.3 Second blind pass

| | pairs | second pass agrees with first |
|---|---:|---:|
| check pool (checker yes, blind no) | 197 | 189 (95.9%) |
| random 300 + held-out disagreements | 76 | 74 (97.4%) |
| **total** | **273** | **263 (96.3%)** |

Where the second pass disagreed it sided with the checker (10 cases: Trafficking 3, PEP, Fraud, Theft, Judicial,
Proliferation 1 each, …). Using the second pass to settle those cases moves PEP to 0.91, Fraud to 0.95, Theft to 0.93,
Trafficking to 0.69 — **no decision changes**. So the precision gaps are real checker errors, not labelling noise.

### 6.4 Recall on the random 300 (real-world mix)

The random 300 is weighted by list size, so it shows what a normal day looks like: mostly PEP and few crime labels.
"System" = lookup rules ∪ checker.

| Label | blind "yes" cases | system found | recall | 95% interval |
|---|---:|---:|---:|---|
| PEP | 103 | 92 | 0.893 | 0.819 – 0.939 |
| Terrorism / Terrorist Financing | 12 | 10 | 0.833 | 0.552 – 0.953 |
| all other 13 labels | 0–9 each | | too few cases | not reported |

Over all 15 labels in the random 300: system precision 0.796, recall 0.850 (147 blind "yes", 157 system "yes", 125 both).
**For the rare labels recall is not measured here**: we rely on the dev "both said no" check (0.5% hidden misses) and on
the per-list onboarding check, as agreed.

### 6.5 Held-out lists (lists the system never trained or tuned on)

~50 blind-labelled records per list.

| List | records | system yes | blind yes | both | precision | recall |
|---|---:|---:|---:|---:|---:|---:|
| EU-MOST-WANTED | 27 | 50 | 50 | 46 | 0.92 | 0.92 |
| DFAT | 50 | 22 | 30 | 19 | 0.86 | 0.63 |
| AFDB-DEBARRED-ENTITIES | 50 | 0 | 0 | 0 | — | — (correctly silent) |
| DILG-LOCAL-OFFICIALS | 50 | 50 | 0 | 0 | see 8.1 | |

## 7. Why the checker fails where it fails (fresh errors)

| Pattern | Example text | Labels hit | Fixable before the model? |
|---|---|---|---|
| Programme names asked as evidence | `ILLICIT-DRUGS-EO14059`, "ViCAP Homicides and Sexual Assaults" | Trafficking, Violent | **yes** — never ask about the `Programs` field (today only PEP skips it) |
| One legal boilerplate paragraph on US-STATE-TERRORIST-EXCLUSION | "…false documentation…", "hostage taking", "a violent attack on an internationally protected person" | Forgery (40 of 40 wrong), Violent | **yes** — skip that list's standard legal text |
| Officials who run courts or prosecutions | "head of Tehran Revolutionary Court", "assistant prosecutor at … District Court" | Judicial on sanctions lists | partly — rule exists; the 4B model ignores it |
| Person is a witness or linked to a victim | "may have critical information pertaining to … a child victim" | Trafficking | partly |
| Committee names of House members | committees on drugs etc. | Trafficking on PH-HOUSE-MEMBERS (0/9) | yes — committee field is not conduct |
| Interpol charge wording | "Participation in illegal armed formation" | Violent | no — needs the model / rules |

## 8. Decisions that were needed (all answered on 13 Oct — see section 11)

### 8.1 Are local elected officials PEP? (DILG-LOCAL-OFFICIALS)

The lookup rule labels every DILG record PEP (mayors, vice-mayors, governors, municipal / city / provincial council
members), and the 900 reference agrees. Our Rule 1 lists only national-level functions, so the blind labeller says
"not PEP" for a "Sangguniang Bayan member" (municipal council). This is a **definition** question, not a model error.
Options: (a) keep local officials as PEP (then add them to Rule 1); (b) PEP only for governors / mayors of large cities;
(c) not PEP. Until you decide, DILG is not counted in any score.

**Decided (13 Oct): PEP** — option (a), applied to every country (section 11).

### 8.2 Build v5 and prove it on untouched records?

v4 stays frozen and its numbers above stand. Proposal: v5 = v4 + the structural highlighter fixes from section 7
(no questions about `Programs` for any label; skip the US exclusion legal paragraph; skip committee lists). Prove v5 on a
**new check pool drawn from the training records**, which nobody has looked at yet, using the same blind protocol and
Wilson rule. Those records then leave training. No v5 number would come from data used to design v5.

**Decided: yes** — v5 was built, frozen and proved on 701 untouched records (sections 12–15).

### 8.3 Labels that cannot be proven with this export

Corruption (32 "yes" in total) and Tax Crime (8) cannot reach the proof; they stay in the review queue. More data from
lists with corruption / tax cases would be needed.

## 9. Second teacher (Granite 3.3 8B)

Granite 3.3 8B (4-bit, 5.5 GB VRAM) answered the same 3,430 frozen v4 dev questions in 1,367 s (`scripts/runTeacher.py`,
`scripts/compareTeachers.py` → `results/teachers_dev_qwen4b_vs_granite.csv`). Dev numbers only:

| Teacher | labels added | precision | recall |
|---|---:|---:|---:|
| Qwen3-4B (frozen v4) | 667 | 0.843 | 0.843 |
| Granite 3.3 8B | 1,035 | 0.610 | 0.946 |
| both say yes | 663 | 0.846 | 0.841 |

* Granite says "yes" far more often; raising its cut-off to 0.99 only lifts precision to 0.69.
* Its extra "yes" answers were never in the reviewed reference, so we blind-checked a random 40: **10 of 40 correct
  (25%, 95% CI 14–40%)**; the reference agreed with the blind labeller on 95% of them, so the comparison is fair.
* Almost every Qwen "yes" is also a Granite "yes", so "both agree" adds nothing.
* **Recommendation: drop Granite as a second teacher.** Qwen3-8B (size comparison) is still downloading.

## 9b. The CPU student (multilingual-e5-small)

`scripts/student.py` trains e5-small to copy the frozen checker's P(yes) per flagged piece, plus the victim question
as one extra "label", on the **training split only** (23,136 pieces; 2,461 held aside). 3 epochs, 304 s on the GPU.
`scripts/evalStudent.py` scores it exactly like the teacher, on the same fresh check pool and blind labels.

* Agreement with the teacher: 94.2% (held-aside training pieces), **93.3% on the fresh test splits**.
* **CPU, 4 threads: 34 ms per piece (~29 pieces/s)**; the export averages 2.1 pieces per record → about **70 ms per
  record**, i.e. 100,000 records in about 2 hours on 4 threads (the 4B LLM took 24 s per piece).
* Precision on the fresh check pool (records the student keeps of the teacher's "yes"):

| Label | student keeps | precision | 95% low | decision |
|---|---:|---:|---:|---|
| Cybercrime | 60 | 1.000 | 0.940 | **auto-apply** |
| Fraud | 50 | 0.960 | 0.865 | review queue |
| Theft | 39 | 0.949 | 0.831 | review queue |
| Corruption | 17 | 0.941 | 0.730 | review queue |
| Human Rights | 97 | 0.938 | 0.872 | review queue |
| Judicial | 86 | 0.919 | 0.841 | review queue |
| PEP | 99 | 0.899 | 0.824 | review queue |
| Terrorism | 94 | 0.894 | 0.815 | review queue |
| Money Laundering | 60 | 0.850 | 0.739 | review queue |
| Proliferation | 56 | 0.839 | 0.722 | review queue |
| Organized Crime | 57 | 0.807 | 0.687 | review queue |
| Trafficking | 99 | 0.667 | 0.569 | review queue |
| Violent Crime | 89 | 0.652 | 0.548 | review queue |
| Tax Crime | 8 | 0.625 | 0.306 | review queue |
| Forgery | 60 | 0.333 | 0.227 | review queue |

* Random 300 (lookup ∪ student): precision 0.801, recall 0.878 — the same as the teacher (0.796 / 0.850).
* **Plain words:** the small CPU model reproduces the teacher, including its mistakes. Getting more labels to 90% needs
  a better teacher signal (the v5 fixes in 8.2), not a bigger student. mDeBERTa-v3-base is not tried yet.

## 10. Files created or changed

| File | What |
|---|---|
| `scripts/buildFreshSplit.py` | person-level split of the export (dev overlap, held-out lists, random 300) |
| `scripts/runFreshChecker.py` | frozen checker over the fresh records, resumable |
| `scripts/buildCheckPool.py` | check pool (60 / 100 / all rule), final split, count tables |
| `scripts/scoreFresh.py` | precision per label and list type, recall on the random 300, held-out lists |
| `scripts/runTeacher.py` | second teacher on the dev 900 with the frozen v4 questions |
| `scripts/compareTeachers.py` | teacher-vs-teacher comparison on the dev 900 |
| `results/frozen_versions.json` | frozen versions and hashes |
| `results/fresh_*.csv` | all fresh count and score tables |
| `data/labelling_rules_pdg84.md`, `data/blind_labelling_instructions.md` | rules and blind protocol |
| `data/fresh_blind_labels.csv`, `data/dev_blind_labels.csv` | blind labels (random 300 + held-out; dev both-no) |
| `cache/blind/` | blind batches, outputs, second pass (`second_pass_compare.csv`) — personal data, git-ignored |

---

# Part 2b — checker v5 (13 Oct)

## 11. Your decisions

1. **PEP covers national and local officials.** The product serves the Philippines. The AMLC 2018 IRR defines a PEP
   by a prominent public position with "substantial authority over policy, operations, or the use or allocation of
   government-owned resources", without a national-only limit. Rule 1 now covers, in every country: heads and deputy
   heads of regions, provinces, states, cities and municipalities (governor, mayor, vice-mayor), and members or chairs
   of regional, provincial, city or municipal councils. Still not PEP: candidates, village / barangay officials,
   leaders of non-state armed groups. The DILG lookup (PEP) stands. Please ask compliance to confirm council members
   against the full AMLC text.
2. **Corruption and Tax:** review queue, plus promotion from reviewer decisions for every label (section 17).
3. **Quality before cost** — use the time and compute needed.

Because Rule 1 changed, all 552 blind-labelled records of the random 300, the held-out samples and the dev both-no
check were **labelled again from scratch** under the new rule (`data/fresh_blind_labels_v2.csv`,
`data/dev_blind_labels_v2.csv`). 61 of 7,155 answers changed: 56 are PEP no → yes (50 DILG council members, 6
regional officials). Only 5 other answers changed, so a fresh re-label reproduces the first one **99.9%** of the time.

## 12. What v5 changes (designed on the old check pool, which is now design data only)

**a) Highlighter: never ask about text that cannot be evidence** (`highlighter.is_boilerplate`, used only when the
list name is passed, so v4 stays reproducible):

| Skipped | Why | v4 errors it caused |
|---|---|---|
| `Programs` on sanctions lists and FBI-WANTED | programme names (`ILLICIT-DRUGS-EO14059`) and FBI categories ("Criminal Enterprise Investigations"); the lookup already reads them | Trafficking, Violent, Organized Crime |
| `Additional info` on US-STATE-TERRORIST-EXCLUSION | one legal paragraph repeated on every record ("false documentation", "hostage taking") | Forgery 40 of 40 wrong, Violent |
| `Additional info` on PH-HOUSE-MEMBERS | committee names | Trafficking 0 of 9 |
| "Rewards For Justice" text, EU "Council Decision…" and US "Executive Order…" titles | reward and law titles | Proliferation |

On INTERPOL the `Programs` field holds the **charges** (193 of 217 correct), so it is still asked. On the old pool the
skips lost **0 correct labels that the lookup does not already give**.

**b) Clearer rule notes** in the question (`checker.NOTES_V5`), taken from the taxonomy and the six rules:
PEP (the new Rule 1); Violent ("participation in an illegal armed formation" without violence, and the person's own
death, do not count); Trafficking (sale, distribution, possession *with intent to distribute* count; simple possession,
anti-drug officials and witnesses do not); Judicial (a named charge, flight to avoid prosecution, parole violation count;
only the judge or prosecutor of *other people's* cases is excluded); Money Laundering (laundering attributed only to
other people does not count); Human Rights (private crimes with no state or conflict context are Violent only).

A first wording of three notes made the 4B model say "no" to real cases ("Sell Cocaine", a plain "Money Laundering"
charge, "Unlawful Flight to Avoid Prosecution – Murder"). The notes were rewritten to state the positive cases first;
that fixed it. Both versions are kept (`checker_answers_v5a.csv`, `v5b.csv`).

**c) Teacher per label.** Qwen3-8B (non-thinking mode) answered the same v5 questions on the 1,335 design records:

| | Qwen3-4B precision | recall | Qwen3-8B precision | recall |
|---|---:|---:|---:|---:|
| all 15 labels | **0.896** | 0.826 | 0.873 | **0.872** |
| Fraud | 0.922 | 0.617 | 0.930 | **0.955** |
| Judicial | 0.907 | 0.932 | **0.954** | 0.925 |
| Theft | **0.900** | 0.779 | 0.699 | 0.894 |
| PEP | **0.686** | 0.673 | 0.593 | 0.654 |

Qwen3-4B stays the main teacher; Qwen3-8B answers **Fraud and Judicial**, where it was clearly better on design data.

**d) A bug of mine, found and fixed.** `runV5.py` kept a progress file from an earlier run and reused 3,587 old answers
to other labels as PEP answers. The PEP results looked plausible, but the bug showed up as a crash in the dev step.
The progress files are now tied to the model and labels and deleted after use; the answers were restored from backup
and PEP was asked again from scratch.

**Dev 900 (development numbers only):** v4 precision 0.843 / recall 0.843 → **v5 0.865 / 0.874**.

## 13. Freeze and the new, untouched check pool

`scripts/freezeV5.py` added a `v5` block to `results/frozen_versions.json` (teacher per label, cut-off, victim rule,
and file hashes of the code, rules, blind instructions and answer files) **before** the new pool was drawn.

`scripts/buildCheckPoolV5.py` drew the new pool from the **training records**, which nobody had inspected (the student
saw only teacher answers on them). Same rule as before (100 / 60 / all if fewer than 60), same person-level separation.
The pool records left training.

| Split after v5 | Records |
|---|---:|
| training | 12,727 |
| old check pool (v4 proof, now design data) | 908 |
| **v5 check pool** | **701** |
| random 300 / held-out lists / dev overlap / excluded copies | 300 / 2,985 / 414 / 396 |

| Label | v5 "yes" left in training | reserved |
|---|---:|---:|
| PEP | 1,859 | 100 |
| Violent Crime | 408 | 100 |
| Terrorism | 454 | 100 |
| Judicial | 285 | 100 |
| Fraud | 132 | 60 |
| Trafficking | 126 | 60 |
| Human Rights | 101 | 60 |
| Theft | 63 | 60 |
| Proliferation | 58 | 58 (all) |
| Organized Crime | 45 | 45 (all) |
| Cybercrime | 33 | 33 (all) |
| Money Laundering | 24 | 24 (all) |
| Forgery | 4 | 4 (all) |
| Corruption, Tax | 0 | 0 |

The old pool had already used many of the rare-label "yes" records, so several labels could only reserve what was left.
All 701 records were blind-labelled (15 batches, 15 rows per record each, rules v2).

## 14. v5 proof — precision on the untouched records (headline numbers)

`scripts/scoreV5.py` → `results/v5_precision_by_label.csv`. Rule: auto-apply only if the Wilson 95% lower bound ≥ 0.90.

| Label | checked | correct | v5 precision | 95% low | v4 precision (old pool) | decision |
|---|---:|---:|---:|---:|---:|---|
| **Human Rights / Atrocities** | 60 | 59 | **0.983** | **0.911** | 0.930 | **auto-apply** |
| Cybercrime | 33 (all) | 32 | 0.970 | 0.847 | 1.000 | review queue |
| Proliferation | 58 (all) | 55 | 0.948 | 0.859 | 0.817 | review queue |
| Violent Crime | 100 | 94 | 0.940 | 0.875 | 0.640 | review queue |
| Terrorism | 100 | 94 | 0.940 | 0.875 | 0.890 | review queue |
| Judicial / Court | 100 | 92 | 0.920 | 0.850 | 0.870 | review queue |
| Money Laundering | 24 (all) | 22 | 0.917 | 0.742 | 0.850 | review queue |
| Theft & Property | 60 | 54 | 0.900 | 0.799 | 0.917 | review queue |
| PEP | 100 | 88 | 0.880 | 0.802 | 0.900 | review queue |
| Organized Crime | 45 (all) | 39 | 0.867 | 0.738 | 0.817 | review queue |
| Trafficking | 60 | 47 | 0.783 | 0.664 | 0.660 | review queue |
| Fraud | 60 | 45 | 0.750 | 0.628 | 0.933 | review queue |
| Forgery | 4 (all) | 4 | 1.000 | 0.510 | 0.333 | review queue |
| Corruption, Tax | 0 | — | — | — | — | review queue |

Per label **and list type** (cells with ≥ 10 checks): **Human Rights on sanctions lists passes** (56 of 57, low
0.907). Close but not proven: Judicial on wanted lists 0.961 (low 0.890), Violent on wanted lists 0.952 (0.883),
Cybercrime on wanted lists 1.000 (25 of 25, low 0.867), Terrorism on wanted lists 0.972 (0.858).

**Why so few pass although most labels are at 0.92–0.97:** with 100 checks, proving 90% needs about 96 right; with 60,
about 59; with 33, it is impossible even at 33 of 33. The precision is close to the target, but the evidence is not
enough to *prove* it.

**What v5 still gets wrong** (the frozen v5 was not changed after this):

| Label | Error | Count |
|---|---|---:|
| Fraud | "Conspiracy to defraud the United States": the US legal term for obstructing government functions, not economic deception (one FBI case on 6 records) | 7 |
| Fraud | election fraud; scam centres run by a *group* rather than the listed person | 5 |
| PEP | Philippine Senate **candidates** still called PEP (the 4B model ignores "candidates are not PEP") | 4 of 12 |
| Trafficking | drug *possession* or an unspecified "narcotics charge" | 6 of 13 |

The Fraud result also shows the value of the untouched pool. Qwen3-8B looked better for Fraud on design data
(0.93 / recall 0.96), but on new records it reached only 0.75.

## 15. v5 recall and held-out lists (rules v2 labels)

* **Random 300** (lookup ∪ v5): precision 0.828, recall 0.828. PEP recall 0.879 (95% CI 0.80–0.93, 107 cases);
  Terrorism 0.833 (12 cases). The other labels have too few cases to report, as agreed.
* **Held-out lists**, never used for design or training:

| List | records | precision | recall |
|---|---:|---:|---:|
| DILG-LOCAL-OFFICIALS | 50 | 1.000 | 1.000 |
| EU-MOST-WANTED | 27 | 0.918 | 0.882 |
| DFAT | 50 | 0.955 | 0.656 |
| AFDB-DEBARRED-ENTITIES | 50 | no labels given, none expected | |

DFAT recall is the weak spot: Australian sanctions records are short and often state the regime only.

## 16. CPU students for the server (v5 teacher)

Trained on the training records plus the old check pool (design data, never v5 proof data), with teacher answers as
targets. Without the old pool, six labels had **no "yes" examples** left to learn from.

**multilingual-e5-small** (`cache/student_e5small_v5`):

* Agrees with the teacher on 94.3% of held-aside training pieces.
* **CPU, 4 threads: 30.6 ms per piece.** About half of all records get flagged pieces (3.8 per flagged record),
  so roughly **60 ms per record, ~1.6 hours per 100,000 records**.
* Precision on the untouched pool, where it keeps the teacher's "yes":

| Label | student keeps | precision | 95% low | correct dropped |
|---|---:|---:|---:|---:|
| Human Rights | 60 | 0.983 | 0.911 | 0 |
| Proliferation | 44 | 1.000 | 0.920 | 11 |
| Violent Crime | 89 | 0.966 | 0.906 | 8 |
| Terrorism | 90 | 0.967 | 0.907 | 7 |
| Judicial | 97 | 0.938 | 0.872 | 1 |
| PEP | 97 | 0.907 | 0.833 | 0 |

* **Caution:** the student also says "yes" on its own where the teacher said "no", about 6% of its "yes" answers, and
  only 37 of 85 of those (44%) are correct. Raising its cut-off removes few of them. Estimated production precision with
  both effects included: Cybercrime ~0.95, Violent ~0.94, Terrorism ~0.92, Human Rights ~0.92, Proliferation ~0.90,
  Judicial ~0.90, PEP ~0.89. These are **estimates, not a proof**; the review queue covers this.
* Random 300 (lookup ∪ student): precision 0.824, recall 0.868.

**mDeBERTa-v3-base: ruled out.** Its training collapsed: it answered "no" to everything, a known instability of
DeBERTa-v3 with bf16 mixed precision. More decisive, it needs **2.4 s per piece on 4 CPU threads**, about 80× slower
than e5-small, which is too slow for the server whatever its accuracy. A stable retrain was not worth the time.

## 17. Recommendation for production

1. **Lookup rules** as today (Sanctions / Regulatory / Law Enforcement + list rules such as DILG → PEP).
2. **Highlighter v5 + e5-small student** on the CPU server for the 15 text labels.
3. **Auto-apply only Human Rights** (proven on untouched records). Every other text-layer "yes" goes to the **review
   queue** with its flagged sentence as evidence.
4. **Reviewer decisions are logged per label and list type.** A label is promoted to auto-apply automatically once the
   logged decisions give a Wilson 95% lower bound ≥ 0.90; a label is demoted if it drops below. If precision stays at
   today's level, the reviewed answers needed are about **70 for Cybercrime, 140 for Proliferation, 200 each for Violent
   and Terrorism, and 830 for Judicial**. PEP at 0.88 would never qualify; it needs the v6 fixes first. The same rule
   covers Corruption, Tax and any label that is rare today.
5. **Before a v6:** "Conspiracy to defraud the United States" handled as not-Fraud; a list rule so that
   COMELEC candidates are never PEP; possession-only drug charges. A v6 needs its **own** untouched data, which means a
   new server export, since this one is used up for the rare labels.

## 18. Files added or changed in part 2b

| File | What |
|---|---|
| `scripts/highlighter.py` | `NAME_ONLY_PROGRAMS`, `BOILERPLATE_FIELDS`, `BOILERPLATE_UNIT`, `is_boilerplate`; `flag_units(..., source=None)` (v4 unchanged without `source`) |
| `scripts/checker.py` | `PROMPT_VERSION = "v5"`, `NOTES_V5`, `questions(..., version)`, `enable_thinking=False`, `self.model_id` |
| `scripts/runV5.py` | v5 questions; reuses unchanged answers; asks selected labels with a chosen model |
| `scripts/runTeacherV5.py`, `scripts/compareTeachersV5.py` | Qwen3-8B on the v5 design records and dev |
| `scripts/freezeV5.py`, `scripts/buildCheckPoolV5.py`, `scripts/scoreV5.py` | freeze, new pool, proof |
| `scripts/student.py` | v4 / v5 modes, key-based join, training splits per version, batch-size argument |
| `scripts/evalStudentV5.py` | student precision, recall and CPU speed on v5 data |
| `data/labelling_rules_pdg84.md` | Rule 1 (PEP) widened to sub-national functions, with a DILG example |
| `data/fresh_blind_labels_v2.csv`, `data/dev_blind_labels_v2.csv` | re-labels under the new Rule 1 |
| `results/frozen_versions.json` | `v5` block |
| `results/v5_*.csv`, `teachers_v5_design.csv`, `student_*_precision.csv` | v5 results |
| `requirements-research.txt` | + `sentencepiece==0.2.2`, `protobuf==7.36.2` (mDeBERTa tokenizer) |

## 19. Follow-up decisions (13 Oct, after the v5 proof)

1. **COMELEC-2025-SENATORS → PEP (user decision).** Every record carries the provider's
   `DatasetCategory: PEP (Politically Exposed Person)`. The old rule "candidates are not PEP" came from the original 900
   reference labels, not from the data. Changed in `scripts/lookupRules.py` (from `([], GUIDE, "Candidates are not
   PEP")` to `([PEP], DATA, ...)`) and in Rule 1. Effect on the v5 proof: the 4 COMELEC "errors" become correct, so PEP
   precision is **92 / 100 = 0.92** (95% low 0.850), still review queue.
2. **US-STATE-TERRORIST-EXCLUSION repeated text.** All 59 records carry the same `additionalInfo` item with
   `"Type": "Designation Criteria"`: the list's legal criteria, not facts about the organisation. It can be removed at
   the source; v5 already skips it.
3. **"Conspiracy to defraud the United States"** (18 U.S.C. § 371) means obstructing a government function and needs no
   money loss. Under the taxonomy's Fraud definition (deception for economic gain) it is not Fraud by itself; it stays
   Judicial. A v6 note or rule should say so.
4. **Minimum data to keep a category active.** Proving 90% needs at least **35** checked "yes" answers, all correct
   (53 with one wrong, 69 with two wrong). Proposed policy: a category is offered only if there are enough cases to
   check it. It becomes automatic only when proven; until then it goes to the review queue.

## 20. Second follow-up (13 Oct)

1. **Candidates are PEP** (user decision): "election candidates" was deleted from Rule 1's "Not PEP" list, and
   candidates for the listed functions are now PEP. The frozen v5 question still says candidates are not PEP, so a v6
   question must drop that. For COMELEC the list rule already gives PEP.
2. **Rule 7 — Fraud** added to `data/labelling_rules_pdg84.md`: deception for money or property counts.
   "Conspiracy to defraud the United States" with no money or property obtained, election fraud, and false statements
   with no economic benefit are not Fraud by themselves. Running a scam counts; building or leasing a site that others
   use for scams does not.
3. **US-STATE-TERRORIST-EXCLUSION cleaned by the user** (`cache/incoming/US-STATE-TERRORIST-EXCLUSION_final2.jsonl`:
   "Designation Criteria", "Designation Process" and "Effects of Designation" removed; only five list tags remain).
   v5 run **without** any skip rule: 236 questions (Terrorism and Violent only, all triggered by tags); **the model said
   no to every one** (mean P(yes) 0.00). Forgery and Proliferation are no longer asked at all. Terrorism still comes
   from the list rule.
4. **Final scores per label** (`scripts/finalScores.py` → `results/final_scores_by_label.csv`): whole system
   (rules + AI) on the dev 900, reviewed reference, with DILG and COMELEC = PEP. These are development numbers: the
   system was designed on the dev 900, so they are optimistic. The untouched-pool precision (section 14) is the honest
   number. Of the correct text-layer labels on the dev 900, **246 came from rules and 583 from the AI (~70%)**.
5. In the random 300 (a normal day), 185 of 300 records got **no** AI label, 105 got one, 10 got two or three.

## 21. Checker v6: general fixes (13 Oct)

**What changed (all general wording, no list names, so it should carry over to new lists):**

1. **Highlighter:** a trigger word is skipped when it names the *fight against* a conduct ("anti-corruption commission",
   "counter-terrorism unit", "drug enforcement administration"). Tested so that "foreign fighters", "dog fighting
   enterprise" and "cartel enforcement crew" are still flagged.
2. **Question:** one sentence added: "A word that is only part of a name (of a person, company, vessel, place or
   organisation) is not evidence" (the vessel *Chong Chon Gang* was being called Organized Crime).
3. **Label notes:** PEP (candidates for the listed functions are PEP; sub-national roles kept), and new notes for Fraud,
   Organized Crime, Corruption and Tax, each saying first what counts and then what does not.
4. **Lookup:** COMELEC-2025-SENATORS → PEP (section 19).

**Two bugs found and fixed during the run:**

* The first v6 run silently used the v5 questions (a text replacement in `questions()` did not apply). It was found
  because the answers were identical to v5, and fixed with a test that checks the v6 prompts contain the new notes. That
  run is kept as `checker_answers_v6_highlighter_only.csv`.
* The first Fraud note **lowered Fraud recall from 1.00 to 0.61**. Asked to explain, Qwen3-8B said "Conspiracy to Commit
  Wire Fraud" was excluded because of the "conspiracy to defraud the United States" sentence: it mixed up two different
  offences. Then it was too strict with short entries ("Fraudulent Practice" on the World Bank debarment list, "Fraud"
  as an Interpol charge). The final note says first that **a named fraud offence, or the word fraud/fraudulent naming the
  person's charge, sanction or debarment ground, always counts**, and only then lists the exclusions. Organized Crime got
  the same treatment ("leader of a gang", "asociación ilícita" count). Only these two labels were re-asked
  (`scripts/runV6Fix.py`); the first v6 answers are kept as `checker_answers_v6a.csv`.

**Results on the 1,178 blind-labelled design records** (v5 check pool, random 300, held-out samples; labels added by the
AI beyond the lookup; `scripts/compareVersions.py`). These records were used to design v6, so this shows direction,
not proof:

| Label | v5 P | v6 P | v5 R | v6 R |
|---|---|---|---|---|
| Fraud | 0.80 | **0.93** | 1.00 | 0.90 |
| Organized Crime | 0.88 | **0.98** | 0.72 | 0.74 |
| Judicial | 0.95 | 0.96 | 0.93 | 0.92 |
| Violent | 0.94 | 0.94 | 0.80 | 0.81 |
| Human Rights | 0.98 | 0.98 | 0.79 | 0.75 |
| PEP | 0.77 | 0.78 | 0.72 | 0.72 |
| all 15 | 0.89 | **0.91** | 0.85 | 0.84 |

PEP looks low here because these blind labels were made when candidates were *not* PEP; the model now (correctly, under
the new rule) calls them PEP and is scored wrong. Corruption and Tax have 0–1 true cases in these records and cannot be
measured. On the dev 900 (`results/final_scores_by_label_v6.csv`, whole system, also design data) the teacher's
precision went up for Organized Crime (0.83 → 0.98) and Tax (0.82 → 0.90) and stayed the same elsewhere.

**Student v6** (intfloat/multilingual-e5-small trained on the v6 answers, `cache/student_e5small_v6`): 94% agreement with
the teacher per piece. On the 1,178 records (none used in its training): precision 0.88, recall 0.84 (teacher 0.91 /
0.84). The student is weaker than its teacher mainly on Organized Crime (0.84 vs 0.98) and PEP (0.72 vs 0.78). On the
dev 900 it is better than the v5 student for Corruption (0.61 → 0.90), Organized Crime (0.68 → 0.78) and Fraud
(0.83 → 0.87); Tax stays weak (0.61).

**CPU speed with 10 threads:** 28.6 ms per piece and 1.9 pieces per record, so **about 1.5 hours per 100,000 records**
(a 12-second test; with 4 threads it was 30.6 ms, so extra threads barely help at this batch size).

**Frozen:** `results/frozen_versions.json` → `v6` (`scripts/freezeV6.py`). There are no untouched rare-label records left
in this export, so v6 can only be **proven on a new export**.

**Temperature:** the Qwen runs peaked at 78–79 °C (the pause guard stopped the GPU ~10 s at a time). The student
evaluation reached **82 °C for one 15-second reading** because the student code had no guard. Since then: the guard is in
`student.py` (training and GPU scoring), and the pause point is lowered to 76 °C, resuming at 68 °C.

## 22. Decisions needed

1. **Which model on the server.** (a) The e5-small student on CPU: ~1.5 h per 100k records, precision about 3 points
   lower than Qwen. (b) Qwen on a GPU server: better precision, needs a GPU. Recommendation: (a), because everything not
   proven goes to review anyway, and reviewer decisions will promote labels.
2. **What is applied automatically on day one.** The rule labels (Sanctions, Regulatory, Law Enforcement, list rules
   such as DILG/COMELEC → PEP) are certain. Human Rights was proven on v5 (95% low 0.911); its v6 question differs only
   by the "part of a name" sentence. Recommendation: auto-apply Human Rights as well, and send everything else to review
   as "suggested" with its evidence sentence.
3. **Proving v6:** a new, untouched export is needed. For each label to be proven, at least 35 checked "yes" answers
   (all correct), or 53 with one mistake. Rare labels (Corruption, Tax, Cybercrime, Forgery) need a larger export or
   come from reviewer decisions over time.
4. **PEP test labels** have to be redone under the new rule (candidates and local officials are PEP) before PEP can be
   proven.
5. **Number of labels.** 15 text labels are active. If the senior prefers fewer, the candidates to keep only in review
   (or merge) are those that cannot be measured yet: Corruption, Tax, Cybercrime, Forgery.

## 23. Files added or changed (v6)

| File | What |
|---|---|
| `scripts/highlighter.py` | `FIGHTING_BEFORE`, `FIGHTING_AFTER`, `fighting()`; `flag_units(..., version="v5")` (v6 skips "fight against" words) |
| `scripts/checker.py` | `PROMPT_VERSION = "v6"`, `QUESTION_V6`, `NOTES_V6` (PEP, Fraud, Organized Crime, Corruption, Tax), `questions()` picks notes and template by version; `gpu_temperature()`, `cool_down()`, pause at 76 °C / resume at 68 °C |
| `scripts/lookupRules.py` | COMELEC-2025-SENATORS → PEP |
| `scripts/runV6.py`, `scripts/runV6Fix.py` | v6 run; re-asking only reworded labels |
| `scripts/compareVersions.py` | v4 / v5 / v6 on the 1,178 design records |
| `scripts/student.py` | v6 mode; cooling pause in training and GPU scoring |
| `scripts/evalStudentV6.py` | student vs teacher on the design records, CPU speed with N threads |
| `scripts/finalScores.py` | version argument (`python finalScores.py v6`) |
| `scripts/freezeV6.py` | `v6` block in `results/frozen_versions.json` |
| `data/labelling_rules_pdg84.md` | Rules 1, 7–10 as above; general principle on "fighting the conduct" and names |
| `results/versions_design_comparison.csv`, `final_scores_by_label_v6.csv`, `student_e5small_v6_vs_teacher_v6.csv` | v6 results |

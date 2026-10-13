# To the RTX 4070 session — decisions and next steps (from the original laptop session, 2026-10-12)

Reply to `SESSION_2026-10-12_RTX4070.md`. Read this fully, save the decisions to your memory, then start with step 1.
Good work: the evidence-unit design is confirmed as the direction. The problems below are about **how much we can trust
the numbers**, not about the design.

---

## 0. Decisions from the user

| # | Topic | Decision |
|---|---|---|
| 1 | Design (rules → highlighter → checker → CPU student) | **Accepted as the direction.** Final numbers must be confirmed on fresh records |
| 2 | Precision target | **Per label and per list type**, not overall. Auto-apply a label only where its **lower 95% Wilson bound ≥ 0.90** on the fresh test set; all other labels go to the review queue |
| 3 | RCA, State-Owned, Securities, Environmental, Jurisdiction Risk | **Out of PDG-84 scope**, reported separately |
| 4 | Reference review | **No human time is available. Claude does all reviewing**, under the blind protocol in section 2 (this is the user's decision) |
| 5 | The 6 labelling rules in report section 6 | **Accepted** (Claude decides borderline rules — standing instruction). Write them to `data/labelling_rules_pdg84.md` with one positive and one negative example each, and apply them **consistently** everywhere (prompts, reviews, reference) |
| 6 | Production | CPU student + review queue on the server |
| 7 | Commits | none on this laptop; the folder comes back to the original laptop for commits |

---

## 1. Why the current numbers are optimistic (fix before any new headline number)

1. **Only disagreements were reviewed** (239) plus 40 "both yes". Reviewing only disagreements can only move the
   reference toward the checker; most of the 0.73 → 0.82 precision jump came from the 60 "add" decisions.
2. **"Both said no" was never checked**, so missed labels were never searched for → recall 0.89 is probably overstated.
3. Prompt v2, trigger words v2 and the review were all made **after seeing errors on the same 900 records**.

From now on the **900 records are the development set** (tune on them freely). **No headline number comes from them.**

---

## 2. Blind review protocol (because Claude is the only reviewer)

Claude is from a different model family than the Qwen teachers, so its mistakes are largely independent of theirs —
that is what makes Claude-only review acceptable. But it must be **blind**:

1. **Freeze first.** Before touching any fresh record, freeze prompt version, trigger list, labelling rules and the
   cut-off; write the versions into `results/frozen_versions.json` with the date.
2. **Label blind.** Use a **separate subagent** whose instructions allow reading **only** the fresh records' text,
   the taxonomy (`data/taxonomy/…xlsx`, columns C and E) and `data/labelling_rules_pdg84.md` — **never** checker or
   teacher outputs. For each record it writes, for each of the 15 labels, yes/no + the evidence sentence + a one-line reason,
   to `data/fresh_blind_labels.csv`. Save it before any comparison.
3. **Then compare.** Join with checker / student output. For disagreements, a **second blind subagent pass** (again
   without seeing which side said what) decides; record both passes and the final value.
4. **Also on the 900 (dev):** review a random sample of **~150 "both said no" (record, label) pairs**, weighted toward
   long-text records and records with zero flags, to estimate the hidden miss rate. Report it with a 95% interval.
5. Report review agreement (first vs second pass) as a quality indicator of the review itself.

---

## 3. Step A — rule fixes (on the dev set)

As proposed in your report: PEP candidates note; stop flagging PEP words in the `Programs:` line **only if the data
confirms** no real PEP evidence sits there; Terrorism boundary note from the taxonomy; one **role question per record**
(suspect / victim / other) so victims get no crime labels. Re-score on the dev 900 against the reviewed reference.

---

## 4. Second teacher: use a **different model family**

Qwen3-4B and Qwen3-8B share training data and make correlated mistakes, so their agreement is weak proof.
Keep Qwen3-4B (current teacher). Add **one non-Qwen model** that fits 8 GB VRAM in 4-bit. Candidates, in order:

| Model | Size | Licence / access | Notes |
|---|---|---|---|
| `ibm-granite/granite-3.3-8b-instruct` | 8B | Apache-2.0, not gated | multilingual (incl. ES/FR/DE/PT); good first choice |
| `microsoft/Phi-4-mini-instruct` | 3.8B | MIT, not gated | small and fast; weaker multilingual |
| `google/gemma-3-12b-it` / `gemma-3-4b-it` | 12B / 4B | gated (licence must be accepted on HF by the user) | 12B is tight in 8 GB |
| `meta-llama/Llama-3.1-8B-Instruct` | 8B | gated (licence must be accepted) | |

Check on Hugging Face that the model exists, its licence and 4-bit VRAM before downloading (these names are from the
original session's knowledge, not verified). Qwen3-8B can still be run as a size comparison, but it is **not** the
second teacher. Then:
* Same questions (frozen prompt), same P(yes) scoring (check the model's answer-token format first).
* Report on the dev 900: each teacher's precision/recall per label, their **agreement rate**, and precision **when both agree**.
* Student training data = pieces where both teachers agree; disagreements → review sample / left out. State in the
  report that this biases training toward easier cases (the review queue covers the hard middle).

---

## 5. Fresh data — use this SQL instead of `sql/teacherSample.sql`

Changes: tie-breaker on `EntityId` (null `SourceRecordId` gave equal hashes); `entity_id` returned so the 900 can be
removed locally.

```sql
WITH members AS (
    SELECT wm.full_payload AS p,
           wm.full_payload -> 'Sources' -> 0 ->> 'ListName' AS list_name,
           wm.full_payload -> 'Sources' -> 0 ->> 'SourceRecordId' AS source_record_id,
           wm.full_payload ->> 'EntityId' AS entity_id
    FROM core.watchlist_member wm
    WHERE wm.is_current
),
ranked AS (
    SELECT list_name, source_record_id, entity_id, p,
           row_number() OVER (PARTITION BY list_name
                              ORDER BY md5(coalesce(source_record_id, '') || '|' || coalesce(entity_id, '') || '|PDG-84')) AS rn,
           count(*) OVER (PARTITION BY list_name) AS list_size
    FROM members
)
SELECT list_name, source_record_id, entity_id, list_size, rn, p::text AS payload
FROM ranked
WHERE rn <= 1000
ORDER BY list_name, rn;
```

Before using it, check `full_payload ->> 'EntityId'` exists on the server (if not, use the table's primary key instead).
The user should check company policy on moving this personal data to the laptop; export as properly quoted CSV and
check the file size first.

After the export, **locally**:
1. **Remove the 900** (match on list + EntityId, i.e. the `record_key` `LIST:EntityId`).
2. **Hold out whole lists** from all teacher/student training — the new-list test (the user's top requirement):
   one wanted list (`EU-MOST-WANTED`), one sanctions list with narrative text (`DFAT`), one regulatory list
   (`AFDB-DEBARRED-ENTITIES`), one officials list (`DILG-LOCAL-OFFICIALS`). Adjust only if a list turns out empty.
3. **Fresh random test:** ~300 records drawn at random over **all members** of the remaining lists, weighted by
   `list_size` (real-world frequencies), never used for training.
4. Everything else = teacher data (cap per list as in the query).
5. Blind-label (section 2) the fresh 300 **and** ~50 records per held-out list.

---

## 6. Student (after 3–5)

As in your report: fine-tune a pre-trained small multilingual model on (piece + label definition → teacher yes/no),
one model for all 15 labels. Start with `intfloat/multilingual-e5-small` (fast on CPU); try `mDeBERTa-v3-base` if it is
clearly better. Measure:
* precision / recall per label on the **fresh 300** and on the **held-out lists**, against the blind labels;
* **CPU speed** with 4 threads (`torch.set_num_threads(4)`) in pieces per second → time for a full 300k backfill
  (≈ 1.2 M piece-label questions) and for a daily update;
* which labels pass the auto-apply rule (lower 95% bound ≥ 0.90), per list type; the rest → review queue size per day.

---

## 7. Report back

Write `SESSION_<date>_RTX4070_part2.md` in the same style: numbers only from files you name; the score views from
`experimentKit.py` (whole set / ML part / per label / per segment / per list type); **dev numbers and fresh numbers
in separate tables**; plain-language explanations for the user (top priority: plain answer → big picture → why with
alternatives → example → terms → takeaways). Keep `.xlsx` untouched, no commits, no AI attribution anywhere.

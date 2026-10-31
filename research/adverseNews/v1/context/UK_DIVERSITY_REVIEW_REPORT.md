# UK_GOV broader-export review, 2026-10-31

## Plain answer

The new CSV is useful. It contains 1,500 distinct UK_GOV logical articles, of which 1,443 are absent from the earlier 5,000-row UK export. It exposes document types that the old extract barely represented. A 21-article full-text GPT diagnostic across seven URL sections found **seven clear disagreements** with the current LSA 200 + RBF SVM at score threshold zero: three missed positives and four false alarms. This is evidence of specific failure patterns, **not** a production accuracy or F1 estimate. No training model was changed.

## What the export adds

| URL section | New export | Earlier UK export |
| --- | ---: | ---: |
| `/government/news` | 208 | 3,969 |
| `/government/publications` | 224 | 283 |
| `/government/case-studies` | 216 | 71 |
| `/government/speeches` | 205 | 605 |
| `/government/fatalities` | 87 | 18 |
| `/drug-device-alerts` | 353 | 37 |
| `/drug-safety-update` | 207 | 17 |

The new export contains 115 nonempty distinct `Publisher.Name` values (and 27 records with an empty name). This is **organisational variety within the single UK_GOV source**, not 115 independent publishers for the publisher-holdout metric. The largest organisation in this sample is the Medicines and Healthcare products Regulatory Agency, with 562 articles. The structured `Metadata.Tags`, `Topics`, `Keywords`, `Categories`, and `Language` fields are empty in all 1,500 records. `Content.Title` and `Content.BodyText` are present in all 1,500. Of the bodies, 103 are shorter than 300 characters, 1,263 are 300–10,000, and 134 exceed 10,000.

**Sampling limitation:** the SQL that generated this export used the first two URL path parts for `url_section`. For paths such as `/drug-device-alerts/specific-article`, that makes each article its own section, rather than grouping the alerts together. The `LIMIT 1500` then yielded a medicine-heavy sample. It is still useful for diversity and finding cases, but its section counts and label mix cannot be read as proportions of the 143,757-article database. Do not use this sample to estimate prevalence or production accuracy.

## Blind full-text diagnostic

The 21-article packet was frozen before GPT review, with three articles selected from each of the seven normalised URL sections. The selection removed all 57 articles from the older raw UK extract, excluded exact original-corpus text duplicates and previous packets, and used only bodies 300–10,000 characters long. The resulting candidate pool had 1,217 articles. `blind.jsonl`, `selection-internal.csv`, `gpt-review.jsonl`, and `manifest.json` preserve provenance. All 21 titles and full saved bodies were read before the model scores were revealed. All five positive evidence quotes occur exactly in their saved bodies. GPT opinions: five certain positive, 15 certain negative, one uncertain negative.

| Model error type under GPT opinion | Article | Why it matters |
| --- | --- | --- |
| Missed positive | `db:85480` | A career profile contains a substantive ongoing investigation of prohibited pharmaceutical agreements. |
| Missed positive | `db:67039` | A call for private divestment also reports actual UK sanctions on Russia in the body. |
| Missed positive | `db:38261` | A diplomatic speech reports a specific recent attack on Ukrainian civilian infrastructure. |
| False alarm | `db:71198` | Closed school financial-improvement notice, without a specific qualifying breach, under the product owner's confirmed rule. |
| False alarm | `db:94701` | Precautionary product recall based on a possible specification issue, without reported misconduct. |
| False alarm | `db:176090` | Military obituary provides no cause of death or criminal attack. |
| False alarm | `db:112814` | Accidental publication of a school test and operational inquiry, without qualifying wrongdoing. |

The remaining 13 certain GPT opinions agree with the model; one peace-speech case (`db:45121`) is uncertain and was not counted as an error. Scoring used scikit-learn 1.9.1 to load a bundle fitted under 1.8.0, which emitted a compatibility warning. Several disagreement scores are close to zero, so their exact sign should be rechecked in the original runtime before relying on the error count for a release decision.

## Decision

This packet gives us better training targets than the earlier keyword-only packet, which found no certain disagreements. The highest-value next experiment is a **small training addition built around these seven patterns**, plus the six separately confirmed UK notice-label corrections. Keep this 21-article packet as a diagnostic holdout; select separate, non-overlapping articles for training, review their full text, and refit an experimental copy of the fast model. Compare both models on this frozen packet and on the older 150-article reserved model-opinion packet. Do not claim production accuracy from either GPT-reviewed packet. Preserve the original model if the experiment does not improve the relevant errors without creating new ones.

For a proper production gate, independently labeled articles from actual new arrivals and genuinely new publishers are still needed. More organisation names inside UK_GOV do not substitute for new source publishers.

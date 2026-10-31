# UK_GOV random shadow comparison

## Plain result

The 117 usable articles from the user's random UK_GOV export were read in full
and given GPT opinions **before** either model was scored. The original model
and the experimental model each agreed with 106 of the 117 GPT opinions
(90.6% agreement; F1 0.645). They each missed 8 of the 18 GPT-positive articles.
The experimental update changed just two decisions: it removed one false alarm
on a school financial improvement notice and added one false alarm on an
uncertain historical-war article. This random packet does **not** support
replacing the original production candidate with the experimental update.

These numbers measure agreement with one GPT review under the current policy,
not independently established production accuracy. The 117 articles contain
only 18 GPT positives, and 10 GPT opinions were flagged uncertain. The packet
is useful as a source-random UK check, but its 60/60 time stratification and
small size do not establish population-wide prevalence or stable performance
across all publishers or content types.

## Frozen inputs and sequence

1. The 120 source-random database rows were frozen; three overlapping an older
   UK export were excluded before labeling. The 117 retained articles include
   58 all-time and 59 recent articles. No model score influenced this selection.
2. `blind.jsonl` contained only titles and full bodies. Every article received
   a GPT 0/1 opinion and a reason in `gpt-review.jsonl`; each of the 18 positive
   opinions includes a quote verified as an exact substring of the body.
3. Only after all 117 reviews were frozen were both joblib bundles loaded and
   scored on the same title plus body text at decision threshold zero.
4. `comparison.csv` contains article-level GPT opinions, metadata, model scores,
   and predictions. `metrics.csv` contains group counts. The manifests retain
   hashes of the packet, review, models, and outputs.

The original bundle was trained under scikit-learn 1.8, whereas the experimental
bundle was trained under 1.9.1. Both were scored in the same 1.9.1 environment.
This is a cross-version loading limitation to check before operational use.

## Results

| Packet | Model | GPT positive / n | Correct / n | Agreement | Precision | Recall | F1 | False alarms | Misses |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All opinions | Original | 18 / 117 | 106 / 117 | 90.6% | 76.9% | 55.6% | 64.5% | 3 | 8 |
| All opinions | Experimental | 18 / 117 | 106 / 117 | 90.6% | 76.9% | 55.6% | 64.5% | 3 | 8 |
| Certain opinions only | Original | 16 / 107 | 100 / 107 | 93.5% | 90.9% | 62.5% | 74.1% | 1 | 6 |
| Certain opinions only | Experimental | 16 / 107 | 101 / 107 | 94.4% | 100% | 62.5% | 76.9% | 0 | 6 |
| All-time arm | Original | 7 / 58 | 55 / 58 | 94.8% | 83.3% | 71.4% | 76.9% | 1 | 2 |
| All-time arm | Experimental | 7 / 58 | 56 / 58 | 96.6% | 100% | 71.4% | 83.3% | 0 | 2 |
| Recent arm | Original | 11 / 59 | 51 / 59 | 86.4% | 71.4% | 45.5% | 55.6% | 2 | 6 |
| Recent arm | Experimental | 11 / 59 | 50 / 59 | 84.7% | 62.5% | 45.5% | 52.6% | 3 | 6 |

"Agreement" here is the usual accuracy formula, but its reference is a GPT
opinion, so it must not be called verified production accuracy. F1 combines
precision (how often model positives matched GPT positives) and recall (how
many GPT positives the model found). The recent arm is especially concerning:
both models found only five of eleven GPT-positive articles. Do not treat this
small split as a precise trend estimate; it does identify cases to study.

## What the misses show

The six missed articles with *certain* GPT-positive opinions are:

| Article ID | Concrete fact missed | Likely failure mode |
| --- | --- | --- |
| `db:36440` | Illegal medicine listings removed from eBay | Cooperative platform and public-health wording dilute illegal-sales signal. |
| `db:41552` | Named triple murder recounted in firearms guidance | Crime is embedded in a policy announcement. |
| `db:36478` | Alleged Iranian attacks on civilian infrastructure and deaths | Diplomatic wording obscures violent event. |
| `db:178115` | Reported DPRK sanctions evasion and exploited overseas labour | Long geopolitical statement, several qualifying facts. |
| `db:35468` | Ambulances destroyed in an arson attack | Specific crime appears near the end of a long NHS policy article. |
| `db:134129` | UN inquiry testimony of torture in DPRK prison camps | Human-rights abuse expressed in formal diplomatic language. |

Two more misses (`db:176717`, `db:37199`) depend on policy boundaries that GPT
flagged uncertain: nuclear-safeguards noncompliance and state missile attacks.
They should not drive a training decision until the binary policy is clarified.

The school notice correction worked as intended on `db:119491`: original
predicted adverse and experimental predicted non-adverse, matching the user's
confirmed rule. The experimental model also crossed the threshold on
`db:177685`, a historical soldier identification article with uncertain GPT
non-adverse opinion. The score moved only from -0.001 to +0.003, so this case
is especially sensitive to a tiny score shift. The other two false alarms
against GPT are also uncertain opinions.

## Decision and next pipeline

1. **Keep the original classifier as the current candidate.** The small
   experimental update is retained as a research artifact. It fixed the known
   notice error, but did not improve finding adverse articles in this random
   packet. Do not promote it on the earlier targeted 20-case gain alone.
2. **Keep new publishers and low-confidence cases in a full-text review queue.**
   The current model missed six certain adverse UK articles here. An automatic
   negative prediction is not enough to clear an article from a new publisher.
3. **Build a separate UK training batch around the observed failure patterns.**
   Select articles from the unused UK export, removing exact and near matches
   to every frozen evaluation packet. Cover crime mentioned inside otherwise
   neutral policy text, medicine-market enforcement, sanctions-evasion and
   forced-labour reports, and formal human-rights statements. Include ordinary
   non-adverse articles from the same sections so the model learns the
   difference. Read the full text and record exact evidence for positive GPT
   opinions. Hold uncertain policy cases out of training.
4. **Refit and compare without changing this packet.** Use the same text
   preparation, architecture, and threshold. The 117 articles stay a diagnostic
   holdout; once their errors have guided training, improvement on this packet
   is no longer an unbiased test of the new model. Obtain a fresh, independently
   sampled UK batch after retraining, and review it blind before scoring.
5. **For a production accuracy claim, get independent human adjudication.**
   GPT can make a useful and fast review queue but cannot validate itself.
   In particular, review the 10 uncertain cases and a fresh sample of both
   model-positive and model-negative articles with the agreed article policy.

More UK_GOV rows by themselves will not address the demonstrated misses. The
next useful work is diversity in *labeled examples of the missed situations*,
followed by a fresh random check. There is no evidence here to justify the
slower teacher-student path yet.

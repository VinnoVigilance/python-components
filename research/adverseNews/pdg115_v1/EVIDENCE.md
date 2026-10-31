# Updated UK_GOV model: final research decision

This is the frozen research decision for `pdg115_v1`. The bundle called
`candidate-bundle.joblib` below is stored here as `model/model.joblib` with
the same SHA-256. Paths under `results/` describe the original local research
workspace; the curated training and evaluation files live in this folder.

## Plain result

The new UK_GOV classifier is trained and packaged. It uses the same fast
LSA 200 + SVM design and score threshold as the original. The training data
now contain 5,044 articles: the original 5,000 with six confirmed school
notice corrections, 18 earlier certain GPT-labeled UK articles, and 26 new
certain GPT-labeled UK articles (13 adverse, 13 non-adverse). Ten ambiguous
new training candidates were excluded. The original model and original data
files were not overwritten.

The updated bundle is `candidate-bundle.joblib`, SHA-256
`eb5829ac409f02d79bd161d973574f82141eaf7bde512da2164987c487df05da`.
The research review router now uses this updated bundle by default. It still
sets `auto_clear=false` on every article and sends UK_GOV, PTV, and unfamiliar
`source_name` values to full-text review. It does not maintain a separate
registry of publisher organisations within a known source.

**This closes the requested research/training iteration, not a production
release.** The new bundle is a usable shadow/pilot model. It has not earned a
claim of production accuracy or permission to clear articles automatically.

## How the data were handled

The earlier 1,500-row UK_GOV export was reused. Old UK raw-export IDs, prior
training and diagnostic packets, the 117-article random packet, and unusable
or very long texts were excluded. From the remaining 1,205 candidates, 35
articles across seven URL sections were reserved by deterministic hash **before**
any model score or new label was examined. A separate 36-article training
packet targeted likely missed topics and included matched section controls.
The two packets do not share article IDs or exact normalized article bodies.

Every article in both packets was read in full by GPT under policy v0.2.
Positive opinions have exact supporting text in the article. The 35 reserved
reviews were frozen before comparing model outputs. The packet, review, bundle,
and comparison hashes are in the adjacent and source manifests. These are GPT
opinions, not independent human adjudication.

## Comparison at threshold zero

| Packet | Model | Correct | GPT-positive found | False alarms | Misses | F1 vs GPT |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Fresh reserved 35 | Original | 30/35 | 4/8 | 1 | 4 | 0.615 |
| Fresh reserved 35 | Earlier experimental | 30/35 | 4/8 | 1 | 4 | 0.615 |
| Fresh reserved 35 | Updated | **31/35** | **4/8** | **0** | **4** | **0.667** |
| Earlier random 117 | Original | 106/117 | 10/18 | 3 | 8 | 0.645 |
| Earlier random 117 | Updated | 109/117 | 12/18 | 2 | 6 | 0.750 |
| Older reserved 150 | Original | 133/150 | 63/73 | 7 | 10 | 0.881 |
| Older reserved 150 | Updated | 134/150 | 63/73 | 6 | 10 | 0.887 |

The earlier random 117 results are **diagnostic**, because their misses guided
the choice of training topics. They do not provide an unbiased estimate of
the training change's gain. The fresh 35 are independently reserved from the
1,500-row source export, but were balanced across URL sections rather than
sampled in live production proportions. Eight GPT positives are too few for a
precise recall estimate. The older 150 labels were produced by Astra-medium;
they are also model opinions. On the 33 certain fresh opinions, original and
updated each matched 29/33, with identical four misses and no false alarms.

The updated model's fresh improvement is only removal of one **uncertain**
false alarm. It did not improve adverse recall in the fresh packet. The four
certain missed cases concern counterfeit medical devices, prolonged political
detention, child abuse buried in a general press briefing, and a formal
medical-device certification suspension. These cases remain in the reserved
packet and must never be added to training for this model version.

### Why several F1 figures appear

The earlier `0.800` F1 was measured on **20 certain articles selected for a
targeted UK diagnostic** for the previous 5,018-row model. It was not the
new model's general UK score. The updated 5,044-row model has F1 `0.815` on
the 107 certain opinions in the earlier 117-article UK random diagnostic,
`0.750` on all 117 including uncertain opinions, and `0.667` on the **new,
untouched** 35-article UK section-balanced packet. These packets contain
different articles and different proportions of adverse articles, so their
F1 values are not a time series showing that one model fell from 0.80 to
0.67. The fresh 35-article result is also statistically fragile: only eight
were GPT-positive.

For a broader, disjoint held-data view, the new 35 can be pooled with the
older 150 reserved articles. That gives 185 articles, 81 GPT/Astra positives,
and for the updated model **165/185 agreement (89.2% accuracy), 67/73
precision (91.8%), 67/81 recall (82.7%), and F1 0.870**. The original model
on those same 185 has 163/185 agreement (88.1%) and F1 0.859, with the same
67/81 recall. This pooled figure is useful for checking broad regression;
it is **not a UK_GOV-specific or production-wide F1**, because the older
150 mix multiple sources and were reviewed by Astra-medium, while the new
35 are UK_GOV and were reviewed by GPT-6. Both are model opinions, not
independent human truth.

## Operational recommendation

Use the updated bundle in a **review-assisted pilot or shadow comparison**
only. Keep the original bundle available for rollback. UK_GOV and PTV, and any
new publisher, continue to require full-text review regardless of a negative
model score. The score helps order the queue; it is not a clearance decision.
New publisher organisations inside a known source require an upstream
publisher registry or full-review policy; UK_GOV is covered by the latter.

For a production release that automatically clears or labels articles, the
remaining gate is a new random sample from live arrivals with independent
human adjudication and an explicit acceptable miss rate. A larger and more
diverse UK training set may help, but this iteration shows that merely adding
26 carefully reviewed examples did **not** fix the four fresh adverse misses.
The next model experiment should target those failure types using a separate
training pool and another untouched evaluation packet. A slower teacher–student
system is not justified by these results yet.

## Reproducibility and limits

- Training/review packets and GPT opinions:
  `results/uk-followup-v1/`.
- Packaged model, score comparison, and manifest:
  `results/uk-followup-candidate-v1/`.
- Entry scripts: `prepare_uk_followup.py`, `fit_uk_followup_candidate.py`,
  `summarize_uk_followup_candidate.py`, and `triage_new_publishers.py`.
- Runtime: Python environment `vv-env`, scikit-learn 1.9.1. The original
  bundle was serialized under 1.8.0, so cross-version comparison carries the
  usual model-persistence compatibility caveat.
- No software tests were added or run, following project instructions.
- No deployment or external publication occurred.

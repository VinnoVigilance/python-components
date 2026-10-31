# PDG-115: classical model screen on the 5,000-row label pool

## Plain result

The leading family is **TF-IDF followed by latent semantic analysis (LSA) and an
RBF support vector machine (SVM)**. LSA compresses word and character features
into a small numeric space; the RBF kernel then learns a nonlinear boundary.
The 200-component, C=3 version had the highest mean F1 when a publisher was
entirely withheld (0.811 across six publishers). The 100-component, C=3 version
was close (0.802) and scored slightly higher on 2025–2026 news (0.937 versus
0.932). These are scores against proposed labels, not verified real-world
accuracy. Both remain finalists.

The best sparse linear candidate, hybrid word-plus-character TF-IDF with a
linear SVM, had the highest three-fold group CV F1 (0.912) but lower mean
publisher-held-out F1 (0.763). This repeats the transfer weakness seen in the
original 500-row experiment. Higher within-source scores alone are not enough
to select the production model.

## Data and evaluation flow

- Input: the completed 5,000-row dataset, containing 2,273 positive and 2,727
  negative targets. Models read only article title and body. IDs, publisher,
  dates, and label origin were excluded from their features.
- Older pool: 4,053 articles dated before 2025, with 1,701 positive targets.
  A three-fold **group cross-validation** (CV) fit each text transformer inside
  the training portion of its fold. A prepared duplicate group never crossed a
  fold. These groups do not yet cover every possible same-case pair.
- Recent-news holdout: train on all 4,053 older articles and predict 947 articles
  dated 2025–2026, of which 572 are positive. This is a chronological stress
  test. Its higher positive share and source mix make its F1 incomparable to
  group CV as a simple improvement claim.
- Publisher holdout: six publishers with at least 40 older articles were each
  withheld in turn. The score is the unweighted mean across publishers.
  AMLC has no pre-2025 examples and ADB has one 2026 example; PCIJ has fewer
  than 40 older examples. They remain visible in the recent holdout, but are
  too small for stable older-publisher folds.
- **F1** combines positive precision and recall; **average precision (AP)**
  assesses the ranking of positive articles across score thresholds. Publisher
  F1 gives each publisher equal weight even though their sample sizes differ.

## Search coverage

The prior 500-row screen tested 62 configurations. This larger screen compared
22 broad representatives: logistic, Ridge, linear and nonlinear SVM, SGD and
passive-aggressive linear updates, multinomial and Complement Naive Bayes,
NB-SVM, decision tree, random and extra-tree forests, histogram boosting,
nearest centroid, cosine k-nearest neighbors, and a majority baseline. Feature
families included word, character, hybrid word-and-character TF-IDF, word counts,
and 100-component LSA. We then compared 18 parameter variants in the stronger
linear and LSA families, followed by six 50- and 200-component LSA variants.
That is 46 configurations and 394 completed model/split fits, with no fit
failures. One Windows CPU-core detection warning did not affect results.

The complete ranking is [model-screen-comparison.csv](model-screen-comparison.csv).
The larger local research workspace also retained fold metrics, article-level
held-out predictions, issue logs, and per-run manifests. Those intermediate
files were not copied into this compact version-2 handoff.

## Representative results

| Configuration | Group CV F1 | Mean publisher F1 | 2025–2026 F1 | Recent recall |
|---|---:|---:|---:|---:|
| LSA 200 + RBF SVM, C=3 | 0.908 | **0.811** | 0.932 | 0.934 |
| LSA 100 + RBF SVM, C=3 | 0.906 | 0.802 | **0.937** | **0.939** |
| LSA 100 + RBF SVM, C=1 | 0.900 | 0.793 | 0.935 | 0.928 |
| Character TF-IDF + linear SVM, C=1 | 0.909 | 0.778 | 0.932 | 0.925 |
| Hybrid TF-IDF + linear SVM, C=1 | **0.912** | 0.763 | 0.933 | 0.921 |
| Hybrid TF-IDF + NB-SVM, C=1 | 0.909 | 0.749 | 0.930 | 0.913 |
| Word TF-IDF + Complement NB | 0.867 | 0.655 | 0.926 | 0.909 |
| Word TF-IDF + random forest | 0.878 | 0.539 | 0.927 | 0.904 |
| Word TF-IDF + depth-20 decision tree | 0.831 | not screened | 0.842 | 0.836 |
| Word TF-IDF + cosine k-NN | 0.875 | not screened | 0.900 | 0.904 |

All F1 values above use the same threshold embedded in each classifier. The
recent holdout produced 35 false negatives and 37 false positives for LSA 100
with C=3; LSA 200 with C=3 produced 38 and 40. These are disagreements with
proposed labels. The 50-component variants did not improve the combined
publisher and recent-news result; 200 dimensions modestly improved publisher
transfer but did not clearly improve recent-news F1.

## Where transfer still fails

| Withheld older publisher | Articles | Positives | LSA 100 C=3 F1 | LSA 200 C=3 F1 |
|---|---:|---:|---:|---:|
| DOJ_PH | 303 | 104 | 0.858 | 0.855 |
| DTI_PH | 60 | 44 | 0.918 | 0.953 |
| NBI | 567 | 517 | 0.974 | 0.972 |
| PTV | 1,293 | 284 | 0.695 | 0.662 |
| SEC_PH | 591 | 577 | 0.946 | 0.966 |
| UK_GOV | 1,203 | 161 | **0.424** | **0.458** |

The UK result is the main blocker for a generalization claim: LSA 200 C=3
missed 104 of 161 positive UK articles when the entire source was unseen.
It also made 229 false-positive predictions on PTV. Source-level class balance
is extreme: NBI and SEC advisories are mostly positive, while UK and PTV are
mostly negative. The model may learn article format and publisher-specific
language as shortcuts. Error review should focus on UK and PTV, including
whether the proposed labels follow the policy consistently in those sources.

## Decision and next evaluation

Keep **LSA 100/RBF SVM C=3**, **LSA 200/RBF SVM C=3**, and **character TF-IDF/linear
SVM C=1** as finalists. The 200-component variant leads the mean publisher
stress test; the 100-component variant has the best recent-news F1 and fewer
recent false negatives. The character linear model is a simpler, high-ranking
fallback. No small difference here establishes a definitive winner.

Before choosing a production threshold, review same-case links, freeze final
case-safe splits, and score these finalists on an independent answer-blind human
reference sample, emphasizing UK, PTV, rare sources and recent news. The current
5,000 targets are largely generated by language models and reflect a deliberately
source-balanced sample. These experiments measure ability to reproduce those
targets under several shifts; they do not measure production precision or recall.

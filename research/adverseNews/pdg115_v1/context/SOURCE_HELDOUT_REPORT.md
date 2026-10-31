# Source-held-out classical model screen

- Data: 500 teacher-/human-adjudicated articles; 8 publishers.
- Validation: leave one publisher out per fold; 62 of 62 configurations completed.
- Elapsed seconds: 129.8.
- Each publisher is excluded entirely from that fold's model fitting and text-feature fitting.
- Publisher class balance and sample sizes vary widely; per-source results are descriptive.
- This remains evaluation against mostly teacher-derived labels, not independent production truth.

## Top 15 by mean publisher F1

| Model | Mean publisher F1 ± SD | Recall | Precision | AP | Worst publisher F1 | Pooled OOF F1 | FN | FP |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| lsa100::logistic_C1 | 0.763 ± 0.170 | 0.753 | 0.798 | 0.841 | 0.545 | 0.857 | 43 | 30 |
| lsa100::rbf_svm_C1 | 0.759 ± 0.173 | 0.835 | 0.726 | 0.867 | 0.567 | 0.829 | 30 | 65 |
| lsa100::linear_svm_C1 | 0.748 ± 0.173 | 0.785 | 0.758 | 0.859 | 0.522 | 0.830 | 42 | 48 |
| hybrid_tfidf::passive_aggressive | 0.711 ± 0.212 | 0.678 | 0.815 | 0.858 | 0.393 | 0.812 | 65 | 26 |
| hybrid_tfidf::nbsvm | 0.704 ± 0.177 | 0.671 | 0.781 | 0.836 | 0.417 | 0.804 | 64 | 32 |
| word_tfidf::sgd_hinge_a1e-05 | 0.704 ± 0.188 | 0.655 | 0.798 | 0.818 | 0.444 | 0.819 | 60 | 29 |
| char_tfidf::passive_aggressive | 0.694 ± 0.206 | 0.659 | 0.782 | 0.859 | 0.467 | 0.807 | 65 | 29 |
| char_tfidf::sgd_hinge | 0.691 ± 0.230 | 0.616 | 0.845 | 0.855 | 0.385 | 0.819 | 69 | 16 |
| word_tfidf::sgd_hinge_a0.001 | 0.690 ± 0.232 | 0.639 | 0.845 | 0.850 | 0.275 | 0.800 | 73 | 21 |
| word_tfidf::knn_cosine_k3 | 0.690 ± 0.193 | 0.778 | 0.674 | 0.695 | 0.493 | 0.781 | 40 | 84 |
| hybrid_tfidf::linear_svm_C10 | 0.689 ± 0.216 | 0.628 | 0.829 | 0.866 | 0.444 | 0.812 | 69 | 20 |
| word_tfidf::knn_cosine_k7 | 0.689 ± 0.192 | 0.817 | 0.664 | 0.777 | 0.480 | 0.780 | 36 | 91 |
| word_tfidf::linear_svm_C10 | 0.687 ± 0.225 | 0.634 | 0.835 | 0.849 | 0.308 | 0.796 | 74 | 22 |
| word_tfidf::ridge_a0.1 | 0.686 ± 0.229 | 0.629 | 0.846 | 0.848 | 0.275 | 0.794 | 76 | 20 |
| lsa100::hist_gradient_boosting | 0.679 ± 0.234 | 0.700 | 0.710 | 0.776 | 0.286 | 0.797 | 59 | 44 |

Full rankings: `summary.csv`; publisher-by-publisher behavior: `fold-metrics.csv`; per-article held-out predictions: `oof-predictions.csv`.

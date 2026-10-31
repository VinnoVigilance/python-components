# UK_GOV diversity update: controlled model-opinion comparison

## Plain answer

The small, targeted update is promising and remains **experimental**. On the 20 certain GPT-reviewed articles in the new UK_GOV diagnostic packet, the fast model's agreement increased from 13/20 to 18/20. F1 agreement increased from 0.364 to 0.800. On the older 132 certain reserved opinions, all three fitted versions made exactly the same predictions, with F1 agreement 0.923. These are agreement scores against GPT opinions, **not production accuracy or an independent human benchmark**. The current production/research baseline was not replaced.

## Three comparable fits

All three versions used the same word/character TF-IDF, LSA 200 and RBF SVM C=3 design, the same random seed, and the same scikit-learn 1.9.1 runtime. Each was fit from scratch. The original corpus remained untouched.

| Version | Training records | Label changes | New records |
| --- | ---: | ---: | ---: |
| Original | 5,000 | 0 | 0 |
| Corrected | 5,000 | 6 UK notice silver labels changed 1→0 in an in-memory copy | 0 |
| Updated | 5,018 | Same six corrections | 18 certain full-text GPT opinions from a separate UK_GOV packet |

Three uncertain articles in the 21-article training packet were excluded. The 21-article diagnostic packet and the older 150-article reserved packet were never added to training. Exact article IDs and title/body hashes were checked against both held packets. Case-level links remain incompletely reviewed.

## Held-packet results (certain opinions only)

| Held packet | Original | Corrected | Updated |
| --- | --- | --- | --- |
| New UK_GOV, 20 certain | 13/20 agreement; F1 0.364; 3 misses, 4 false alarms | 15/20; F1 0.444; 3 misses, 2 false alarms | **18/20; F1 0.800; 1 miss, 1 false alarm** |
| Older reserved, 132 certain across sources | 122/132; F1 0.923; 8 misses, 2 false alarms | 122/132; F1 0.923; 8 misses, 2 false alarms | 122/132; F1 0.923; 8 misses, 2 false alarms |

The correction-only fit fixed the closed school notice (`db:71198`) and the school-test operational inquiry (`db:112814`) in the new packet. Adding the 18 reviewed articles also fixed the precautionary product recall (`db:94701`) and two misses: actual sanctions inside an investment announcement (`db:67039`) and a civilian attack inside a diplomatic statement (`db:38261`). The updated fit still misses the formal competition investigation inside a legal-trainee profile (`db:85480`) and flags a military obituary with no stated cause of death (`db:176090`).

On all 150 older reserved opinions, including uncertain ones, the updated fit changed one prediction: an Astra-uncertain negative article moved from positive to negative. F1 agreement changed from 0.881 to 0.887. No certain reserved prediction changed. The original baseline was refitted under the same runtime for comparability, rather than mixing a saved scikit-learn 1.8.0 model with new 1.9.1 fits.

## What this does and does not mean

This shows the new examples taught the fast model at least two patterns it previously missed in a separate packet from the same export. It also shows that the six confirmed policy corrections can remove false alarms. The new packet was intentionally selected by source section and model score, so its label mix and 0.800 F1 are **not** representative of UK_GOV arrivals. Both held packets use stronger-model opinions rather than independent people; they cannot certify safety for automatic clearance. Twenty certain examples are too few to estimate rare misses reliably. The export itself overrepresents medicine URLs because of the first query's URL grouping error.

## Decision

Keep the original model as the research baseline; retain this refit as a promising experimental candidate. The next comparison should use a larger, genuinely random and case-separated sample of new UK_GOV articles, plus actual new source publishers when they appear. Review the two remaining failure patterns and the ordinary-war boundary. If those checks hold, package the updated model with the confirmed policy version and a review route for unfamiliar publishers. Do not use the 0.800 figure as a production accuracy claim.

Exact per-article predictions and scores are in `scores.csv`; all packet metrics are in `metrics.csv`; input hashes and runtime are in `manifest.json`.

# Watchlist v6 CPU runtime

This package contains the inference subset of the watchlist classification
research. It does not import the research folder, call a teacher model, train
weights, or download model files at runtime.

## Files

- `watchlistText.py`: canonical payload to the research input text.
- `highlighter.py`: evidence splitting and frozen v6 candidate selection.
- `lookupRules.py`: researched list, measure and programme rules.
- `student.py`: local CPU model loading and label/passage scoring.
- `models/student-e5small-v6/`: weights, tokenizer, config and manifest.

The original files remain in research/watchlistRiskClassification. Helpers were
copied with only documentation shortened. The manifest records original source
hashes, asset hashes, the research freeze and inference settings. Model weights
use the same Git LFS storage mechanism as the research copy.

## Decision settings

The student receives label/text pairs, truncates the second sequence to a total
maximum of 256 tokens, and uses 150 characters of neighboring evidence context.
A text label is selected if any candidate passage score is >= 0.5, unless the
victim/missing-person score is >= 0.5. Victim input excludes the source-list line
and is limited to the original 1,200 characters. Lookup labels remain separate.

The model supports 15 text labels; three additional labels (Sanctions,
Regulatory and Law Enforcement) come from lookup rules. The shared business
workbook supplies stable IDs and display names. Its 23 labels are not all
assessed by this package. No candidate passage means unassessed by the text
model for that label, not a scored negative.

Inference uses the frozen weights without retraining or threshold optimization.
The runtime requires the research-recorded torch 2.14.1 and transformers 5.19.0
versions, pinned in the application requirements. These packages were absent
from the local vv-env when this implementation was prepared; they have not been
installed or exercised here. Model assets are checked against their manifest.

## Interpretation and versions

Scores are uncalibrated sigmoid outputs, not demonstrated probabilities of
real-world wrongdoing. V6 is the latest frozen candidate and still needs a new,
untouched evaluation export. The initial runtime marks text-only positive
labels for review. A label independently supplied by lookup rules does not
require approval merely because the text model also supplies it.

Stored assessments separate final lookup labels in `riskCategories` from selected
text-model labels awaiting review in `reviewRequired`. Review events remain
separate and do not automatically promote labels to the client-facing result.
Metadata records model, artifact, decision-policy, taxonomy, rule and runtime
versions. These values are part of the result hash.

See docs/risk-pipeline-design.md for the pipeline modes, persistence, review API,
database setup and remaining verification.

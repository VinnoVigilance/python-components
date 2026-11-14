# Watchlist v6 CPU runtime

This package contains the inference subset of the watchlist classification
research. It does not import the research folder, call a teacher model, train
weights, or download model files at runtime.

## Files

- `watchlistText.py`: canonical payload to the research input text.
- `highlighter.py`: evidence splitting and runtime keyword candidate selection.
- `lookupRules.py`: validates and reads mapped `RiskCategory.Labels[]` assertions.
- `student.py`: local CPU model loading and label/passage scoring.
- `models/student-e5small-v6/`: weights, tokenizer, config and manifest.

The original files remain in research/watchlistRiskClassification. The manifest
records original source hashes, asset hashes, the research freeze and inference
settings. Model weights use the same Git LFS storage mechanism as the research copy.

## Decision settings

Canonical mapping supplies a `RiskCategory` object. Its `Labels[]` entries have
`Type` (`List` or `Record`) and `Value` (an exact display name from the shared
taxonomy workbook).
The workbook groups `RiskCategory.RiskModelEnabled` with those labels. The
mapper writes the boolean setting as a scalar and builds `Labels[]` from the
array rows. Only `true`, `false`, or blank are accepted for the setting.
List assertions apply to every record of that list; record assertions depend on
that record's source data. Mapping drops completely empty entries and rejects
half-filled entries. The lookup rejects unknown types and names, deduplicates
labels, and records the mapped origin in each explanation. It does not infer
risk from `Sources[].DatasetCategory`, source-list names, measures, or programmes.
`RiskCategory.RiskModelEnabled` controls text-model inference, separate from mapped risk
labels. `true` runs the model even when `RiskCategory.Labels[]` is empty. `false`,
blank, or missing does not run the model. Mapped labels are still validated and
saved without model inference. A record with no mapped labels and no model run
is skipped; skipping a current version expires its current assessment, while a
historical skip leaves the current assessment unchanged. Existing stored
payloads must be normalized again to receive the nested structure. Sources must
populate the mapping rows before a risk INITIAL run. The mapper retains a
complete list assertion even when both fields are constants.

The student receives label/text pairs, truncates the second sequence to a total
maximum of 256 tokens, and uses 150 characters of neighboring evidence context.
A text label is selected if any candidate passage score is >= 0.5, unless the
victim/missing-person score is >= 0.5. Programme text is eligible for keyword
matching for all text labels, including PEP, subject to the usual boilerplate
and keyword filters. The source-list name is used to filter boilerplate but is
absent from model text. Victim input is limited to the original 1,200 characters.
Lookup labels remain separate. Business activity
descriptions and notes, plus vessel and aircraft notes, are included when mapped;
identifiers and physical specifications remain excluded.
The decision policy automatically publishes Fraud, Money Laundering, Trafficking
& Smuggling, Violent Crime, Theft & Property Crime, Forgery & Counterfeiting,
Terrorism / Financing, Proliferation / Financing, Human Rights / Atrocities, and
Judicial / Court. The other five text labels remain review suggestions. A lookup
label takes precedence when both methods select the same category.

The model supports 15 text labels; three additional labels (Sanctions,
Regulatory and Law Enforcement) come from lookup rules. The shared business
workbook supplies stable IDs and display names. Its 23 labels are not all
assessed by this package. No candidate passage means unassessed by the text
model for that label, not a scored negative.

Inference uses the frozen weights without retraining or threshold optimization.
The runtime requires the research-recorded torch 2.14.1 and transformers 5.19.0
versions, pinned in the application requirements. Both are installed in the
Python 3.11.9 `vv-env`; local CPU inference has been exercised. Model assets are
checked against their manifest.

## Interpretation and versions

Scores are uncalibrated sigmoid outputs, not demonstrated probabilities of
real-world wrongdoing. V6 is the latest frozen candidate and still needs a new,
untouched evaluation export. Automatic publication of the ten text labels is a
product decision at the existing 0.5 cutoff, not an independently proven
precision claim. A label independently supplied by lookup rules does not
require approval merely because the text model also supplies it.

Stored assessments place final lookup and selected automatic text labels in
`riskCategories`; the remaining selected text labels go to `reviewRequired`.
Review events remain separate and do not automatically promote labels to the
client-facing result.
Metadata records model, artifact, decision-policy, taxonomy, rule and runtime
versions. These values are part of the result hash.

See docs/risk-pipeline-design.md for the pipeline modes, persistence, review API,
database setup and remaining verification.

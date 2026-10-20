# Media risk classification service

Standalone NB-SVM inference using the current 11-label model and the user's
23-label business workbook. No trained weights, cutoffs, text preparation or
scoring formula are changed.

## Install

Use the existing project environment with Python 3.11.x (training: 3.11.9):

```powershell
python -m pip install -r requirements.txt
```

There is one application requirements file. NumPy, SciPy, scikit-learn and
Joblib are pinned there to the selected run.json versions. openpyxl, already
used by the project, reads the workbook. Docker installs this same file.
This is not a complete lock of all transitive packages or OS dependencies.

## Files

```text
data/rules/VV_RiskCategory_Labels.xlsx
intelligence/risk/mediaRiskCategory/models/nbsvm-v1/
    model.joblib
    run.json
    manifest.json
```

Keep the existing model.joblib and run.json from run
`20261019T071053_870233Z_nbsvm_oof`. No retraining or export is required.

The uploaded workbook is copied without modification. It is the sole business
taxonomy; there is no separate taxonomy JSON to maintain. Its Risk Labels sheet
owns the 23 names and English descriptions. Annotation Rules supplies each
label's existing Stable Label ID, including TER_GEN, REG_GEN, LE_GEN and CR_FRAUD.
Shared guidance rows without a Risk Label cell are not extra classification
labels. Original Coverage, Change Log, examples, indicators and all detailed
business criteria remain in that same file.

The manifest records the model ID/run ID, a project-relative taxonomy_file,
and the ordered model columns. Each column pairs the workbook's stable ID with
its frozen artifact_name. Output order comes from the model manifest, not Excel
row order. The loader checks the ordered artifact names against both run.json
and the serialized model, and retains fitted dimension/threshold checks.
The business workbook has 23 labels; this model only evaluates its 11 supported
columns. Reading all definitions does not add new learned classifiers.

The workbook is read once at service initialization. Results include an automatic
`taxonomy_version` content fingerprint (`sha256:...`) of the exact workbook bytes
used. Editing or replacing the workbook therefore changes its recorded version
without another version file or export step. Preserve historical snapshots for
results that need later reproduction, and restart workers after changing it.
Descriptions are metadata: changing them does not retrain the model or add rule
logic. Keep Stable Label IDs stable for the same meaning. A changed meaning or
model scope needs compatibility review and, where appropriate, retraining.

The default path lives under data/rules to match the project's other reference
workbooks. .dockerignore includes this specific workbook so a Docker build can
copy it. No other ignored data assets or unrelated Docker behavior were changed.
Custom model directories can still be selected through MEDIA_RISK_MODEL_DIR or
--model-path; their manifests refer to the shared project workbook.

## Use

```python
from intelligence.risk.mediaRiskCategory.predictor import MediaRiskPredictor

service = MediaRiskPredictor()
result = service.classify(title=news_title, body=news_body)
labels = result["labels"]
label_ids = result["label_ids"]
```

Existing result fields status, stage, model_version and labels retain their
meaning and initial names. Added fields are label_ids (now the workbook's own
stable IDs) and taxonomy_version. Consumers with an exact output schema must
allow these fields. supported_labels, supported_label_ids and taxonomy_version
expose the evaluated scope and the workbook snapshot.

Use plain-text Content.Title and full Content.BodyText. Preparation still uses
NFKC and whitespace collapse, then joins title and body with two newlines.
A missing title is allowed. A blank/missing body yields SKIPPED_EMPTY_BODY with
empty labels and IDs. Wrong types raise TypeError. Scores are not probabilities;
the rule is still score >= the saved threshold. Empty CLASSIFIED results only
cover evaluated labels. The 12 unsupported taxonomy labels remain unassessed.

`classify_many` preserves input order;
use bounded batches for large corpora. The existing historical Joblib class
adapter and exact runtime checks remain. Only load trusted model files.

## Tests and scope

```powershell
python -m unittest discover -s test/unit/adverseMedia/services -p test_mediaRiskCategoryContract.py -v
```

Thirty focused tests cover the actual workbook structure, 23-to-11 scope,
ordering, stable IDs, content version, malformed contracts, preserved scoring
and dependencies. They use synthetic estimators and a substituted artifact
reader, never the supplied original pickle. They are not an accuracy evaluation.
A bounded real-model smoke run passed in the local Python 3.11 environment.

The standalone `predictor.py` returns `labelDecisions` with actual SVM margins,
cutoffs and selection decisions for all 11 evaluated labels. These are not
calibrated probabilities. The common pipeline uses `mediaRiskClassifier.py` to
shape the stored assessment; see `docs/risk-pipeline-design.md` for database
processing, initial/incremental/reprocess modes and persistent label review.

Business taxonomy loading is shared in `intelligence/risk/labelContract.py`.
The original contract module preserves its imports. The updated 30-test media
contract suite passed under the local Python 3.11 environment. The full project
suite has not completed.

If the first patch was applied, remove its now-unused requirements-media-risk.txt
and intelligence/risk/mediaRiskCategory/models/nbsvm-v1/taxonomy.v1.json.

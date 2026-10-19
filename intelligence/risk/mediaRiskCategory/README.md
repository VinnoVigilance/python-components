# Media risk classification service

One ML stage for the selected 11-label NB-SVM. It receives a normalized plain-text
news title and full body and returns labels with the source model version.

## Files and location

| Location | Purpose |
| --- | --- |
| `services/adverseMediaPipeline/mediaRiskCategoryService.py` | Public service and terminal entry point |
| `intelligence/risk/mediaRiskCategory/nbsvm.py` | Load the saved model and perform inference |
| `intelligence/risk/mediaRiskCategory/__init__.py` | Package initialization |
| `intelligence/risk/mediaRiskCategory/models/nbsvm-v1/model.joblib` | Your existing trained model, copied manually |
| `intelligence/risk/mediaRiskCategory/models/nbsvm-v1/run.json` | Metadata from that same run, copied manually |

The ZIP contains runtime code and this README. It does not contain a replacement
trained model. Keep your selected run:
`20261019T071053_870233Z_nbsvm_oof`.

## Install

1. Copy the ZIP's `intelligence` and `services` contents into the matching project
   folders. Replace the service and nbsvm.py from previous addon versions.
2. Create `intelligence/risk/mediaRiskCategory/models/nbsvm-v1/` if absent.
3. Copy `model.joblib` and `run.json` from
   `research/mediaRiskCategoryClassification/outputs/20261019T071053_870233Z_nbsvm_oof/`
   into that version directory. If already copied there, keep them.
4. Use the same Python major/minor and the same NumPy, SciPy, scikit-learn and
   Joblib versions recorded in run.json. The service validates them before loading.

No export command or retraining step is required. The service reads the original
model directly and does not rewrite it. It maps the two historical research class
names inside its own Joblib reader, without importing a root `models` package,
changing import paths, or altering process-wide module mappings. This small reader
uses Joblib's numpy-pickle container API and requires the recorded Joblib version.
Only load model files from the trusted project.

## Use from Python

```python
from services.adverseMediaPipeline.mediaRiskCategoryService import (
    MediaRiskCategoryService,
)

# Initialize once per worker, outside the article loop.
service = MediaRiskCategoryService()

# Per article, after normalization:
result = service.classify(title=news_title, body=news_body)
labels = result["labels"]
```

Use Content.Title and full Content.BodyText. Existing normalization remains
responsible for extracting plain text from HTML. The service applies Unicode
NFKC and whitespace collapse, then joins title and body with two newlines.
This matches the previously verified frozen research input policy.

Batch input: `service.classify_many([(title1, body1), (title2, body2)])`.
The result order matches the input order. `service.supported_labels` exposes the
11-label scope. Default paths resolve relative to the project. Override the
model directory using MEDIA_RISK_MODEL_DIR or
`MediaRiskCategoryService(model_path=directory)`.

Each result contains `status`, `stage`, `model_version`, and `labels`.
The stage is `ml`; the model version includes the original research run ID.
A missing or blank body yields `SKIPPED_EMPTY_BODY`. A processed article yields
`CLASSIFIED`, including when no supported label crosses its threshold.
A missing title is allowed. Wrong input types raise TypeError. Model loading
errors raise MediaRiskModelError; inference errors propagate to the caller.

Decision rule: raw score >= the saved Train-OOF threshold for each label.
Scores are not probabilities. This model produces labels rather than evidence
sentences. No result certifies absence of risk outside the supported scope.

## Terminal check

From the project root with the model's environment active:

```powershell
python -m services.adverseMediaPipeline.mediaRiskCategoryService --title "News title" --body "Full plain-text news article."
```

For longer news, use `--body-file article.txt` for a UTF-8 plain-text body.

## Integration and handoff

Deliver these runtime files and the version directory containing your two copied
files. The recipient does not need training data or the research folder.
Existing intelligence/llm.py and the risk engine remain independent.
The service neither stores results nor modifies the news payload. A future
pipeline can call this ML stage, add a rule stage, resolve their outputs, and
persist the result in the planned dedicated media risk table. Record the news
version, model version and stage with every persisted result, including empty
classifications. The caller owns those responsibilities.

## Remove obsolete addon files

If earlier addons created the following files, they are unused by this version:

- `models/__init__.py`, `models/_nbsvm.py`, `models/_oof_thresholds.py` in the project root.
- `scripts/adverseMedia/export_media_risk_model.py`.
- `risk/media/nbsvm.py`, `risk/media/__init__.py`, `risk/media/README.md`.
- `test/unit/adverseMedia/services/test_mediaRiskCategoryService.py` from the earlier addon.

Remove only those old addon files if present. Keep the existing risk engine,
research outputs, and `intelligence/risk/mediaRiskCategory/models/nbsvm-v1/`.

"""Media risk ML stage: receive title/body and return labels with provenance."""
from __future__ import annotations

import os
from pathlib import Path

from intelligence.risk.mediaRiskCategory.nbsvm import decision_scores, load_model, prepare_text


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_DIR = ROOT_DIR / "intelligence/risk/mediaRiskCategory/models/nbsvm-v1"


class MediaRiskCategoryService:
    """Load once per worker; classify normalized plain-text news articles."""

    def __init__(self, model_path: str | Path | None = None):
        directory = Path(model_path or os.getenv("MEDIA_RISK_MODEL_DIR") or DEFAULT_MODEL_DIR).expanduser()
        if not directory.is_absolute():
            directory = ROOT_DIR / directory
        self._model, self._metadata = load_model(directory.resolve())

    @property
    def model_version(self) -> str:
        return self._model["label_contract"].model_id + ":" + self._metadata["run_id"]

    @property
    def supported_labels(self) -> tuple[str, ...]:
        return self._model["label_contract"].display_labels

    @property
    def supported_label_ids(self) -> tuple[str, ...]:
        return self._model["label_contract"].label_ids

    @property
    def taxonomy_version(self) -> str:
        return self._model["label_contract"].taxonomy_version

    def classify(self, title: str | None, body: str | None) -> dict:
        return self.classify_many([(title, body)])[0]

    def classify_many(self, articles: list[tuple[str | None, str | None]]) -> list[dict]:
        texts = [prepare_text(title, body) for title, body in articles]
        positions = [i for i, text in enumerate(texts) if text is not None]
        scores = decision_scores(self._model, [texts[i] for i in positions])
        results = [{"status": "SKIPPED_EMPTY_BODY", "stage": "ml",
                    "model_version": self.model_version, "taxonomy_version": self.taxonomy_version,
                    "labels": [], "label_ids": []} for _ in texts]
        for i, values in zip(positions, scores):
            selected = [index for index, (value, cutoff) in enumerate(
                zip(values, self._model["thresholds"])) if value >= cutoff]
            results[i] = {
                "status": "CLASSIFIED", "stage": "ml", "model_version": self.model_version,
                "taxonomy_version": self.taxonomy_version,
                "labels": [self.supported_labels[index] for index in selected],
                "label_ids": [self.supported_label_ids[index] for index in selected],
            }
        return results


if __name__ == "__main__":
    import argparse
    import json
    parser = argparse.ArgumentParser(description="Run the media risk model on one plain-text article.")
    parser.add_argument("--title", default="")
    parser.add_argument("--model-path", type=Path, help="Directory containing model.joblib, run.json and manifest.json.")
    body_args = parser.add_mutually_exclusive_group(required=True)
    body_args.add_argument("--body")
    body_args.add_argument("--body-file", type=Path)
    args = parser.parse_args()
    body = args.body_file.read_text(encoding="utf-8-sig") if args.body_file else args.body
    print(json.dumps(MediaRiskCategoryService(args.model_path).classify(args.title, body),
                     ensure_ascii=False, indent=2))

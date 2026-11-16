"""Adverse-news prediction from article titles and bodies."""

import hashlib
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.preprocessing import normalize


MODEL_VERSION = "adverse-news-word-char-lsa200-rbf-svm-v1"
MODEL_SHA256 = "eb5829ac409f02d79bd161d973574f82141eaf7bde512da2164987c487df05da"
DEFAULT_MODEL = Path(__file__).parent / "models" / MODEL_VERSION / "model.joblib"
DECISION_THRESHOLD = 0.0
REVIEW_REQUIRED = True


class AdverseNewsPredictor:
    """Load the frozen model once and score batches of full articles."""

    def __init__(self, model_path=None):
        path = Path(model_path) if model_path else DEFAULT_MODEL
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != MODEL_SHA256:
            raise ValueError(f"Adverse-news model checksum mismatch: {path}")
        self._bundle = joblib.load(path)

    def predict_many(self, articles):
        """Return predictions in input order. Missing title or body is skipped."""
        results = [None] * len(articles)
        eligible = []
        for index, article in enumerate(articles):
            title, body = article.get("title"), article.get("body")
            if not all(isinstance(value, str) and value.strip() for value in (title, body)):
                results[index] = {
                    "status": "SKIPPED_MISSING_TEXT",
                    "model_positive": None,
                    "score": None,
                    "review_required": REVIEW_REQUIRED,
                }
            else:
                eligible.append((index, article))

        for start in range(0, len(eligible), 100):
            batch = eligible[start:start + 100]
            texts = [(item["title"] + "\n\n" + item["body"]).strip() for _, item in batch]
            matrix = hstack([
                self._bundle["word_vectorizer"].transform(texts),
                self._bundle["char_vectorizer"].transform(texts),
            ], format="csr", dtype=np.float32)
            reduced = normalize(self._bundle["lsa200_svd"].transform(matrix)).astype(np.float32)
            scores = self._bundle["lsa200_rbf_c3"].decision_function(reduced)
            for (index, _), score in zip(batch, scores):
                results[index] = {
                    "status": "SCORED",
                    "model_positive": bool(score >= DECISION_THRESHOLD),
                    "score": float(score),
                    "review_required": REVIEW_REQUIRED,
                }
        return results

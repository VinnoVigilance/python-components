"""Verify the adverse-news predictor's output and model integrity contract."""

from hashlib import sha256
from unittest.mock import Mock

import numpy as np
import pytest
from scipy.sparse import csr_matrix

from intelligence.adverseNews import predictor


def test_prediction_preserves_order_and_uses_text_only(tmp_path, monkeypatch):
    model = tmp_path / "model.joblib"
    model.write_bytes(b"test model")
    monkeypatch.setattr(
        predictor, "MODEL_SHA256", sha256(b"test model").hexdigest(),
    )
    vectorizer = Mock()
    vectorizer.transform.return_value = csr_matrix(np.ones((3, 1)))
    reducer = Mock()
    reducer.transform.return_value = np.ones((3, 2))
    classifier = Mock()
    classifier.decision_function.return_value = np.array([0.0, 0.0, -0.4])
    monkeypatch.setattr(predictor.joblib, "load", Mock(return_value={
        "word_vectorizer": vectorizer,
        "char_vectorizer": vectorizer,
        "lsa200_svd": reducer,
        "lsa200_rbf_c3": classifier,
    }))
    article = {"title": "Headline", "body": "Article body"}

    results = predictor.AdverseNewsPredictor(model).predict_many([
        article,
        {"title": "Missing body", "body": " "},
        {**article, "source_name": "unfamiliar publisher"},
        {"title": "Another headline", "body": "Another body"},
    ])

    assert results == [
        {"status": "SCORED", "model_positive": True, "score": 0.0, "review_required": True},
        {
            "status": "SKIPPED_MISSING_TEXT",
            "model_positive": None,
            "score": None,
            "review_required": True,
        },
        {"status": "SCORED", "model_positive": True, "score": 0.0, "review_required": True},
        {"status": "SCORED", "model_positive": False, "score": -0.4, "review_required": True},
    ]
    vectorizer.transform.assert_called_with([
        "Headline\n\nArticle body",
        "Headline\n\nArticle body",
        "Another headline\n\nAnother body",
    ])


def test_checksum_mismatch_prevents_deserialization(tmp_path, monkeypatch):
    model = tmp_path / "model.joblib"
    model.write_bytes(b"wrong model")
    load = Mock()
    monkeypatch.setattr(predictor.joblib, "load", load)

    with pytest.raises(ValueError, match="checksum mismatch"):
        predictor.AdverseNewsPredictor(model)

    load.assert_not_called()

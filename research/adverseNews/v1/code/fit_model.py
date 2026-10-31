"""Refit the frozen LSA 200 + RBF SVM research model from final-5044.jsonl.gz.

This is a manual reproduction script. It will not overwrite the frozen model.
Run in an environment with the versions in ../requirements.txt.
"""

import argparse
import gzip
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize
from sklearn.svm import SVC
from threadpoolctl import threadpool_limits


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; frozen model will not be overwritten")
    with gzip.open(ROOT / "training/final-5044.jsonl.gz", "rt", encoding="utf-8") as stream:
        articles = [json.loads(line) for line in stream if line.strip()]
    if len(articles) != 5044:
        raise ValueError("Expected exactly 5,044 training articles")
    texts = [(item["title"] + "\n\n" + item["body"]).strip() for item in articles]
    labels = np.asarray([item["label"] for item in articles], dtype=int)
    with threadpool_limits(limits=4):
        word = TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_df=0.98,
                               max_features=60000, sublinear_tf=True,
                               strip_accents="unicode", dtype=np.float32)
        char = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2,
                               max_features=120000, sublinear_tf=True, dtype=np.float32)
        matrix = hstack([word.fit_transform(texts), char.fit_transform(texts)],
                        format="csr", dtype=np.float32)
        svd = TruncatedSVD(n_components=200, n_iter=5, random_state=115)
        reduced = normalize(svd.fit_transform(matrix)).astype(np.float32)
        model = SVC(C=3, kernel="rbf", gamma="scale", class_weight="balanced")
        model.fit(reduced, labels)
        bundle = {
            "word_vectorizer": word,
            "char_vectorizer": char,
            "lsa200_svd": svd,
            "lsa200_rbf_c3": model,
            "default_thresholds": {"lsa200_rbf_c3": 0.0},
            "model_inputs": ["title", "body"],
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, args.output, compress=3)
    print(json.dumps({"training_articles": len(articles), "output": str(args.output)}))


if __name__ == "__main__":
    main()

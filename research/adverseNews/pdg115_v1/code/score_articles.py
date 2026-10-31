"""Score full-text JSONL articles using the frozen research model.

Every returned article still requires review. The model never auto-clears.
"""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.preprocessing import normalize


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "model/model.joblib"
KNOWN_SOURCES = frozenset({"ADB", "AMLC", "DOJ_PH", "DTI_PH", "NBI", "PCIJ", "PTV", "SEC_PH", "UK_GOV"})


def scores(bundle, articles):
    values = []
    for start in range(0, len(articles), 100):
        batch = articles[start : start + 100]
        texts = [(article["title"] + "\n\n" + article["body"]).strip() for article in batch]
        matrix = hstack([bundle["word_vectorizer"].transform(texts),
                         bundle["char_vectorizer"].transform(texts)],
                        format="csr", dtype=np.float32)
        reduced = normalize(bundle["lsa200_svd"].transform(matrix)).astype(np.float32)
        values.extend(map(float, bundle["lsa200_rbf_c3"].decision_function(reduced)))
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists")
    with args.input.open(encoding="utf-8-sig") as stream:
        articles = [json.loads(line) for line in stream if line.strip()]
    if not articles:
        parser.error("input contains no articles")
    for article in articles:
        if not isinstance(article, dict) or any(
            not isinstance(article.get(key), str) or not article[key].strip()
            for key in ("article_id", "source_name", "title", "body")
        ):
            raise ValueError("each article needs article_id, source_name, title, and full body")
    bundle = joblib.load(args.model)
    values = scores(bundle, articles)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        for article, value in zip(articles, values):
            source = article["source_name"]
            route = ("weak_source_full_review" if source in {"UK_GOV", "PTV"}
                     else "new_source_full_review" if source not in KNOWN_SOURCES
                     else "positive_score_full_review" if value >= 0
                     else "routine_full_review")
            stream.write(json.dumps({
                "article_id": article["article_id"],
                "source_name": source,
                "publisher_name": article.get("publisher_name"),
                "score": value,
                "threshold": 0.0,
                "model_positive": value >= 0,
                "route": route,
                "review_required": True,
                "auto_clear": False,
                "model_version": "pdg115-lsa200-rbf-c3-5044-v1",
            }, ensure_ascii=False) + "\n")
    print(json.dumps({"articles": len(articles), "output": str(args.output)}))


if __name__ == "__main__":
    main()

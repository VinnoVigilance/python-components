"""Fit and package the three classical finalists on the complete silver pool."""

import json
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from sklearn.svm import LinearSVC, SVC

from contracts import file_digest
from run_classical_screen import read_jsonl, tfidf_word, tfidf_char


DATA = Path('research/adverseNews/data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl')
OUT = Path('research/adverseNews/results/finalist-models-v1')
SEED = 115


def main():
    if OUT.exists():
        raise ValueError(f'output exists: {OUT}')
    rows = read_jsonl(DATA)
    if len(rows) != 5000 or len({r['prepared_group_id'] for r in rows}) != 5000:
        raise ValueError('unexpected training corpus or duplicate prepared groups')
    texts = [(r['title'] + '\n\n' + r['body']).strip() for r in rows]
    y = np.asarray([r['label'] for r in rows], dtype=int)
    word, char = tfidf_word(), tfidf_char()
    x_word = word.fit_transform(texts)
    x_char = char.fit_transform(texts)
    x_hybrid = hstack([x_word, x_char], format='csr', dtype=np.float32)
    bundle = {'word_vectorizer': word, 'char_vectorizer': char,
              'default_thresholds': {'lsa100_rbf_c3': 0.0, 'lsa200_rbf_c3': 0.0,
                                     'char_linear_svm_c1': 0.0},
              'model_inputs': ['title', 'body']}
    for dimensions in (100, 200):
        svd = TruncatedSVD(n_components=dimensions, n_iter=5, random_state=SEED)
        x_low = normalize(svd.fit_transform(x_hybrid)).astype(np.float32, copy=False)
        model = SVC(C=3, kernel='rbf', gamma='scale', class_weight='balanced')
        model.fit(x_low, y)
        bundle[f'lsa{dimensions}_svd'] = svd
        bundle[f'lsa{dimensions}_rbf_c3'] = model
    linear = LinearSVC(C=1, class_weight='balanced', max_iter=12000,
                       random_state=SEED)
    linear.fit(x_char, y)
    bundle['char_linear_svm_c1'] = linear
    OUT.mkdir(parents=True)
    artifact = OUT / 'finalist-bundle.joblib'
    joblib.dump(bundle, artifact, compress=3)
    manifest = {'status': 'research_artifact_not_production_approved',
                'data_file': str(DATA), 'data_sha256': file_digest(DATA),
                'training_rows': len(rows), 'label_counts': dict(Counter(str(v) for v in y)),
                'model_inputs': ['title','body'], 'seed': SEED,
                'models': ['lsa100_rbf_c3','lsa200_rbf_c3','char_linear_svm_c1'],
                'primary': 'lsa200_rbf_c3', 'initial_threshold': 0.0,
                'bundle_file': str(artifact), 'bundle_sha256': file_digest(artifact),
                'score_reference': 'results/FINAL_TRADITIONAL_ML_RECOMMENDATION.md',
                'same_case_review_complete': False,
                'independent_human_reference_available': False}
    (OUT / 'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'models': manifest['models'], 'rows': len(rows),
                      'bundle_size_bytes': artifact.stat().st_size,
                      'bundle_sha256': manifest['bundle_sha256']}, indent=2))


if __name__ == '__main__':
    main()

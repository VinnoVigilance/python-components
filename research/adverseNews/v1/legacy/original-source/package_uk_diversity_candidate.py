"""Package the reviewed UK_GOV model update as a research-only artifact.

The original corpus/bundle remain untouched. This package is for shadow
comparison, not for production promotion or automatic article clearance.
"""

import csv
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn import __version__ as sklearn_version
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from sklearn.svm import SVC
from threadpoolctl import threadpool_limits

from contracts import file_digest
from run_classical_screen import tfidf_char, tfidf_word


ROOT = Path(__file__).resolve().parent
OLD = ROOT / 'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
CORRECTIONS = ROOT / 'results/focus-review-v2/uk-notice-policy-corrections.csv'
TRAIN = ROOT / 'results/uk-diversity-training-v1'
EXPERIMENT = ROOT / 'results/uk-diversity-update-v1/scores.csv'
HOLD = ROOT / 'results/uk-diversity-review-v1/blind.jsonl'
RESERVED = ROOT / 'results/source-stage-v1/reference-blind.jsonl'
OUT = ROOT / 'results/uk-diversity-candidate-v1'


def jsonl(path):
    with path.open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def score(bundle, articles):
    texts = [(r['title'] + '\n\n' + r['body']).strip() for r in articles]
    x = hstack([bundle['word_vectorizer'].transform(texts),
                bundle['char_vectorizer'].transform(texts)],
               format='csr', dtype=np.float32)
    z = normalize(bundle['lsa200_svd'].transform(x)).astype(np.float32)
    return bundle['lsa200_rbf_c3'].decision_function(z)


def main():
    if OUT.exists():
        raise ValueError('output exists; do not overwrite a packaged candidate')
    original = jsonl(OLD)
    if len(original) != 5000:
        raise ValueError('original corpus changed')
    by_id = {r['article_id']: i for i, r in enumerate(original)}
    corrected = [dict(r) for r in original]
    with CORRECTIONS.open(encoding='utf-8-sig', newline='') as stream:
        corrections = list(csv.DictReader(stream))
    for row in corrections:
        target = corrected[by_id[row['article_id']]]
        if target['label'] != int(row['old_silver_label']):
            raise ValueError('stale notice correction')
        target['label'] = int(row['policy_label'])
    packet, opinions = jsonl(TRAIN/'blind.jsonl'), jsonl(TRAIN/'gpt-review.jsonl')
    if [r['article_id'] for r in packet] != [r['article_id'] for r in opinions]:
        raise ValueError('training packet/opinion mismatch')
    additions = [dict(r, label=o['label']) for r,o in zip(packet,opinions) if not o['uncertain']]
    if len(corrections) != 6 or len(additions) != 18:
        raise ValueError('unexpected correction or addition count')
    training = corrected + additions
    texts = [(r['title'] + '\n\n' + r['body']).strip() for r in training]
    y = np.asarray([r['label'] for r in training], dtype=int)
    with threadpool_limits(limits=4):
        word, char = tfidf_word(), tfidf_char()
        x = hstack([word.fit_transform(texts), char.fit_transform(texts)],
                   format='csr', dtype=np.float32)
        svd = TruncatedSVD(n_components=200, n_iter=5, random_state=115)
        z = normalize(svd.fit_transform(x)).astype(np.float32)
        model = SVC(C=3, kernel='rbf', gamma='scale', class_weight='balanced')
        model.fit(z, y)
        bundle = dict(word_vectorizer=word, char_vectorizer=char,
                      lsa200_svd=svd, lsa200_rbf_c3=model,
                      default_thresholds={'lsa200_rbf_c3':0.0},
                      model_inputs=['title','body'])
        held = jsonl(HOLD) + jsonl(RESERVED)
        values = score(bundle,held)
    with EXPERIMENT.open(encoding='utf-8-sig', newline='') as stream:
        expected = list(csv.DictReader(stream))
    if [r['article_id'] for r in held] != [r['article_id'] for r in expected]:
        raise ValueError('experiment comparison ordering changed')
    largest_difference = max(abs(float(e['updated_score'])-float(a)) for e,a in zip(expected,values))
    if largest_difference > 0.00001:
        raise ValueError(f'packaged model differs from experiment: {largest_difference}')
    OUT.mkdir()
    artifact = OUT/'experimental-bundle.joblib'
    joblib.dump(bundle,artifact,compress=3)
    manifest = dict(status='experimental_shadow_candidate_not_production_approved',
                    original_rows=len(original),policy_corrections=len(corrections),
                    new_certain_rows=len(additions),training_rows=len(training),
                    sklearn_version=sklearn_version,model='lsa200_rbf_c3',threshold=0.0,
                    model_inputs=['title','body'],review_is_independent_human_reference=False,
                    case_level_link_review_complete=False,
                    original_sha256=file_digest(OLD),
                    corrections_sha256=file_digest(CORRECTIONS),
                    training_packet_sha256=file_digest(TRAIN/'blind.jsonl'),
                    training_opinions_sha256=file_digest(TRAIN/'gpt-review.jsonl'),
                    experiment_scores_sha256=file_digest(EXPERIMENT),
                    bundle_sha256=file_digest(artifact),
                    max_abs_score_difference_from_experiment=largest_difference)
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ('status','training_rows','sklearn_version','bundle_sha256','max_abs_score_difference_from_experiment')},indent=2))


if __name__ == '__main__':
    main()

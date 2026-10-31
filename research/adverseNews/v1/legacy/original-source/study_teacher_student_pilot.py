"""Exploratory fast-student update using certain Astra-medium training opinions.

The reserved packet is scored only after teacher labels are complete. Results
measure agreement with a second model and cannot certify production accuracy.
"""
import csv
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.decomposition import TruncatedSVD
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.preprocessing import normalize
from sklearn.svm import SVC
from threadpoolctl import threadpool_limits

from run_classical_screen import tfidf_word, tfidf_char
from evaluate_source_stage_opinions import read_jsonl, read_csv, validate, score, sha

ROOT = Path(__file__).resolve().parent
STAGE = ROOT / 'results/source-stage-v1'
OLD = ROOT / 'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
NEW = STAGE / 'augmented-corpus-astra-medium/augmented-labels.jsonl'
REFERENCE = STAGE / 'reference-blind.jsonl'
OPINIONS = STAGE / 'returns/astra-medium-reference.jsonl'
BASELINE = ROOT / 'results/finalist-models-v1/finalist-bundle.joblib'
OUT = STAGE / 'teacher-student-pilot'


def digest_article(article):
    return hashlib.sha256((article['title'] + '\n\n' + article['body']).strip().encode('utf-8')).hexdigest()


def fit_and_score(training, reference):
    texts = [(r['title'] + '\n\n' + r['body']).strip() for r in training]
    held = [(r['title'] + '\n\n' + r['body']).strip() for r in reference]
    word, char = tfidf_word(), tfidf_char()
    train_matrix = hstack([word.fit_transform(texts), char.fit_transform(texts)],
                          format='csr', dtype=np.float32)
    held_matrix = hstack([word.transform(held), char.transform(held)],
                         format='csr', dtype=np.float32)
    svd = TruncatedSVD(n_components=200, n_iter=5, random_state=115)
    x = normalize(svd.fit_transform(train_matrix)).astype(np.float32)
    z = normalize(svd.transform(held_matrix)).astype(np.float32)
    model = SVC(C=3, kernel='rbf', class_weight='balanced')
    model.fit(x, np.array([r['label'] for r in training]))
    return list(map(float, model.decision_function(z)))


def metrics(rows, name, certain_only):
    if certain_only:
        rows = [r for r in rows if not r['uncertain']]
    if not rows:
        return None
    truth = [r['model_opinion'] for r in rows]
    output = dict(source=name, certain_only=certain_only, n=len(rows),
                  opinion_positive=sum(truth), uncertain=sum(r['uncertain'] for r in rows))
    for prefix in ('baseline', 'updated'):
        pred = [r[f'{prefix}_pred'] for r in rows]
        output.update({f'{prefix}_f1': f1_score(truth, pred, zero_division=0),
                       f'{prefix}_precision': precision_score(truth, pred, zero_division=0),
                       f'{prefix}_recall': recall_score(truth, pred, zero_division=0),
                       f'{prefix}_fn': sum(a == 1 and b == 0 for a, b in zip(truth, pred)),
                       f'{prefix}_fp': sum(a == 0 and b == 1 for a, b in zip(truth, pred))})
    output['f1_change'] = output['updated_f1'] - output['baseline_f1']
    return output


def main():
    if OUT.exists():
        raise ValueError(f'output exists: {OUT}')
    original = read_jsonl(OLD)
    augmented = read_jsonl(NEW)
    reference = read_jsonl(REFERENCE)
    opinions = validate(reference, read_jsonl(OPINIONS), 'gpt-6-astra-medium')
    if len(original) != 5000 or augmented[:5000] != original:
        raise ValueError('augmented corpus does not preserve original records')
    if len(augmented) != 5181 or len(reference) != 150:
        raise ValueError('unexpected corpus/packet size')
    train_groups = {r['prepared_group_id'] for r in augmented}
    meta = read_csv(STAGE / 'reference-selection-internal.csv')
    if train_groups & {r['group_id'] for r in meta.values()}:
        raise ValueError('prepared group overlap with reserved packet')
    if {digest_article(r) for r in augmented} & {digest_article(r) for r in reference}:
        raise ValueError('exact article content overlap')
    start = time.perf_counter()
    baseline_scores = score(joblib.load(BASELINE), reference)
    updated_scores = fit_and_score(augmented, reference)
    rows = [dict(article_id=article['article_id'], source=meta[article['article_id']]['source'],
                 model_opinion=opinion['label'], uncertain=opinion['uncertain'],
                 baseline_score=old_score, baseline_pred=int(old_score >= 0),
                 updated_score=new_score, updated_pred=int(new_score >= 0))
            for article, opinion, old_score, new_score in
            zip(reference, opinions, baseline_scores, updated_scores)]
    grouped = defaultdict(list)
    for row in rows:
        grouped['all'].append(row)
        grouped[row['source']].append(row)
    result = [m for name, group in sorted(grouped.items())
              for certain_only in (False, True)
              if (m := metrics(group, name, certain_only)) is not None]
    OUT.mkdir()
    with (OUT / 'scores.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    with (OUT / 'metrics.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result[0]))
        writer.writeheader(); writer.writerows(result)
    manifest = dict(status='exploratory_agreement_with_model_opinion',
                    original_sha256=sha(OLD), augmented_sha256=sha(NEW),
                    reference_packet_sha256=sha(REFERENCE), opinion_sha256=sha(OPINIONS),
                    baseline_bundle_sha256=sha(BASELINE),
                    original_n=len(original), augmented_n=len(augmented),
                    reserved_n=len(reference), elapsed_seconds=round(time.perf_counter()-start, 2),
                    case_review_complete=False, human_reference=False,
                    note='F1 is against a second model opinion. Held packet was never in training.')
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(dict(elapsed_seconds=manifest['elapsed_seconds'],
                          overall=[r for r in result if r['source']=='all']), indent=2))


if __name__ == '__main__':
    with threadpool_limits(limits=4):
        main()

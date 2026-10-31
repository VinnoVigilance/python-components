"""Compare frozen classifier with blind model opinions; never call them truth.

Run only after all three Astra-medium answer files are complete. This does not
train a model or change the original corpus. The 150 reference articles were
selected without looking at model scores, but their answers are model opinions.
"""
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.preprocessing import normalize

ROOT = Path(__file__).resolve().parent
STAGE = ROOT / 'results/source-stage-v1'
RETURNS = STAGE / 'returns'
BUNDLE = ROOT / 'results/finalist-models-v1/finalist-bundle.joblib'
OUT = STAGE / 'model-opinion-comparison'


def read_jsonl(path):
    with path.open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return {row['article_id']: row for row in csv.DictReader(stream)}


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def validate(packet, answers, expected_reviewer):
    if len(packet) != len(answers):
        raise ValueError(f'expected {len(packet)} model opinions, got {len(answers)}')
    for article, answer in zip(packet, answers):
        if article['article_id'] != answer.get('article_id'):
            raise ValueError('answer IDs must match packet order exactly')
        if type(answer.get('label')) is not int or answer['label'] not in (0, 1):
            raise ValueError(f"{article['article_id']}: label must be integer 0/1")
        if type(answer.get('uncertain')) is not bool:
            raise ValueError(f"{article['article_id']}: uncertain must be boolean")
        if not isinstance(answer.get('reason'), str) or not answer['reason'].strip():
            raise ValueError(f"{article['article_id']}: reason is required")
        if not isinstance(answer.get('evidence'), str):
            raise ValueError(f"{article['article_id']}: evidence must be a string")
        if answer['label'] and not answer['evidence'].strip():
            raise ValueError(f"{article['article_id']}: positive needs evidence")
        if answer.get('reviewer') != expected_reviewer or answer.get('reference') is not False:
            raise ValueError(f"{article['article_id']}: wrong reviewer or reference flag")
    return answers


def score(bundle, articles):
    results = []
    for start in range(0, len(articles), 100):
        chunk = articles[start:start + 100]
        texts = [(r['title'] + '\n\n' + r['body']).strip() for r in chunk]
        word = bundle['word_vectorizer'].transform(texts)
        char = bundle['char_vectorizer'].transform(texts)
        x = hstack([word, char], format='csr', dtype=np.float32)
        z = normalize(bundle['lsa200_svd'].transform(x)).astype(np.float32)
        results.extend(map(float, bundle['lsa200_rbf_c3'].decision_function(z)))
    return results


def summary(rows):
    y = [r['model_opinion'] for r in rows]
    p = [r['baseline_prediction'] for r in rows]
    return dict(n=len(rows), opinion_positive=sum(y), baseline_positive=sum(p),
                agreement=sum(a == b for a, b in zip(y, p)) / len(rows),
                f1_vs_model_opinion=f1_score(y, p, zero_division=0),
                precision_vs_model_opinion=precision_score(y, p, zero_division=0),
                recall_vs_model_opinion=recall_score(y, p, zero_division=0),
                model_opinion_positives_missed=sum(a == 1 and b == 0 for a, b in zip(y, p)),
                model_opinion_negatives_flagged=sum(a == 0 and b == 1 for a, b in zip(y, p)),
                uncertain=sum(r['uncertain'] for r in rows))


def main():
    train_packet = read_jsonl(STAGE / 'training-blind.jsonl')
    ref_packet = read_jsonl(STAGE / 'reference-blind.jsonl')
    uk_path = RETURNS / 'astra-medium-training-uk.jsonl'
    ptv_path = RETURNS / 'astra-medium-training-ptv.jsonl'
    ref_path = RETURNS / 'astra-medium-reference.jsonl'
    train_answers = (validate(train_packet[:100], read_jsonl(uk_path), 'gpt-6-astra-medium') +
                     validate(train_packet[100:], read_jsonl(ptv_path), 'gpt-6-astra-medium'))
    ref_answers = validate(ref_packet, read_jsonl(ref_path), 'gpt-6-astra-medium')
    if {r['article_id'] for r in train_packet} & {r['article_id'] for r in ref_packet}:
        raise ValueError('training and reserved packets overlap')
    train_meta = read_csv(STAGE / 'training-selection-internal.csv')
    ref_meta = read_csv(STAGE / 'reference-selection-internal.csv')
    if set(train_meta) != {r['article_id'] for r in train_packet} or set(ref_meta) != {r['article_id'] for r in ref_packet}:
        raise ValueError('packet metadata IDs do not match')
    if {m['group_id'] for m in train_meta.values()} & {m['group_id'] for m in ref_meta.values()}:
        raise ValueError('prepared group overlap across packets')
    bundle = joblib.load(BUNDLE)
    train_scores = score(bundle, train_packet)
    ref_scores = score(bundle, ref_packet)
    rows = []
    for name, packet, answers, scores, metadata in (
        ('training_targeted', train_packet, train_answers, train_scores, train_meta),
        ('reserved_random', ref_packet, ref_answers, ref_scores, ref_meta),
    ):
        for article, answer, value in zip(packet, answers, scores):
            rows.append(dict(packet=name, article_id=article['article_id'],
                             source=metadata[article['article_id']]['source'],
                             model_opinion=answer['label'], uncertain=answer['uncertain'],
                             baseline_score=value, baseline_prediction=int(value >= 0),
                             reason=answer['reason'], evidence=answer['evidence']))
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row['packet'], 'all')].append(row)
        grouped[(row['packet'], row['source'])].append(row)
        if not row['uncertain']:
            grouped[(row['packet'], 'certain_only')].append(row)
    results = [dict(packet=packet, source=source, **summary(group))
               for (packet, source), group in sorted(grouped.items())]
    if OUT.exists():
        raise ValueError(f'output already exists: {OUT}')
    OUT.mkdir()
    with (OUT / 'agreement.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (OUT / 'metrics.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    manifest = dict(status='model_opinion_comparison_not_human_accuracy',
                    training_packet_n=len(train_packet), reserved_packet_n=len(ref_packet),
                    input_sha256={p.name: sha(p) for p in (uk_path, ptv_path, ref_path)},
                    baseline_bundle_sha256=sha(BUNDLE),
                    uncertain_by_packet=dict(Counter(r['packet'] for r in rows if r['uncertain'])),
                    trained_on_new_labels=False, case_review_complete=False,
                    note='Reserved packet was selected independently of classifier scores; model opinions are not human truth.')
    (OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()

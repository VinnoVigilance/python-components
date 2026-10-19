"""Shared scoring and reports; thresholds are fixed and never chosen from evaluation targets."""
import csv
import json
import time

import numpy as np
from sklearn.metrics import (
    average_precision_score, hamming_loss, precision_recall_fscore_support,
)

from config import LABELS


def score_model(model, texts, method=None):
    if method is None:
        method = 'predict_proba' if hasattr(model, 'predict_proba') else 'decision_function'
    if method not in ('predict_proba', 'decision_function') or not hasattr(model, method):
        raise ValueError('The model must expose predict_proba or decision_function.')
    started = time.perf_counter()
    scores = np.asarray(getattr(model, method)(texts), dtype=float)
    elapsed = time.perf_counter() - started
    if scores.shape != (len(texts), len(LABELS)) or not np.isfinite(scores).all():
        raise ValueError(f'Expected finite scores with shape {(len(texts), len(LABELS))}, got {scores.shape}.')
    if method == 'predict_proba' and ((scores < 0).any() or (scores > 1).any()):
        raise ValueError('predict_proba returned values outside [0, 1].')
    return scores, method, elapsed


def classify(scores, thresholds):
    scores, thresholds = np.asarray(scores), np.asarray(thresholds)
    if scores.ndim != 2 or thresholds.shape != (scores.shape[1],):
        raise ValueError('There must be one threshold per score column.')
    if not np.isfinite(thresholds).all():
        raise ValueError('Thresholds must be finite.')
    return (scores >= thresholds).astype(np.int8)


def metrics(targets, scores, thresholds):
    targets, scores = np.asarray(targets), np.asarray(scores)
    if targets.shape != scores.shape or targets.ndim != 2 or targets.shape[1] != len(LABELS):
        raise ValueError(f'Target and score matrices must have the same {len(LABELS)}-column shape.')
    if not np.isfinite(scores).all() or not np.isin(targets, [0, 1]).all():
        raise ValueError('Targets must be binary and scores finite.')
    predicted = classify(scores, thresholds)
    p, r, f1, support = precision_recall_fscore_support(targets, predicted, average=None, zero_division=0)
    summary = {'rows': len(targets), 'labels': len(LABELS),
               'label_source': 'claude_gpt_positive_intersection',
               'exact_match': float(np.all(targets == predicted, axis=1).mean()),
               'hamming_loss': float(hamming_loss(targets, predicted)),
               'all_zero_target_rows': int((targets.sum(axis=1) == 0).sum())}
    for average in ('micro', 'macro'):
        pr, rc, ff, _ = precision_recall_fscore_support(targets, predicted, average=average, zero_division=0)
        summary.update({f'{average}_precision': float(pr), f'{average}_recall': float(rc), f'{average}_f1': float(ff)})
    details, aps, supported_f1 = [], [], []
    for j, label in enumerate(LABELS):
        y, pred = targets[:, j], predicted[:, j]
        tp = int(((y == 1) & (pred == 1)).sum())
        fp = int(((y == 0) & (pred == 1)).sum())
        fn = int(((y == 1) & (pred == 0)).sum())
        ap = float(average_precision_score(y, scores[:, j])) if support[j] else None
        if ap is not None:
            aps.append(ap)
            supported_f1.append(float(f1[j]))
        details.append({'label': label, 'support': int(support[j]), 'predicted_positive': int(pred.sum()),
                        'tp': tp, 'fp': fp, 'fn': fn, 'precision': float(p[j]), 'recall': float(r[j]),
                        'f1': float(f1[j]), 'average_precision': ap, 'threshold': float(thresholds[j]),
                        'support_note': 'no_positive' if support[j] == 0 else 'fewer_than_10' if support[j] < 10 else 'at_least_10'})
    summary['macro_average_precision_supported'] = float(np.mean(aps)) if aps else None
    summary['macro_f1_supported'] = float(np.mean(supported_f1)) if supported_f1 else None
    summary['positive_supported_labels'] = len(aps)
    return summary, details, predicted


def write_csv(path, rows, columns):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def write_reports(directory, rows, targets, scores, thresholds, split, timings, train_support):
    summary, details, predicted = metrics(targets, scores, thresholds)
    summary.update({'split': split, **timings})
    for j, item in enumerate(details):
        item['train_support'] = int(train_support[j])
    (directory / f'{split}_summary.json').write_text(json.dumps(summary, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    write_csv(directory / f'{split}_per_label.csv', details, list(details[0]))
    predictions = []
    joined = lambda values: ', '.join(label for label, value in zip(LABELS, values) if value)
    for i, source in enumerate(rows):
        y, pred = targets[i], predicted[i]
        row = {'news_id': source['news_id'], 'title': source['title'], 'body': source['body'],
               'true_labels': joined(y), 'predicted_labels': joined(pred),
               'missed_labels': joined((y == 1) & (pred == 0)),
               'extra_labels': joined((y == 0) & (pred == 1)), 'exact_match': int(np.array_equal(y, pred))}
        row.update({f'score::{label}': float(scores[i, j]) for j, label in enumerate(LABELS)})
        predictions.append(row)
    columns = list(predictions[0])
    write_csv(directory / f'{split}_predictions.csv', predictions, columns)
    write_csv(directory / f'{split}_errors.csv', [p for p in predictions if not p['exact_match']], columns)
    return summary

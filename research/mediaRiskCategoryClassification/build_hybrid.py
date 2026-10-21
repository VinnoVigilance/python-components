"""Build and validate a fixed 11-label hybrid from three existing trained runs."""
import argparse
import csv
from datetime import datetime, timezone
import json
import sys
import time

import joblib
import numpy as np
from threadpoolctl import threadpool_limits

from config import LABELS, OUTPUT_DIR, ROOT
from data import check_separation, dataset_id, load_split, sha256
from evaluation import score_model, write_reports
from models._per_label_hybrid import PerLabelHybrid, label_routes
from run import environment, protocol_id, refresh_comparison, write_json


SOURCE_RUNS = {
    'base': ('char_svm_oof_thresholds', '20261019T055745_463400Z_char_svm_oof_thresholds'),
    'word_lr': ('tfidf_lr', '20261017T083635_266238Z_tfidf_lr'),
    'char_svm': ('char_tfidf_svm', '20261017T084741_797613Z_char_tfidf_svm'),
}


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def checked_source(directory, model_name, validation_rows):
    meta = read_json(directory / 'run.json')
    if meta.get('status') != 'completed' or meta.get('model_name') != model_name:
        raise ValueError(f'{directory.name}: expected a completed {model_name} run.')
    labels = meta.get('labels', [])
    if len(labels) != len(set(labels)) or not set(LABELS).issubset(labels):
        raise ValueError(f'{directory.name}: incompatible labels.')
    with (directory / 'validation_predictions.csv').open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    saved = {row['news_id']: row for row in rows}
    if len(saved) != len(rows) or set(saved) != {row['news_id'] for row in validation_rows}:
        raise ValueError(f'{directory.name}: Validation article IDs differ or are duplicated.')
    for source in validation_rows:
        row = saved[source['news_id']]
        if any(row[key] != source[key] for key in ('title', 'body')):
            raise ValueError(f'{directory.name}: Validation text differs: {source["news_id"]}.')
        true = set(filter(None, row['true_labels'].split(', ')))
        if any((label in true) != (source[label] == '1') for label in LABELS):
            raise ValueError(f'{directory.name}: Validation references differ: {source["news_id"]}.')
    return meta, saved


def load_component(directory, meta):
    bundle = joblib.load(directory / 'model.joblib')
    for key in ('labels', 'dataset_id', 'protocol_id'):
        if bundle.get(key) != meta.get(key):
            raise ValueError(f'{directory.name}: saved model and run metadata disagree on {key}.')
    labels = list(bundle['labels'])
    thresholds = np.asarray(bundle['thresholds'], dtype=float)
    support = np.asarray(bundle['train_support'])
    method = bundle['score_method']
    if (thresholds.shape != (len(labels),) or not np.isfinite(thresholds).all()
            or support.shape != (len(labels),)
            or method not in ('decision_function', 'predict_proba')
            or not hasattr(bundle['model'], method)):
        raise ValueError(f'{directory.name}: invalid score method, thresholds or train support.')
    return {'model': bundle['model'], 'labels': labels, 'thresholds': thresholds,
            'score_method': method, 'train_support': support}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--threads', type=int, default=4)
    for key, (_, run_id) in SOURCE_RUNS.items():
        parser.add_argument('--' + key.replace('_', '-') + '-run', default=run_id)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error('--threads must be positive')
    routes = label_routes(LABELS)
    train_rows, _, train_targets = load_split('train')
    validation_rows, texts, targets = load_split('validation')
    check_separation(train_rows, validation_rows)
    current_dataset, current_protocol = dataset_id(), protocol_id()
    build_started = time.perf_counter()
    components, provenance, saved_predictions = {}, {}, {}
    for key, (name, _) in SOURCE_RUNS.items():
        directory = ROOT / 'outputs' / getattr(args, key + '_run')
        meta, saved = checked_source(directory, name, validation_rows)
        if key == 'base' and (meta['labels'] != list(LABELS)
                             or meta.get('dataset_id') != current_dataset
                             or meta.get('protocol_id') != current_protocol):
            raise ValueError('The base run must match the current 11-label dataset and protocol.')
        print(f'Loading {name}: {directory.name}', flush=True)
        component = load_component(directory, meta)
        for index, label in enumerate(LABELS):
            if component['train_support'][component['labels'].index(label)] != train_targets[:, index].sum():
                raise ValueError(f'{directory.name}: Train reference support differs for {label}.')
        components[key] = component
        saved_predictions[key] = saved
        provenance[key] = {'run_id': directory.name, 'model_name': name,
                           'model_sha256': sha256(directory / 'model.joblib'),
                           'dataset_id': meta['dataset_id'], 'protocol_id': meta['protocol_id'],
                           'fit_seconds': float(meta['fit_seconds'])}
    model = PerLabelHybrid(components, list(LABELS), routes)
    print(f'Hybrid: {len(LABELS)} labels; 3 frozen models. No fitting or threshold tuning.', flush=True)
    with threadpool_limits(limits=args.threads):
        scores, method, seconds = score_model(model, texts, 'decision_function')
    expected = np.array([
        [int(label in set(filter(None, saved_predictions[routes[label]][row['news_id']]
                                ['predicted_labels'].split(', ')))) for label in LABELS]
        for row in validation_rows], dtype=np.int8)
    if not np.array_equal(scores >= 0, expected):
        raise ValueError('Loaded-model predictions differ from the saved source Validation reports. '
                         'No hybrid run was created. Check the source files and Python environment.')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '_hybrid_per_label_v1'
    directory = OUTPUT_DIR / run_id
    directory.mkdir(parents=True, exist_ok=False)
    thresholds = np.zeros(len(LABELS))
    support = train_targets.sum(axis=0)
    historical_fit = sum(item['fit_seconds'] for item in provenance.values())
    meta = {'run_id': run_id, 'status': 'running', 'model_name': 'hybrid_per_label_v1',
            'labels': list(LABELS), 'dataset_id': current_dataset, 'protocol_id': current_protocol,
            'description': 'Fixed per-label routing of three previously fitted CPU models.',
            'threads': args.threads, 'environment': environment(), 'score_method': method,
            'thresholds': thresholds.tolist(), 'routes': routes, 'components': provenance,
            'threshold_policy': 'Original component thresholds preserved; no new tuning.',
            'selection_policy': 'Routing selected using observed Validation results; exploratory evaluation.',
            'score_semantics': 'Per-label source score minus its original cutoff; not probabilities.',
            'fit_seconds': historical_fit, 'new_fit_seconds': 0.0,
            'fit_time_note': 'Sum of historical component fit times; no fitting in this build.',
            'train_rows': len(train_rows), 'validation_rows': len(validation_rows)}
    write_json(directory / 'run.json', meta)
    try:
        summary = write_reports(directory, validation_rows, targets, scores, thresholds, 'validation',
                                {'fit_seconds': historical_fit, 'score_seconds': seconds}, support)
        bundle = {'model': model, 'labels': list(LABELS), 'thresholds': thresholds,
                  'score_method': method, 'train_support': support,
                  'dataset_id': current_dataset, 'protocol_id': current_protocol}
        joblib.dump(bundle, directory / 'model.joblib', compress=3)
        meta.update(status='completed', artifact_bytes=(directory / 'model.joblib').stat().st_size,
                    build_seconds=time.perf_counter() - build_started)
        write_json(directory / 'run.json', meta)
    except Exception as exc:
        meta.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        write_json(directory / 'run.json', meta)
        raise
    refresh_comparison()
    print(f'Validation ({len(LABELS)} labels; routing selected on this Validation)')
    print(f'{"Average":<10} {"Precision":>10} {"Recall":>10} {"F1":>10}')
    for average in ('macro', 'micro'):
        print(f'{average:<10} {summary[average + "_precision"]:>10.4f} '
              f'{summary[average + "_recall"]:>10.4f} {summary[average + "_f1"]:>10.4f}')
    print(f'Score: {seconds:.2f} s | {1000 * seconds / len(texts):.3f} ms/article')
    print('Source Validation predictions reproduced exactly. Hybrid saved with all three fitted models.')
    print(f'Saved: {directory}')
    print('Compare: python run.py --compare')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, ImportError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)

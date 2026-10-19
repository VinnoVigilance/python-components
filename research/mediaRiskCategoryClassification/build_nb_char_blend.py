"""Combine saved NB-SVM and character SVM using verified Train OOF predictions."""
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
from evaluation import score_model, write_csv, write_reports
from models._nb_char_blend import TrainOOFMarginBlend
from run import environment, protocol_id, refresh_comparison, write_json, write_tuning_reports


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def load_source(directory, name, train_rows, targets):
    meta = json.loads((directory / 'run.json').read_text(encoding='utf-8-sig'))
    if (meta.get('status') != 'completed' or meta.get('model_name') != name
            or meta.get('labels') != list(LABELS) or meta.get('dataset_id') != dataset_id()
            or meta.get('protocol_id') != protocol_id()):
        raise ValueError(f'{directory.name}: expected a compatible completed {name} run.')
    bundle = joblib.load(directory / 'model.joblib')
    for key in ('labels', 'dataset_id', 'protocol_id'):
        if bundle.get(key) != meta.get(key):
            raise ValueError(f'{directory.name}: model and metadata disagree on {key}.')
    if bundle.get('score_method') != 'decision_function':
        raise ValueError(f'{directory.name}: expected decision margins.')
    rows = read_csv(directory / 'train_oof_scores.csv')
    if [row['news_id'] for row in rows] != [row['news_id'] for row in train_rows]:
        raise ValueError(f'{directory.name}: Train OOF article order differs.')
    truth = np.array([[int(row[f'true::{label}']) for label in LABELS] for row in rows])
    scores = np.array([[float(row[f'score::{label}']) for label in LABELS] for row in rows])
    folds = np.array([int(row['fold']) for row in rows])
    if (not np.array_equal(truth, targets) or not np.isfinite(scores).all()
            or not np.array_equal(scores, bundle['model'].oof_scores_)
            or not np.array_equal(folds, bundle['model'].oof_fold_ids_)
            or not np.array_equal(np.asarray(bundle['train_support']), targets.sum(axis=0))
            or not np.array_equal(bundle['thresholds'], bundle['model'].decision_thresholds_)):
        raise ValueError(f'{directory.name}: inconsistent Train OOF scores, folds, support or cutoffs.')
    return bundle, meta, scores, folds


def verify_validation(directory, rows, component_scores, thresholds):
    cached = read_csv(directory / 'validation_predictions.csv')
    indexed = {row['news_id']: row for row in cached}
    if len(indexed) != len(cached) or set(indexed) != {row['news_id'] for row in rows}:
        raise ValueError(f'{directory.name}: Validation article IDs differ.')
    predictions = component_scores >= thresholds
    for index, source in enumerate(rows):
        saved = indexed[source['news_id']]
        if any(source[key] != saved[key] for key in ('title', 'body')):
            raise ValueError(f'{directory.name}: Validation text differs.')
        expected = {label for label in LABELS if source[label] == '1'}
        actual = {label for label, value in zip(LABELS, predictions[index]) if value}
        if (set(filter(None, saved['true_labels'].split(', '))) != expected
                or set(filter(None, saved['predicted_labels'].split(', '))) != actual):
            raise ValueError(f'{directory.name}: source Validation references/predictions do not reproduce.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nb-run', default='20261019T071053_870233Z_nbsvm_oof')
    parser.add_argument('--char-run', default='20261019T055745_463400Z_char_svm_oof_thresholds')
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error('--threads must be positive')
    if len(LABELS) != 11:
        raise ValueError('This experiment requires the agreed 11-label scope.')
    started = time.perf_counter()
    train_rows, _, targets = load_split('train')
    nb_path, char_path = ROOT / 'outputs' / args.nb_run, ROOT / 'outputs' / args.char_run
    print(f'Loading NB-SVM: {nb_path.name}', flush=True)
    nb, nb_meta, nb_oof, nb_folds = load_source(nb_path, 'nbsvm_oof', train_rows, targets)
    print(f'Loading character SVM: {char_path.name}', flush=True)
    char, char_meta, char_oof, char_folds = load_source(char_path, 'char_svm_oof_thresholds', train_rows, targets)
    if not np.array_equal(nb_folds, char_folds):
        raise ValueError('Source OOF fold assignments differ; use runs with the same fixed Train folds.')
    model = TrainOOFMarginBlend(nb['model'], char['model']).fit_oof(
        nb_oof, char_oof, targets, nb['thresholds'], char['thresholds'])
    model.oof_fold_ids_ = nb_folds.copy()
    print(f'Train-selected weights: NB={model.nb_weight_:.2f}, char={1-model.nb_weight_:.2f}', flush=True)
    if model.nb_weight_ in (0.0, 1.0):
        print('Train OOF selected a single source model; mixing did not improve the tuning objective.', flush=True)
    # All weights, scaling and thresholds are frozen before loading Validation.
    rows, texts, y_val = load_split('validation')
    check_separation(train_rows, rows)
    with threadpool_limits(limits=args.threads):
        nb_scores, _, _ = score_model(nb['model'], texts, 'decision_function')
        char_scores, _, _ = score_model(char['model'], texts, 'decision_function')
        verify_validation(nb_path, rows, nb_scores, nb['thresholds'])
        verify_validation(char_path, rows, char_scores, char['thresholds'])
        scores, method, seconds = score_model(model, texts, 'decision_function')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '_nb_char_blend_oof'
    output = OUTPUT_DIR / run_id
    output.mkdir(parents=True, exist_ok=False)
    historical_fit = float(nb_meta['fit_seconds']) + float(char_meta['fit_seconds'])
    meta = {'run_id': run_id, 'status': 'running', 'model_name': 'nb_char_blend_oof',
            'description': 'Train-OOF-selected convex blend of saved NB-SVM and character SVM.',
            'labels': list(LABELS), 'dataset_id': dataset_id(), 'protocol_id': protocol_id(),
            'threads': args.threads, 'environment': environment(), 'score_method': method,
            'thresholds': model.decision_thresholds_.tolist(), 'threshold_policy': model.threshold_policy_,
            'threshold_tuning': model.tuning_summary_, 'fit_seconds': historical_fit,
            'fit_time_note': 'Sum of historical source fit times; this build refits neither base model.',
            'new_base_fit_seconds': 0.0, 'train_rows': len(train_rows), 'validation_rows': len(rows),
            'score_semantics': 'Weighted standardized SVM margins, not calibrated probabilities.',
            'source_runs': {'nb': nb_path.name, 'char': char_path.name},
            'source_sha256': {'nb_model': sha256(nb_path / 'model.joblib'),
                              'char_model': sha256(char_path / 'model.joblib'),
                              'build_script': sha256(ROOT / 'build_nb_char_blend.py'),
                              'blend_code': sha256(ROOT / 'models/_nb_char_blend.py')}}
    write_json(output / 'run.json', meta)
    try:
        summary = write_reports(output, rows, y_val, scores, model.decision_thresholds_, 'validation',
                                {'fit_seconds': historical_fit, 'score_seconds': seconds}, targets.sum(axis=0))
        write_tuning_reports(output, model, train_rows, targets)
        write_csv(output / 'weight_search_train.csv', model.weight_search_, list(model.weight_search_[0]))
        bundle = {'model': model, 'labels': list(LABELS), 'thresholds': model.decision_thresholds_.copy(),
                  'score_method': method, 'train_support': targets.sum(axis=0),
                  'dataset_id': meta['dataset_id'], 'protocol_id': meta['protocol_id']}
        joblib.dump(bundle, output / 'model.joblib', compress=3)
        meta.update(status='completed', artifact_bytes=(output / 'model.joblib').stat().st_size,
                    build_seconds=time.perf_counter() - started)
        write_json(output / 'run.json', meta)
    except Exception as exc:
        meta.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        write_json(output / 'run.json', meta)
        raise
    refresh_comparison()
    print(f'Validation ({len(LABELS)} labels; all blend choices made on Train OOF)')
    print(f'{"Average":<10} {"Precision":>10} {"Recall":>10} {"F1":>10}')
    for average in ('macro', 'micro'):
        print(f'{average:<10} {summary[average + "_precision"]:>10.4f} '
              f'{summary[average + "_recall"]:>10.4f} {summary[average + "_f1"]:>10.4f}')
    print(f'Score: {seconds:.2f} s | {1000*seconds/len(rows):.3f} ms/article')
    print(f'Saved: {output}')
    print('Both source runs preserved. Compare: python run.py --compare')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, ImportError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)

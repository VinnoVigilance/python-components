"""Train one model on the fixed training split and evaluate on validation only."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
import platform
from pathlib import Path
import sys
import time
import warnings

try:
    import joblib
    import numpy as np
    import scipy
    import sklearn
    from sklearn.exceptions import ConvergenceWarning
    from threadpoolctl import threadpool_limits
except ImportError as exc:
    raise SystemExit(f'Missing dependency: {exc.name}. Install requirements.txt in your virtual environment; see README.md.') from exc

from config import DATA_DIR, LABELS, OUTPUT_DIR, ROOT, SEED
from data import check_separation, dataset_id, load_split, manifest, sha256
from evaluation import score_model, write_csv, write_reports

COMPARISON_COLUMNS = (
    'run_id', 'model', 'split', 'macro_precision', 'macro_recall', 'macro_f1',
    'micro_f1', 'micro_precision', 'micro_recall', 'macro_average_precision',
    'exact_match', 'fit_seconds', 'score_ms_per_article', 'artifact_mb', 'threads',
    'dataset_id', 'protocol_id', 'label_count',
)


def write_json(path, content):
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def protocol_id():
    content = b''.join((ROOT / name).read_bytes() for name in ('config.py', 'data.py', 'evaluation.py'))
    return hashlib.sha256(content).hexdigest()


def environment():
    return {'python': platform.python_version(), 'platform': platform.platform(),
            'processor': platform.processor(), 'logical_cpus': os.cpu_count(),
            'numpy': np.__version__, 'scipy': scipy.__version__,
            'scikit_learn': sklearn.__version__, 'joblib': joblib.__version__}


def available_models():
    return sorted(p.stem for p in (ROOT / 'models').glob('*.py') if not p.stem.startswith('_'))


def trained_thresholds(model, method):
    default = np.full(len(LABELS), 0.5 if method == 'predict_proba' else 0.0)
    values = np.asarray(getattr(model, 'decision_thresholds_', default), dtype=float)
    if values.shape != (len(LABELS),) or not np.isfinite(values).all():
        raise ValueError('Expected one finite trained threshold per label.')
    return values.copy()


def write_tuning_reports(directory, model, train_rows, targets):
    if not hasattr(model, 'threshold_report_'):
        return
    rows = [{'label': label, **detail} for label, detail in zip(LABELS, model.threshold_report_)]
    write_csv(directory / 'threshold_tuning_per_label.csv', rows, list(rows[0]))
    write_json(directory / 'threshold_tuning.json', model.tuning_summary_)
    predictions = []
    for i, source in enumerate(train_rows):
        row = {'news_id': source['news_id'], 'fold': int(model.oof_fold_ids_[i])}
        row.update({f'true::{label}': int(targets[i, j]) for j, label in enumerate(LABELS)})
        row.update({f'score::{label}': float(model.oof_scores_[i, j]) for j, label in enumerate(LABELS)})
        predictions.append(row)
    write_csv(directory / 'train_oof_scores.csv', predictions, list(predictions[0]))


def refresh_comparison():
    rows = []
    if not OUTPUT_DIR.exists():
        return rows
    for directory in sorted(OUTPUT_DIR.iterdir()):
        meta_path, score_path = directory / 'run.json', directory / 'validation_summary.json'
        if not meta_path.is_file() or not score_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding='utf-8'))
        if meta.get('status') != 'completed':
            continue
        if (meta.get('dataset_id') != dataset_id() or meta.get('protocol_id') != protocol_id()
                or meta.get('labels') != list(LABELS)):
            continue
        scores = json.loads(score_path.read_text(encoding='utf-8'))
        rows.append({'run_id': directory.name, 'model': meta['model_name'], 'split': 'validation',
                     'macro_precision': scores['macro_precision'], 'macro_recall': scores['macro_recall'],
                     'macro_f1': scores['macro_f1'], 'micro_f1': scores['micro_f1'],
                     'micro_precision': scores['micro_precision'], 'micro_recall': scores['micro_recall'],
                     'macro_average_precision': scores['macro_average_precision_supported'],
                     'exact_match': scores['exact_match'], 'fit_seconds': scores['fit_seconds'],
                     'score_ms_per_article': 1000 * scores['score_seconds'] / scores['rows'],
                     'artifact_mb': meta['artifact_bytes'] / (1024 ** 2), 'threads': meta['threads'],
                     'dataset_id': meta['dataset_id'], 'protocol_id': meta['protocol_id'],
                     'label_count': len(LABELS)})
    rows.sort(key=lambda r: (-r['macro_f1'], -r['micro_f1'], r['run_id']))
    # This CSV is a derived index. Clear stale rows after a scope/protocol change;
    # the original run folders and their reports remain untouched.
    write_csv(OUTPUT_DIR / 'model_comparison.csv', rows, COMPARISON_COLUMNS)
    return rows


def show_comparison():
    rows = refresh_comparison()
    if not rows:
        print(f'No completed {len(LABELS)}-label runs match the current dataset and evaluation protocol. '
              'Historical run folders are preserved; train a new run first.')
        return
    latest = {}
    for row in sorted(rows, key=lambda row: row['run_id']):
        latest[row['model']] = row
    selected = sorted(latest.values(), key=lambda row: (-row['macro_f1'], -row['micro_f1']))
    width = max(20, max(len(row['model']) for row in selected))
    print('Validation comparison: latest completed run per model, sorted by Macro-F1')
    print(f'Scores use the same {len(LABELS)} intersection labels and fixed data split.')
    print(f"{'Model':<{width}} {'Macro P':>8} {'Macro R':>8} {'Macro F1':>8} "
          f"{'Micro P':>8} {'Micro R':>8} {'Micro F1':>8} {'Fit(s)':>8} {'Score ms':>9}")
    for row in selected:
        print(f"{row['model']:<{width}} {row['macro_precision']:>8.4f} {row['macro_recall']:>8.4f} "
              f"{row['macro_f1']:>8.4f} {row['micro_precision']:>8.4f} {row['micro_recall']:>8.4f} "
              f"{row['micro_f1']:>8.4f} {row['fit_seconds']:>8.2f} {row['score_ms_per_article']:>9.3f}")
    print('Score ms = scoring milliseconds per article; compare timings on the same machine.')
    print(f'Full history: {OUTPUT_DIR / "model_comparison.csv"}')
    for row in selected:
        print(f"{row['model']}: {row['run_id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    names = available_models()
    parser.add_argument('--model', choices=names, help='Omit to choose from an interactive menu')
    parser.add_argument('--compare', action='store_true', help='Show validation results side by side; no training')
    parser.add_argument('--preview', type=int, help='Preview N Train articles for a supported zero-shot model; no benchmark run')
    parser.add_argument('--threads', type=int, default=4, help='Limit CPU math-library threads; default: 4')
    args = parser.parse_args()
    if args.compare:
        if args.model is not None or args.preview is not None:
            parser.error('Use --compare on its own, without --model or --preview.')
        show_comparison()
        return
    if args.model is None:
        for number, name in enumerate(names, 1):
            print(f'{number}. {name}')
        choice = input('Choose model number: ').strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(names):
            parser.error('Enter one of the model numbers shown above.')
        args.model = names[int(choice) - 1]
    if args.threads < 1:
        parser.error('--threads must be positive')
    if args.preview is not None and args.preview < 1:
        parser.error('--preview must be positive')
    train_rows, train_text, y_train = load_split('train')
    if ((y_train.sum(axis=0) == 0) | (y_train.sum(axis=0) == len(y_train))).any():
        raise ValueError('Each active label must have positive and negative training examples.')
    module = importlib.import_module(f'models.{args.model}')
    model = module.build(seed=SEED)
    if hasattr(model, 'torch_threads'):
        model.set_params(torch_threads=args.threads)
    encoder = getattr(model, 'named_steps', {}).get('encoder')
    if encoder is not None and hasattr(encoder, 'torch_threads'):
        encoder.set_params(torch_threads=args.threads)
    if args.preview is not None:
        if not getattr(model, 'supports_preview', False):
            parser.error('--preview is available for Laya zero-shot models; use a normal run for trained models.')
        count = min(args.preview, len(train_text))
        print(f'Train preview: {count} articles. No Validation/Test scoring or comparison entry.', flush=True)
        with threadpool_limits(limits=args.threads):
            started = time.perf_counter()
            model.fit(train_text[:count], y_train[:count])
            fit_seconds = time.perf_counter() - started
            scores, method, score_seconds = score_model(model, train_text[:count])
        thresholds = trained_thresholds(model, method)
        for row, values in zip(train_rows[:count], scores):
            labels = [label for label, value, cutoff in zip(LABELS, values, thresholds) if value >= cutoff]
            print(f"{row['news_id']} | {row['title'][:100]}")
            print('Predicted: ' + (', '.join(labels) if labels else '(none)'))
        print(f'Load/setup: {fit_seconds:.2f} s | Score: {score_seconds:.2f} s | '
              f'{score_seconds / count:.2f} s/article in this preview')
        print('Preview checks runtime and output format; it is not a model-quality estimate.')
        return
    val_rows, val_text, y_val = load_split('validation')
    check_separation(train_rows, val_rows)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ') + '_' + args.model
    directory = OUTPUT_DIR / run_id
    directory.mkdir(parents=True, exist_ok=False)
    meta = {'run_id': run_id, 'status': 'running', 'model_name': args.model,
            'description': module.DESCRIPTION, 'dataset_version': manifest()['dataset_version'],
            'dataset_id': dataset_id(), 'protocol_id': protocol_id(), 'labels': list(LABELS),
            'seed': SEED, 'threads': args.threads, 'input_column': 'text',
            'threshold_policy': 'fixed defaults; no validation or test threshold tuning',
            'environment': environment(), 'model_definition': repr(model),
            'source_sha256': {name: sha256(ROOT / name) for name in
                              ['config.py', 'data.py', 'evaluation.py', 'run.py', f'models/{args.model}.py',
                               *getattr(module, 'SOURCE_FILES', ())]}}
    write_json(directory / 'run.json', meta)
    try:
        print(f'Model: {args.model} | Train: {len(y_train)} | Validation: {len(y_val)} | Labels: {len(LABELS)}', flush=True)
        with threadpool_limits(limits=args.threads), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always')
            started = time.perf_counter()
            model.fit(train_text, y_train)
            fit_seconds = time.perf_counter() - started
            if any(issubclass(w.category, ConvergenceWarning) for w in caught):
                raise RuntimeError('Training did not converge. Increase max_iter in the model file and run again.')
            if hasattr(model, 'training_summary_'):
                write_json(directory / 'training_summary.json', model.training_summary_)
            scores, method, score_seconds = score_model(model, val_text)
        meta['warnings'] = [str(w.message) for w in caught]
        if encoder is not None and hasattr(encoder, 'runtime_metadata'):
            meta['encoder'] = encoder.runtime_metadata()
        thresholds = trained_thresholds(model, method)
        meta['threshold_policy'] = getattr(model, 'threshold_policy_', meta['threshold_policy'])
        if hasattr(model, 'tuning_summary_'):
            meta['threshold_tuning'] = model.tuning_summary_
        write_tuning_reports(directory, model, train_rows, y_train)
        support = y_train.sum(axis=0)
        summary = write_reports(directory, val_rows, y_val, scores, thresholds, 'validation',
                                {'fit_seconds': fit_seconds, 'score_seconds': score_seconds}, support)
        bundle = {'model': model, 'labels': list(LABELS), 'thresholds': thresholds,
                  'score_method': method, 'train_support': support,
                  'dataset_id': meta['dataset_id'], 'protocol_id': meta['protocol_id']}
        joblib.dump(bundle, directory / 'model.joblib', compress=3)
        meta.update({'status': 'completed', 'score_method': method, 'thresholds': thresholds.tolist(),
                     'fit_seconds': fit_seconds,
                     'train_rows': len(y_train), 'validation_rows': len(y_val),
                     'artifact_bytes': (directory / 'model.joblib').stat().st_size,
                     'feature_count': len(model.named_steps['tfidf'].vocabulary_)
                     if hasattr(model, 'named_steps') and 'tfidf' in model.named_steps else None})
        write_json(directory / 'run.json', meta)
        refresh_comparison()
        print(f'Validation ({len(LABELS)} labels)')
        print(f"{'Average':<10} {'Precision':>10} {'Recall':>10} {'F1':>10}")
        for average in ('macro', 'micro'):
            print(f"{average:<10} {summary[f'{average}_precision']:>10.4f} "
                  f"{summary[f'{average}_recall']:>10.4f} {summary[f'{average}_f1']:>10.4f}")
        print(f'Fit: {fit_seconds:.2f} s | Score: {score_seconds:.2f} s | Saved: {directory}')
    except Exception as exc:
        meta.update({'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'})
        write_json(directory / 'run.json', meta)
        raise


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)

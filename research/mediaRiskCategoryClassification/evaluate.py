"""Evaluate a saved run without fitting; use --split test only after selecting the final model."""
import argparse
import json
from pathlib import Path
import sys

from run import environment, protocol_id, write_json
import joblib
from threadpoolctl import threadpool_limits

from config import LABELS, ROOT
from data import check_separation, dataset_id, load_split
from evaluation import score_model, write_reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True, help='Completed output folder')
    parser.add_argument('--split', choices=['validation', 'test'], default='validation')
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error('--threads must be positive')
    directory = args.run if args.run.is_absolute() else ROOT / args.run
    meta = json.loads((directory / 'run.json').read_text(encoding='utf-8'))
    if meta['status'] != 'completed':
        raise ValueError('Only a completed training run can be evaluated.')
    if (meta.get('labels') != list(LABELS) or meta.get('dataset_id') != dataset_id()
            or meta.get('protocol_id') != protocol_id()):
        raise ValueError(f'This saved run belongs to a different label scope or protocol. '
                         f'Train a new {len(LABELS)}-label run; keep historical outputs unchanged.')
    bundle = joblib.load(directory / 'model.joblib')
    if (bundle['labels'] != list(LABELS) or bundle['dataset_id'] != dataset_id()
            or bundle['protocol_id'] != protocol_id()):
        raise ValueError('Saved model does not match the current dataset, label order or evaluation code.')
    train_rows, _, _ = load_split('train')
    rows, texts, targets = load_split(args.split)
    check_separation(train_rows, rows)
    if hasattr(bundle['model'], 'torch_threads'):
        bundle['model'].set_params(torch_threads=args.threads)
    encoder = getattr(bundle['model'], 'named_steps', {}).get('encoder')
    if encoder is not None and hasattr(encoder, 'torch_threads'):
        encoder.set_params(torch_threads=args.threads)
    with threadpool_limits(limits=args.threads):
        scores, method, seconds = score_model(bundle['model'], texts, bundle['score_method'])
    summary = write_reports(directory, rows, targets, scores, bundle['thresholds'], args.split,
                            {'fit_seconds': meta['fit_seconds'], 'score_seconds': seconds}, bundle['train_support'])
    write_json(directory / f'{args.split}_evaluation.json', {
        'split': args.split, 'threads': args.threads, 'environment': environment(),
        'dataset_id': dataset_id(), 'score_method': method,
        'thresholds': bundle['thresholds'].tolist(), 'threshold_policy': 'loaded from trained run; never retuned here',
    })
    print(f'{args.split.capitalize()} ({len(LABELS)} labels)')
    print(f"{'Average':<10} {'Precision':>10} {'Recall':>10} {'F1':>10}")
    for average in ('macro', 'micro'):
        print(f"{average:<10} {summary[f'{average}_precision']:>10.4f} "
              f"{summary[f'{average}_recall']:>10.4f} {summary[f'{average}_f1']:>10.4f}")
    if args.split == 'validation':
        from run import refresh_comparison
        refresh_comparison()
    print(f'Saved in {directory}')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, RuntimeError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise SystemExit(1)

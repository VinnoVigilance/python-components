"""Broad 5,000-row classical screen with chronological and publisher stress tests."""

import csv
import json
import time
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.model_selection import StratifiedGroupKFold

from run_classical_screen import get_scores, make_model, metrics, read_jsonl, vectorize_fold


DATA = Path('research/adverseNews/data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl')
OUT = Path('research/adverseNews/results/expanded-screen-v1')
SEED = 115


def spec(name, features, kind, **params):
    return {'name': name, 'features': features, 'kind': kind, 'params': params}


BROAD = [
    spec('majority', 'none', 'dummy'),
    spec('word_logistic', 'word', 'logistic', C=1),
    spec('word_linear_svm', 'word', 'linear_svm', C=1),
    spec('word_ridge', 'word', 'ridge', alpha=1),
    spec('word_sgd_hinge', 'word', 'sgd_hinge', alpha=1e-4),
    spec('word_pa1', 'word', 'passive_aggressive'),
    spec('word_complement_nb', 'word', 'complement_nb', alpha=0.1),
    spec('count_multinomial_nb', 'count', 'multinomial_nb', alpha=0.1),
    spec('count_complement_nb', 'count', 'complement_nb', alpha=0.1),
    spec('char_linear_svm', 'char', 'linear_svm', C=1),
    spec('hybrid_linear_svm', 'hybrid', 'linear_svm', C=1),
    spec('hybrid_logistic', 'hybrid', 'logistic', C=1),
    spec('hybrid_nbsvm', 'hybrid', 'nbsvm', C=1),
    spec('lsa_logistic', 'lsa', 'logistic', C=1),
    spec('lsa_rbf_svm', 'lsa', 'rbf_svm', C=1),
    spec('lsa_extra_trees', 'lsa', 'extra_trees', n_estimators=100),
    spec('lsa_hist_boost', 'lsa', 'hist_gradient_boosting'),
    spec('word_decision_tree', 'word', 'decision_tree', max_depth=20),
    spec('word_random_forest', 'word', 'random_forest', n_estimators=100),
    spec('word_extra_trees', 'word', 'extra_trees', n_estimators=100),
    spec('word_knn', 'word', 'knn', n_neighbors=7),
    spec('word_nearest_centroid', 'word', 'nearest_centroid'),
]


def save_csv(path, rows):
    if not rows:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(specs=BROAD, output=OUT, publisher_names=None, lsa_components=100):
    if output.exists():
        raise ValueError(f'output already exists: {output}')
    rows = read_jsonl(DATA)
    assert len(rows) == 5000 and len({r['prepared_group_id'] for r in rows}) == 5000
    dates = [r['published_date'][:10] for r in rows]
    assert all(len(d) == 10 for d in dates)
    y = np.array([r['label'] for r in rows], dtype=int)
    texts = [(r['title'] + '\n\n' + r['body']).strip() for r in rows]
    sources = np.array([r['source_name'] for r in rows])
    groups = np.array([r['prepared_group_id'] for r in rows])
    old = np.array([i for i, d in enumerate(dates) if d < '2025-01-01'])
    new = np.array([i for i, d in enumerate(dates) if d >= '2025-01-01'])
    assert len(old) > 2000 and len(new) > 500
    cv = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=SEED)
    splits = [('cv', f'fold-{k}', old[tr], old[va]) for k, (tr, va) in
              enumerate(cv.split(np.zeros(len(old)), y[old], groups[old]), 1)]
    splits.append(('temporal', '2025-plus', old, new))
    # ADB has one 2026 example and AMLC has no pre-2025 examples. Those sources
    # are reported in the temporal holdout and cannot have stable publisher F1.
    for source, count in Counter(sources[old]).items():
        if count >= 40:
            va = old[sources[old] == source]
            tr = old[sources[old] != source]
            splits.append(('publisher', source, tr, va))
    output.mkdir(parents=True)
    all_results, all_predictions, issues = [], [], []
    started = time.perf_counter()
    for stage, fold, train, valid in splits:
        assert not set(groups[train]) & set(groups[valid])
        assert set(y[train]) == {0, 1} and set(y[valid]) == {0, 1}
        print(f'{stage} {fold}: {len(train)} train, {len(valid)} held out', flush=True)
        matrix, feature_info = vectorize_fold([texts[i] for i in train],
                                              [texts[i] for i in valid], SEED,
                                              lsa_components=lsa_components)
        stage_specs = specs if stage != 'publisher' or publisher_names is None else [
            s for s in specs if s['name'] in publisher_names]
        for candidate in stage_specs:
            model = make_model(candidate, SEED)
            xtr, xva = matrix.get(candidate['features'], (None, None))
            if xtr is None:
                xtr, xva = np.zeros((len(train), 1)), np.zeros((len(valid), 1))
            try:
                t0 = time.perf_counter()
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    model.fit(xtr, y[train])
                    pred = model.predict(xva).astype(int)
                    score, score_kind = get_scores(model, xva, pred)
                result = metrics(y[valid], pred, score)
                all_results.append({'stage': stage, 'fold': fold, 'model': candidate['name'],
                                    'kind': candidate['kind'], 'features': candidate['features'],
                                    'train_n': len(train), 'valid_n': len(valid),
                                    'train_pos': int(y[train].sum()), 'valid_pos': int(y[valid].sum()),
                                    'seconds': round(time.perf_counter()-t0, 3), **result})
                for idx, truth, guess, value in zip(valid, y[valid], pred, score):
                    all_predictions.append({'stage': stage, 'fold': fold, 'model': candidate['name'],
                                            'article_id': rows[idx]['article_id'],
                                            'source': sources[idx], 'published_date': dates[idx],
                                            'y_true': int(truth), 'y_pred': int(guess),
                                            'score': float(value), 'score_kind': score_kind})
                for w in caught:
                    issues.append({'stage': stage, 'fold': fold, 'model': candidate['name'],
                                   'warning': str(w.message)})
            except Exception as error:
                issues.append({'stage': stage, 'fold': fold, 'model': candidate['name'],
                               'error': f'{type(error).__name__}: {error}'})
                print(f'FAILED {candidate["name"]}: {error}', flush=True)
        del matrix
        save_csv(output / 'metrics.csv', all_results)
        save_csv(output / 'predictions.csv', all_predictions)
        save_csv(output / 'issues.csv', issues)
        (output / 'progress.json').write_text(json.dumps({'completed_split': f'{stage}:{fold}',
            'splits_completed': len({(r['stage'], r['fold']) for r in all_results}),
            'models_completed': len(all_results), 'elapsed_seconds': time.perf_counter()-started,
            'feature_info': feature_info}, indent=2), encoding='utf-8')
    (output / 'manifest.json').write_text(json.dumps({'data': str(DATA), 'rows': len(rows),
        'pre_2025': len(old), 'news_2025_plus': len(new), 'candidate_models': len(specs),
        'lsa_components': lsa_components,
        'metrics_rows': len(all_results), 'issues': len(issues),
        'total_seconds': time.perf_counter()-started,
        'label_reference': 'proposed silver; no independent human benchmark',
        'same_case_review_complete': False}, indent=2), encoding='utf-8')
    print('DONE', len(all_results), 'model/split fits', flush=True)


if __name__ == '__main__':
    main(publisher_names={
        'majority', 'word_linear_svm', 'word_complement_nb', 'char_linear_svm',
        'hybrid_linear_svm', 'hybrid_logistic', 'hybrid_nbsvm', 'lsa_logistic',
        'lsa_rbf_svm', 'lsa_extra_trees', 'word_random_forest'})

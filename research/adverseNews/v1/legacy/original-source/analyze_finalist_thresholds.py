"""Select provisional operating thresholds on older OOF scores; assess on recent news."""

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.metrics import f1_score, precision_score, recall_score, confusion_matrix


ROOT = Path('research/adverseNews/results')
OUT = ROOT / 'finalist-thresholds-v1'
SOURCES = {
    'lsa100_c3': ('refined-screen-v1', 'lsa_rbf_C3'),
    'lsa200_c3': ('lsa200-screen-v1', 'lsa200_rbf_C3'),
    'char_svm_c1': ('expanded-screen-v1', 'char_linear_svm'),
}


def read_predictions(run, model, stage):
    result = {}
    with (ROOT / run / 'predictions.csv').open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row['stage'] == stage and row['model'] == model:
                result[row['article_id']] = {'y': int(row['y_true']),
                                             'score': float(row['score']),
                                             'source': row['source']}
    return result


def measures(y, pred):
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    return {'f1': float(f1_score(y, pred, zero_division=0)),
            'precision': float(precision_score(y, pred, zero_division=0)),
            'recall': float(recall_score(y, pred, zero_division=0)),
            'tn': int(tn), 'fp': int(fp), 'fn': int(fn), 'tp': int(tp)}


def main():
    if OUT.exists():
        raise ValueError(f'output exists: {OUT}')
    OUT.mkdir(parents=True)
    score_data = {name: {stage: read_predictions(*source, stage) for stage in ('cv', 'temporal')}
                  for name, source in SOURCES.items()}
    cv_ids = sorted(set.intersection(*(set(d['cv']) for d in score_data.values())))
    recent_ids = sorted(set.intersection(*(set(d['temporal']) for d in score_data.values())))
    assert len(cv_ids) == 4053 and len(recent_ids) == 947
    rows = []
    votes = defaultdict(dict)
    for name, stages in score_data.items():
        old = stages['cv']; new = stages['temporal']
        y_old = np.array([old[i]['y'] for i in cv_ids]); scores_old = np.array([old[i]['score'] for i in cv_ids])
        y_new = np.array([new[i]['y'] for i in recent_ids]); scores_new = np.array([new[i]['score'] for i in recent_ids])
        candidates = np.unique(np.r_[0.0, np.quantile(scores_old, np.linspace(0.01, 0.99, 199))])
        choices = []
        for threshold in candidates:
            m = measures(y_old, scores_old >= threshold)
            choices.append((float(threshold), m))
        # Thresholds are selected only with out-of-fold predictions on older news.
        optimized = {
            'default_zero': (0.0, measures(y_old, scores_old >= 0)),
            'best_f1': max(choices, key=lambda x: (x[1]['f1'], x[1]['recall'])),
            'recall_at_least_95': max((x for x in choices if x[1]['recall'] >= 0.95),
                                      key=lambda x: (x[1]['precision'], x[1]['f1'])),
            'fn_cost_5x_fp': min(choices, key=lambda x: (5*x[1]['fn']+x[1]['fp'], -x[1]['f1']))
        }
        for operation, (threshold, cv_metrics) in optimized.items():
            new_pred = (scores_new >= threshold).astype(int)
            rows.append({'model': name, 'operation': operation, 'threshold': threshold,
                         **{'cv_'+k: v for k,v in cv_metrics.items()},
                         **{'recent_'+k: v for k,v in measures(y_new, new_pred).items()}})
            if operation == 'default_zero':
                votes[name]['cv'] = (scores_old >= threshold).astype(int)
                votes[name]['temporal'] = new_pred

    for stage, ids in (('cv', cv_ids), ('temporal', recent_ids)):
        yy = np.array([score_data['lsa100_c3'][stage][i]['y'] for i in ids])
        tally = sum(votes[name][stage] for name in SOURCES)
        for operation, pred in (('any_positive', tally >= 1),
                                ('majority_2_of_3', tally >= 2),
                                ('all_positive', tally == 3)):
            row = next((r for r in rows if r['model']==f'vote_{operation}'), None)
            if row is None:
                row = {'model': f'vote_{operation}', 'operation': operation,
                       'threshold': None}
                rows.append(row)
            prefix = 'recent' if stage == 'temporal' else 'cv'
            row.update({prefix+'_'+k: v for k,v in measures(yy, pred).items()})
    with (OUT / 'operating-points.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        columns = ['model','operation','threshold','cv_f1','cv_precision','cv_recall',
                   'cv_tn','cv_fp','cv_fn','cv_tp','recent_f1','recent_precision',
                   'recent_recall','recent_tn','recent_fp','recent_fn','recent_tp']
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader();writer.writerows(rows)
    (OUT / 'manifest.json').write_text(json.dumps({'older_oof_n':len(cv_ids),
         'recent_n':len(recent_ids),'threshold_selection':'older out-of-fold silver labels only',
         'recent_selection_used':False,'reviewed_truth_available':False,
         'same_case_review_complete':False},indent=2),encoding='utf-8')
    print(json.dumps(rows,indent=2))


if __name__ == '__main__':
    main()

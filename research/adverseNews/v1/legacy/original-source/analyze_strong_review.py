"""Join blind strong-model review to prior labels and finalist predictions."""

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from sklearn.metrics import f1_score, precision_score, recall_score


ROOT = Path('research/adverseNews')
REVIEW = ROOT / 'data/review/strong-review-v1'
PRED = ROOT / 'results'
SOURCES = {
    'lsa100_c3': ('refined-screen-v1', 'lsa_rbf_C3'),
    'lsa200_c3': ('lsa200-screen-v1', 'lsa200_rbf_C3'),
    'char_svm_c1': ('expanded-screen-v1', 'char_linear_svm'),
}


def jsonl(path):
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def predictions(run, model):
    result = {}
    with (PRED / run / 'predictions.csv').open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row['model'] == model and row['stage'] in ('temporal','publisher'):
                result[(row['stage'], row['article_id'])] = int(row['y_pred'])
    return result


def score(rows, field):
    y = [r['astra_label'] for r in rows]
    p = [r[field] for r in rows]
    return {'n': len(rows), 'review_positive': sum(y),
            'f1': round(f1_score(y, p, zero_division=0), 3),
            'precision': round(precision_score(y, p, zero_division=0), 3),
            'recall': round(recall_score(y, p, zero_division=0), 3),
            'disagreements': sum(a != b for a,b in zip(y,p))}


def main():
    out = REVIEW / 'comparison'
    if out.exists():
        raise ValueError(f'output exists: {out}')
    session = json.loads((REVIEW / 'session.json').read_text(encoding='utf-8'))
    labels = jsonl(REVIEW / 'returns/astra-high/review-labels.jsonl')
    expected = [rid for b in session['batches'] for rid in b['article_ids']]
    assert len(labels) == len(expected) == 100
    assert [r['article_id'] for r in labels] == expected
    assert all(set(r)=={'article_id','label','evidence','reason','uncertain'}
               and type(r['label']) is int and r['label'] in (0,1)
               and type(r['uncertain']) is bool for r in labels)
    internal = {r['article_id']:r for r in jsonl(REVIEW / 'internal-selection.jsonl')}
    pred_maps = {name:predictions(*source) for name,source in SOURCES.items()}
    rows=[]
    for item in labels:
        rid=item['article_id']; meta=internal[rid]
        stage='temporal' if meta['stratum']=='recent_random' else 'publisher'
        row={'article_id':rid,'stratum':meta['stratum'],'source':meta['source'],
             'published_date':meta['published_date'],'silver_label':meta['existing_label'],
             'astra_label':item['label'],'astra_uncertain':item['uncertain'],
             'astra_evidence':item['evidence'],'astra_reason':item['reason']}
        for name, predictions_for_model in pred_maps.items():
            row[name]=predictions_for_model[(stage,rid)]
        rows.append(row)
    out.mkdir()
    with (out/'review-comparison.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    disagreements=[r for r in rows if r['silver_label']!=r['astra_label']]
    with (out/'silver-disagreements.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(disagreements)
    summary={'article_count':len(rows),'astra_uncertain':sum(r['astra_uncertain'] for r in rows),
             'silver_disagreements':len(disagreements),'strata':{}}
    for stratum in ('recent_random','transfer_targeted'):
        subset=[r for r in rows if r['stratum']==stratum]
        summary['strata'][stratum]={name:score(subset,name) for name in
            ('silver_label',*SOURCES)}
        summary['strata'][stratum]['source_counts']=dict(Counter(r['source'] for r in subset))
    (out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__ == '__main__':
    main()

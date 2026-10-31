"""Measure queue size and silver misses at existing fixed LSA thresholds."""
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from sklearn.metrics import f1_score,precision_score,recall_score

from run_encoder_screen import csv_write,json_write

ROOT=Path(__file__).resolve().parent
INPUT=ROOT/'results/lsa200-screen-v1/predictions.csv'
OUT=ROOT/'results/source-triage-v1'


def main():
    with INPUT.open(encoding='utf-8-sig',newline='') as f:
        rows=[r for r in csv.DictReader(f) if r['model']=='lsa200_rbf_C3' and r['stage']=='publisher']
    by_source=defaultdict(list)
    for r in rows: by_source[r['fold']].append(r)
    output=[]
    for source,items in sorted(by_source.items()):
        truth=np.array([int(r['y_true']) for r in items]); score=np.array([float(r['score']) for r in items])
        for threshold,name in [(0.,'default'),(-0.619,'old_cv_high_recall')]:
            pred=(score>=threshold).astype(int)
            output.append(dict(source=source,threshold_name=name,threshold=threshold,
                articles=len(items),silver_positive=int(truth.sum()),review_queue=int(pred.sum()),
                silver_missed=int(((truth==1)&(pred==0)).sum()),
                silver_false_flags=int(((truth==0)&(pred==1)).sum()),
                f1=float(f1_score(truth,pred,zero_division=0)),
                precision=float(precision_score(truth,pred,zero_division=0)),
                recall=float(recall_score(truth,pred,zero_division=0))))
    OUT.mkdir(parents=True,exist_ok=True)
    csv_write(OUT/'publisher-queue-tradeoffs.csv',output)
    json_write(OUT/'manifest.json',dict(score_input=str(INPUT),
        thresholds={'default':0,'old_cv_high_recall':-0.619},
        policy='Unknown publisher remains in human review even below threshold; score only prioritizes queue',
        labels='silver; this cannot certify auto-clear',
        rows=len(output)))
    print(json.dumps(output,indent=2))

if __name__=='__main__': main()

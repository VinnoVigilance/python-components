"""Retrospective source-depth curves for later UK_GOV and PTV news."""
import hashlib
import json
import time
from pathlib import Path

import numpy as np
from scipy.sparse import hstack
from sklearn.decomposition import TruncatedSVD
from sklearn.preprocessing import normalize
from sklearn.svm import SVC
from threadpoolctl import threadpool_limits

from run_classical_screen import tfidf_word,tfidf_char,read_jsonl
from run_encoder_screen import scores,digest,json_write,csv_write

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
OUT=ROOT/'results/source-depth-v1'
SPLITS=(('PTV','2019-01-01'),('UK_GOV','2020-01-01'))


def order(r):
    return hashlib.sha256(('PDG-115-source-depth-v1|'+r['prepared_group_id']).encode()).hexdigest()


def main():
    rows=read_jsonl(DATA)
    OUT.mkdir(parents=True,exist_ok=True)
    prior=OUT/'metrics.csv'
    if prior.exists():
        import csv
        with prior.open(encoding='utf-8-sig') as f: results=list(csv.DictReader(f))
    else: results=[]
    done={(r['source'],r['source_fraction']) for r in results}
    for source,cutoff in SPLITS:
        train_base=[r for r in rows if r['published_date']<cutoff and r['source_name']!=source]
        source_pool=sorted((r for r in rows if r['published_date']<cutoff and r['source_name']==source),key=order)
        valid=[r for r in rows if r['published_date']>=cutoff and r['source_name']==source]
        assert len(valid)>=400 and sum(r['label'] for r in valid)>=50
        for fraction in (0.25,0.5,1.0):
            tag=f'{fraction:g}'
            if (source,tag) in done: continue
            n=round(len(source_pool)*fraction)
            train=train_base+source_pool[:n]
            assert not {r['prepared_group_id'] for r in train}&{r['prepared_group_id'] for r in valid}
            text=[(r['title']+'\n\n'+r['body']).strip() for r in train]
            held=[(r['title']+'\n\n'+r['body']).strip() for r in valid]
            y=np.array([r['label'] for r in train])
            truth=np.array([r['label'] for r in valid])
            t=time.perf_counter()
            word=tfidf_word(); char=tfidf_char()
            x=hstack([word.fit_transform(text),char.fit_transform(text)],format='csr',dtype=np.float32)
            va=hstack([word.transform(held),char.transform(held)],format='csr',dtype=np.float32)
            svd=TruncatedSVD(n_components=200,n_iter=5,random_state=115)
            z=normalize(svd.fit_transform(x)).astype(np.float32)
            vz=normalize(svd.transform(va)).astype(np.float32)
            model=SVC(C=3,kernel='rbf',class_weight='balanced')
            model.fit(z,y)
            value=model.decision_function(vz)
            result=dict(source=source,cutoff=cutoff,source_fraction=tag,
                        source_train_n=n,other_train_n=len(train_base),held_n=len(valid),
                        source_train_pos=sum(r['label'] for r in source_pool[:n]),
                        seconds=round(time.perf_counter()-t,2),
                        **scores(truth,(value>=0).astype(int),value))
            results.append(result)
            csv_write(OUT/'metrics.csv',results)
            print(source,tag,'F1',round(result['f1'],3),flush=True)
    json_write(OUT/'manifest.json',dict(data_sha256=digest(DATA),seed=115,splits=SPLITS,
        fits=len(results),status='exploratory_silver_label_time_splits',
        caveats=['same-case links beyond prepared groups unresolved',
                 'historical model search has seen this corpus',
                 'source sample follows fixed hash, not active-label selection',
                 'not independent production accuracy']))

if __name__=='__main__':
    with threadpool_limits(limits=4): main()

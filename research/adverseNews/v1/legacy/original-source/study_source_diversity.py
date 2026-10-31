"""Controlled 3-vs-4 source screen for unseen-publisher LSA transfer.

All fits contain 900 older articles. This tests five available large publishers,
not a universal publisher-count threshold or production accuracy.
"""
import csv
import hashlib
import itertools
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
OUT=ROOT/'results/source-diversity-v1'
MAJOR=('DOJ_PH','NBI','PTV','SEC_PH','UK_GOV')


def order(row):
    return hashlib.sha256(('PDG-115-diversity-v1|'+row['prepared_group_id']).encode()).hexdigest()


def main():
    rows=read_jsonl(DATA)
    old=[r for r in rows if (r['published_date'][:10]<'2025-01-01'
         and r['source_name'] in MAJOR)]
    pools={s:sorted((r for r in old if r['source_name']==s),key=order) for s in MAJOR}
    assert all(len(pools[s])>=300 for s in MAJOR)
    OUT.mkdir(parents=True,exist_ok=True)
    prior=OUT/'metrics.csv'
    results=[]
    if prior.exists():
        with prior.open(encoding='utf-8-sig',newline='') as f: results=list(csv.DictReader(f))
    done={(r['held_source'],r['training_sources']) for r in results}
    started=time.perf_counter()
    for held in MAJOR:
        available=[s for s in MAJOR if s!=held]
        for count in (3,4):
            for chosen in itertools.combinations(available,count):
                name=','.join(chosen)
                if (held,name) in done: continue
                per_source=900//count
                train=[r for s in chosen for r in pools[s][:per_source]]
                valid=pools[held]
                assert len(train)==900 and not {r['prepared_group_id'] for r in train}&{r['prepared_group_id'] for r in valid}
                texts_train=[(r['title']+'\n\n'+r['body']).strip() for r in train]
                texts_valid=[(r['title']+'\n\n'+r['body']).strip() for r in valid]
                y=np.array([r['label'] for r in train],dtype=int)
                truth=np.array([r['label'] for r in valid],dtype=int)
                t=time.perf_counter()
                word=tfidf_word(); char=tfidf_char()
                a=word.fit_transform(texts_train); b=char.fit_transform(texts_train)
                x=hstack([a,b],format='csr',dtype=np.float32)
                va=hstack([word.transform(texts_valid),char.transform(texts_valid)],format='csr',dtype=np.float32)
                svd=TruncatedSVD(n_components=200,n_iter=5,random_state=115)
                low=normalize(svd.fit_transform(x)).astype(np.float32)
                held_low=normalize(svd.transform(va)).astype(np.float32)
                model=SVC(C=3,kernel='rbf',gamma='scale',class_weight='balanced')
                model.fit(low,y)
                score=model.decision_function(held_low)
                pred=(score>=0).astype(int)
                row=dict(held_source=held,training_source_count=count,training_sources=name,
                         training_articles=len(train),articles_per_source=per_source,
                         training_positives=int(y.sum()),seconds=round(time.perf_counter()-t,2),
                         **scores(truth,pred,score))
                results.append(row)
                csv_write(OUT/'metrics.csv',results)
                print(held,count,name,'F1',round(row['f1'],3),flush=True)
    by_held={}
    for held in MAJOR:
        a=[r for r in results if r['held_source']==held and int(r['training_source_count'])==3]
        b=[r for r in results if r['held_source']==held and int(r['training_source_count'])==4]
        assert len(a)==4 and len(b)==1
        by_held[held]=dict(three_source_mean_f1=float(np.mean([float(r['f1']) for r in a])),
                          four_source_f1=float(b[0]['f1']),
                          three_source_mean_recall=float(np.mean([float(r['recall']) for r in a])),
                          four_source_recall=float(b[0]['recall']))
    json_write(OUT/'summary.json',dict(data_sha256=digest(DATA),seed=115,major_sources=MAJOR,
        fixed_training_articles=900,models=len(results),elapsed_seconds=time.perf_counter()-started,
        per_held_source=by_held,
        macro_three_source_f1=float(np.mean([r['three_source_mean_f1'] for r in by_held.values()])),
        macro_four_source_f1=float(np.mean([r['four_source_f1'] for r in by_held.values()])),
        scope='Five large existing sources only; not evidence for a minimum number of publishers',
        caveat='Changing source count changes article mix and per-source depth; labels are silver; same-case links unresolved'))

if __name__=='__main__':
    with threadpool_limits(limits=4): main()

"""Research queue router for new-publisher and weak-source adverse news.

Every article gets the current research LSA 200 score. No output is an automatic
clearance; the route is a conservative pilot recommendation for human review.
"""
import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.preprocessing import normalize

ROOT=Path(__file__).resolve().parent
BUNDLE=ROOT/'results/uk-followup-candidate-v1/candidate-bundle.joblib'
DATA=ROOT/'data/labeling/final-pilot-5000-v1/manifest.json'
WATCH={'UK_GOV','PTV'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True,
                        help='JSONL with article_id, source_name, title, body')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--bundle',type=Path,default=BUNDLE,
                        help='model bundle; defaults to the updated UK research candidate')
    args=parser.parse_args()
    if args.output.exists(): raise ValueError('output exists')
    known=set(json.loads(DATA.read_text(encoding='utf-8'))['source_counts'])
    with args.input.open(encoding='utf-8-sig') as f:
        articles=[json.loads(s) for s in f if s.strip()]
    if not articles or any(not all(k in r for k in ('article_id','source_name','title','body')) for r in articles):
        raise ValueError('each article needs article_id, source_name, title, body')
    bundle=joblib.load(args.bundle)
    scores=[]
    for start in range(0,len(articles),100):
        chunk=articles[start:start+100]
        texts=[(r['title']+'\n\n'+r['body']).strip() for r in chunk]
        word=bundle['word_vectorizer'].transform(texts)
        char=bundle['char_vectorizer'].transform(texts)
        hybrid=hstack([word,char],format='csr',dtype=np.float32)
        features=normalize(bundle['lsa200_svd'].transform(hybrid)).astype(np.float32)
        scores.extend(map(float,bundle['lsa200_rbf_c3'].decision_function(features)))
    with args.output.open('x',encoding='utf-8',newline='\n') as f:
        for row,score in zip(articles,scores):
            source=row['source_name']
            if source not in known:
                route='new_publisher_full_review'
            elif source in WATCH:
                route='weak_source_full_review'
            elif score>=0:
                route='positive_score_review'
            elif score>=-0.619:
                route='low_margin_review'
            else:
                route='routine_reference_review'
            f.write(json.dumps(dict(article_id=row['article_id'],source_name=source,
                score=score,default_positive=int(score>=0),route=route,
                auto_clear=False,model='lsa200_rbf_c3',
                note='research routing only; no production accuracy certification'),ensure_ascii=False)+'\n')
    print(json.dumps(dict(articles=len(articles),output=str(args.output),
        model_sha256=hashlib.sha256(args.bundle.read_bytes()).hexdigest())))

if __name__=='__main__': main()

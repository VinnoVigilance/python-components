"""Suggest possible same-case links before reference/training packets are used."""
import csv
import json
from datetime import date
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from run_encoder_screen import csv_write,json_write

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'results/source-stage-v1'
CORPUS=ROOT/'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'


def read_jsonl(path):
    with path.open(encoding='utf-8') as f:
        return [json.loads(s) for s in f if s.strip()]


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f: return list(csv.DictReader(f))


def main():
    existing=read_jsonl(CORPUS)
    selected=read_jsonl(OUT/'reference-blind.jsonl')+read_jsonl(OUT/'training-blind.jsonl')
    meta={r['article_id']:r for name in ('reference-selection-internal.csv','training-selection-internal.csv') for r in read_csv(OUT/name)}
    titles=[r['title'] for r in existing+selected]
    vec=TfidfVectorizer(analyzer='char_wb',ngram_range=(3,5),min_df=2,max_features=100000)
    matrix=vec.fit_transform(titles)
    suggestions=[]
    for start in range(0,len(selected),50):
        sim=cosine_similarity(matrix[len(existing)+start:len(existing)+start+50],matrix[:len(existing)])
        for local,row in enumerate(selected[start:start+50]):
            m=meta[row['article_id']]
            i=int(np.argmax(sim[local])); old=existing[i]
            similarity=float(sim[local,i])
            gap=abs((date.fromisoformat(m['published_date'][:10])-date.fromisoformat(old['published_date'][:10])).days)
            flag=(similarity>=0.85 or (similarity>=0.70 and gap<=30))
            suggestions.append(dict(article_id=row['article_id'],packet='reference' if start+local<150 else 'training',
                source=m['source'],title=row['title'],candidate_date=m['published_date'],
                closest_existing_id=old['article_id'],closest_existing_source=old['source_name'],
                closest_existing_title=old['title'],closest_existing_date=old['published_date'],
                title_cosine=round(similarity,4),day_gap=gap,review_link=flag,
                linked_by_human=False))
    csv_write(OUT/'case-link-review.csv',suggestions)
    json_write(OUT/'case-link-audit.json',dict(candidates=len(selected),flagged=sum(r['review_link'] for r in suggestions),
        method='title character TF-IDF nearest old article; heuristic only',
        exact_prepared_group_overlap=0,manual_case_review_complete=False,
        note='Do not claim final case-safe reference until flagged and unflagged coverage is adjudicated'))
    print(json.dumps(dict(candidates=len(selected),flagged=sum(r['review_link'] for r in suggestions))))

if __name__=='__main__': main()

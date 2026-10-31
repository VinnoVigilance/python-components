"""Freeze blind reference and choose source-specific articles for new labels.

This script selects articles and scores *unlabeled training candidates* for
annotation priority. It never assigns an adversity label or changes the corpus.
"""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.preprocessing import normalize

ROOT = Path(__file__).resolve().parent
PREP = ROOT/'data/prepared/db-selection-v3'
CORPUS = ROOT/'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
OUT = ROOT/'results/source-stage-v1'
REFERENCE_QUOTA = {'UK_GOV':30,'PTV':30,'NBI':20,'SEC_PH':20,'DOJ_PH':20,
                   'DTI_PH':10,'AMLC':10,'PCIJ':10}
TRAINING_SOURCES = ('UK_GOV','PTV')


def rows(path):
    with path.open(encoding='utf-8-sig') as f:
        for line in f:
            if line.strip(): yield json.loads(line)


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()


def order(row,tag):
    return hashlib.sha256(f'PDG-115-source-stage-v1|{tag}|{row["group_id"]}'.encode()).hexdigest()


def write_jsonl(path,items):
    with path.open('x',encoding='utf-8',newline='\n') as f:
        for item in items: f.write(json.dumps(item,ensure_ascii=False,sort_keys=True)+'\n')


def write_csv(path,items):
    with path.open('x',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(items[0])); writer.writeheader(); writer.writerows(items)


def main():
    if OUT.exists(): raise ValueError(f'already exists: {OUT}')
    snapshot=json.loads((PREP/'snapshot.json').read_text(encoding='utf-8'))
    for name,value in snapshot['outputs_sha256'].items():
        assert sha(PREP/name)==value, name
    labels=list(rows(CORPUS)); labeled_hash={r['content_hash'] for r in labels}
    labeled_groups={r['prepared_group_id'] for r in labels}
    candidates=[r for r in rows(PREP/'working-candidates.jsonl')
                if r['content_hash'] not in labeled_hash and r['group_id'] not in labeled_groups]
    assert len(candidates)==len({r['group_id'] for r in candidates})
    reserved=[r for r in candidates if r['candidate_split']=='reference_reserved_candidate']
    eligible=[r for r in candidates if r['candidate_split'] in ('train_candidate','dev_candidate')]
    assert not {r['group_id'] for r in reserved}&{r['group_id'] for r in eligible}
    reference=[]
    for source,n in REFERENCE_QUOTA.items():
        pool=sorted((r for r in reserved if r['source_name']==source),key=lambda r:order(r,'reference'))
        assert len(pool)>=n,(source,len(pool),n)
        reference.extend(pool[:n])
    # Reference selection is source-stratified random, never model-score-based.
    # Packets contain no model output or previous label.
    bundle_path=ROOT/'results/finalist-models-v1/finalist-bundle.joblib'
    bundle=joblib.load(bundle_path)
    training=[r for r in eligible if r['source_name'] in TRAINING_SOURCES]
    scores=[]
    for start in range(0,len(training),100):
        chunk=training[start:start+100]
        text=[(r['title']+'\n\n'+r['body']).strip() for r in chunk]
        word=bundle['word_vectorizer'].transform(text)
        char=bundle['char_vectorizer'].transform(text)
        hybrid=hstack([word,char],format='csr',dtype=np.float32)
        low=normalize(bundle['lsa200_svd'].transform(hybrid)).astype(np.float32)
        scores.extend(map(float,bundle['lsa200_rbf_c3'].decision_function(low)))
    score_by_group={r['group_id']:v for r,v in zip(training,scores)}
    selected=[]; selection=[]
    for source in TRAINING_SOURCES:
        remaining=[r for r in training if r['source_name']==source]
        # Four complementary slices. All proposed labels remain unknown.
        plans=[('high_score',25,lambda r:-score_by_group[r['group_id']]),
               ('low_score',25,lambda r:score_by_group[r['group_id']]),
               ('near_boundary',25,lambda r:abs(score_by_group[r['group_id']])),
               ('random',25,lambda r:order(r,'random'))]
        for reason,n,key in plans:
            take=sorted(remaining,key=lambda r:(key(r),order(r,reason)))[:n]
            assert len(take)==n
            for r in take:
                selected.append(r)
                selection.append(dict(article_id=r['article_id'],source=r['source_name'],
                    published_date=r['published_date'],group_id=r['group_id'],
                    selection_reason=reason,baseline_score=score_by_group[r['group_id']],
                    candidate_split=r['candidate_split']))
            used={r['group_id'] for r in take}
            remaining=[r for r in remaining if r['group_id'] not in used]
    assert len(selected)==200 and len(reference)==150
    assert not {r['group_id'] for r in selected}&{r['group_id'] for r in reference}
    OUT.mkdir(parents=True)
    def packet(items):
        return [dict(article_id=r['article_id'],title=r['title'],body=r['body']) for r in items]
    write_jsonl(OUT/'reference-blind.jsonl',packet(reference))
    write_jsonl(OUT/'training-blind.jsonl',packet(selected))
    write_csv(OUT/'training-selection-internal.csv',selection)
    write_csv(OUT/'reference-selection-internal.csv',[
        dict(article_id=r['article_id'],source=r['source_name'],published_date=r['published_date'],
             group_id=r['group_id'],candidate_split=r['candidate_split']) for r in reference])
    (OUT/'label-return-schema.json').write_text(json.dumps({
        'article_id':'exact packet ID','label':'integer 0 or 1',
        'evidence':'exact supporting passage for 1; empty for 0',
        'reason':'short policy-based explanation','uncertain':'boolean',
        'reviewer':'identifier of independent reviewer or teacher',
        'reference':'true only after independent, answer-blind human adjudication'},indent=2),encoding='utf-8')
    manifest=dict(status='packets_prepared_no_new_labels',corpus_sha256=sha(CORPUS),
        snapshot_sha256=sha(PREP/'snapshot.json'),bundle_sha256=sha(bundle_path),
        working_pool=len(candidates),reference_reserved_pool=len(reserved),
        nonreserved_pool=len(eligible),training_candidates_by_source=dict(Counter(r['source_name'] for r in training)),
        reference_quota=REFERENCE_QUOTA,training_quota={s:100 for s in TRAINING_SOURCES},
        model_scores_used_for='training selection only',reference_scored=False,
        trained_on_new_labels=False,case_review_complete=False,
        output_hashes={p.name:sha(p) for p in OUT.iterdir() if p.is_file()})
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps({k:manifest[k] for k in ('status','training_candidates_by_source','reference_quota')},indent=2))

if __name__=='__main__': main()

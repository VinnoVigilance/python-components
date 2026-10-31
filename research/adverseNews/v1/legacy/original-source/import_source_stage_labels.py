"""Validate returned source-stage labels and build a separate training corpus.

Never changes the original 5,000 labels. Reference answers are never appended.
"""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'results/source-stage-v1'
ORIGINAL=ROOT/'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            value.update(chunk)
    return value.hexdigest()


def json_write(path,value):
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')


def jsonl(path):
    with path.open(encoding='utf-8-sig') as f:
        return [json.loads(s) for s in f if s.strip()]


def csv_map(path):
    with path.open(encoding='utf-8-sig',newline='') as f:
        return {r['article_id']:r for r in csv.DictReader(f)}


def check(packet,returns,reference,allow_uncertain=False):
    expected={r['article_id'] for r in packet}
    assert len(expected)==len(packet)
    by_id={r['article_id']:r for r in returns}
    if len(by_id)!=len(returns) or set(by_id)!=expected:
        raise ValueError('returns must contain each packet article exactly once')
    packet_by_id={r['article_id']:r for r in packet}
    for r in returns:
        if type(r.get('label')) is not int or r['label'] not in (0,1):
            raise ValueError(f"{r['article_id']}: label must be integer 0/1")
        if type(r.get('uncertain')) is not bool:
            raise ValueError(f"{r['article_id']}: uncertain must be boolean")
        if not isinstance(r.get('reason'),str) or not r['reason'].strip():
            raise ValueError(f"{r['article_id']}: policy reason required")
        if not isinstance(r.get('evidence'),str) or (r['label']==1 and not r['evidence'].strip()):
            raise ValueError(f"{r['article_id']}: positive requires exact evidence")
        if r['label']==1 and r['evidence'].casefold() not in (
            packet_by_id[r['article_id']]['title']+' '+packet_by_id[r['article_id']]['body']
        ).casefold():
            raise ValueError(f"{r['article_id']}: positive evidence is not in the article")
        if not isinstance(r.get('reviewer'),str) or not r['reviewer'].strip():
            raise ValueError(f"{r['article_id']}: reviewer identity required")
        if r.get('reference') is not reference:
            raise ValueError(f"{r['article_id']}: incorrect reference flag")
        if r['uncertain'] and not allow_uncertain:
            raise ValueError(f"{r['article_id']}: uncertain case needs policy adjudication")
    return by_id


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--training-labels',type=Path,required=True)
    p.add_argument('--human-reference',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists(): raise ValueError('output exists')
    packet=jsonl(STAGE/'training-blind.jsonl')
    labels=check(packet,jsonl(args.training_labels),False,allow_uncertain=True)
    metadata=csv_map(STAGE/'training-selection-internal.csv')
    old=jsonl(ORIGINAL)
    assert len(old)==5000 and len(packet)==200
    oldgroups={r['prepared_group_id'] for r in old}
    if any(metadata[r['article_id']]['group_id'] in oldgroups for r in packet):
        raise ValueError('new article shares prepared group with old corpus')
    augmented=old[:]
    accepted=[]
    excluded=[]
    for r in packet:
        m=metadata[r['article_id']]; answer=labels[r['article_id']]
        if answer['uncertain']:
            excluded.append(r['article_id'])
            continue
        accepted.append(r['article_id'])
        augmented.append(dict(article_id=r['article_id'],title=r['title'],body=r['body'],
            source_name=m['source'],published_date=m['published_date'],
            prepared_group_id=m['group_id'],label=answer['label'],
            label_origin='source_stage_teacher_proposed',
            evidence=answer['evidence'],reason=answer['reason'],reviewer=answer['reviewer']))
    reference_manifest=None
    if args.human_reference:
        ref_packet=jsonl(STAGE/'reference-blind.jsonl')
        ref=check(ref_packet,jsonl(args.human_reference),True)
        refmeta=csv_map(STAGE/'reference-selection-internal.csv')
        if any(refmeta[r['article_id']]['group_id'] in {x['prepared_group_id'] for x in augmented} for r in ref_packet):
            raise ValueError('reference shares a prepared group with training')
        reference_manifest=dict(count=len(ref),input_sha256=digest(args.human_reference),
            human_declared=True,case_review_complete=False)
    args.output.mkdir(parents=True)
    target=args.output/'augmented-labels.jsonl'
    with target.open('x',encoding='utf-8',newline='\n') as f:
        for r in augmented: f.write(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n')
    json_write(args.output/'manifest.json',dict(status='experimental_training_corpus_not_production_truth',
        original_sha256=digest(ORIGINAL),training_label_input_sha256=digest(args.training_labels),
        augmented_sha256=digest(target),articles=len(augmented),
        accepted_training_opinions=len(accepted),uncertain_excluded=len(excluded),
        uncertain_article_ids=excluded,
        new_label_counts=dict(Counter(str(labels[article_id]['label']) for article_id in accepted)),
        reference=reference_manifest,case_review_complete=False,
        note='Reference answers are never included in augmented training file'))
    print(json.dumps(dict(articles=len(augmented),reference_present=bool(reference_manifest))))

if __name__=='__main__': main()

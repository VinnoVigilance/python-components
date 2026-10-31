"""Compare UK notice corrections and a small reviewed training addition.

All reported metrics are agreement with GPT opinions on untouched packets, not
production accuracy. This script does not modify the original corpus/model.
"""

import csv
import hashlib
import json
from pathlib import Path

from sklearn import __version__ as sklearn_version
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score
from threadpoolctl import threadpool_limits

from contracts import file_digest
from study_teacher_student_pilot import fit_and_score


ROOT = Path(__file__).resolve().parent
OLD = ROOT / 'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
CORRECTIONS = ROOT / 'results/focus-review-v2/uk-notice-policy-corrections.csv'
TRAIN = ROOT / 'results/uk-diversity-training-v1'
HOLD = ROOT / 'results/uk-diversity-review-v1'
RESERVED = ROOT / 'results/source-stage-v1'
OUT = ROOT / 'results/uk-diversity-update-v1'


def rows(path):
    with path.open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def text_hash(article):
    return hashlib.sha256((article['title'] + '\n\n' + article['body']).strip().encode()).hexdigest()


def metric(rows_in, group_name, model_name, certain_only):
    subset = [r for r in rows_in if not certain_only or not r['uncertain']]
    truth = [r['opinion_label'] for r in subset]
    pred = [r[model_name + '_pred'] for r in subset]
    return dict(packet=group_name, model=model_name, certain_only=certain_only,
                n=len(subset), positive_opinions=sum(truth),
                accuracy_agreement=accuracy_score(truth,pred),
                f1_agreement=f1_score(truth,pred,zero_division=0),
                precision_agreement=precision_score(truth,pred,zero_division=0),
                recall_agreement=recall_score(truth,pred,zero_division=0),
                false_negative_opinions=sum(a == 1 and b == 0 for a,b in zip(truth,pred)),
                false_positive_opinions=sum(a == 0 and b == 1 for a,b in zip(truth,pred)))


def main():
    if OUT.exists():
        raise ValueError('output already exists; comparison is immutable')
    original = rows(OLD)
    if len(original) != 5000:
        raise ValueError('original corpus size changed')
    by_id = {r['article_id']: i for i,r in enumerate(original)}
    corrected = [dict(r) for r in original]
    with CORRECTIONS.open(encoding='utf-8-sig',newline='') as stream:
        corrections = list(csv.DictReader(stream))
    for correction in corrections:
        target = corrected[by_id[correction['article_id']]]
        if target['label'] != int(correction['old_silver_label']):
            raise ValueError('stale correction: ' + correction['article_id'])
        target['label'] = int(correction['policy_label'])

    train_blind, train_opinions = rows(TRAIN/'blind.jsonl'), rows(TRAIN/'gpt-review.jsonl')
    if [r['article_id'] for r in train_blind] != [r['article_id'] for r in train_opinions]:
        raise ValueError('training packet and opinions out of order')
    additions = [dict(a,label=b['label']) for a,b in zip(train_blind,train_opinions) if not b['uncertain']]
    if len(additions) != 18 or len(corrections) != 6:
        raise ValueError('unexpected experimental batch size')
    updated = corrected + additions

    held_packets = (
        ('new_uk_21', HOLD/'blind.jsonl', HOLD/'gpt-review.jsonl'),
        ('reserved_150', RESERVED/'reference-blind.jsonl', RESERVED/'returns/astra-medium-reference.jsonl'),
    )
    held = []
    packet_sizes = {}
    for name, blind_path, opinion_path in held_packets:
        blind, opinions = rows(blind_path), rows(opinion_path)
        if [a['article_id'] for a in blind] != [b['article_id'] for b in opinions]:
            raise ValueError('held packet and opinions out of order: ' + name)
        packet_sizes[name] = len(blind)
        held += [dict(article_id=a['article_id'], title=a['title'], body=a['body'],
                      packet=name, opinion_label=b['label'], uncertain=b['uncertain'])
                 for a,b in zip(blind,opinions)]
    all_train_hashes = {text_hash(r) for r in updated}
    if all_train_hashes & {text_hash(r) for r in held}:
        raise ValueError('exact training/held text overlap')
    train_ids = {'db:' + r['physical_article_id'].split(':')[-1] for r in original}
    train_ids |= {r['article_id'] for r in additions}
    if train_ids & {r['article_id'] for r in held}:
        raise ValueError('training/held article ID overlap')

    score_sets = {}
    with threadpool_limits(limits=4):
        for name, training in (('original',original),('corrected',corrected),('updated',updated)):
            print('fitting',name,len(training),flush=True)
            score_sets[name] = fit_and_score(training,held)
    for name, scores in score_sets.items():
        for row, value in zip(held,scores):
            row[name+'_score'] = value
            row[name+'_pred'] = int(value >= 0)

    metric_rows = []
    for packet in ('new_uk_21','reserved_150'):
        group = [r for r in held if r['packet']==packet]
        for name in ('original','corrected','updated'):
            for certain_only in (False,True):
                metric_rows.append(metric(group,packet,name,certain_only))
    OUT.mkdir()
    with (OUT/'scores.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        fields=['article_id','packet','opinion_label','uncertain']
        for name in ('original','corrected','updated'):
            fields += [name+'_score',name+'_pred']
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
        writer.writeheader();writer.writerows(held)
    with (OUT/'metrics.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(metric_rows[0]))
        writer.writeheader();writer.writerows(metric_rows)
    manifest=dict(status='exploratory_gpt_opinion_agreement',sklearn_version=sklearn_version,
                  original_n=len(original),corrections_n=len(corrections),additions_n=len(additions),
                  held_packets=packet_sizes,original_sha256=file_digest(OLD),
                  correction_sha256=file_digest(CORRECTIONS),
                  train_review_sha256=file_digest(TRAIN/'gpt-review.jsonl'),
                  hold_review_sha256=file_digest(HOLD/'gpt-review.jsonl'),
                  human_reference=False,case_level_link_review_complete=False,
                  note='All three models refitted under the same runtime. GPT-reviewed packets remain outside training. No production metric is claimed.')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([r for r in metric_rows if r['certain_only']],indent=2))


if __name__ == '__main__':
    main()

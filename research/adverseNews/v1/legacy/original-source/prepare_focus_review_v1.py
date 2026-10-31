"""Freeze a compact, answer-blind GPT policy review packet with durable provenance."""
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / 'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
PRED = ROOT / 'results/lsa200-screen-v1/predictions.csv'
OUT = ROOT / 'results/focus-review-v1'


def order(row):
    return hashlib.sha256(('PDG-115-focus-review-v1|' + row['article_id']).encode()).hexdigest()


def section(row):
    path = urlsplit(row['url']).path
    bits = [x for x in path.split('/') if x]
    return '/'.join(bits[:2]) if bits else '<missing>'


def main():
    if OUT.exists():
        raise ValueError('output exists')
    with CORPUS.open(encoding='utf-8-sig') as stream:
        corpus = {r['article_id']: r for r in map(json.loads, stream)}
    with PRED.open(encoding='utf-8-sig', newline='') as stream:
        pred = {r['article_id']: r for r in csv.DictReader(stream)
                if r['model'] == 'lsa200_rbf_C3' and r['stage'] == 'publisher'
                and r['source'] in ('UK_GOV', 'PTV')}
    selected = []
    seen_groups = set()

    def take(name, count, rows):
        candidates = sorted(rows, key=order)
        made = 0
        for row in candidates:
            if row['prepared_group_id'] in seen_groups:
                continue
            if len(row['body']) > 10000:
                continue
            selected.append((name, row))
            seen_groups.add(row['prepared_group_id'])
            made += 1
            if made == count:
                return
        raise ValueError(f'only found {made} for {name}')

    take('uk_publications_publisher_miss', 3,
         (corpus[i] for i, p in pred.items() if p['source'] == 'UK_GOV'
          and p['y_true'] == '1' and p['y_pred'] == '0'
          and section(corpus[i]) == 'government/publications'))
    take('uk_news_publisher_miss', 3,
         (corpus[i] for i, p in pred.items() if p['source'] == 'UK_GOV'
          and p['y_true'] == '1' and p['y_pred'] == '0'
          and section(corpus[i]) == 'government/news'))
    take('ptv_publisher_false_alarm', 3,
         (corpus[i] for i, p in pred.items() if p['source'] == 'PTV'
          and p['y_true'] == '0' and p['y_pred'] == '1'))
    take('uk_random', 2,
         (corpus[i] for i, p in pred.items() if p['source'] == 'UK_GOV'))
    take('ptv_random', 1,
         (corpus[i] for i, p in pred.items() if p['source'] == 'PTV'))
    # Blind packet is shuffled independently of score/stratum and has no label.
    selected.sort(key=lambda pair: order(pair[1]))
    OUT.mkdir()
    with (OUT / 'blind.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
        for _, row in selected:
            stream.write(json.dumps({k: row[k] for k in ('article_id', 'title', 'body')},
                                    ensure_ascii=False) + '\n')
    internal = []
    for stratum, row in selected:
        p = pred[row['article_id']]
        internal.append(dict(article_id=row['article_id'], stratum=stratum,
                             source=row['source_name'], url_section=section(row),
                             old_silver_label=p['y_true'], old_model_prediction=p['y_pred'],
                             prepared_group_id=row['prepared_group_id']))
    with (OUT / 'selection-internal.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(internal[0]))
        writer.writeheader(); writer.writerows(internal)
    digest = hashlib.sha256((OUT / 'blind.jsonl').read_bytes()).hexdigest()
    (OUT / 'manifest.json').write_text(json.dumps(dict(status='blind_packet_ready',
          count=len(selected), strata=dict(Counter(s for s, _ in selected)),
          blind_sha256=digest, labels_are_silver=True,
          note='GPT review is a second model opinion, not independent human truth.'), indent=2)+'\n',
          encoding='utf-8')
    print(json.dumps(dict(count=len(selected), strata=dict(Counter(s for s, _ in selected)),
                          body_chars=sum(len(r['body']) for _,r in selected))))


if __name__ == '__main__':
    main()

"""Build a frozen UK_GOV diagnostic packet from a supplied full-payload CSV.

The packet contains no labels. It excludes prior raw-export IDs, original
training text duplicates, and reserved diagnostic packets. Model scores and
selection strata are stored separately from the blind title/body packet.
"""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import joblib

from contracts import digest, file_digest, normalized_body
from prepare_pattern_training_v2 import ROOT, BUNDLE, score


SECTIONS = (
    'government/news', 'government/publications',
    'government/case-studies', 'government/speeches',
    'government/fatalities', 'drug-device-alerts', 'drug-safety-update',
)


def section(url):
    parts = [x for x in urlparse(url).path.split('/') if x]
    return '/'.join(parts[:2]) if parts[:1] == ['government'] else (parts[0] if parts else 'other')


def order(article, salt):
    return hashlib.sha256((salt + '|' + article['article_id']).encode()).hexdigest()


def jsonl(path):
    with path.open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('export', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'results/uk-diversity-review-v1')
    parser.add_argument('--exclude-packet', action='append', type=Path, default=[])
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError('output exists; frozen packet must not be overwritten')

    csv.field_size_limit(100_000_000)
    with args.export.open(encoding='utf-8-sig', newline='') as stream:
        raw = list(csv.DictReader(stream))
    if not raw or len({r['vv_media_id'] for r in raw}) != len(raw):
        raise ValueError('empty export or duplicate logical IDs')

    old_raw = set()
    with (ROOT / 'data/raw/adverse-news-uk-sample-5000.csv').open(encoding='utf-8-sig', newline='') as stream:
        old_raw = {r['vv_media_id'] for r in csv.DictReader(stream)}
    original = jsonl(ROOT / 'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl')
    old_content = {r['content_hash'] for r in original}
    old_bodies = {digest(normalized_body(r['body'])) for r in original if r['body'].strip()}
    reserved = set()
    for path in (
        ROOT / 'results/source-stage-v1/training-blind.jsonl',
        ROOT / 'results/source-stage-v1/reference-blind.jsonl',
        ROOT / 'results/pattern-training-v1/blind.jsonl',
        ROOT / 'results/pattern-training-v2/blind.jsonl',
        ROOT / 'results/focus-review-v1/blind.jsonl',
        ROOT / 'results/focus-review-v2/blind.jsonl',
    ):
        if path.exists():
            reserved.update(r['article_id'] for r in jsonl(path))
    for path in args.exclude_packet:
        reserved.update(r['article_id'] for r in jsonl(path))

    all_articles = []
    candidates = []
    excluded = Counter()
    for row in raw:
        payload = json.loads(row['media_payload'])
        content = payload.get('Content') or {}
        title, body = content.get('Title'), content.get('BodyText')
        if not isinstance(title, str) or not isinstance(body, str):
            excluded['invalid_title_or_body'] += 1
            continue
        article = {
            'article_id': 'db:' + row['media_record_id'],
            'vv_media_id': row['vv_media_id'],
            'title': title, 'body': body,
            'section': section(row['source_url']),
            'published_year': row['published_year'],
            'publisher': (payload.get('Publisher') or {}).get('Name') or '',
            'source_url': row['source_url'],
        }
        all_articles.append(article)
        if row['vv_media_id'] in old_raw:
            excluded['in_old_uk_export'] += 1
        elif article['article_id'] in reserved:
            excluded['prior_diagnostic_packet'] += 1
        elif digest([title, body]) in old_content or digest(normalized_body(body)) in old_bodies:
            excluded['original_corpus_duplicate'] += 1
        elif len(body) < 300 or len(body) > 10000:
            excluded['body_outside_review_length'] += 1
        elif article['section'] not in SECTIONS:
            excluded['other_section'] += 1
        else:
            candidates.append(article)

    scores = score(joblib.load(BUNDLE), candidates)
    for article, value in zip(candidates, scores):
        article['score'] = value
    selected = []
    selected_ids = set()
    for sec in SECTIONS:
        pool = [a for a in candidates if a['section'] == sec]
        near_negative = sorted((a for a in pool if a['score'] < 0), key=lambda a: abs(a['score']))
        random_negative = sorted((a for a in pool if a['score'] < 0), key=lambda a: order(a, 'uk-diversity-negative'))
        random_positive = sorted((a for a in pool if a['score'] >= 0), key=lambda a: order(a, 'uk-diversity-positive'))
        fallback = sorted(pool, key=lambda a: order(a, 'uk-diversity-fallback'))
        for purpose, choices in (
            ('near_negative', near_negative),
            ('random_negative', random_negative),
            ('random_positive', random_positive),
        ):
            article = next((a for a in choices + fallback if a['article_id'] not in selected_ids), None)
            if article is None:
                raise ValueError(f'not enough distinct articles in {sec}')
            selected.append((article, purpose))
            selected_ids.add(article['article_id'])
    selected.sort(key=lambda pair: order(pair[0], 'uk-diversity-blind-order'))

    args.output.mkdir(parents=True)
    with (args.output / 'blind.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
        for article, _ in selected:
            stream.write(json.dumps({k: article[k] for k in ('article_id','title','body')}, ensure_ascii=False) + '\n')
    with (args.output / 'selection-internal.csv').open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['article_id','section','publisher','published_year','score','purpose','source_url'])
        writer.writeheader()
        for article, purpose in selected:
            writer.writerow({k: (purpose if k == 'purpose' else article[k]) for k in writer.fieldnames})
    summary = {
        'status': 'unlabeled_blind_gpt_diagnostic',
        'export_sha256': file_digest(args.export),
        'export_rows': len(raw),
        'valid_articles': len(all_articles),
        'export_by_section': dict(Counter(a['section'] for a in all_articles)),
        'distinct_publisher_names': len({a['publisher'] for a in all_articles if a['publisher']}),
        'excluded_from_candidate_pool': dict(excluded),
        'candidate_count': len(candidates),
        'candidate_by_section': dict(Counter(a['section'] for a in candidates)),
        'selected_count': len(selected),
        'explicitly_excluded_packets': [str(path) for path in args.exclude_packet],
        'selected_by_section': dict(Counter(a['section'] for a,_ in selected)),
        'blind_sha256': file_digest(args.output / 'blind.jsonl'),
        'model_bundle_sha256': file_digest(BUNDLE),
        'score_note': 'Diagnostic only; bundle was fitted under scikit-learn 1.8.0. Record the executing runtime version when comparing scores.',
    }
    (args.output / 'manifest.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()

"""Freeze the new source-random UK_GOV CSV for blind full-text review.

No model scores, model calls, or labels are generated here. The original CSV is
read only and the output directory must be new.
"""

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

from contracts import digest, file_digest, normalized_body


ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results/uk-random-shadow-v1'


def jsonl(path):
    with path.open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def url_section(url):
    parts = [p for p in urlparse(url).path.split('/') if p]
    return '/'.join(parts[:2]) if parts[:1] == ['government'] else (parts[0] if parts else 'other')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('export', type=Path)
    args = parser.parse_args()
    if OUT.exists():
        raise ValueError('frozen output already exists')
    csv.field_size_limit(100_000_000)
    with args.export.open(encoding='utf-8-sig', newline='') as stream:
        raw = list(csv.DictReader(stream))
    if len(raw) != 120 or Counter(r['sample_arm'] for r in raw) != {'all_time':60,'recent_12_months':60}:
        raise ValueError('unexpected random export size or sample arms')
    if len({r['vv_media_id'] for r in raw}) != len(raw):
        raise ValueError('duplicate logical IDs in export')

    with (ROOT/'data/raw/adverse-news-uk-sample-5000.csv').open(encoding='utf-8-sig',newline='') as stream:
        old_ids = {r['vv_media_id'] for r in csv.DictReader(stream)}
    # The earlier diversity export had score-selected research packets. Any
    # overlap is removed so this packet remains a genuinely fresh check.
    wide_export = Path('C:/Users/admin/Desktop/data-new-adverse.csv')
    with wide_export.open(encoding='utf-8-sig',newline='') as stream:
        wide_ids = {r['vv_media_id'] for r in csv.DictReader(stream)}
    original = jsonl(ROOT/'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl')
    old_content = {r['content_hash'] for r in original}
    old_body = {digest(normalized_body(r['body'])) for r in original if r['body'].strip()}
    prior_ids = set()
    for path in (
        ROOT/'results/source-stage-v1/training-blind.jsonl',
        ROOT/'results/source-stage-v1/reference-blind.jsonl',
        ROOT/'results/pattern-training-v1/blind.jsonl',
        ROOT/'results/pattern-training-v2/blind.jsonl',
        ROOT/'results/uk-diversity-review-v1/blind.jsonl',
        ROOT/'results/uk-diversity-training-v1/blind.jsonl',
    ):
        prior_ids.update(r['article_id'] for r in jsonl(path))
    exclusions = Counter()
    selected = []
    seen_body = set()
    for row in raw:
        payload = json.loads(row['media_payload'])
        content = payload.get('Content') or {}
        title, body = content.get('Title'), content.get('BodyText')
        if not isinstance(title,str) or not title.strip() or not isinstance(body,str) or not body.strip():
            exclusions['missing_usable_title_or_body'] += 1
            continue
        article_id = 'db:' + row['media_record_id']
        body_hash = digest(normalized_body(body))
        if row['vv_media_id'] in old_ids:
            exclusions['old_uk_export_id'] += 1
        elif row['vv_media_id'] in wide_ids:
            exclusions['old_diversity_export_id'] += 1
        elif article_id in prior_ids:
            exclusions['prior_packet_id'] += 1
        elif digest([title,body]) in old_content or body_hash in old_body:
            exclusions['original_corpus_text_duplicate'] += 1
        elif body_hash in seen_body:
            exclusions['within_packet_body_duplicate'] += 1
        else:
            selected.append(dict(article_id=article_id,title=title,body=body,
                                 sample_arm=row['sample_arm'],
                                 vv_media_id=row['vv_media_id'],
                                 published_date=row['published_date'],
                                 publisher_name=row['publisher_name'],
                                 source_url=row['source_url'],
                                 url_section=url_section(row['source_url']),
                                 content_hash=digest([title,body]),body_hash=body_hash))
            seen_body.add(body_hash)
    selected.sort(key=lambda r: hashlib.sha256(('uk-random-shadow-v1|'+r['article_id']).encode()).hexdigest())
    OUT.mkdir()
    with (OUT/'blind.jsonl').open('x',encoding='utf-8',newline='\n') as stream:
        for r in selected:
            stream.write(json.dumps({k:r[k] for k in ('article_id','title','body')},ensure_ascii=False)+'\n')
    with (OUT/'selection-internal.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        fields=['article_id','sample_arm','vv_media_id','published_date','publisher_name','source_url','url_section','content_hash','body_hash']
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore')
        writer.writeheader();writer.writerows(selected)
    manifest=dict(status='frozen_blind_unlabeled',source_export_sha256=file_digest(args.export),
                  earlier_diversity_export_sha256=file_digest(wide_export),
                  exported_rows=len(raw),excluded=dict(exclusions),blind_count=len(selected),
                  blind_by_arm=dict(Counter(r['sample_arm'] for r in selected)),
                  blind_by_section=dict(Counter(r['url_section'] for r in selected)),
                  blind_sha256=file_digest(OUT/'blind.jsonl'),
                  selection_internal_sha256=file_digest(OUT/'selection-internal.csv'),
                  note='Selection used only source-random SQL assignment and overlap/usable-text filters. No model scores or labels were inspected.')
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,indent=2))


if __name__ == '__main__':
    main()

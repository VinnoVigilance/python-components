"""Audit source tags for sampling coverage; tags never become model features."""
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'data/raw/adverse-news-non-uk.csv'
UK = ROOT / 'data/raw/adverse-news-uk-sample-5000.csv'
LABELS = ROOT / 'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
OUT = ROOT / 'results/source-taxonomy-audit-v1'


def main():
    if OUT.exists():
        raise ValueError(f'output exists: {OUT}')
    csv.field_size_limit(2147483647)
    with LABELS.open(encoding='utf-8-sig') as stream:
        labeled = {r['physical_article_id']: int(r['label']) for r in map(json.loads, stream)
                   if r['source_name'] == 'PTV'}
    tag_all, tag_labeled, tag_positive = Counter(), Counter(), Counter()
    row_count = tag_rows = missing_payload = 0
    tag_patterns = Counter()
    with RAW.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row['source_name'] != 'PTV':
                continue
            row_count += 1
            payload = row.get('canonical_payload')
            if not payload:
                missing_payload += 1
                continue
            data = json.loads(payload)
            metadata = data.get('Metadata') or {}
            tags = {str(tag).strip() for tag in (metadata.get('Tags') or []) if str(tag).strip()}
            if tags:
                tag_rows += 1
            tag_patterns[len(tags)] += 1
            tag_all.update(tags)
            article_id = 'db:' + row['media_record_id']
            if article_id in labeled:
                tag_labeled.update(tags)
                if labeled[article_id] == 1:
                    tag_positive.update(tags)
    with UK.open(encoding='utf-8-sig', newline='') as stream:
        uk_columns = list(csv.DictReader(stream).fieldnames or [])
    OUT.mkdir()
    report = dict(ptv_raw_rows=row_count, ptv_labeled_rows=len(labeled),
                  ptv_rows_with_tags=tag_rows, ptv_missing_payload=missing_payload,
                  ptv_distinct_tags=len(tag_all),
                  ptv_tag_count_distribution=dict(sorted(tag_patterns.items())),
                  ptv_top_tags=[dict(tag=tag, raw_articles=count,
                                     labeled_articles=tag_labeled[tag],
                                     labeled_positive=tag_positive[tag])
                                for tag, count in tag_all.most_common(60)],
                  uk_local_export='5000 sampled articles, not complete UK_GOV collection',
                  uk_columns=uk_columns,
                  uk_explicit_category_field_present=any('categor' in col.lower() or 'tag' in col.lower()
                                                         for col in uk_columns),
                  caution='Tags may overlap and vary over time; use to audit/select slices, never as adversity labels or model features.')
    (OUT / 'summary.json').write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n',
                                      encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('ptv_raw_rows', 'ptv_labeled_rows',
                                             'ptv_rows_with_tags', 'ptv_distinct_tags',
                                             'uk_explicit_category_field_present')}))
    print(json.dumps(report['ptv_top_tags'][:20], ensure_ascii=False))


if __name__ == '__main__':
    main()

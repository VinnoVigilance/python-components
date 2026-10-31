"""Proxy UK article-type coverage from URL paths in saved research exports."""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
META = ROOT / 'data/raw/adverse-news-uk-metadata-5000.csv'
CORPUS = ROOT / 'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
PRED = ROOT / 'results/lsa200-screen-v1/predictions.csv'
OUT = ROOT / 'results/source-taxonomy-audit-v1/uk-url-section-coverage.csv'


def section(url):
    parts = [part for part in urlsplit(url or '').path.split('/') if part]
    if not parts:
        return '<missing>'
    if parts[0] == 'government' and len(parts) > 1:
        return 'government/' + parts[1]
    return parts[0]


def main():
    if OUT.exists():
        raise ValueError(f'output exists: {OUT}')
    with META.open(encoding='utf-8-sig', newline='') as stream:
        all_ids = {r['media_record_id']: section(r['url']) for r in csv.DictReader(stream)}
    with CORPUS.open(encoding='utf-8-sig') as stream:
        training = {r['article_id']: (r['physical_article_id'][3:], section(r['url']))
                    for r in map(json.loads, stream) if r['source_name'] == 'UK_GOV'}
    with PRED.open(encoding='utf-8-sig', newline='') as stream:
        held = [r for r in csv.DictReader(stream) if r['stage'] == 'publisher'
                and r['fold'] == 'UK_GOV' and r['model'] == 'lsa200_rbf_C3']
    by_section = defaultdict(Counter)
    for category in all_ids.values():
        by_section[category]['raw_sample'] += 1
    for _, category in training.values():
        by_section[category]['training'] += 1
    for r in held:
        category = training[r['article_id']][1]
        y, p = int(r['y_true']), int(r['y_pred'])
        c = by_section[category]
        c['heldout'] += 1
        c['positive'] += y
        c['tp'] += int(y == 1 and p == 1)
        c['tn'] += int(y == 0 and p == 0)
        c['fn'] += int(y == 1 and p == 0)
        c['fp'] += int(y == 0 and p == 1)
    results = []
    for name, c in sorted(by_section.items(), key=lambda item: (-item[1]['raw_sample'], item[0])):
        n, tp, tn, fn, fp = (c[k] for k in ('heldout', 'tp', 'tn', 'fn', 'fp'))
        results.append(dict(url_section=name, raw_sample=c['raw_sample'],
                            training=c['training'], heldout=n, silver_positive=c['positive'],
                            accuracy=(tp + tn) / n if n else '',
                            f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else '',
                            false_negative=fn, false_positive=fp))
    with OUT.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader(); writer.writerows(results)
    print(json.dumps(results[:12], ensure_ascii=False))


if __name__ == '__main__':
    main()

"""Descriptive PTV tag slices for publisher-heldout predictions (silver labels)."""
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / 'data/raw/adverse-news-non-uk.csv'
CORPUS = ROOT / 'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'
PRED = ROOT / 'results/lsa200-screen-v1/predictions.csv'
OUT = ROOT / 'results/source-taxonomy-audit-v1/ptv-tag-error-slices.csv'


def main():
    if OUT.exists():
        raise ValueError(f'output exists: {OUT}')
    csv.field_size_limit(2147483647)
    with CORPUS.open(encoding='utf-8-sig') as stream:
        article_to_physical = {r['article_id']: r['physical_article_id']
                               for r in map(json.loads, stream) if r['source_name'] == 'PTV'}
    with PRED.open(encoding='utf-8-sig', newline='') as stream:
        pred = [r for r in csv.DictReader(stream)
                if r['stage'] == 'publisher' and r['fold'] == 'PTV'
                and r['model'] == 'lsa200_rbf_C3']
    if len(pred) != 1293 or any(r['article_id'] not in article_to_physical for r in pred):
        raise ValueError('unexpected PTV publisher-holdout coverage')
    physical_to_prediction = {article_to_physical[r['article_id']]: r for r in pred}
    counts = Counter()
    examples = defaultdict(list)
    with RAW.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if row['source_name'] != 'PTV':
                continue
            article_id = 'db:' + row['media_record_id']
            if article_id not in physical_to_prediction:
                continue
            prediction = physical_to_prediction[article_id]
            tags = set((json.loads(row['canonical_payload']).get('Metadata') or {}).get('Tags') or [])
            true = int(prediction['y_true'])
            proposed = int(prediction['y_pred'])
            for tag in tags:
                counts[(tag, 'n')] += 1
                counts[(tag, 'positive')] += true
                counts[(tag, 'fn')] += int(true == 1 and proposed == 0)
                counts[(tag, 'fp')] += int(true == 0 and proposed == 1)
                if true != proposed and len(examples[tag]) < 5:
                    examples[tag].append(prediction['article_id'])
    top = sorted({tag for tag, metric in counts if metric == 'n'},
                 key=lambda tag: (-counts[(tag, 'n')], tag))[:30]
    results = [dict(tag=tag, heldout_articles=counts[(tag, 'n')],
                    silver_positive=counts[(tag, 'positive')],
                    false_negative=counts[(tag, 'fn')], false_positive=counts[(tag, 'fp')],
                    example_error_ids='|'.join(examples[tag])) for tag in top]
    with OUT.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader(); writer.writerows(results)
    print(json.dumps(results[:12], ensure_ascii=False))


if __name__ == '__main__':
    main()

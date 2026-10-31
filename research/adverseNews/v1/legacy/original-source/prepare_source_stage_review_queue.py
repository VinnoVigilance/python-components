"""Write a compact policy/case review queue from completed model opinions."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAGE = ROOT / 'results/source-stage-v1'
AGREEMENT = STAGE / 'model-opinion-comparison/agreement.csv'
CASE_LINK = STAGE / 'case-link-review.csv'
OUT = STAGE / 'review-queue.csv'


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return list(csv.DictReader(stream))


def read_jsonl(path):
    with path.open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def main():
    if OUT.exists():
        raise ValueError(f'output exists: {OUT}')
    packets = {r['article_id']: r for name in ('training-blind', 'reference-blind')
               for r in read_jsonl(STAGE / (name + '.jsonl'))}
    links = {r['article_id']: r for r in read_csv(CASE_LINK)}
    records = []
    for row in read_csv(AGREEMENT):
        uncertain = row['uncertain'].lower() == 'true'
        disagree = int(row['model_opinion']) != int(row['baseline_prediction'])
        possible_link = links[row['article_id']]['review_link'].lower() == 'true'
        if not (uncertain or disagree or possible_link):
            continue
        article = packets[row['article_id']]
        records.append(dict(packet=row['packet'], article_id=row['article_id'],
                            source=row['source'], title=article['title'],
                            uncertain=uncertain, model_opinion=row['model_opinion'],
                            baseline_prediction=row['baseline_prediction'],
                            baseline_score=row['baseline_score'], disagree=disagree,
                            possible_same_case=possible_link,
                            closest_existing_id=links[row['article_id']]['closest_existing_id'],
                            closest_existing_title=links[row['article_id']]['closest_existing_title'],
                            model_reason=row['reason'], evidence=row['evidence'],
                            action='policy and case review; read full article in blind packet'))
    with OUT.open('x', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader(); writer.writerows(records)
    print(json.dumps(dict(review_rows=len(records), uncertain=sum(r['uncertain'] for r in records),
                          disagreements=sum(r['disagree'] for r in records),
                          possible_same_case=sum(r['possible_same_case'] for r in records))))


if __name__ == '__main__':
    main()

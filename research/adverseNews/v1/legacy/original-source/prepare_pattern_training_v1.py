"""Select new unlabeled UK hidden-positive and PTV likely-false-alarm candidates."""
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.preprocessing import normalize

ROOT = Path(__file__).resolve().parent
PREP = ROOT / 'data/prepared/db-selection-v3/working-candidates.jsonl'
EXISTING = ROOT / 'results/source-stage-v1'
BUNDLE = ROOT / 'results/finalist-models-v1/finalist-bundle.joblib'
OUT = ROOT / 'results/pattern-training-v1'
UK_PATTERNS = {
    'violence': r'\b(abduct(?:ion|ed)?|invasion|attacks?|bomb(?:ing|ed)?|terroris[tm])\b',
    'rights': r'\b(detain(?:ed|ment)?|torture|forced labo[u]?r|human rights abuse)\b',
    'enforcement': r'\b(sanction(?:s|ed)?|prosecut(?:ed|ion)?|arrest(?:ed)?|investigat(?:ion|ed)?|convict(?:ed|ion)?)\b',
}
PTV_PATTERNS = {
    'future_penalty': r'\b(will|could|may|would|future|propos(?:ed|al)|plans?|policy|ordinance)\b',
    'incident': r'\b(accident|storm|fire|flood|earthquake|injur(?:y|ies))\b',
    'politics': r'\b(election|party|rally|protest|government)\b',
}


def order(article):
    return hashlib.sha256(('PDG-115-pattern-training-v1|' + article['article_id']).encode()).hexdigest()


def rows(path):
    with path.open(encoding='utf-8-sig') as stream:
        return [json.loads(line) for line in stream if line.strip()]


def score(bundle, articles):
    values = []
    for start in range(0, len(articles), 100):
        chunk = articles[start:start + 100]
        texts = [(r['title'] + '\n\n' + r['body']).strip() for r in chunk]
        word = bundle['word_vectorizer'].transform(texts)
        char = bundle['char_vectorizer'].transform(texts)
        x = hstack([word, char], format='csr', dtype=np.float32)
        z = normalize(bundle['lsa200_svd'].transform(x)).astype(np.float32)
        values.extend(map(float, bundle['lsa200_rbf_c3'].decision_function(z)))
    return values


def main():
    if OUT.exists():
        raise ValueError('output exists')
    already = {r['article_id'] for name in ('training-blind.jsonl','reference-blind.jsonl')
               for r in rows(EXISTING / name)}
    candidates = [r for r in rows(PREP)
                  if r['source_name'] in ('UK_GOV', 'PTV')
                  and r['candidate_split'] in ('train_candidate','dev_candidate')
                  and r['article_id'] not in already
                  and 300 <= len(r['body']) <= 10000]
    scores = score(joblib.load(BUNDLE), candidates)
    pools = {'UK_GOV': [], 'PTV': []}
    for article, value in zip(candidates, scores):
        source = article['source_name']
        if source == 'UK_GOV' and value < -0.2:
            matches = [name for name, pattern in UK_PATTERNS.items()
                       if re.search(pattern, article['body'], re.I)
                       and not re.search(pattern, article['title'], re.I)]
        elif source == 'PTV' and value > 0.2:
            matches = [name for name, pattern in PTV_PATTERNS.items()
                       if re.search(pattern, article['body'], re.I)]
        else:
            matches = []
        if matches:
            pools[source].append((article, value, matches))
    selected = []
    used = set()
    for source, patterns in (('UK_GOV', UK_PATTERNS), ('PTV', PTV_PATTERNS)):
        pool = sorted(pools[source], key=lambda x: order(x[0]))
        for name in patterns:
            count = 0
            for article, value, matches in pool:
                if name not in matches or article['group_id'] in used:
                    continue
                selected.append((article, value, name))
                used.add(article['group_id'])
                count += 1
                if count == 3:
                    break
            if count != 3:
                raise ValueError(f'not enough {source}/{name}: {count}')
    selected.sort(key=lambda x: order(x[0]))
    OUT.mkdir()
    with (OUT / 'blind.jsonl').open('x', encoding='utf-8', newline='\n') as stream:
        for article, _, _ in selected:
            stream.write(json.dumps({k: article[k] for k in ('article_id','title','body')},
                                    ensure_ascii=False) + '\n')
    internal = [dict(article_id=a['article_id'],source=a['source_name'],
                     pattern=p,score=v,group_id=a['group_id'],
                     candidate_split=a['candidate_split']) for a,v,p in selected]
    with (OUT / 'selection-internal.csv').open('x',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(internal[0]))
        writer.writeheader(); writer.writerows(internal)
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        status='unlabeled_training_candidates',count=len(selected),
        candidate_pool_by_source={k:len(v) for k,v in pools.items()},
        selected_by_pattern=dict(Counter(x['source']+'/'+x['pattern'] for x in internal)),
        blind_sha256=hashlib.sha256((OUT/'blind.jsonl').read_bytes()).hexdigest(),
        model_bundle_sha256=hashlib.sha256(BUNDLE.read_bytes()).hexdigest(),
        note='Heuristics and model score selected candidates; neither assigned labels. Excludes source-stage blind packets and reserved candidates.'),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(selected=len(selected),pools={k:len(v) for k,v in pools.items()},
                          characters=sum(len(a['body']) for a,_,_ in selected))))


if __name__ == '__main__':
    main()

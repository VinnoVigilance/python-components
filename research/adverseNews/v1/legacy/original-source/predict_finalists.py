"""Score article JSONL with the research finalist bundle."""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
from scipy.sparse import hstack
from sklearn.preprocessing import normalize


DEFAULT_BUNDLE = Path('research/adverseNews/results/finalist-models-v1/finalist-bundle.joblib')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True,
                        help='JSONL with article_id, title and body fields')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError(f'output exists: {args.output}')
    rows = [json.loads(s) for s in args.input.read_text(encoding='utf-8').splitlines() if s.strip()]
    if not rows or any(not all(k in r for k in ('article_id','title','body')) for r in rows):
        raise ValueError('input requires nonempty article_id, title, body JSONL')
    bundle = joblib.load(args.bundle)
    texts = [(r['title'] + '\n\n' + r['body']).strip() for r in rows]
    x_word = bundle['word_vectorizer'].transform(texts)
    x_char = bundle['char_vectorizer'].transform(texts)
    x_hybrid = hstack([x_word, x_char], format='csr', dtype=np.float32)
    scores = {'char_linear_svm_c1': bundle['char_linear_svm_c1'].decision_function(x_char)}
    for dimensions in (100, 200):
        name = f'lsa{dimensions}_rbf_c3'
        x_low = normalize(bundle[f'lsa{dimensions}_svd'].transform(x_hybrid)).astype(np.float32, copy=False)
        scores[name] = bundle[name].decision_function(x_low)
    with args.output.open('x',encoding='utf-8',newline='\n') as stream:
        for i,row in enumerate(rows):
            result={'article_id':row['article_id']}
            for name in ('lsa100_rbf_c3','lsa200_rbf_c3','char_linear_svm_c1'):
                score=float(scores[name][i]);result[name+'_score']=score
                result[name+'_label']=int(score >= bundle['default_thresholds'][name])
            stream.write(json.dumps(result,ensure_ascii=False)+'\n')
    print(json.dumps({'articles_scored':len(rows),'output':str(args.output)}))


if __name__ == '__main__':
    main()

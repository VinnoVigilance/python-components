"""Frozen CPU encoders, full-text chunks, and leakage-safe PDG-115 comparisons.

Run from repository root. Model downloads are explicit (--download); all other
commands operate locally. This is a research experiment, not a test suite.
"""
import argparse
import csv
import gc
import hashlib
import importlib.metadata
import json
import os
import platform
import time
from collections import Counter
from pathlib import Path

os.environ.setdefault('OMP_NUM_THREADS', '4')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
import numpy as np
import onnxruntime as ort
import psutil
from sklearn.metrics import average_precision_score, f1_score, precision_score, recall_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.preprocessing import normalize
from sklearn.svm import LinearSVC, SVC
from threadpoolctl import threadpool_limits
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parent
MODELS = {
    'minilm': dict(repo='sentence-transformers/all-MiniLM-L6-v2',
                   revision='1110a243fdf4706b3f48f1d95db1a4f5529b4d41',
                   window=256, prefix='', license='Apache-2.0', parameters=22713216),
    'e5small': dict(repo='intfloat/multilingual-e5-small',
                    revision='614241f622f53c4eeff9890bdc4f31cfecc418b3',
                    window=512, prefix='query: ', license='MIT', parameters=117653760),
}


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def json_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    temp.replace(path)


def csv_write(path, rows):
    if not rows:
        return
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def scores(y, pred, score):
    return dict(n=len(y), positives=int(np.sum(y)),
                f1=float(f1_score(y, pred, zero_division=0)),
                precision=float(precision_score(y, pred, zero_division=0)),
                recall=float(recall_score(y, pred, zero_division=0)),
                ap=float(average_precision_score(y, score)) if np.any(y) else None,
                fn=int(np.sum((y == 1) & (pred == 0))),
                fp=int(np.sum((y == 0) & (pred == 1))))


def model_dir(out, name):
    return out / 'models' / name


def download(out, name):
    from huggingface_hub import hf_hub_download
    spec = MODELS[name]
    dest = model_dir(out, name)
    for filename in ('tokenizer.json', 'config.json', 'onnx/model.onnx', 'README.md'):
        hf_hub_download(spec['repo'], filename=filename, revision=spec['revision'], local_dir=dest)
    json_write(dest / 'provenance.json', {**spec, 'files': {
        f: {'sha256': digest(dest / f), 'bytes': (dest/f).stat().st_size}
        for f in ('tokenizer.json', 'config.json', 'onnx/model.onnx', 'README.md')}})


def resources():
    return dict(cpu_logical=psutil.cpu_count(), cpu_physical=psutil.cpu_count(logical=False),
                platform=platform.platform(), ram=psutil.virtual_memory()._asdict(),
                process=psutil.Process().memory_info()._asdict(),
                packages={p: importlib.metadata.version(p) for p in
                          ('numpy','scikit-learn','onnxruntime','tokenizers','psutil','huggingface_hub')})


class Encoder:
    def __init__(self, out, name, threads, batch):
        self.name = name
        self.spec = MODELS[name]
        self.batch = batch
        directory = model_dir(out, name)
        self.tokenizer = Tokenizer.from_file(str(directory / 'tokenizer.json'))
        self.tokenizer.no_truncation()
        self.tokenizer.no_padding()
        config = json.loads((directory/'config.json').read_text(encoding='utf-8'))
        self.pad = config['pad_token_id']
        self.prefix = self.tokenizer.encode(self.spec['prefix'], add_special_tokens=False).ids
        # Obtain architecture-specific single-sequence special tokens (BERT CLS/SEP).
        empty = self.tokenizer.encode('', add_special_tokens=True).ids
        assert len(empty) == 2, empty
        self.start, self.end = empty
        self.capacity = self.spec['window'] - len(self.prefix) - 2
        self.overlap = 32
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(directory/'onnx/model.onnx'),
                                           sess_options=opts, providers=['CPUExecutionProvider'])
        self.inputs = {x.name for x in self.session.get_inputs()}
        self.contract = {**self.spec, 'onnx_sha256': digest(directory/'onnx/model.onnx'),
                         'tokenizer_sha256': digest(directory/'tokenizer.json'),
                         'overlap': self.overlap, 'pooling': 'masked_token_mean_then_L2',
                         'article_text': 'title + two_newlines + body, stripped',
                         'runtime': ort.__version__, 'precision': 'float32', 'schema': 1}

    def chunks(self, text):
        ids = self.tokenizer.encode(text, add_special_tokens=False).ids
        chunks = []
        for offset in range(0, max(len(ids), 1), self.capacity-self.overlap):
            part = ids[offset:offset+self.capacity]
            chunks.append([self.start, *self.prefix, *part, self.end])
            if offset+self.capacity >= len(ids):
                break
        return chunks, len(ids)

    def embed(self, chunks):
        result = []
        for offset in range(0, len(chunks), self.batch):
            batch = chunks[offset:offset+self.batch]
            width = max(map(len, batch))
            ids = np.full((len(batch), width), self.pad, dtype=np.int64)
            mask = np.zeros_like(ids)
            for j, part in enumerate(batch):
                ids[j, :len(part)] = part
                mask[j, :len(part)] = 1
            feed = {'input_ids': ids, 'attention_mask': mask, 'token_type_ids': np.zeros_like(ids)}
            outputs = self.session.run(None, {k:v for k,v in feed.items() if k in self.inputs})
            hidden = outputs[0]
            assert hidden.ndim == 3 and hidden.shape[:2] == ids.shape, hidden.shape
            pooled = (hidden * mask[:,:,None]).sum(axis=1) / mask.sum(axis=1)[:,None]
            result.append(normalize(pooled.astype(np.float32)))
        return np.concatenate(result)


def encode(rows, out, name, threads, batch, pilot):
    started = time.perf_counter()
    before = resources()
    encoder = Encoder(out, name, threads, batch)
    load_seconds = time.perf_counter()-started
    contract = encoder.contract
    key = hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()
    cache = out / 'cache' / name / key
    cache.mkdir(parents=True, exist_ok=True)
    json_write(cache/'contract.json', contract)
    texts = [(r['title']+'\n\n'+r['body']).strip() for r in rows]
    # Label-blind, length-stratified sample spanning all article-length deciles.
    indices = np.argsort([len(t) for t in texts])
    if pilot:
        indices = indices[np.linspace(0, len(indices)-1, 40, dtype=int)]
    else:
        indices = np.arange(len(rows))
    coverage, mean_vectors, max_vectors = [], [], []
    elapsed_inference, elapsed_tokenize, computed, total_chunks = 0., 0., 0, 0
    rss_peak = psutil.Process().memory_info().rss
    inference_start = time.perf_counter()
    for position, index in enumerate(indices):
        text = texts[index]
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        path = cache/f'{text_hash}.npz'
        if path.exists() and not pilot:
            with np.load(path) as saved:
                vectors = saved['embeddings']
                tokens = int(saved['tokens'])
        else:
            t = time.perf_counter()
            chunks, tokens = encoder.chunks(text)
            elapsed_tokenize += time.perf_counter()-t
            t = time.perf_counter()
            vectors = encoder.embed(chunks)
            elapsed_inference += time.perf_counter()-t
            computed += 1
            np.savez_compressed(path, embeddings=vectors, tokens=tokens)
        mean_vectors.append(vectors.mean(axis=0))
        max_vectors.append(vectors.max(axis=0))
        total_chunks += len(vectors)
        rss_peak = max(rss_peak, psutil.Process().memory_info().rss)
        coverage.append(dict(article_id=rows[index]['article_id'], text_sha256=text_hash,
                             token_count=tokens, chunks=len(vectors),
                             first_window_tokens=min(tokens, encoder.capacity),
                             first_window_fraction=min(tokens,encoder.capacity)/max(tokens,1),
                             all_tokens_covered=True))
        if position % 100 == 0 or position+1 == len(indices):
            json_write(out/f'{name}-progress.json', dict(phase='pilot' if pilot else 'encode',
                       articles=position+1, total=len(indices), chunks=total_chunks,
                       wall_seconds=time.perf_counter()-inference_start, rss_bytes=rss_peak))
            print(name, position+1, '/', len(indices), 'chunks', total_chunks, flush=True)
    wall = time.perf_counter()-inference_start
    manifest = dict(model=name, contract=contract, cache_key=key, pilot=pilot,
                    articles=len(indices), chunks=total_chunks, computed_articles=computed,
                    model_load_seconds=load_seconds, wall_seconds=wall,
                    onnx_inference_seconds=elapsed_inference, tokenize_seconds=elapsed_tokenize,
                    articles_per_second=len(indices)/wall,
                    peak_sampled_rss_bytes=rss_peak,
                    peak_os_working_set_bytes=getattr(psutil.Process().memory_info(),'peak_wset',None),
                    before=before, after=resources(),
                    model_disk_bytes=sum(p.stat().st_size for p in model_dir(out,name).rglob('*') if p.is_file()),
                    cache_disk_bytes=sum(p.stat().st_size for p in cache.glob('*.npz')),
                    long_articles=sum(r['chunks']>1 for r in coverage),
                    max_chunks=max(r['chunks'] for r in coverage),
                    first_window_token_coverage=sum(r['first_window_tokens'] for r in coverage)/sum(r['token_count'] for r in coverage))
    csv_write(out/f'{name}-{"pilot" if pilot else "coverage"}.csv', coverage)
    json_write(out/f'{name}-{"pilot" if pilot else "encoding"}.json', manifest)
    if not pilot:
        mean = normalize(np.stack(mean_vectors))
        meanmax = normalize(np.concatenate([np.stack(mean_vectors), np.stack(max_vectors)], axis=1))
        np.savez_compressed(out/f'{name}-features.npz', mean=mean, meanmax=meanmax,
                            article_ids=np.array([r['article_id'] for r in rows]))
    print(json.dumps({k:manifest[k] for k in ('model','articles','chunks','wall_seconds','peak_sampled_rss_bytes','long_articles','max_chunks')}), flush=True)


def evaluate(rows, data, out):
    y = np.array([r['label'] for r in rows])
    source = np.array([r['source_name'] for r in rows])
    groups = np.array([r['prepared_group_id'] for r in rows])
    old = np.array([i for i,r in enumerate(rows) if r['published_date'][:10]<'2025-01-01'])
    new = np.array([i for i,r in enumerate(rows) if r['published_date'][:10]>='2025-01-01'])
    assert (len(old),len(new),len(set(groups))) == (4053,947,5000)
    cv = StratifiedGroupKFold(n_splits=3, shuffle=True, random_state=115)
    splits = [('cv', f'fold-{k}', old[tr], old[va]) for k,(tr,va) in enumerate(cv.split(np.zeros(len(old)),y[old],groups[old]),1)]
    splits += [('temporal','2025-plus',old,new)]
    splits += [('publisher',s,old[source[old]!=s],old[source[old]==s]) for s,n in Counter(source[old]).items() if n>=40]
    baseline_file = data/'results/lsa200-screen-v1/predictions.csv'
    baseline = [r for r in read_csv(baseline_file) if r['model']=='lsa200_rbf_C3']
    baseline_maps = {(r['stage'],r['fold'],r['article_id']):r for r in baseline}
    split_manifest=[]
    for stage,fold,tr,va in splits:
        assert not set(groups[tr]) & set(groups[va])
        expected = {r['article_id'] for r in baseline if r['stage']==stage and r['fold']==fold}
        assert expected == {rows[i]['article_id'] for i in va}, (stage,fold)
        for i in va:
            assert int(baseline_maps[(stage,fold,rows[i]['article_id'])]['y_true']) == y[i]
        split_manifest.append(dict(stage=stage,fold=fold,
            train_ids=[rows[i]['article_id'] for i in tr], valid_ids=[rows[i]['article_id'] for i in va]))
    json_write(out/'splits.json',split_manifest)
    metrics, predictions, per_source = [], [], []
    for stage,fold,tr,va in splits:
        bp = [baseline_maps[(stage,fold,rows[i]['article_id'])] for i in va]
        record_result(rows, y, source, stage, fold, va, 'lsa200_rbf_C3',
                      np.array([int(r['y_pred']) for r in bp]),np.array([float(r['score']) for r in bp]),
                      metrics,predictions,per_source,None,None)
    candidate_features={}
    for name in MODELS:
        with np.load(out/f'{name}-features.npz') as saved:
            assert list(saved['article_ids']) == [r['article_id'] for r in rows]
            features = {k:saved[k] for k in ('mean','meanmax')}
        for aggregation,x in features.items():
            for kind in ('linear_C1','rbf_C3'):
                label = f'{name}_{aggregation}_{kind}'
                candidate_features[label]=(x,kind)
                for stage,fold,tr,va in splits:
                    model = LinearSVC(C=1,class_weight='balanced',random_state=115,dual='auto',max_iter=10000) if kind=='linear_C1' else SVC(C=3,class_weight='balanced',kernel='rbf',cache_size=128)
                    t=time.perf_counter()
                    model.fit(x[tr],y[tr])
                    fit=time.perf_counter()-t
                    t=time.perf_counter()
                    score=model.decision_function(x[va])
                    elapsed=time.perf_counter()-t
                    pred=(score>=0).astype(int)
                    record_result(rows,y,source,stage,fold,va,label,pred,score,
                                  metrics,predictions,per_source,fit,elapsed)
                print('evaluated',label,flush=True)
    csv_write(out/'metrics.csv',metrics)
    csv_write(out/'predictions.csv',predictions)
    csv_write(out/'per-source.csv',per_source)
    csv_write(out/'false-negatives.csv',[r for r in predictions if r['y_true']==1 and r['y_pred']==0])
    # Global older-CV selection indirectly sees every older publisher's labels.
    # Evaluate a nested selection rule using only each outer training pool.
    inner_metrics,nested_metrics,nested_predictions,nested_splits=[],[],[],[]
    for stage,fold,tr,va in splits:
        if stage!='publisher':
            continue
        inner=StratifiedGroupKFold(n_splits=3,shuffle=True,random_state=115)
        inner_splits=[(tr[a],tr[b]) for a,b in inner.split(np.zeros(len(tr)),y[tr],groups[tr])]
        for k,(itr,iva) in enumerate(inner_splits,1):
            assert not set(groups[itr]) & set(groups[iva])
            assert not set(groups[va]) & (set(groups[itr])|set(groups[iva]))
            nested_splits.append(dict(publisher=fold,inner_fold=k,
                train_ids=[rows[i]['article_id'] for i in itr],valid_ids=[rows[i]['article_id'] for i in iva]))
        ranks=[]
        for label,(x,kind) in candidate_features.items():
            values=[]
            for k,(itr,iva) in enumerate(inner_splits,1):
                model=LinearSVC(C=1,class_weight='balanced',random_state=115,dual='auto',max_iter=10000) if kind=='linear_C1' else SVC(C=3,class_weight='balanced',kernel='rbf',cache_size=128)
                t=time.perf_counter()
                model.fit(x[itr],y[itr])
                fit=time.perf_counter()-t
                t=time.perf_counter()
                pred=model.predict(x[iva])
                elapsed=time.perf_counter()-t
                value=float(f1_score(y[iva],pred,zero_division=0))
                values.append(value)
                inner_metrics.append(dict(publisher=fold,inner_fold=k,model=label,
                    train_n=len(itr),valid_n=len(iva),f1=value,fit_seconds=fit,predict_seconds=elapsed))
            ranks.append((float(np.mean(values)),label))
        best=max(ranks,key=lambda r:r[0])[1]
        chosen=[r for r in predictions if r['model']==best and r['stage']=='publisher' and r['fold']==fold]
        nested_metrics.append(dict(publisher=fold,selected_model=best,
            **scores(np.array([r['y_true'] for r in chosen]),np.array([r['y_pred'] for r in chosen]),np.array([r['score'] for r in chosen]))))
        nested_predictions.extend([{**r,'selected_model':best,'model':'nested_encoder_selection'} for r in chosen])
        print('nested publisher selection',fold,best,flush=True)
    csv_write(out/'nested-inner-metrics.csv',inner_metrics)
    csv_write(out/'nested-publisher-metrics.csv',nested_metrics)
    csv_write(out/'nested-publisher-predictions.csv',nested_predictions)
    json_write(out/'nested-inner-splits.json',nested_splits)
    summary=[]
    for name in sorted({r['model'] for r in metrics}):
        record=dict(model=name)
        for stage in ('cv','publisher','temporal'):
            subset=[r for r in metrics if r['model']==name and r['stage']==stage]
            for field in ('f1','recall','precision','ap'):
                record[f'{stage}_{field}']=float(np.mean([r[field] for r in subset]))
            record[f'{stage}_fn']=sum(r['fn'] for r in subset)
            record[f'{stage}_fp']=sum(r['fp'] for r in subset)
        record['classifier_fit_seconds']=sum(r['fit_seconds'] or 0 for r in metrics if r['model']==name)
        record['classifier_inference_seconds']=sum(r['predict_seconds'] or 0 for r in metrics if r['model']==name)
        summary.append(record)
    # Choose representation/classifier using older group CV only; holdouts remain diagnostics.
    selected=max((r for r in summary if r['model']!='lsa200_rbf_C3'),key=lambda r:r['cv_f1'])['model']
    csv_write(out/'model-comparison.csv',summary)
    review_file=data/'data/review/strong-review-v1/comparison/review-comparison.csv'
    lookup={(r['model'],r['stage'],r['article_id']):r for r in predictions}
    reviewed=[]
    nested_lookup={r['article_id']:r for r in nested_predictions}
    hard={'AN-P115-R1412','AN-P115-R0431','AN-P115-R3260','AN-P115-R3366','AN-P115-R3336','AN-P115-R3255','AN-P115-R2554','AN-P115-R1329','AN-P115-R1379'}
    for r in read_csv(review_file):
        stage='temporal' if r['stratum']=='recent_random' else 'publisher'
        for name in sorted({m['model'] for m in metrics}|{'encoder_selection_rule'}):
            if name=='encoder_selection_rule':
                p=nested_lookup[r['article_id']] if stage=='publisher' else lookup[(selected,stage,r['article_id'])]
            else:
                p=lookup[(name,stage,r['article_id'])]
            reviewed.append({**r,'policy_boundary':r['article_id'] in hard,'model':name,
                             'y_pred':p['y_pred'],'score':p['score']})
    csv_write(out/'review-cases.csv',reviewed)
    csv_write(out/'policy-boundary-cases.csv',[r for r in reviewed if r['policy_boundary'] or r['astra_uncertain'].lower()=='true'])
    review_metrics=[]
    for name in sorted({m['model'] for m in reviewed}):
        for stratum in ('recent_random','transfer_targeted'):
            for scope in ('all','certain_nonboundary'):
                subset=[r for r in reviewed if r['model']==name and r['stratum']==stratum
                        and (scope=='all' or (not r['policy_boundary'] and r['astra_uncertain'].lower()!='true'))]
                review_metrics.append(dict(model=name,stratum=stratum,scope=scope,
                    **scores(np.array([int(r['astra_label']) for r in subset]),np.array([r['y_pred'] for r in subset]),np.array([r['score'] for r in subset]))))
    csv_write(out/'review-metrics.csv',review_metrics)
    json_write(out/'evaluation-manifest.json',dict(data_sha256=digest(data/'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'),
        baseline_predictions_sha256=digest(baseline_file),review_sha256=digest(review_file),
        selected_by_old_group_cv=selected,selection_metric='unweighted mean of three group-fold F1 scores',
        holdout_tuning=False,supervised_fits='training articles only',threshold=0,
        fixed_candidate_fits=80,nested_selection_fits=len(inner_metrics),
        publisher_nested_macro_f1=float(np.mean([r['f1'] for r in nested_metrics])),
        publisher_nested_macro_recall=float(np.mean([r['recall'] for r in nested_metrics])),
        resources=resources(),label_reference='silver; Astra is second-model opinion',
        same_case_review_complete=False))


def record_result(rows,y,sources,stage,fold,valid,name,pred,score,metrics,predictions,per_source,fit,elapsed):
    metrics.append(dict(model=name,stage=stage,fold=fold,fit_seconds=fit,predict_seconds=elapsed,
                        **scores(y[valid],pred,score)))
    for source in sorted(set(sources[valid])):
        mask=sources[valid]==source
        per_source.append(dict(model=name,stage=stage,fold=fold,source=source,
                               **scores(y[valid][mask],pred[mask],score[mask])))
    for i,p,s in zip(valid,pred,score):
        predictions.append(dict(model=name,stage=stage,fold=fold,article_id=rows[i]['article_id'],
            source=sources[i],published_date=rows[i]['published_date'],y_true=int(y[i]),y_pred=int(p),score=float(s)))


def benchmark_baseline(rows,data,out):
    """Time the existing bundle only; never use its full-data fit for accuracy."""
    import joblib
    from scipy.sparse import hstack
    started=time.perf_counter()
    path=data/'results/finalist-models-v1/finalist-bundle.joblib'
    bundle=joblib.load(path)
    load=time.perf_counter()-started
    texts=[(r['title']+'\n\n'+r['body']).strip() for r in rows]
    started=time.perf_counter()
    # Batches of 100 bound sparse-matrix memory without changing any predictions.
    for start in range(0,len(texts),100):
        batch=texts[start:start+100]
        word=bundle['word_vectorizer'].transform(batch)
        char=bundle['char_vectorizer'].transform(batch)
        hybrid=hstack([word,char],format='csr',dtype=np.float32)
        low=normalize(bundle['lsa200_svd'].transform(hybrid)).astype(np.float32)
        bundle['lsa200_rbf_c3'].decision_function(low)
    elapsed=time.perf_counter()-started
    json_write(out/'baseline-latency.json',dict(articles=len(texts),wall_seconds=elapsed,
        articles_per_second=len(texts)/elapsed,model_load_seconds=load,
        artifact_bytes=path.stat().st_size,artifact_sha256=digest(path),batch_articles=100,
        resources=resources(),purpose='latency only; full-data bundle excluded from held-out accuracy'))
    print('baseline timing seconds',elapsed,flush=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['download','pilot','encode','evaluate','benchmark-baseline'])
    p.add_argument('--model',choices=list(MODELS),default='minilm')
    p.add_argument('--data-root',type=Path,default=ROOT)
    p.add_argument('--output',type=Path,default=ROOT/'results/encoder-screen-v1')
    p.add_argument('--threads',type=int,default=4)
    p.add_argument('--batch',type=int,default=8)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    if args.action=='download':
        download(args.output,args.model)
        return
    rows=[json.loads(s) for s in (args.data_root/'data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    with threadpool_limits(limits=args.threads):
        if args.action=='evaluate':
            evaluate(rows,args.data_root,args.output)
        elif args.action=='benchmark-baseline':
            benchmark_baseline(rows,args.data_root,args.output)
        else:
            encode(rows,args.output,args.model,args.threads,args.batch,args.action=='pilot')


if __name__=='__main__':
    main()

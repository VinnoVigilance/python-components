"""Build the durable encoder comparison and paired error diagnostics."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from run_encoder_screen import ROOT, MODELS, csv_write, digest, json_write, read_csv, scores


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |',
                      '| '+' | '.join(['---']*len(headers))+' |',
                      *['| '+' | '.join(map(str,row))+' |' for row in rows]])


def f(value):
    return f'{float(value):.3f}'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'results/encoder-screen-v1')
    parser.add_argument('--data-root',type=Path,default=ROOT)
    args=parser.parse_args()
    out=args.output
    manifest=json.loads((out/'evaluation-manifest.json').read_text())
    selected=manifest['selected_by_old_group_cv']
    baseline='lsa200_rbf_C3'
    summaries=read_csv(out/'model-comparison.csv')
    summaries.sort(key=lambda r:float(r['cv_f1']),reverse=True)
    by_name={r['model']:r for r in summaries}
    candidate=by_name[selected]
    base=by_name[baseline]
    best_publisher=max((r for r in summaries if r['model']!=baseline),key=lambda r:float(r['publisher_f1']))
    delta=float(candidate['publisher_f1'])-float(base['publisher_f1'])
    nested=read_csv(out/'nested-publisher-metrics.csv')
    nested_pred=read_csv(out/'nested-publisher-predictions.csv')
    nested_f1=manifest['publisher_nested_macro_f1']
    nested_delta=nested_f1-float(base['publisher_f1'])
    temporal_delta=float(candidate['temporal_f1'])-float(base['temporal_f1'])
    metrics=read_csv(out/'metrics.csv')
    predictions=read_csv(out/'predictions.csv')
    source_metrics=read_csv(out/'per-source.csv')
    review=read_csv(out/'review-metrics.csv')
    lookup={(r['model'],r['stage'],r['fold'],r['article_id']):r for r in predictions}
    paired=[]
    for r in predictions:
        if r['model']!=selected:
            continue
        b=lookup[(baseline,r['stage'],r['fold'],r['article_id'])]
        if r['y_pred']!=b['y_pred']:
            paired.append(dict(stage=r['stage'],fold=r['fold'],article_id=r['article_id'],
                source=r['source'],y_true=r['y_true'],baseline_pred=b['y_pred'],encoder_pred=r['y_pred'],
                baseline_score=b['score'],encoder_score=r['score'],
                outcome='encoder_correct' if r['y_pred']==r['y_true'] else 'baseline_correct'))
    csv_write(out/'selected-paired-disagreements.csv',paired)
    coverage={r['article_id']:int(r['chunks']) for r in read_csv(out/f'{selected.split("_")[0]}-coverage.csv')}
    length_metrics=[]
    for stage in ('cv','publisher','temporal'):
        for bucket,low,high in [('one',1,1),('two_to_four',2,4),('five_plus',5,10**9)]:
            for name in (baseline,selected):
                subset=[r for r in predictions if r['model']==name and r['stage']==stage and low<=coverage[r['article_id']]<=high]
                if subset:
                    length_metrics.append(dict(stage=stage,chunks=bucket,model=name,
                        **scores(np.array([int(r['y_true']) for r in subset]),np.array([int(r['y_pred']) for r in subset]),np.array([float(r['score']) for r in subset]))))
    csv_write(out/'length-metrics.csv',length_metrics)
    # Paired, source-stratified article bootstrap: descriptive conditional interval,
    # not a confidence interval over the population of unseen publishers.
    rng=np.random.default_rng(115)
    source_deltas=[]
    for source in sorted({r['fold'] for r in predictions if r['stage']=='publisher'}):
        rows=[r for r in nested_pred if r['fold']==source]
        y=np.array([int(r['y_true']) for r in rows])
        cp=np.array([int(r['y_pred']) for r in rows])
        bp=np.array([int(lookup[(baseline,'publisher',source,r['article_id'])]['y_pred']) for r in rows])
        sample=rng.integers(0,len(rows),size=(2000,len(rows)))
        def boot_f1(pred):
            tp=((y[sample]==1)&(pred[sample]==1)).sum(axis=1)
            denom=(y[sample]==1).sum(axis=1)+(pred[sample]==1).sum(axis=1)
            return np.divide(2*tp,denom,out=np.zeros(2000),where=denom>0)
        source_deltas.append(boot_f1(cp)-boot_f1(bp))
    interval=np.quantile(np.mean(source_deltas,axis=0),[.025,.975]).tolist()
    json_write(out/'paired-bootstrap.json',dict(selected='nested_encoder_selection',seed=115,replicates=2000,
        publisher_macro_f1_delta=nested_delta,conditional_article_bootstrap_95_percent=interval,
        caveat='conditional on these six publishers and silver labels; same-case dependence unresolved; not an interval over unseen-publisher population'))
    before=json.loads((out/'input-integrity-before.json').read_text())
    after={p:digest(args.data_root/p) for p in before}
    assert before==after, 'existing corpus/audit input changed'
    full_manifest=json.loads((args.data_root/'results/finalist-models-v1/manifest.json').read_text())
    corpus_key=str(Path('data/labeling/final-pilot-5000-v1/final-5000-title-body-labels.jsonl'))
    assert after[corpus_key]==full_manifest['data_sha256']
    json_write(out/'input-integrity-after.json',dict(unchanged=True,hashes=after,
        matches_existing_finalist_data_hash=True))
    enc={name:json.loads((out/f'{name}-encoding.json').read_text()) for name in MODELS}
    latency=json.loads((out/'baseline-latency.json').read_text())
    lines=['# PDG-115: frozen pretrained encoder comparison', '',
        'Completed 2026-10-29. All classification metrics use unchanged silver labels unless explicitly marked Astra.', '',
        '## Result and decision', '',
        '**Keep LSA 200 + RBF SVM C=3 as the primary research baseline. None of the eight '
        'tested frozen-encoder configurations improves mean publisher transfer.** The strongest fixed '
        'encoder publisher result is about seven F1 percentage points lower, and encoding is much '
        'slower on this CPU. MiniLM meanmax/RBF gives recent F1 0.936 versus 0.932 (36/37 FN/FP '
        'versus 38/40), but that small descriptive recent gain does not offset its publisher loss. '
        'This conclusion applies to these frozen approaches; it does not establish that all encoders '
        'or supervised fine-tuning would fail.', '',
        f'With configuration selection confined to each publisher holdout\'s training data, the encoder '
        f'selection procedure scores mean publisher F1 **{nested_f1:.3f}**, versus '
        f'**{f(base["publisher_f1"])}** for LSA 200 C=3 (difference **{nested_delta:+.3f}**). '
        'Each publisher is unseen during both fitting and configuration selection.', '',
        f'The configuration selected using older group cross-validation alone is **{selected}**. '
        f'Its mean publisher-held-out F1 is **{f(candidate["publisher_f1"])}**, versus **{f(base["publisher_f1"])}** '
        f'for LSA 200 / RBF SVM C=3 (difference {delta:+.3f}). Its 2025–2026 F1 is '
        f'**{f(candidate["temporal_f1"])}**, versus **{f(base["temporal_f1"])}** (difference {temporal_delta:+.3f}).', '',
        f'The highest observed encoder publisher mean is **{best_publisher["model"]}** at '
        f'**{f(best_publisher["publisher_f1"])}**. This is a descriptive maximum over the eight fixed '
        'configurations, not a configuration selected independently of that holdout.', '',
        'This is an exploratory representation comparison on established research holdouts. '
        'A higher score measures reproduction of the proposed labels under shift, not verified production accuracy. '
        'The six publishers are a small fixed set; article-level bootstrap uncertainty does not establish transfer to every new publisher.', '',
        '## Where this fits in the product flow', '',
        'Article title and full body → tokenizer → overlapping chunks → frozen encoder → article aggregation → '
        'small supervised classifier → decision score → default threshold zero → assisted-review queue. '
        'An embedding is a numeric vector representing text meaning. A frozen encoder never updates its pretrained '
        'weights. The classifier learns which vectors correspond to this adverse-news policy. '
        'No publisher, date, ID, silver-label provenance, or review answer enters the feature vector.', '',
        'Every chunk is encoded. Mean pooling averages the chunk vectors; meanmax concatenates the average with '
        'the maximum value in each vector coordinate. Mean can dilute a brief adverse passage. Maximum can retain '
        'a strong local feature but can also retain noise; it does not prove a policy rule was met. '
        'For example, a late paragraph about an arrest remains in the encoded article even if the opening paragraphs '
        'only discuss routine public activity. The article classifier can still miss that paragraph after aggregation.', '',
        'Linear SVM learns a straight separating boundary in vector space. RBF SVM uses a radial-basis-function '
        'kernel to allow a curved boundary. C controls the penalty for training mistakes. Both use balanced '
        'class weights calculated from training labels only and decision threshold zero. These scores are margins, '
        'not calibrated probabilities.', '',
        '## Exact comparison and selection', '',
        'Same 4,053 pre-2025 articles, StratifiedGroupKFold with three folds/shuffle/seed 115, '
        '947 2025–2026 chronological holdout articles, and six older publisher holdouts. '
        'Every held-out ID and label was asserted equal to the saved LSA 200 prediction file. '
        'Explicit training and validation IDs are saved in `splits.json`. No prepared duplicate group crosses a split. '
        'Same-case links beyond those prepared groups remain unresolved.', '',
        'All eight configurations were fixed before evaluation. Selection uses mean older-fold F1 only; '
        'publisher and temporal holdouts did not tune aggregation, classifier parameters or thresholds. '
        'Pretrained text embeddings were cached label-free before fitting. All 80 classifier fits see only their '
        'training rows. The global older-CV choice is independent of the recent holdout, but includes labels '
        'from publishers later withheld in publisher diagnostics. To assess the selection process itself, '
        'three inner group folds choose the configuration separately inside each outer publisher training '
        'pool (144 additional fits; 224 total). No outer publisher label enters this nested selection. '
        'Baseline held-out predictions were reused from the original separate fits. '
        'The full-data classical bundle is used solely for latency measurement.', '',
        'F1 combines positive precision (how many flags are silver-positive) and recall (how many silver-positive '
        'articles are found). AP is average precision, a threshold-independent ranking measure. '
        'Publisher results average publishers equally; group results average three folds equally. '
        'Different source mixes and positive rates make group and recent F1 different tasks.', '',
        table(['Configuration','Group F1','Publisher F1','Publisher recall','Recent F1','Recent recall','Recent FN / FP'],[
            [r['model'],f(r['cv_f1']),f(r['publisher_f1']),f(r['publisher_recall']),f(r['temporal_f1']),
             f(r['temporal_recall']),f"{r['temporal_fn']} / {r['temporal_fp']}"] for r in summaries]), '',
        f'For the nested encoder-selection procedure, the paired source-stratified article bootstrap gives a conditional 95% interval '
        f'[{interval[0]:+.3f}, {interval[1]:+.3f}] for publisher mean F1 difference (2,000 resamples, seed 115). '
        'This conditions on these publishers and silver labels. It excludes label uncertainty, same-case dependence, '
        'publisher-population uncertainty and the history of model searches.', '',
        '## Unseen publishers', '']
    pub=[]
    for source in sorted({r['fold'] for r in metrics if r['stage']=='publisher'}):
        b=next(r for r in metrics if r['stage']=='publisher' and r['fold']==source and r['model']==baseline)
        c=next(r for r in metrics if r['stage']=='publisher' and r['fold']==source and r['model']==selected)
        pub.append([source,c['n'],c['positives'],f(b['f1']),f(c['f1']),f(b['recall']),f(c['recall']),f"{b['fn']} → {c['fn']}",f"{b['fp']} → {c['fp']}"])
    lines += [table(['Publisher','N','Positive','LSA F1','Encoder F1','LSA recall','Encoder recall','FN','FP'],pub),'',
              'The preceding table describes the single global older-CV configuration. The next table evaluates '
              'the training-only nested selection procedure:', '',
              table(['Withheld publisher','Selected inside training data','F1','Recall','FN / FP'],[
                  [r['publisher'],r['selected_model'],f(r['f1']),f(r['recall']),f"{r['fn']} / {r['fp']}"] for r in nested]), '',
              '## Recent-news sources','']
    recent=[]
    for source in sorted({r['source'] for r in source_metrics if r['stage']=='temporal'}):
        b=next(r for r in source_metrics if r['stage']=='temporal' and r['source']==source and r['model']==baseline)
        c=next(r for r in source_metrics if r['stage']=='temporal' and r['source']==source and r['model']==selected)
        recent.append([source,c['n'],c['positives'],f(b['f1']),f(c['f1']),f(b['recall']),f(c['recall']),f"{b['fn']} → {c['fn']}",f"{b['fp']} → {c['fp']}"])
    lines += [table(['Source','N','Positive','LSA F1','Encoder F1','LSA recall','Encoder recall','FN','FP'],recent),'',
        'Rows with one or very few articles are case outcomes, not stable source-level performance estimates. '
        'All source/fold/candidate metrics are retained in `per-source.csv`, including group-fold sources.', '',
        '## Strong-model diagnostic review and policy boundaries', '',
        'Astra/high is a blinded second-model opinion, not human truth. The 60 recent-random articles are '
        'source-stratified; the 40 transfer-targeted articles were selected because all classical finalists '
        'disagreed with silver. Their results cannot estimate unbiased overall accuracy. No review answer '
        'was used to fit, select or tune an encoder. No corpus label was changed.', '',
        table(['Model','Slice','Scope','N','F1 vs Astra','Recall vs Astra','FN / FP'],[
            [r['model'],r['stratum'],r['scope'],r['n'],f(r['f1']),f(r['recall']),f"{r['fn']} / {r['fp']}"]
            for r in review if r['model'] in (baseline,selected,'encoder_selection_rule')]), '',
        'encoder_selection_rule uses nested publisher selection for transfer cases and the older-CV choice '
        'for recent cases. Candidate-level review scores remain separately available.', '',
        '`policy-boundary-cases.csv` isolates the nine named boundary cases and all uncertain judgments. '
        'The certain_nonboundary rows exclude their union; they still reflect another model\'s judgments. '
        'Earlier user exclusions for cyber libel (R1412) and preliminary Show Cause Orders (R0431) take '
        'precedence over Astra\'s positive calls. Ordinary combat, settlement illegality, and investigations '
        'of deaths without stated wrongdoing remain separate policy decisions. See `../POLICY_BOUNDARY_REVIEW.md`.', '',
        '## Full-text coverage, CPU cost and memory', '',
        table(['Encoder','Articles / chunks','Multi-chunk articles','Longest chunks','First-window token coverage','Encoding seconds','Articles/sec','OS peak memory MiB','Model directory MiB','Chunk cache MiB'],[
            [name,f"{r['articles']} / {r['chunks']}",r['long_articles'],r['max_chunks'],f"{100*r['first_window_token_coverage']:.1f}%",
             f"{r['wall_seconds']:.1f}",f"{r['articles_per_second']:.2f}",f"{r['peak_os_working_set_bytes']/2**20:.0f}",
             f"{r['model_disk_bytes']/2**20:.1f}",f"{r['cache_disk_bytes']/2**20:.1f}"] for name,r in enc.items()]), '',
        'First-window coverage counts unique content tokens, before overlap. Every token was covered by the '
        'actual chunked run. Both models use 384-dimensional chunk vectors; meanmax has 768 article dimensions. '
        'The tokenizer counts differ by model, so coverage percentages are model-specific.', '',
        table(['Stage','Chunks under selected encoder','Model','N','F1','Recall','FN / FP'],[
            [r['stage'],r['chunks'],r['model'],r['n'],f(r['f1']),f(r['recall']),f"{r['fn']} / {r['fp']}"] for r in length_metrics]), '',
        'Length slices are pooled article metrics, not publisher-macro metrics, and their class/source mixes differ. '
        'They show residual misses after complete token coverage; coverage alone does not guarantee policy recognition.', '',
        'Encoding wall time includes tokenization, inference, article aggregation, cache reads/writes and progress '
        'updates, but excludes model load/download and final feature-file writing. Forty pilot articles per '
        'encoder were already cached; the full run computes the remaining articles. Pilot timings are saved '
        'separately. E5 pilot briefly overlapped MiniLM encoding; full model runs were sequential. '
        'Peak memory is the Windows process high-water working set, not total system demand. '
        'A source-length mix can change throughput substantially.', '',
        f'The existing LSA bundle took **{latency["wall_seconds"]:.1f}s** to transform and score the same '
        f'5,000 full articles in batches of 100 (**{latency["articles_per_second"]:.2f} articles/s**), '
        f'plus **{latency["model_load_seconds"]:.1f}s** to load the three-finalist bundle. '
        'Its loaded bundle includes other classical finalists; its size/memory is not a minimal LSA-only package. '
        'This is a local throughput comparison with different natural batching, not a controlled online p95 latency benchmark.', '',
        table(['Configuration','Classifier fitting, all 10 splits (s)','Classifier scoring, all 10 splits (s)'],[
            [r['model'],f(r['classifier_fit_seconds']),f(r['classifier_inference_seconds'])]
            for r in summaries if r['model']!=baseline]), '',
        'Classifier-only costs exclude encoder extraction. Baseline historical full feature fitting was shared '
        'across three SVM variants and included other feature families, so it is not presented as an isolated '
        'LSA training benchmark. Nested selection costs are saved separately in `nested-inner-metrics.csv`. '
        'No paid inference/API calls were used; no dollar cost is inferred from CPU time.', '',
        '## Error investigation and reproducibility', '',
        '`false-negatives.csv` lists every silver-positive miss for all models and splits. '
        '`selected-paired-disagreements.csv` distinguishes encoder rescues from encoder regressions on the same '
        'articles. `review-cases.csv` preserves separate silver, Astra, uncertainty and prediction columns. '
        'Read these outcomes as evidence for review routing and training coverage, not automatic relabeling.', '',
        '### What the errors show', '',
        'The global older-CV MiniLM choice misses **274 of 577** silver-positive SEC articles when SEC '
        'is withheld; LSA misses **28**. Nested selection chooses meanmax for that source and still misses '
        '**264**. It misses **16 of 44** DTI positives versus **3** for LSA. On UK_GOV, the global choice '
        'misses **104 of 161**, exactly the LSA count; nested selection misses **103**. PTV is a trade-off: '
        'the global choice reduces false positives from **229 to 183**, but increases misses from **30 to 36**. '
        'A broad semantic representation therefore did not translate into broader policy transfer. '
        'The experiment does not isolate whether the limiting cause is pretraining, aggregation, training '
        'coverage or source/label patterns; complete token coverage rules out silent first-window truncation '
        'as an explanation for this run.', '',
        '| Case | Observed behavior and interpretation |\n'
        '| --- | --- |\n'
        '| AN-P115-R3082, SEC | NEW WORLD AI advisory: unauthorized solicitation and a possible Ponzi scheme are explicit in the full text. LSA flags it; global MiniLM clears it despite encoding all six chunks. This is a concrete policy-relevant miss relative to the supplied text and silver label. |\n'
        '| AN-P115-X287, DTI | Uncertified fireworks were actually seized. LSA flags it; MiniLM clears it despite all three chunks. The seizure distinguishes this from an excluded preliminary request for explanation alone. |\n'
        '| AN-P115-R2255, PTV | Silver-positive, LSA-positive, MiniLM-negative. Full text reports general crime trends and prevention-policy claims without a concrete qualifying incident. This reading suggests a label-audit candidate; it is not evidence that every silver-relative encoder miss is a real policy miss. No label was changed. |\n'
        '| AN-P115-R0001, ADB | MiniLM rescues the recent debarment/procurement-fraud case that LSA misses; both silver and Astra are positive. This is useful case-level improvement despite the aggregate regression. |\n'
        '| AN-P115-R4395, UK | MiniLM predicts negative and agrees with silver; Astra identifies a specific hospital attack and predicts positive, while LSA predicts positive. A wrongly negative silver label can hide a miss from the ordinary false-negatives file. This remains a separate review observation, not human adjudication. |', '',
        'The first three examples were selected after scoring to inspect disagreements, with full supplied '
        'bodies saved in `regression-case-extracts.json`; they are illustrative, not a random error-rate sample. '
        'The last two come from the existing blinded review. Transformer semantics can help particular cases '
        'while losing enforcement details in others. Meanmax did not remove the SEC regression, so simply '
        'changing from mean to maximum-inclusive pooling was insufficient here.', '',
        'The 5,000-row corpus matches the earlier finalist data hash. Fifteen corpus/audit files have identical '
        'before/after SHA-256 hashes. Reserved data was never opened or used. Pinned model revisions, file hashes, '
        'cache contracts, package versions, resource snapshots and exact split IDs are saved alongside results.', '',
        'Run `run_encoder_screen.py download --model minilm`, then `pilot`, then `encode`; repeat for '
        '`e5small`; run `evaluate`, `benchmark-baseline`, and `summarize_encoder_screen.py`. '
        'Use `--data-root` and `--output` for explicit paths when running from the research checkout. '
        'Install `requirements-encoder.txt` in the isolated research environment. See `DESIGN.md` for official '
        'model-card links, licenses, selection rationale and exact representation definitions.', '',
        'Fine-tuning was not attempted. There is no confirmed CUDA GPU, available RAM is limited, '
        'and frozen-feature evidence should be assessed before increasing training cost. A separate, '
        'case-safe benchmark and independent reference would be needed to justify a training experiment '
        'and establish production performance. Given the observed publisher regression and 30–71x '
        'higher extraction time in this local throughput comparison, extra encoder training was not '
        'justified in this stage. The next useful evidence is an answer-blind reference and targeted '
        'SEC/DTI/UK label/source review, with policy boundaries resolved separately. '
        'No unit tests were added or run under the project workflow.', '',
        'Three takeaways: (1) better older-fold F1 did not imply better unseen-publisher transfer; '
        '(2) full-text coverage is necessary but does not guarantee the policy is recognized; '
        '(3) LSA remains the stronger, faster research option, while label uncertainty and weak UK '
        'recall remain unresolved by either representation.', '']
    (out/'ENCODER_COMPARISON_REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(selected=selected,publisher_fixed_delta=delta,publisher_nested_delta=nested_delta,recent_delta=temporal_delta,
                         conditional_interval=interval,inputs_unchanged=True),indent=2))


if __name__=='__main__':
    main()

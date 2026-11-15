"""Benchmark the local NER service; no production pipeline or database required."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import html
import itertools
import json
import logging
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY", "DO_NOT_TRACK"):
    os.environ[key] = "1"


def variant_cases():
    def case(identifier, title, body, people=(), organizations=()):
        return {"sample_id": identifier, "title": title, "body": body,
                "referenceGroups": [
                    {"type": kind, "names": list(names)}
                    for kind, groups in (("INDIVIDUAL", people), ("ORGANIZATION", organizations))
                    for names in groups], "referenceKind": "synthetic_manual"}

    return [
        case("V01", "Maria Santos presents the annual report",
             "Maria Santos presented the annual report. Santos answered questions after the meeting.",
             people=[("Maria Santos", "Santos")]),
        case("V02", "Juan dela Cruz speaks at the meeting",
             "Juan dela Cruz attended the meeting. Cruz explained the proposed schedule.",
             people=[("Juan dela Cruz", "Cruz")]),
        case("V03", "Two people with the same surname attend a meeting",
             "John Smith met Jane Smith at the meeting. Smith later answered a question; the transcript does not say which person spoke.",
             people=[("John Smith",), ("Jane Smith",), ("Smith",)]),
        case("V04", "A speaker uses an alias",
             "Carlos Reyes, also known as Miguel Santos, spoke at the conference. Miguel Santos answered questions.",
             people=[("Carlos Reyes", "Miguel Santos")]),
        case("V05", "An alias differs from another person's real name",
             "Carlos Reyes, also known as Miguel Santos, spoke first. The real Miguel Santos is a different person and spoke later.",
             people=[("Carlos Reyes",), ("Miguel Santos",)]),
        case("V06", "An agency publishes its annual report",
             "The Federal Bureau of Investigation (FBI) published its annual report. The FBI held a press briefing.",
             organizations=[("Federal Bureau of Investigation", "FBI")]),
        case("V07", "A regulator announces a public consultation",
             "The Securities and Exchange Commission (SEC) announced a public consultation. The SEC invited written comments.",
             organizations=[("Securities and Exchange Commission", "SEC")]),
        case("V08", "A bank uses an abbreviation",
             "Cedar Bank (CB) released its annual report. CB announced a new branch.",
             organizations=[("Cedar Bank", "CB")]),
        case("V09", "Two separate banks publish reports",
             "Cedar Bank and Cedar Capital Bank published separate reports. The two banks operate independently.",
             organizations=[("Cedar Bank",), ("Cedar Capital Bank",)]),
        case("V10", "A person's name appears with and without an initial",
             "John A. Smith presented the report. The minutes refer to the same person as John Smith.",
             people=[("John A. Smith", "John Smith")]),
        case("V11", "An agency uses a shorter name",
             "The National Crime Agency published a report. The report later refers to the same agency as the Crime Agency.",
             organizations=[("National Crime Agency", "Crime Agency")]),
        case("V12", "Nagpaliwanag si Maria Santos sa pulong",
             "Dumalo si Maria Santos sa pulong. Ipinaliwanag ni Santos ang bagong programa.",
             people=[("Maria Santos", "Santos")]),
    ]


def read_news(path):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(rows) != 100:
        raise ValueError(f"Expected the selected 100-news JSONL file; found {len(rows)} rows.")
    identifiers = set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str) for k in ("title", "body")):
            raise ValueError(f"Row {index} needs string fields title and body.")
        row["sample_id"] = str(row.get("sample_id", f"N{index:03d}"))
        if row["sample_id"] in identifiers:
            raise ValueError(f"Duplicate sample_id: {row['sample_id']}")
        identifiers.add(row["sample_id"])
        if not (row["title"].strip() or row["body"].strip()):
            raise ValueError(f"Empty news: {row['sample_id']}")
    return rows


def assess(row, result):
    forms, memberships, predicted_pairs = set(), {}, set()
    for index, entity in enumerate(result["entities"]):
        names = {m["text"] for m in entity["mentions"]}
        if names != {entity["memberName"], *entity["aliases"]}:
            raise ValueError("Group names do not match accepted mentions.")
        for name in names:
            key = (entity["entityType"], name)
            if key in memberships:
                raise ValueError("The same accepted name was split across groups.")
            forms.add(key)
            memberships[key] = index
        predicted_pairs.update((entity["entityType"], *pair)
                               for pair in itertools.combinations(sorted(names), 2))
        for m in entity["mentions"]:
            source = row[m["field"]]
            if not (source[m["start"]:m["end"]] == m["text"]
                    and m["evidenceSentence"] in source
                    and m["text"] in m["evidenceSentence"]
                    and m["votes"] == len(set(m["models"])) >= 2
                    and set(m["models"]) == set(m["scores"])
                    and all(score >= 0.8 for score in m["scores"].values())):
                raise ValueError(f"Invalid mention: {m}")
    if "referenceGroups" not in row:
        return {"sourceChecks": "PASSED"}
    reference = {(g["type"], name) for g in row["referenceGroups"] for name in g["names"]}
    expected_pairs = {(g["type"], *pair) for g in row["referenceGroups"]
                      for pair in itertools.combinations(sorted(g["names"]), 2)}
    eligible = {pair for pair in expected_pairs if (pair[0], pair[1]) in forms and (pair[0], pair[2]) in forms}
    encode = lambda values: [list(item) for item in sorted(values)]
    return {"sourceChecks": "PASSED", "correctNames": len(forms & reference),
            "missedNames": encode(reference - forms), "extraNames": encode(forms - reference),
            "expectedAliasPairs": len(expected_pairs), "assessableAliasPairs": len(eligible),
            "correctAliasPairs": len(predicted_pairs & eligible),
            "missedAliasPairsAfterExtraction": encode(eligible - predicted_pairs),
            "aliasPairsBlockedByMissingNames": encode(expected_pairs - eligible),
            "unexpectedMerges": encode(predicted_pairs - expected_pairs)}


def write_review(path, report, records):
    esc = lambda value: html.escape(str(value))
    parts = ['<!doctype html><html lang="fa" dir="rtl"><meta charset="utf-8">',
             '<title>NER review</title><style>body{font:15px/1.8 Tahoma,sans-serif;max-width:1100px;margin:24px auto;padding:16px;background:#f5f7fa}article{background:white;padding:20px;margin:16px 0;border:1px solid #ddd}.src,pre{direction:ltr;text-align:left;white-space:pre-wrap}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;vertical-align:top}</style>',
             '<h1>بررسی استخراج نام‌ها و گروه‌بندی</h1>',
             '<p>زمان inference مجموع پردازش سه مدل برای هر خبر است؛ زمان بارگذاری در آن نیست. زمان کل شامل بارگذاری و بررسی فایل‌هاست.</p>',
             '<details><summary>گزارش زمان و اجرا</summary><pre>' + esc(json.dumps(report, ensure_ascii=False, indent=2)) + '</pre></details>']
    for record in records:
        parts += ['<article><h2>' + esc(record['sample_id']) + ' — ' + esc(round(record['inferenceSeconds'], 3)) + ' s</h2>',
                  '<h3 class="src">' + esc(record['title']) + '</h3>',
                  '<details><summary>متن کامل</summary><p class="src">' + esc(record['body']) + '</p></details>',
                  '<table><tr><th>نوع</th><th>نام و شکل‌های پذیرفته‌شده</th><th>جمله‌های شاهد و رأی</th></tr>']
        for entity in record['entities']:
            evidence = ''.join('<p class="src">' + esc(m['text']) + ' (' + str(m['votes']) + ')<br>'
                               + esc(m['evidenceSentence']) + '</p>' for m in entity['mentions'])
            parts.append('<tr><td>' + esc(entity['entityType']) + '</td><td class="src">'
                         + esc(entity['memberName']) + '<br>' + esc(', '.join(entity['aliases'])) + '</td><td>' + evidence + '</td></tr>')
        parts += ['</table><details><summary>شاهد گروه‌بندی و موارد مبهم</summary><pre>'
                  + esc(json.dumps({"groupingEvidence": [e for g in record['entities'] for e in g['groupingEvidence']],
                                    "unresolvedNameLinks": record['unresolvedNameLinks']}, ensure_ascii=False, indent=2)) + '</pre></details>']
        if 'referenceGroups' in record:
            parts += ['<h3>انتظارِ نمونهٔ ساختگی و نتیجهٔ مقایسه</h3><pre>'
                      + esc(json.dumps({"referenceGroups": record['referenceGroups'], "assessment": record['assessment']}, ensure_ascii=False, indent=2)) + '</pre>']
        parts.append('</article>')
    path.write_text(''.join(parts) + '</html>', encoding='utf-8')


def peak_memory_mib():
    if sys.platform == 'win32':
        import psutil
        info = psutil.Process().memory_info()
        return getattr(info, 'peak_wset', info.rss) / 1024**2
    import resource
    divisor = 1024**2 if sys.platform == 'darwin' else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / divisor


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['news100', 'variants'], default='news100')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--batch-size', type=int, default=32)
    args = parser.parse_args()
    if args.threads < 1 or not 1 <= args.batch_size <= 128:
        parser.error('threads must be positive; batch-size must be 1..128.')
    source = args.input or Path.home() / 'Downloads/NER_Local_Trial_Windows/NER_Local_Trial/data/news100.jsonl'
    if args.suite == 'variants' and args.input:
        parser.error('--input is only used with --suite news100.')
    if args.suite == 'news100' and not source.is_file():
        parser.error('News100 file not found. Supply its JSONL path with --input.')
    rows = variant_cases() if args.suite == 'variants' else read_news(source)
    out = ROOT / 'data/reports/entityExtraction' / (
        datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + args.suite + '_' + uuid.uuid4().hex[:6])
    out.mkdir(parents=True, exist_ok=False)
    report = {'status': 'RUNNING', 'suite': args.suite, 'expectedNews': len(rows), 'completedNews': 0,
              'python': platform.python_version(), 'platform': platform.platform(), 'threads': args.threads,
              'batchSize': args.batch_size, 'batchSeconds': [], 'newsInferenceSeconds': [],
              'referenceKind': 'synthetic_manual' if args.suite == 'variants' else 'no_reference_scored',
              'inputSha256': hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    save = lambda: (out / 'summary.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    save()
    print('OUTPUT:', out, flush=True)
    logging.basicConfig(level=logging.INFO)
    started = time.perf_counter()
    records = []
    core = original = None
    try:
        from intelligence.entityExtraction import extractor as core
        from services.mediaPostProcess.mediaEntityExtractionService import MediaEntityExtractionService
        original = core.collect_mentions
        model_seconds = {key: 0.0 for key in core.MODEL_KEYS}
        def timed_collect(row, adapter, **kwargs):
            tick = time.perf_counter()
            try:
                return original(row, adapter, **kwargs)
            finally:
                model_seconds[adapter.key] += time.perf_counter() - tick
        # Test-only timing wrapper; model predictions and runtime checks are unchanged.
        core.collect_mentions = timed_collect
        with MediaEntityExtractionService(threads=args.threads) as service, (out / 'entities.jsonl').open('w', encoding='utf-8') as stream:
            for offset in range(0, len(rows), args.batch_size):
                batch = rows[offset:offset + args.batch_size]
                tick = time.perf_counter()
                outputs = service.extract_many([(r['title'], r['body']) for r in batch], batch_size=args.batch_size)
                report['batchSeconds'].append(time.perf_counter() - tick)
                if len(outputs) != len(batch):
                    raise ValueError('Unexpected result count.')
                per_news = service.metadata['newsModelSeconds']
                for position, (row, result) in enumerate(zip(batch, outputs)):
                    if result['status'] != 'COMPLETED':
                        raise ValueError(f"Incomplete result: {row['sample_id']}")
                    record = {**row, **result, 'inferenceSeconds': per_news[position],
                              'assessment': assess(row, result)}
                    records.append(record)
                    stream.write(json.dumps(record, ensure_ascii=False) + '\n')
                    report['completedNews'] += 1
                    print(f"{report['completedNews']}/{len(rows)} {row['sample_id']}: {len(result['entities'])} entities", flush=True)
                stream.flush()
                report['runtime'] = service.metadata
                report['newsInferenceSeconds'].extend(service.metadata['newsModelSeconds'])
                save()
        durations = report['newsInferenceSeconds']
        report.update(status='COMPLETED', totalSeconds=time.perf_counter() - started,
                      inferenceP50Seconds=statistics.median(durations),
                      inferenceP95Seconds=sorted(durations)[math.ceil(.95 * len(durations)) - 1],
                      modelInferenceSeconds=model_seconds,
                      peakRssMiB=peak_memory_mib(),
                      totalEntities=sum(len(r['entities']) for r in records),
                      groupsWithAliases=sum(bool(e['aliases']) for r in records for e in r['entities']))
        report['wallSecondsPerNews'] = report['totalSeconds'] / len(rows)
        report['newsPerMinute'] = 60 * len(rows) / report['totalSeconds']
        report['timingNote'] = ('totalSeconds includes model imports, asset verification, loading, unloading, inference, '
                                'postprocessing and incremental JSON writes; excludes final report/HTML generation. '
                                'Inference includes chunking, tokenization and decoding. P50/P95 exclude loading and '
                                'grouping and are NOT single-request end-to-end latency. No warm-up is excluded; '
                                'OS file caches are uncontrolled. Model loads once per nonempty batch.')
        if args.suite == 'variants':
            assessments = [r['assessment'] for r in records]
            report['variantAssessment'] = {k: sum(a[k] if isinstance(a[k], int) else len(a[k]) for a in assessments)
                                          for k in assessments[0] if k != 'sourceChecks'}
            totals = report['variantAssessment']
            ratio = lambda numerator, denominator: numerator / denominator if denominator else None
            report['variantMetrics'] = {
                'surfacePrecision': ratio(totals['correctNames'], totals['correctNames'] + totals['extraNames']),
                'surfaceRecall': ratio(totals['correctNames'], totals['correctNames'] + totals['missedNames']),
                'aliasPairRecallWhenNamesDetected': ratio(totals['correctAliasPairs'], totals['assessableAliasPairs']),
                'aliasPairRecallEndToEnd': ratio(totals['correctAliasPairs'], totals['expectedAliasPairs']),
                'aliasPairPrecision': ratio(totals['correctAliasPairs'], totals['correctAliasPairs'] + totals['unexpectedMerges']),
            }
            report['referenceLimit'] = ('Curated synthetic scenarios, not a representative estimate of production accuracy. '
                                        'V03 keeps the ambiguous surname separate. V05 contains two people sharing a '
                                        'name: surface-name grouping does not resolve mention-level identity.')
        save()
        write_review(out / 'review.html', report, records)
        print(f"DONE: {len(rows)} news in {report['totalSeconds']:.1f}s; p50={report['inferenceP50Seconds']:.2f}s; p95={report['inferenceP95Seconds']:.2f}s", flush=True)
        print('REVIEW:', out / 'review.html', flush=True)
        print('SUMMARY:', out / 'summary.json', flush=True)
    except (Exception, KeyboardInterrupt) as error:
        report.update(status='INTERRUPTED' if isinstance(error, KeyboardInterrupt) else 'FAILED', error=str(error), totalSeconds=time.perf_counter() - started)
        save()
        raise
    finally:
        if original is not None:
            core.collect_mentions = original


if __name__ == '__main__':
    main()

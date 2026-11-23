"""Media Member Post Processing: independent, bounded, migration-free pipeline.

Run AFTER article Media Post Processing has completed. Initial/Reprocess scan
current core articles. Incremental processes the union of core Daily Delta
and independent adverse-analysis updates for EXPLICIT effective dates.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import date
from time import perf_counter

from infrastructure.database.connection import get_db_connection
from repositories.mediaPostProcess import mediaMemberPostProcessRepository as repository
from services.mediaPostProcess.mediaEntityExtractionService import MediaEntityExtractionService
from services.mediaPostProcess.mediaMemberPostProcessingService import MediaMemberPostProcessingService

logger = logging.getLogger(__name__)


def run_media_member_post_processing(
        mode: str, *, effective_date: date | None = None,
        through_date: date | None = None, vv_media_ids: list[int] | None = None,
        all_current: bool = False, batch_size: int = 16,
        max_batch_chars: int = 200_000, threads: int = 4,
        model_dir=None, extractor_factory=None):
    """Return explicit SUCCESS/PARTIAL and per-action counts.

    Incremental dates are mandatory: there is no durable checkpoint in the
    approved schema. An implicit last-created-at cursor could silently skip
    partially processed dates. Orchestration must supply and replay dates.
    """
    if mode not in ('initial', 'incremental', 'reprocess'):
        raise ValueError('mode must be initial, incremental, or reprocess')
    if type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError('batch_size must be in 1..128')
    if type(max_batch_chars) is not int or max_batch_chars < 1:
        raise ValueError('max_batch_chars must be a positive integer')
    if type(threads) is not int or threads < 1:
        raise ValueError('threads must be a positive integer')
    selected_ids = list(dict.fromkeys(vv_media_ids or []))
    if any(type(item) is not int or item < 1 for item in selected_ids):
        raise ValueError('vv_media_ids must be positive integers')
    if mode == 'incremental':
        if not isinstance(effective_date, date) or isinstance(effective_date, type) or (
                through_date is not None and not isinstance(through_date, date)):
            raise ValueError('incremental requires an explicit --effective-date')
        through_date = through_date or effective_date
        if through_date < effective_date:
            raise ValueError('through_date must be on or after effective_date')
        if selected_ids or all_current:
            raise ValueError('incremental cannot use explicit IDs or --all')
    elif effective_date is not None or through_date is not None:
        raise ValueError('effective dates apply only to incremental')
    if mode == 'initial' and (selected_ids or all_current):
        raise ValueError('initial scans all current news without selection')
    if mode == 'reprocess' and bool(selected_ids) == bool(all_current):
        raise ValueError('reprocess requires exactly one of --vv-media-id or --all')

    counts = Counter()
    errors = []
    started = perf_counter()
    with get_db_connection() as db:
        extra_columns = repository.preflight(db)
        core_cap, delta_cap, analysis_cap = repository.scan_bounds(db)
        if mode == 'incremental':
            repository.validate_delta_references(db, effective_date, through_date, delta_cap)
    counts['analysis_carried_forward'] = 0

    def extractor_creator():
        return (extractor_factory or (lambda: MediaEntityExtractionService(
            model_dir=model_dir, threads=threads)))()

    def candidate_pages():
        if mode == 'reprocess' and selected_ids:
            for i in range(0, len(selected_ids), batch_size):
                yield selected_ids[i:i+batch_size]
        elif mode == 'incremental':
            after_vv = 0
            while True:
                with get_db_connection() as db:
                    page = repository.find_incremental_batch(
                        db, after_vv, delta_cap, analysis_cap,
                        effective_date, through_date, batch_size)
                if not page:
                    return
                yield page
                after_vv = page[-1]
        else:
            after_id = 0
            while True:
                with get_db_connection() as db:
                    rows = repository.find_initial_batch(db, after_id, core_cap, batch_size)
                if not rows:
                    return
                yield [row[1] for row in rows]
                after_id = rows[-1][0]

    pending = []
    pending_chars = 0

    def flush(service):
        nonlocal pending, pending_chars
        if not pending:
            return
        batch = pending
        pending, pending_chars = [], 0

        def run_subset(items):
            try:
                results = service.extract_and_save_batch(items)
            except Exception as error:
                # A model installation/configuration error is global. Do not
                # repeatedly reload three broken models to bisect the batch.
                from intelligence.entityExtraction.contract import EntityExtractionError
                if (len(items) > 1 and not isinstance(error, EntityExtractionError)):
                    logger.warning('Batch failed; isolating bad article via split: %s', error)
                    middle = len(items) // 2
                    run_subset(items[:middle])
                    run_subset(items[middle:])
                    return
                logger.exception('Member inference failed on %s articles', len(items))
                for item in items:
                    counts['failed'] += 1
                    errors.append({'vv_media_id':item['snapshot']['vv_media_id'], 'error':str(error)})
                return
            for vv_media_id, status, error in results:
                counts[status.lower()] += 1
                if error is not None:
                    logger.error('Member save failed vv_media_id=%s: %s', vv_media_id, error)
                    errors.append({'vv_media_id':vv_media_id,'error':str(error)})
                else:
                    logger.info('Member saved vv_media_id=%s result=%s', vv_media_id, status)

        run_subset(batch)

    with MediaMemberPostProcessingService(extra_columns=extra_columns,
                                          extractor_factory=extractor_creator) as service:
        for page in candidate_pages():
            for vv_media_id in page:
                counts['scanned'] += 1
                try:
                    source = service.read(vv_media_id)
                    if source['core_id'] > core_cap:
                        raise repository.PendingSource('Core changed after scan began; replay this date')
                    if (not source['deleted'] and
                            source['analysis_source_version'] < source['version']):
                        counts['analysis_carried_forward'] += 1
                        logger.warning('Carried-forward analysis vv_media_id=%s '
                                       'core_version=%s analysis_version=%s; ensure upstream finished',
                                       vv_media_id, source['version'], source['analysis_source_version'])
                    status, task = service.prepare(source, force=(mode == 'reprocess'))
                    if task is None:
                        if status == 'ALREADY_PROCESSED':
                            counts['already_processed'] += 1
                        else:
                            # Never close current rows based on unknown adverse status.
                            outcome = service.expire(source)
                            counts[outcome.lower()] += 1
                    else:
                        size = len(source['title']) + len(source['body'])
                        if pending and (len(pending) >= batch_size or
                                        pending_chars + size > max_batch_chars):
                            flush(service)
                        pending.append(task)
                        pending_chars += size
                        if len(pending) >= batch_size or pending_chars >= max_batch_chars:
                            flush(service)
                except repository.PendingSource as error:
                    counts['pending'] += 1
                    logger.warning('Pending member vv_media_id=%s: %s', vv_media_id, error)
                    errors.append({'vv_media_id':vv_media_id,'error':str(error)})
                except Exception as error:
                    counts['failed'] += 1
                    logger.exception('Member processing failed vv_media_id=%s', vv_media_id)
                    errors.append({'vv_media_id':vv_media_id,'error':str(error)})
        flush(service)

    if mode == 'incremental' and counts['scanned'] == 0:
        logger.warning('No core delta or analysis updates in the selected date range; '
                       'verify upstream Daily Delta generation completed (an empty '
                       'delta is not proof of completeness)')
    status = 'PARTIAL' if counts['pending'] or counts['failed'] else 'SUCCESS'
    elapsed = perf_counter() - started
    return {
        'mode':mode, 'run_status':status, 'effective_date':str(effective_date) if effective_date else None,
        'through_date':str(through_date) if through_date else None,
        'scan_caps':{'core':core_cap,'delta':delta_cap,'analysis':analysis_cap},
        'counts':dict(counts), 'duration_seconds':round(elapsed,3),
        'errors':errors[:100], 'error_count':len(errors),
        'coverage_warning': ('No source events found; verify upstream generation' if
                             mode == 'incremental' and counts['scanned'] == 0 else None),
    }

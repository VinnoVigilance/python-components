"""Independent scheduled entry point for entity extraction AFTER article analysis.

Examples:
    python -m jobs.mediaMemberPostProcessingJob --mode initial
    python -m jobs.mediaMemberPostProcessingJob --mode incremental --effective-date 2026-11-20
    python -m jobs.mediaMemberPostProcessingJob --mode incremental --effective-date 2026-11-18 --through-date 2026-11-20
    python -m jobs.mediaMemberPostProcessingJob --mode reprocess --vv-media-id 410 --vv-media-id 411
    python -m jobs.mediaMemberPostProcessingJob --mode reprocess --all
"""
import argparse
import logging
from datetime import date
from pathlib import Path
from pprint import pformat

from config.loggingConfig import JobLog, configure_logging
from infrastructure.database.jobLock import advisory_job_lock
from pipelines.mediaMemberPostProcessingPipeline import run_media_member_post_processing


_JOB_LOCK_ID = 802156604  # Separate from media ingestion/risk locks.


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True, choices=('initial','incremental','reprocess'))
    parser.add_argument('--effective-date', type=date.fromisoformat)
    parser.add_argument('--through-date', type=date.fromisoformat)
    parser.add_argument('--vv-media-id', type=int, action='append', dest='vv_media_ids')
    parser.add_argument('--all', action='store_true', dest='all_current')
    parser.add_argument('--batch-size', type=int, default=16)
    parser.add_argument('--max-batch-chars', type=int, default=200_000)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--model-dir')
    args = parser.parse_args(argv)
    configure_logging()
    log_dir = Path(__file__).resolve().parents[1] / 'logs'
    with JobLog('media-member-post-process', log_dir) as job:
        job.event('JOB_STARTED', started_at=job.stamp(), sources='media-member',
                  timezone='UTC', mode=args.mode)
        try:
            with advisory_job_lock(_JOB_LOCK_ID) as acquired:
                if not acquired:
                    job.event('JOB_ALREADY_RUNNING', status='ALREADY_RUNNING')
                    return 2
                report = run_media_member_post_processing(
                    args.mode, effective_date=args.effective_date,
                    through_date=args.through_date, vv_media_ids=args.vv_media_ids,
                    all_current=args.all_current, batch_size=args.batch_size,
                    max_batch_chars=args.max_batch_chars, threads=args.threads,
                    model_dir=args.model_dir)
            job.write_block('Media Member Post Processing result:\n' + pformat(report))
            job.event('JOB_FINISHED', status=report['run_status'],
                      mode=args.mode, counts=report['counts'],
                      duration_seconds=report['duration_seconds'])
            return 0 if report['run_status'] == 'SUCCESS' else 2
        except Exception as error:
            job.event('JOB_ABORTED', level=logging.ERROR, error=error)
            return 1


if __name__ == '__main__':
    raise SystemExit(main())

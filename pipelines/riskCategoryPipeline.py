"""Run media or watchlist risk assessment independently of ingestion."""

import argparse
import logging
from datetime import date
from pathlib import Path
from pprint import pprint

from config.loggingConfig import JobLog, configure_logging


def run_risk_category_etl(subject="watchlist", mode="incremental", initial_load=False,
                          effective_date=None, max_rows=None, list_name=None,
                          source_name=None, model_path=None, batch_size=1000, threads=4):
    """Dispatch to one domain service while retaining the initial-load argument."""
    if initial_load:
        if mode not in {"incremental", "initial"}:
            raise ValueError("initial_load requires initial or incremental mode.")
        mode = "initial"
    if list_name and source_name and list_name != source_name:
        raise ValueError("list_name and source_name disagree.")
    options = dict(mode=mode, effective_date=effective_date, max_rows=max_rows,
                   source_name=source_name or list_name, model_path=model_path,
                   batch_size=batch_size, threads=threads)
    if subject == "media":
        from services.risk.mediaRiskService import run_media_risk
        return run_media_risk(**options)
    if subject == "watchlist":
        from services.risk.watchlistRiskService import run_watchlist_risk
        return run_watchlist_risk(**options)
    raise ValueError("subject must be media or watchlist.")


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Versioned media/watchlist risk calculation.")
    parser.add_argument("--subject", choices=("media", "watchlist"), default="watchlist")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--mode", choices=("initial", "incremental"))
    modes.add_argument("--initial", dest="mode", action="store_const", const="initial")
    modes.add_argument("--incremental", dest="mode", action="store_const", const="incremental")
    modes.add_argument("--initial-load", action="store_true")
    parser.add_argument("--effective-date", type=date.fromisoformat,
                        help="Catch up chronologically through this date (default: today).")
    parser.add_argument("--source", "--list", dest="source_name")
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-rows", type=int)
    parser.add_argument("--threads", type=int, default=4)
    return parser.parse_args(argv)


def main(argv=None):
    configure_logging()
    arguments = _parse_args(argv)
    with JobLog(f"risk-{arguments.subject}", Path(__file__).resolve().parents[1] / "logs") as job:
        job.event("JOB_STARTED", subject=arguments.subject)
        try:
            result = run_risk_category_etl(
                subject=arguments.subject, mode=arguments.mode or "incremental",
                initial_load=arguments.initial_load, effective_date=arguments.effective_date,
                source_name=arguments.source_name, model_path=arguments.model_path,
                batch_size=arguments.batch_size, max_rows=arguments.max_rows, threads=arguments.threads,
            )
        except Exception as error:
            job.event("JOB_ABORTED", level=logging.ERROR, error=error)
            raise
        result["error_log"] = str(job.error_path)
        job.event("JOB_FINISHED", status=result["run_status"],
                  last_scanned_date=result["last_scanned_date"],
                  pending_count=result["pending_count"], retried_count=result["retried_count"], error_log=result["error_log"])
    pprint(result)
    return 0 if result["run_status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

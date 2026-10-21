"""Run media and watchlist risk classification with shared job logging."""

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from config.loggingConfig import JobLog
from pipelines.riskCategoryPipeline import run_risk_category_etl


def run_risk_job(mode="incremental", subject="both", effective_date=None,
                 batch_size=1000, max_rows=None, threads=4):
    """Run selected risk domains independently and summarize incomplete work."""
    mode = mode.lower()
    if mode not in {"initial", "incremental"}:
        raise ValueError("mode must be initial or incremental.")
    if subject not in {"both", "media", "watchlist"}:
        raise ValueError("subject must be both, media or watchlist.")
    if mode == "initial" and effective_date is not None:
        raise ValueError("effective_date applies only to incremental processing.")
    if batch_size < 1 or (max_rows is not None and max_rows < 1) or threads < 1:
        raise ValueError("batch_size, max_rows and threads must be positive.")
    domains = ("media", "watchlist") if subject == "both" else (subject,)
    log = JobLog("risk", ROOT_DIR / "logs", os.getenv("WATCHLIST_JOB_TIMEZONE") or None)
    result = dict(status="FAILED", failed=0, domains={}, log_path=str(log.text_path),
                  error_log_path=str(log.error_path), full_log_path=str(log.full_path),
                  json_log_path=str(log.json_path))
    with log:
        log.event("JOB_STARTED", mode=mode, subjects=",".join(domains),
                  errors_file=str(log.error_path))
        try:
            for domain in domains:
                with log.bind(source=domain, attempt=1, stage="RISK"):
                    log.event("ATTEMPT_STARTED", mode=mode)
                    try:
                        outcome = run_risk_category_etl(
                            subject=domain, mode=mode, effective_date=effective_date,
                            batch_size=batch_size, max_rows=max_rows, threads=threads,
                        )
                    except Exception as error:
                        log.event("ATTEMPT_FAILED", level=logging.ERROR, error=error)
                        outcome = dict(run_status="FAILED", error=str(error),
                                       error_type=type(error).__name__)
                    result["domains"][domain] = outcome
                    incomplete = outcome["run_status"] != "SUCCESS"
                    result["failed"] += int(incomplete)
                    log.event("SOURCE_FINISHED", level=logging.WARNING if incomplete else logging.INFO,
                              status=outcome["run_status"],
                              processed=outcome.get("processed_count", 0),
                              failed=outcome.get("failed_count", 0),
                              pending=outcome.get("pending_count", 0),
                              retried=outcome.get("retried_count", 0),
                              reason=outcome.get("no_work_reason", outcome.get("error", "")))
            result["status"] = ("SUCCESS" if not result["failed"] else
                                "FAILED" if result["failed"] == len(domains) else "PARTIAL")
        except BaseException as error:
            result["status"] = "ABORTED"
            log.event("JOB_ABORTED", level=logging.ERROR, error=error)
            raise
        finally:
            level = (logging.ERROR if result["status"] == "ABORTED" else
                     logging.WARNING if result["failed"] else logging.INFO)
            log.event("JOB_FINISHED", level=level,
                      status=result["status"], incomplete_domains=result["failed"],
                      errors_file=str(log.error_path))
        lines = ["", "RISK SUMMARY"]
        for domain, outcome in result["domains"].items():
            lines.append(f"{domain}: {outcome['run_status']} "
                         f"processed={outcome.get('processed_count', 0)} "
                         f"pending={outcome.get('pending_count', 0)} "
                         f"retried={outcome.get('retried_count', 0)}")
            if outcome.get("no_work_reason"):
                lines.append(outcome["no_work_reason"])
        lines.extend((f"Logs: {log.text_path}", f"Errors: {log.error_path}"))
        log.write_block("\n".join(lines))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run media and watchlist risk jobs.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--initial", dest="mode", action="store_const", const="initial")
    modes.add_argument("--incremental", dest="mode", action="store_const", const="incremental")
    modes.add_argument("--mode", type=str.lower, choices=("initial", "incremental"))
    parser.add_argument("--subject", choices=("both", "media", "watchlist"), default="both")
    parser.add_argument("--effective-date", type=date.fromisoformat,
                        help="Inclusive incremental target date (default: today).")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--max-rows", type=int, help="Limit work per domain, including retries.")
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args(argv)
    if args.mode == "initial" and args.effective_date is not None:
        parser.error("--effective-date applies only to incremental processing.")
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT_DIR / ".env")
    except ImportError:
        pass
    try:
        result = run_risk_job(mode=args.mode or "incremental", subject=args.subject,
                              effective_date=args.effective_date, batch_size=args.batch_size,
                              max_rows=args.max_rows, threads=args.threads)
    except KeyboardInterrupt:
        return 130
    return 0 if result["status"] == "SUCCESS" else 2


if __name__ == "__main__":
    raise SystemExit(main())

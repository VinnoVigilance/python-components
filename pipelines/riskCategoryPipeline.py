"""
Member Risk Category ETL - runnable entry point.

This is a mandatory daily step of the synchronization pipeline: after every
successful Daily Delta generation it must run for the same effective_date, so
member risk classifications stay in sync with the latest watchlist data
(guideline: "This ETL is a mandatory step of the daily synchronization
pipeline ... This process must never be skipped").

Sequencing in the daily job:

    Core Population
        -> Daily Delta Generation
        -> Core Spoke Synchronization
        -> Member Risk Category ETL   (this)

Usage
-----
    # Incremental for the latest delta batch (default daily run):
    python -m pipelines.riskCategoryPipeline

    # Incremental for a specific date:
    python -m pipelines.riskCategoryPipeline --effective-date 2026-07-17

    # First-ever population of the risk table:
    python -m pipelines.riskCategoryPipeline --initial-load

    # Stack the LLM layer on top of the rule labels (needs Ollama on the host):
    python -m pipelines.riskCategoryPipeline --use-llm --model qwen2.5:14b
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from pprint import pprint


ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))


from config.loggingConfig import configure_logging  # noqa: E402
from risk.riskEngine import RiskEngine  # noqa: E402
from services.watchlistPipeline import (  # noqa: E402
    watchlistRiskCategoryService as risk_service,
)


logger = logging.getLogger(__name__)


def run_risk_category_etl(
    initial_load: bool = False,
    effective_date=None,
    use_llm: bool = False,
    model: str | None = None,
    max_rows: int | None = None,
    list_name: str | None = None,
) -> dict:
    """Run the Risk Category ETL in the requested mode.

    ``list_name`` (optional, initial-load only): classify just ONE source list
    (e.g. "OFAC-SDN"); ``None`` means every list. It is ignored by the
    incremental mode, which is driven by the delta table.
    """
    engine = RiskEngine(use_llm=use_llm, model=model)

    if initial_load:
        return risk_service.run_initial_load(
            engine=engine,
            max_rows=max_rows,
            list_name=list_name,
        )

    return risk_service.run_incremental(
        effective_date=effective_date,
        engine=engine,
        max_rows=max_rows,
    )


def _parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Member Risk Category Calculation & Versioning ETL.",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--initial-load",
        action="store_true",
        help="first-ever population: process every current watchlist member "
             "(ignores the delta table)",
    )
    mode.add_argument(
        "--effective-date",
        type=date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="incremental run for a specific delta date "
             "(default: the latest available delta batch)",
    )
    ap.add_argument(
        "--use-llm",
        action="store_true",
        help="stack the LLM layer on the rule labels (requires Ollama)",
    )
    ap.add_argument("--model", default=None, help="Ollama model name for --use-llm")
    ap.add_argument(
        "--max-rows",
        type=int,
        default=None,
        metavar="N",
        help="process at most N members then stop (default: no limit / whole table). "
             "Handy for a quick, bounded LLM run.",
    )
    ap.add_argument(
        "--list",
        dest="list_name",
        default=None,
        metavar="LIST_NAME",
        help="initial-load only: classify just ONE source list (e.g. OFAC-SDN); "
             "default is every list.",
    )
    return ap.parse_args(argv)


def main(argv=None) -> None:
    configure_logging()
    args = _parse_args(argv)

    try:
        result = run_risk_category_etl(
            initial_load=args.initial_load,
            effective_date=args.effective_date,
            use_llm=args.use_llm,
            model=args.model,
            max_rows=args.max_rows,
            list_name=args.list_name,
        )
        pprint(result)
    except Exception:
        logger.exception("Risk Category ETL execution failed.")
        raise


if __name__ == "__main__":
    # Two ways to run this file:
    #   * pass command-line flags (see --help) -> CLI mode, honored below;
    #   * run it with NO flags (e.g. your editor's Run button) -> the inline
    #     "edit-and-run" block: set the options here, then run the file.
    import sys

    if len(sys.argv) > 1:
        main()
    else:
        configure_logging()
        try:
            result = run_risk_category_etl(
                initial_load=True,      # True = first-ever full population (ignores delta)
                effective_date=None,    # incremental only: a date, or None for latest batch
                use_llm=True,           # True = add the LLM layer (needs Ollama running)
                model="qwen2.5:14b",    # Ollama model used when use_llm=True
                list_name=None,         # None = ALL lists; or ONE list, e.g. "OFAC-SDN"
                max_rows=None,          # None = whole table; a number bounds a quick test run
            )
            pprint(result)
        except Exception:
            logger.exception("Risk Category ETL execution failed.")
            raise

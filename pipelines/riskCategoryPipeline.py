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
    python -m pipelines.riskCategoryPipeline --use-llm --model qwen2.5:14b-instruct
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
) -> dict:
    """Run the Risk Category ETL in the requested mode."""
    engine = RiskEngine(use_llm=use_llm, model=model)

    if initial_load:
        return risk_service.run_initial_load(engine=engine)

    return risk_service.run_incremental(
        effective_date=effective_date,
        engine=engine,
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
        )
        pprint(result)
    except Exception:
        logger.exception("Risk Category ETL execution failed.")
        raise


if __name__ == "__main__":
    main()

"""The small orchestration entry point for adverse-media acquisition."""

import sys
from pathlib import Path
from pprint import pprint
from time import perf_counter
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ingestion.crawler.configLoader import load_source_config  # noqa: E402
from services.adverseMediaPipeline.adverseMediaAcquisitionService import (  # noqa: E402
    acquire,
)
from services.adverseMediaPipeline.adverseMediaJsonlService import (  # noqa: E402
    write_final_jsonl,
)


def run_adverse_media_pipeline(
    source_name: str,
    mode: str | None = None,
    input_path: str | None = None,
) -> dict[str, Any]:
    started_at = perf_counter()
    source_config = load_source_config(source_name)
    acquisition_result = acquire(source_config, mode=mode, input_path=input_path)

    final_result = {
        "final_output_path": None,
        "final_record_count": 0,
    }
    if (
        acquisition_result["mode"] == "automatic"
        and acquisition_result["status"] == "finished"
    ):
        final_result = write_final_jsonl(
            source_config,
            acquisition_result["files"],
        )

    return {
        "source": source_config["source"]["id"],
        "source_name": source_config["source"]["name"],
        "method": source_config["acquisition"]["method"],
        **acquisition_result,
        **final_result,
        "elapsed_seconds": round(perf_counter() - started_at, 2),
    }


if __name__ == "__main__":
    pprint(run_adverse_media_pipeline(source_name="nbi_press_releases"))

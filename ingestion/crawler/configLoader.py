from pathlib import Path
from typing import Any, Dict

import yaml


def load_crawler_config(
    config_path: str,
) -> Dict[str, Any]:
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(
            f"Crawler config not found: {config_path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        config = yaml.safe_load(file)

    if not isinstance(config, dict):
        raise ValueError(
            f"Invalid crawler config: {config_path}"
        )

    return config
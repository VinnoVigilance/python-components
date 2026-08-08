"""Load and validate adverse-media source configurations."""

import re
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONFIG_DIRECTORY = PROJECT_ROOT / "config" / "adverseMediaSources"
VALID_SOURCE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def load_source_config(source_name: str) -> dict[str, Any]:
    if not source_name or not VALID_SOURCE_NAME.fullmatch(source_name):
        raise ValueError("source must contain only letters, numbers, _ or -")

    config_path = SOURCE_CONFIG_DIRECTORY / f"{source_name}.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(
            f"Adverse-media source configuration was not found: {config_path}"
        )

    with config_path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    if not isinstance(config, dict):
        raise ValueError(f"Invalid source configuration: {config_path}")

    validate_source_config(config, config_path)
    return config


def validate_source_config(config: dict[str, Any], config_path: Path | None = None) -> None:
    location = f" in {config_path}" if config_path else ""
    required_sections = {"source", "acquisition", "defaults", "storage", "output"}
    missing = required_sections - config.keys()
    if missing:
        raise ValueError(f"Missing config sections{location}: {sorted(missing)}")

    method = config["acquisition"].get("method")
    if method not in {"crawler", "downloader", "api"}:
        raise ValueError(f"Unsupported acquisition method{location}: {method}")

    if method == "crawler":
        engine = config["acquisition"].get("engine")
        if engine not in {"scrapy", "playwright"}:
            raise ValueError(f"Unsupported crawler engine{location}: {engine}")

        discovery = config.get("discovery")
        if not isinstance(discovery, dict):
            raise ValueError(f"Crawler configuration requires discovery{location}")
        if discovery.get("type") not in {"rss", "html_listing"}:
            raise ValueError(
                f"Unsupported discovery type{location}: {discovery.get('type')}"
            )
        if not discovery.get("url"):
            raise ValueError(f"Discovery URL is required{location}")

    output_path = config["output"].get("path")
    if not output_path:
        raise ValueError(f"output.path is required{location}")

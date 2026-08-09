"""Load the small YAML configuration used by adverse-media sources."""

import re
from pathlib import Path
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_CONFIG_DIRECTORY = PROJECT_ROOT / "config" / "adverseMediaSources"
VALID_SOURCE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def load_source_config(source_name: str) -> dict[str, Any]:
    """Load one source configuration by filename, for example ``nbi``."""
    if not source_name or not VALID_SOURCE_NAME.fullmatch(source_name):
        raise ValueError("source_name may contain only letters, numbers, _ and -")

    config_path = SOURCE_CONFIG_DIRECTORY / f"{source_name}.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Source configuration was not found: {config_path}")

    with config_path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    if not isinstance(config, dict):
        raise ValueError(f"Source configuration must be a YAML object: {config_path}")

    validate_source_config(config, config_path)
    return config


def validate_source_config(
    config: dict[str, Any], config_path: Path | None = None
) -> None:
    """Validate only the choices required to route acquisition."""
    location = f" in {config_path}" if config_path else ""

    for section_name in ("source", "acquisition", "mapping"):
        if not isinstance(config.get(section_name), dict):
            raise ValueError(f"{section_name} must be a YAML object{location}")

    source = config["source"]
    for field_name in ("id", "name", "section"):
        if not source.get(field_name):
            raise ValueError(f"source.{field_name} is required{location}")

    acquisition = config["acquisition"]
    method = acquisition.get("method")
    if method not in {"crawler", "api_collector", "downloader"}:
        raise ValueError(f"Unsupported acquisition.method{location}: {method}")

    mode = acquisition.get("mode")
    if mode not in {"automatic", "manual"}:
        raise ValueError(f"Unsupported acquisition.mode{location}: {mode}")

    if mode == "manual":
        if not acquisition.get("manual_path"):
            raise ValueError(f"acquisition.manual_path is required{location}")
        return

    if method == "crawler":
        _validate_automatic_crawler(config, location)


def _validate_automatic_crawler(config: dict[str, Any], location: str) -> None:
    acquisition = config["acquisition"]

    engine = acquisition.get("engine")
    if engine not in {"scrapy", "playwright"}:
        raise ValueError(f"Unsupported acquisition.engine{location}: {engine}")

    if not acquisition.get("start_url"):
        raise ValueError(f"acquisition.start_url is required{location}")

    discovery_type = acquisition.get("discovery_type")
    if discovery_type not in {"rss", "html_listing"}:
        raise ValueError(
            f"Unsupported acquisition.discovery_type{location}: {discovery_type}"
        )

    _validate_selector(
        acquisition.get("item_selector"),
        "acquisition.item_selector",
        location,
    )
    _validate_selector(
        config["mapping"].get("article_url"),
        "mapping.article_url",
        location,
    )


def _validate_selector(rule: Any, field_name: str, location: str) -> None:
    if not isinstance(rule, dict) or not (rule.get("css") or rule.get("xpath")):
        raise ValueError(f"{field_name} needs css or xpath{location}")

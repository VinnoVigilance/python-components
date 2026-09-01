from pathlib import Path
from typing import Any

import yaml


ROOT_DIR = Path(__file__).resolve().parent.parent

SEARCH_SCHEMA_DIR = (
    ROOT_DIR
    / "config"
    / "searchSchemas"
)


def load_search_schema(
    schema_name: str,
) -> dict[str, Any]:
    """Load an Elasticsearch schema configuration."""

    schema_path = (
        SEARCH_SCHEMA_DIR
        / f"{schema_name}.yaml"
    )

    if not schema_path.exists():
        raise FileNotFoundError(
            "Search schema not found: "
            f"{schema_path}"
        )

    with schema_path.open(
        "r",
        encoding="utf-8",
    ) as file:
        schema = yaml.safe_load(file)

    if not isinstance(schema, dict):
        raise ValueError(
            f"Invalid search schema: {schema_name}"
        )

    _validate_schema(
        schema_name=schema_name,
        schema=schema,
    )

    return schema


def _validate_schema(
    schema_name: str,
    schema: dict[str, Any],
) -> None:
    """Validate required schema sections."""

    required_sections = {
        "index",
        "mapping",
    }

    missing_sections = (
        required_sections
        - schema.keys()
    )

    if missing_sections:
        missing = ", ".join(
            sorted(missing_sections)
        )

        raise ValueError(
            f"Search schema {schema_name!r} "
            f"is missing required sections: "
            f"{missing}"
        )

    index_config = schema["index"]

    if "alias" not in index_config:
        raise ValueError(
            f"Search schema {schema_name!r} "
            "must define index.alias."
        )

    if "version" not in index_config:
        raise ValueError(
            f"Search schema {schema_name!r} "
            "must define index.version."
        )


def build_index_name(
    schema: dict[str, Any],
) -> str:
    """Build physical Elasticsearch index name."""

    index_config = schema["index"]

    alias = index_config["alias"]
    version = index_config["version"]

    return f"{alias}-{version}"
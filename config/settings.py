"""Application configuration.

Values are read from environment variables so that secrets are never
committed to source control. For local development, create a ``.env`` file in
the project root with the values below; ``.env`` is git-ignored and loaded
here via python-dotenv.

Non-sensitive settings (host, port, bucket names, etc.) fall back to sensible
local-development defaults. The two actual secrets -- DB_PASSWORD and
STORAGE_SECRET_KEY_INGESTION -- have NO default: they must be provided via the
environment (or .env), otherwise the app will fail fast with a clear error.
"""

import os

# Load variables from a local .env file if python-dotenv is installed. This is
# a development convenience only; production environments inject real
# environment variables directly, so a missing package must not break imports.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _require(name: str) -> str:
    """Return a required secret from the environment or raise a clear error."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            "Add it to your .env file, or export it in your environment."
        )
    return value


# --- Storage (SeaweedFS / S3-compatible) ---
STORAGE_S3_ENDPOINT = os.getenv("STORAGE_S3_ENDPOINT", "http://localhost:8333")
STORAGE_ACCESS_KEY_INGESTION = os.getenv(
    "STORAGE_ACCESS_KEY_INGESTION", "vv_ingestion_user"
)
STORAGE_SECRET_KEY_INGESTION = _require("STORAGE_SECRET_KEY_INGESTION")
STORAGE_BUCKET_INGESTION = os.getenv("STORAGE_BUCKET_INGESTION", "ingestion")
STORAGE_REGION = os.getenv("STORAGE_REGION", "us-east-1")

# --- Database (PostgreSQL) ---
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "vinno_vigilance")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = _require("DB_PASSWORD")

# --- Elasticsearch ---
ELASTICSEARCH_URL = os.getenv(
    "ELASTICSEARCH_URL",
    "https://localhost:9200",
)

ELASTICSEARCH_USERNAME = os.getenv(
    "ELASTICSEARCH_USERNAME",
    "elastic",
)

ELASTICSEARCH_PASSWORD = _require(
    "ELASTICSEARCH_PASSWORD"
)

ELASTICSEARCH_VERIFY_CERTS = (
    os.getenv(
        "ELASTICSEARCH_VERIFY_CERTS",
        "true",
    ).lower()
    == "true"
)

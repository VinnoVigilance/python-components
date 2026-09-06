import logging

import urllib3
from elasticsearch import Elasticsearch
from urllib3.exceptions import InsecureRequestWarning

from config.settings import (
    ELASTICSEARCH_PASSWORD,
    ELASTICSEARCH_URL,
    ELASTICSEARCH_USERNAME,
    ELASTICSEARCH_VERIFY_CERTS,
)


logger = logging.getLogger(__name__)


def create_elasticsearch_client() -> Elasticsearch:
    """
    Create and return an Elasticsearch client.
    """

    if not ELASTICSEARCH_VERIFY_CERTS:
        urllib3.disable_warnings(
            InsecureRequestWarning
        )

    return Elasticsearch(
        ELASTICSEARCH_URL,
        basic_auth=(
            ELASTICSEARCH_USERNAME,
            ELASTICSEARCH_PASSWORD,
        ),
        verify_certs=ELASTICSEARCH_VERIFY_CERTS,
    )


def check_elasticsearch_connection(
    client: Elasticsearch,
) -> bool:
    """
    Check whether Elasticsearch is reachable.
    """

    try:
        return bool(client.ping())

    except Exception:
        logger.exception(
            "Failed to connect to Elasticsearch."
        )
        return False


def close_elasticsearch_client(
    client: Elasticsearch,
) -> None:
    """
    Close Elasticsearch client resources.
    """

    client.close()
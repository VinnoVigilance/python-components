import logging
import sys


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | "
            "%(name)s | %(message)s"
        ),
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    logging.getLogger(
        "elastic_transport.transport"
    ).setLevel(logging.WARNING)

    logging.getLogger(
        "elasticsearch"
    ).setLevel(logging.WARNING)

    logging.getLogger(
        "urllib3.connectionpool"
    ).setLevel(logging.WARNING)
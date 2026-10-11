import logging

import pytest

from ingestion.bypassCollector.engines.compatibleDriver import (
    _DisconnectedDriverProbeFilter,
)


pytestmark = pytest.mark.unit


def _record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        "urllib3.connectionpool", logging.WARNING, __file__, 0, message, None, None,
    )


def test_drops_local_chromedriver_probe_retry():
    record = _record(
        "Retrying (Retry(total=0)) after connection broken by "
        "'NewConnectionError(\"HTTPConnection(host='localhost', port=43735): "
        "Connection refused\")': /session/ea3f6c79/window/handles"
    )
    assert _DisconnectedDriverProbeFilter().filter(record) is False


def test_keeps_remote_host_retry():
    record = _record(
        "Retrying (Retry(total=1)) after connection broken by "
        "'NewConnectionError(\"HTTPSConnection(host='www.cftc.gov', port=443)\")': /redlist"
    )
    assert _DisconnectedDriverProbeFilter().filter(record) is True

"""Unit tests for attach_child_job_logs in config/loggingConfig.py."""

import io
import logging
import sys

import pytest

from config.loggingConfig import attach_child_job_logs

pytestmark = pytest.mark.unit


def test_child_full_log_drops_debug_even_when_root_is_opened(tmp_path):
    stdout, stderr = sys.stdout, sys.stderr
    root = logging.getLogger()
    before, level = root.handlers[:], root.level
    console = logging.StreamHandler(io.StringIO())
    root.handlers = [console]
    full = tmp_path / "full.log"

    try:
        attach_child_job_logs(str(full), str(tmp_path / "errors.log"), "SRC")
        root.setLevel(logging.NOTSET)
        logging.getLogger("botocore.hooks").debug("debug-noise")
        logging.getLogger("pipeline").info("info-kept")
    finally:
        sys.stdout, sys.stderr = stdout, stderr
        for handler in root.handlers:
            handler.close()
        root.handlers = before
        root.setLevel(level)

    text = full.read_text(encoding="utf-8")
    assert "info-kept" in text
    assert "debug-noise" not in text

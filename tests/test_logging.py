"""Tests for logging setup and bootstrap failure handling."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import hms_cadcam.application as application_module
from hms_cadcam.core.logging_config import configure_logging


def test_configure_logging_does_not_duplicate_handlers(tmp_path: Path) -> None:
    """Calling logging setup twice must keep one console/file handler pair."""
    root_logger = logging.getLogger()
    existing_handlers = set(root_logger.handlers)
    first_log_dir = tmp_path / "first"
    second_log_dir = tmp_path / "second"

    first_path = configure_logging(first_log_dir)
    repeated_path = configure_logging(first_log_dir)
    second_path = configure_logging(second_log_dir)
    active_handlers = [
        handler for handler in root_logger.handlers if handler not in existing_handlers
    ]

    try:
        assert first_path == repeated_path == first_log_dir / "hms_cadcam.log"
        assert second_path == second_log_dir / "hms_cadcam.log"
        assert len(active_handlers) == 2
        assert any(
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename) == second_path
            for handler in active_handlers
        )
    finally:
        for handler in active_handlers:
            root_logger.removeHandler(handler)
            handler.close()


def test_run_handles_logging_bootstrap_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A log I/O failure must produce a controlled non-zero startup result."""
    def fail_logging(_log_dir: Path) -> Path:
        raise OSError("log directory denied")

    monkeypatch.setattr(application_module, "configure_logging", fail_logging)
    with caplog.at_level(logging.ERROR):
        exit_code = application_module.run([])

    assert exit_code == 1
    assert "Không thể khởi tạo thư mục hoặc file nhật ký" in caplog.text

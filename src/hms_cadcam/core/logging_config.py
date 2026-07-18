"""Logging configuration for console and rotating UTF-8 log files."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_HANDLER_MARKER = "hms_cadcam_handler"


def configure_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    """Configure root logging once and return the active log file path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "hms_cadcam.log"
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if any(getattr(handler, _HANDLER_MARKER, False) for handler in root_logger.handlers):
        return log_path

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    setattr(console_handler, _HANDLER_MARKER, True)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_MARKER, True)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    return log_path

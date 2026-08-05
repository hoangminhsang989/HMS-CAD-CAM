"""Typed, side-effect-free foundations for HMS AI Sync Engine V1.1."""

from __future__ import annotations

from .config import AiSyncConfig, ConfigError, load_config, parse_config_bytes
from .git_reader import (
    CommitVerificationResult,
    GitReaderError,
    capture_git_snapshot,
    resolve_repository_root,
    verify_commit,
)
from .models import CapabilitySet, VersionInfo
from .validation import ValidationCollector, has_blocking_issues

__version__ = "1.1.0"

__all__ = (
    "AiSyncConfig",
    "CapabilitySet",
    "CommitVerificationResult",
    "ConfigError",
    "GitReaderError",
    "ValidationCollector",
    "VersionInfo",
    "capture_git_snapshot",
    "has_blocking_issues",
    "load_config",
    "parse_config_bytes",
    "resolve_repository_root",
    "verify_commit",
)

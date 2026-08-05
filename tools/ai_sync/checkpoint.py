"""Immutable checkpoint naming and exclusive creation primitives."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import os
from pathlib import Path
import re

from .models import validate_utc_datetime


CHECKPOINT_TIMESTAMP_FORMAT = "%Y-%m-%d_%H%M%S"
_CHECKPOINT_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}\.md$")


class CheckpointError(OSError):
    """A fail-closed checkpoint creation failure."""


def checkpoint_filename(created_at: datetime, *, timestamp_format: str = CHECKPOINT_TIMESTAMP_FORMAT) -> str:
    """Return the one permitted UTC checkpoint filename."""

    validate_utc_datetime(created_at, "created_at")
    if timestamp_format not in {CHECKPOINT_TIMESTAMP_FORMAT, f"{CHECKPOINT_TIMESTAMP_FORMAT}.md"}:
        raise CheckpointError("unsupported checkpoint timestamp format")
    name = created_at.strftime(CHECKPOINT_TIMESTAMP_FORMAT) + ".md"
    validate_checkpoint_filename(name)
    return name


def validate_checkpoint_filename(name: str) -> str:
    """Validate and return an immutable checkpoint basename."""

    if not isinstance(name, str) or _CHECKPOINT_NAME_RE.fullmatch(name) is None:
        raise CheckpointError("checkpoint filename is invalid")
    return name


def create_checkpoint_exclusive(
    path: Path,
    content: bytes,
    *,
    fault_hook: Callable[[str], None] | None = None,
) -> None:
    """Create a checkpoint with ``O_EXCL`` and durable file contents."""

    if path.name != validate_checkpoint_filename(path.name):
        raise CheckpointError("checkpoint path is invalid")
    if not isinstance(content, bytes) or not content:
        raise CheckpointError("checkpoint content must be non-empty bytes")
    if content.startswith(b"\xef\xbb\xbf") or b"\r" in content or not content.endswith(b"\n"):
        raise CheckpointError("checkpoint content violates the UTF-8/LF contract")
    content.decode("utf-8", errors="strict")
    if fault_hook is not None:
        fault_hook("checkpoint_exclusive_create")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            if fault_hook is not None:
                fault_hook("checkpoint_flush")
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise CheckpointError("checkpoint already exists") from error
    except (OSError, UnicodeError) as error:
        raise CheckpointError("checkpoint could not be created") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)

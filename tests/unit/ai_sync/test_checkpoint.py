"""WP5 immutable checkpoint primitive tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.ai_sync.checkpoint import (
    CheckpointError,
    checkpoint_filename,
    create_checkpoint_exclusive,
    validate_checkpoint_filename,
)


NOW = datetime(2026, 8, 4, 12, 34, 56, tzinfo=UTC)


def test_checkpoint_filename_is_injected_utc_and_exact() -> None:
    assert checkpoint_filename(NOW) == "2026-08-04_123456.md"
    assert checkpoint_filename(NOW, timestamp_format="%Y-%m-%d_%H%M%S.md") == "2026-08-04_123456.md"


@pytest.mark.parametrize("name", ("../x.md", "2026-08-04.md", "2026-08-04_123456.txt", "C:bad.md"))
def test_checkpoint_filename_rejects_unsafe_or_noncanonical_names(name: str) -> None:
    with pytest.raises(CheckpointError):
        validate_checkpoint_filename(name)


def test_checkpoint_create_is_exclusive_and_never_overwrites(tmp_path: Path) -> None:
    path = tmp_path / checkpoint_filename(NOW)
    create_checkpoint_exclusive(path, b"# checkpoint\n")
    before = path.read_bytes()
    with pytest.raises(CheckpointError):
        create_checkpoint_exclusive(path, b"# replacement\n")
    assert path.read_bytes() == before


@pytest.mark.parametrize("content", (b"", b"\xef\xbb\xbf# x\n", b"# x\r\n", b"# x"))
def test_checkpoint_rejects_noncanonical_content(tmp_path: Path, content: bytes) -> None:
    with pytest.raises(CheckpointError):
        create_checkpoint_exclusive(tmp_path / checkpoint_filename(NOW), content)

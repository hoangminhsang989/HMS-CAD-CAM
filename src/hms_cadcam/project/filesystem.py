"""Transactional Windows filesystem operations for HMS projects."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from hms_cadcam.project.constants import PROJECT_SUFFIX, SOURCE_DIRECTORY
from hms_cadcam.project.exceptions import (
    ProjectAlreadyExistsError,
    ProjectPermissionError,
    ProjectTransactionError,
    SourceFileNotFoundError,
)


def normalized_project_stem(name: str) -> str:
    """Remove repeated .HMS suffixes and surrounding whitespace."""
    normalized = name.strip()
    while normalized.casefold().endswith(PROJECT_SUFFIX.casefold()):
        normalized = normalized[: -len(PROJECT_SUFFIX)].rstrip()
    return normalized


def project_target_path(parent: Path, name: str) -> Path:
    """Build a project path containing exactly one uppercase .HMS suffix."""
    return parent / f"{normalized_project_stem(name)}{PROJECT_SUFFIX}"


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Calculate SHA-256 without loading the complete file into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def unique_source_path(source_dir: Path, file_name: str) -> Path:
    """Return a non-conflicting destination below source/."""
    original = Path(file_name)
    candidate = source_dir / original.name
    index = 2
    while candidate.exists():
        candidate = source_dir / f"{original.stem} ({index}){original.suffix}"
        index += 1
    return candidate


def copy_source_verified(source: Path, destination: Path) -> tuple[int, str]:
    """Copy source read-only and verify source/destination hashes match."""
    if not source.is_file():
        raise SourceFileNotFoundError(f"Source file does not exist: {source}")
    try:
        source_hash = sha256_file(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.importing")
        try:
            shutil.copy2(source, temporary)
            copied_hash = sha256_file(temporary)
            if source_hash != copied_hash:
                raise ProjectTransactionError("Source copy hash mismatch")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    except PermissionError as error:
        raise ProjectPermissionError(str(error)) from error
    return destination.stat().st_size, source_hash


@contextmanager
def staging_directory(parent: Path, project_stem: str) -> Iterator[Path]:
    """Create a sibling staging directory and remove it unless published."""
    try:
        parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(
                prefix=f".{project_stem}.",
                suffix=".creating",
                dir=parent,
            )
        )
    except PermissionError as error:
        raise ProjectPermissionError(str(error)) from error
    try:
        yield staging
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def publish_directory(staging: Path, target: Path, overwrite: bool = False) -> None:
    """Publish a complete staging directory, restoring old target on failure."""
    if target.exists() and not overwrite:
        raise ProjectAlreadyExistsError(f"Project already exists: {target}")
    backup = target.with_name(f".{target.name}.{uuid4().hex}.replaced")
    moved_existing = False
    try:
        if target.exists():
            target.rename(backup)
            moved_existing = True
        staging.rename(target)
        if moved_existing:
            shutil.rmtree(backup, ignore_errors=True)
    except PermissionError as error:
        if moved_existing and backup.exists() and not target.exists():
            backup.rename(target)
        raise ProjectPermissionError(str(error)) from error
    except OSError as error:
        if moved_existing and backup.exists() and not target.exists():
            backup.rename(target)
        raise ProjectTransactionError(str(error)) from error


def remove_imported_source(project_root: Path, relative_path: str) -> None:
    """Remove only a just-created source copy after a failed manifest update."""
    candidate = project_root / Path(relative_path)
    source_root = (project_root / SOURCE_DIRECTORY).resolve()
    resolved = candidate.resolve()
    if resolved.parent == source_root:
        resolved.unlink(missing_ok=True)

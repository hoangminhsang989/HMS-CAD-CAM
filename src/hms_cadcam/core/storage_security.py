"""Fail-closed Windows path validation for non-project application storage."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path, PureWindowsPath
import stat


WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
)
WINDOWS_INVALID_CHARACTERS = frozenset('<>:"/\\|?*')
DEFAULT_PATH_LENGTH_LIMIT = 240


class PathSecurityCode(StrEnum):
    SAFE = "SAFE"
    NOT_ABSOLUTE = "NOT_ABSOLUTE"
    TRAVERSAL = "TRAVERSAL"
    ROOT_ESCAPE = "ROOT_ESCAPE"
    UNC_BLOCKED = "UNC_BLOCKED"
    RESERVED_NAME = "RESERVED_NAME"
    TRAILING_DOT_OR_SPACE = "TRAILING_DOT_OR_SPACE"
    INVALID_CHARACTER = "INVALID_CHARACTER"
    PATH_TOO_LONG = "PATH_TOO_LONG"
    REPARSE_POINT = "REPARSE_POINT"
    FILE_DIRECTORY_COLLISION = "FILE_DIRECTORY_COLLISION"
    CASE_COLLISION = "CASE_COLLISION"
    PARENT_NOT_WRITABLE = "PARENT_NOT_WRITABLE"
    CROSS_VOLUME_ATOMIC_RENAME = "CROSS_VOLUME_ATOMIC_RENAME"
    INSPECTION_FAILED = "INSPECTION_FAILED"


@dataclass(frozen=True, slots=True)
class PathSecurityResult:
    safe: bool
    code: PathSecurityCode
    root: Path
    candidate: Path
    normalized_candidate: Path
    relative_path: str
    writable: bool
    atomic_rename_capable: bool


def validate_storage_write_path(
    root: Path,
    candidate: Path,
    *,
    expect_directory: bool | None = None,
    allow_root: bool = False,
    allow_unc: bool = False,
    path_length_limit: int = DEFAULT_PATH_LENGTH_LIMIT,
) -> PathSecurityResult:
    """Validate one write target without following or creating outside paths."""
    owner = Path(root)
    target = Path(candidate)
    if not owner.is_absolute() or not target.is_absolute():
        return _failure(PathSecurityCode.NOT_ABSOLUTE, owner, target)
    root_text = os.path.normpath(str(owner))
    target_text = os.path.normpath(str(target))
    if not allow_unc and (
        PureWindowsPath(root_text).anchor.startswith("\\\\")
        or PureWindowsPath(target_text).anchor.startswith("\\\\")
    ):
        return _failure(PathSecurityCode.UNC_BLOCKED, owner, target)
    if any(part == ".." for part in target.parts):
        return _failure(PathSecurityCode.TRAVERSAL, owner, target)
    root_key = os.path.normcase(root_text)
    target_key = os.path.normcase(target_text)
    if target_key == root_key:
        if not allow_root:
            return _failure(PathSecurityCode.ROOT_ESCAPE, owner, target)
        relative_parts: tuple[str, ...] = ()
    else:
        prefix = root_key.rstrip("\\/") + os.sep
        if not target_key.startswith(prefix):
            return _failure(PathSecurityCode.ROOT_ESCAPE, owner, target)
        relative_parts = Path(target_text).relative_to(Path(root_text)).parts
    if len(target_text) > path_length_limit:
        return _failure(PathSecurityCode.PATH_TOO_LONG, owner, target)
    for part in relative_parts:
        name = part.rstrip(". ")
        if name != part:
            return _failure(PathSecurityCode.TRAILING_DOT_OR_SPACE, owner, target)
        stem = part.split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            return _failure(PathSecurityCode.RESERVED_NAME, owner, target)
        if any(character in WINDOWS_INVALID_CHARACTERS for character in part):
            return _failure(PathSecurityCode.INVALID_CHARACTER, owner, target)
        if any(ord(character) < 32 for character in part):
            return _failure(PathSecurityCode.INVALID_CHARACTER, owner, target)
    normalized = Path(target_text)
    try:
        current = Path(root_text)
        if _is_reparse(current):
            return _failure(PathSecurityCode.REPARSE_POINT, owner, target)
        for part in relative_parts:
            parent = current
            current = current / part
            if parent.is_dir():
                collisions = tuple(
                    child.name
                    for child in parent.iterdir()
                    if child.name.casefold() == part.casefold()
                    and child.name != part
                )
                if collisions:
                    return _failure(PathSecurityCode.CASE_COLLISION, owner, target)
            if current.exists() and _is_reparse(current):
                return _failure(PathSecurityCode.REPARSE_POINT, owner, target)
        if normalized.exists() and expect_directory is not None:
            matches = normalized.is_dir() if expect_directory else normalized.is_file()
            if not matches:
                return _failure(
                    PathSecurityCode.FILE_DIRECTORY_COLLISION,
                    owner,
                    target,
                )
        parent = _nearest_existing_parent(normalized)
        writable = parent.is_dir() and os.access(parent, os.W_OK)
        same_volume = (
            PureWindowsPath(parent).drive.casefold()
            == PureWindowsPath(normalized).drive.casefold()
        )
        if not same_volume:
            return _failure(
                PathSecurityCode.CROSS_VOLUME_ATOMIC_RENAME,
                owner,
                target,
            )
        if not writable:
            return PathSecurityResult(
                safe=False,
                code=PathSecurityCode.PARENT_NOT_WRITABLE,
                root=owner,
                candidate=target,
                normalized_candidate=normalized,
                relative_path=os.path.relpath(target_text, root_text),
                writable=False,
                atomic_rename_capable=same_volume,
            )
    except OSError:
        return _failure(PathSecurityCode.INSPECTION_FAILED, owner, target)
    return PathSecurityResult(
        safe=True,
        code=PathSecurityCode.SAFE,
        root=owner,
        candidate=target,
        normalized_candidate=normalized,
        relative_path=os.path.relpath(target_text, root_text),
        writable=True,
        atomic_rename_capable=True,
    )


def _nearest_existing_parent(path: Path) -> Path:
    # Atomic publication replaces an entry through its directory; an existing
    # read-only file is still replaceable when its parent directory permits it.
    current = path.parent
    while current != current.parent and not current.exists():
        current = current.parent
    return current


def _is_reparse(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_symlink():
        return True
    try:
        return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    except AttributeError:
        return False


def _failure(
    code: PathSecurityCode,
    root: Path,
    candidate: Path,
) -> PathSecurityResult:
    return PathSecurityResult(
        safe=False,
        code=code,
        root=root,
        candidate=candidate,
        normalized_candidate=Path(os.path.normpath(str(candidate))),
        relative_path="",
        writable=False,
        atomic_rename_capable=False,
    )


__all__ = [
    "DEFAULT_PATH_LENGTH_LIMIT",
    "PathSecurityCode",
    "PathSecurityResult",
    "WINDOWS_RESERVED_NAMES",
    "validate_storage_write_path",
]

"""Windows-safe naming and parent-path policy for Stage 8A.4.2."""

from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from hms_cadcam.project.exceptions import (
    InvalidHmsFilenameError,
    UnsafeWorkspacePathError,
)

_INVALID_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_SAFE_CAM_SEGMENT = re.compile(r"^[A-Za-z0-9-]+$")
_MULTIPLE_HYPHENS = re.compile(r"-+")
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
MAX_CAM_WORKSPACE_PATH = 240


@dataclass(frozen=True, slots=True)
class ParentPathValidation:
    """A UI-ready parent validation result with a Vietnamese reason."""

    valid: bool
    reason: str


def _device_stem(name: str) -> str:
    return name.split(".", 1)[0].upper()


def validate_hms_filename(name: str) -> str:
    """Preserve a Unicode HMS filename while applying Windows restrictions."""
    if not isinstance(name, str) or not name:
        raise InvalidHmsFilenameError("Tên file HMS không được để trống.")
    if name != name.rstrip() or name.endswith("."):
        raise InvalidHmsFilenameError(
            "Tên file HMS không được kết thúc bằng dấu cách hoặc dấu chấm."
        )
    if _INVALID_WINDOWS_CHARS.search(name):
        raise InvalidHmsFilenameError(
            'Tên file HMS chứa ký tự Windows không hợp lệ: < > : " / \\ | ? *.'
        )
    path_name = Path(name).name
    stem = (
        path_name[: -len(".HMS")]
        if path_name.casefold().endswith(".hms")
        else Path(path_name).stem
    )
    if not stem or _device_stem(stem) in _RESERVED_WINDOWS_NAMES:
        raise InvalidHmsFilenameError("Tên file HMS là tên dành riêng của Windows.")
    return name


def ensure_hms_suffix(name: str) -> str:
    """Add one uppercase HMS suffix without normalizing Unicode or spaces."""
    validated = validate_hms_filename(name)
    if validated.casefold().endswith(".hms"):
        return validated[:-4] + ".HMS"
    return validated + ".HMS"


def normalize_cam_project_name(display_name: str) -> str:
    """Return a deterministic ASCII/hyphen physical project directory name."""
    if not isinstance(display_name, str) or not display_name.strip():
        raise UnsafeWorkspacePathError("Tên dự án không được để trống.")
    decomposed = unicodedata.normalize(
        "NFKD",
        display_name.strip().replace("Đ", "D").replace("đ", "d"),
    )
    ascii_text = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character) and ord(character) < 128
    )
    mapped = "".join(
        character if character.isalnum() else "-"
        for character in ascii_text
    )
    normalized = _MULTIPLE_HYPHENS.sub("-", mapped).strip("-")
    if (
        not normalized
        or not _SAFE_CAM_SEGMENT.fullmatch(normalized)
        or _device_stem(normalized) in _RESERVED_WINDOWS_NAMES
    ):
        raise UnsafeWorkspacePathError(
            "Tên dự án không tạo được tên thư mục vật lý an toàn."
        )
    return normalized


def normalize_internal_source_filename(filename: str) -> str:
    """Sanitize only the project-internal source copy, preserving its suffix."""
    source = Path(filename)
    stem = normalize_cam_project_name(source.stem)
    raw_suffix = unicodedata.normalize("NFKD", source.suffix.lstrip("."))
    suffix = "".join(
        character.lower()
        for character in raw_suffix
        if ord(character) < 128 and character.isalnum()
    )
    return stem if not suffix else f"{stem}.{suffix}"


def validate_parent_path(
    parent: Path,
    physical_name: str,
    *,
    check_access: bool = True,
) -> ParentPathValidation:
    """Validate every Windows path segment and the ability to create a child."""
    if not isinstance(parent, Path):
        return ParentPathValidation(False, "Đường dẫn thư mục cha không hợp lệ.")
    text = str(parent)
    if text.startswith(("\\\\", "//")):
        return ParentPathValidation(
            False,
            "Đường dẫn mạng/UNC chưa được cho phép cho workspace CAM.",
        )
    if not parent.is_absolute():
        return ParentPathValidation(False, "Thư mục cha phải là đường dẫn tuyệt đối.")
    windows_path = PureWindowsPath(text)
    for part in windows_path.parts:
        if part in {windows_path.anchor, "\\", "/"}:
            continue
        if part.endswith(":"):
            continue
        if not _SAFE_CAM_SEGMENT.fullmatch(part):
            return ParentPathValidation(
                False,
                "Thư mục cha có dấu cách hoặc ký tự không được phép.",
            )
        if _device_stem(part) in _RESERVED_WINDOWS_NAMES:
            return ParentPathValidation(
                False,
                "Thư mục cha chứa tên dành riêng của Windows.",
            )
    try:
        normalized_name = normalize_cam_project_name(physical_name)
    except UnsafeWorkspacePathError as error:
        return ParentPathValidation(False, str(error))
    target = parent / normalized_name
    if len(str(target)) > MAX_CAM_WORKSPACE_PATH:
        return ParentPathValidation(
            False,
            f"Đường dẫn dự án vượt giới hạn {MAX_CAM_WORKSPACE_PATH} ký tự.",
        )
    if not parent.is_dir():
        return ParentPathValidation(False, "Thư mục cha không tồn tại.")
    if target.exists():
        return ParentPathValidation(
            False,
            "Thư mục dự án đã tồn tại; HMS sẽ không ghi đè.",
        )
    if check_access:
        try:
            descriptor, probe_name = tempfile.mkstemp(
                prefix=".hms-write-probe-",
                dir=parent,
            )
            os.close(descriptor)
            Path(probe_name).unlink()
        except (OSError, PermissionError):
            return ParentPathValidation(
                False,
                "Thư mục cha chỉ đọc hoặc không có quyền tạo thư mục.",
            )
    return ParentPathValidation(True, "Đường dẫn hợp lệ.")


def validated_cam_target(parent: Path, display_name: str) -> tuple[Path, str]:
    """Return the validated CAM workspace target and physical directory name."""
    physical_name = normalize_cam_project_name(display_name)
    assessment = validate_parent_path(parent, physical_name)
    if not assessment.valid:
        raise UnsafeWorkspacePathError(assessment.reason)
    return parent / physical_name, physical_name


def validate_existing_cam_root_path(project_root: Path) -> Path:
    """Validate an existing physical CAM root without applying create-only rules."""
    if not isinstance(project_root, Path):
        raise UnsafeWorkspacePathError("Project root phải là pathlib.Path.")
    text = str(project_root)
    if text.startswith(("\\\\", "//")):
        raise UnsafeWorkspacePathError(
            "Đường dẫn mạng/UNC chưa được phép cho dự án CAM."
        )
    if not project_root.is_absolute() or not project_root.is_dir():
        raise UnsafeWorkspacePathError(
            "Project root phải là thư mục tuyệt đối đang tồn tại."
        )
    windows_path = PureWindowsPath(text)
    for part in windows_path.parts:
        if part in {windows_path.anchor, "\\", "/"} or part.endswith(":"):
            continue
        if not _SAFE_CAM_SEGMENT.fullmatch(part):
            raise UnsafeWorkspacePathError(
                "Thư mục gốc dự án có dấu cách hoặc ký tự không được phép."
            )
        if _device_stem(part) in _RESERVED_WINDOWS_NAMES:
            raise UnsafeWorkspacePathError(
                "Project root chứa tên dành riêng của Windows."
            )
    if normalize_cam_project_name(project_root.name) != project_root.name:
        raise UnsafeWorkspacePathError(
            "Tên thư mục vật lý của dự án CAM không an toàn."
        )
    if len(str(project_root)) > MAX_CAM_WORKSPACE_PATH:
        raise UnsafeWorkspacePathError(
            f"Đường dẫn dự án vượt giới hạn {MAX_CAM_WORKSPACE_PATH} ký tự."
        )
    return project_root

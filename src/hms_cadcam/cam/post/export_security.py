"""Windows filename and filesystem-root guards for NC export."""

from __future__ import annotations

import re
from pathlib import Path

from hms_cadcam.cam.post.export_model import NCExportDiagnosticCode


MAX_EXPORT_FILENAME_LENGTH = 120
_FORBIDDEN = frozenset('<>:"/\\|?*')
_DEVICE_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", re.IGNORECASE)


class NCExportSecurityError(ValueError):
    """A fail-closed validation error carrying its stable diagnostic code."""

    def __init__(self, code: NCExportDiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def sanitize_export_filename(
    filename: str,
    allowed_extensions: tuple[str, ...],
    *,
    maximum_length: int = MAX_EXPORT_FILENAME_LENGTH,
) -> str:
    """Validate an explicit Windows filename and apply only the profile extension."""
    if not isinstance(filename, str) or not filename:
        raise NCExportSecurityError(
            NCExportDiagnosticCode.FILENAME_INVALID, "Export filename is empty"
        )
    if not isinstance(allowed_extensions, tuple) or len(allowed_extensions) != 1:
        raise NCExportSecurityError(
            NCExportDiagnosticCode.EXTENSION_INVALID,
            "Production profile must define exactly one export extension",
        )
    extension = allowed_extensions[0].casefold()
    if (
        not extension.startswith(".")
        or extension.count(".") != 1
        or any(char in _FORBIDDEN for char in extension)
    ):
        raise NCExportSecurityError(
            NCExportDiagnosticCode.EXTENSION_INVALID,
            "Production profile extension is invalid",
        )
    if filename != filename.strip() or filename.endswith((".", " ")):
        raise NCExportSecurityError(
            NCExportDiagnosticCode.FILENAME_INVALID,
            "Export filename cannot have leading/trailing whitespace or a trailing dot",
        )
    if filename in {".", ".."} or ".." in Path(filename).parts:
        raise NCExportSecurityError(
            NCExportDiagnosticCode.FILENAME_INVALID, "Export filename contains traversal"
        )
    if any(char in _FORBIDDEN or ord(char) < 32 or ord(char) == 127 for char in filename):
        raise NCExportSecurityError(
            NCExportDiagnosticCode.FILENAME_INVALID,
            "Export filename contains a forbidden Windows character",
        )
    suffixes = Path(filename).suffixes
    if len(suffixes) > 1:
        raise NCExportSecurityError(
            NCExportDiagnosticCode.EXTENSION_INVALID,
            "Export filename cannot contain a double extension",
        )
    if suffixes:
        if suffixes[0].casefold() != extension:
            raise NCExportSecurityError(
                NCExportDiagnosticCode.EXTENSION_INVALID,
                "Export filename extension does not match the production profile",
            )
        normalized = filename[: -len(suffixes[0])] + extension
    else:
        normalized = filename + extension
    stem = normalized[: -len(extension)]
    if not stem or stem.endswith((".", " ")) or _DEVICE_NAME.fullmatch(stem) is not None:
        raise NCExportSecurityError(
            NCExportDiagnosticCode.FILENAME_INVALID,
            "Export filename stem is empty or a reserved Windows device name",
        )
    if len(normalized) > maximum_length:
        raise NCExportSecurityError(
            NCExportDiagnosticCode.FILENAME_INVALID, "Export filename is too long"
        )
    return normalized


def is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def require_real_directory(path: Path, *, create: bool = False) -> Path:
    """Resolve a real directory without accepting a symlink/junction boundary."""
    if not isinstance(path, Path):
        raise NCExportSecurityError(
            NCExportDiagnosticCode.TARGET_UNSUPPORTED, "Export target must be pathlib.Path"
        )
    if create and not path.exists():
        try:
            path.mkdir()
        except PermissionError as error:
            raise NCExportSecurityError(
                NCExportDiagnosticCode.PERMISSION_DENIED, str(error)
            ) from error
        except OSError as error:
            raise NCExportSecurityError(
                NCExportDiagnosticCode.TARGET_MISSING, str(error)
            ) from error
    if not path.exists():
        raise NCExportSecurityError(
            NCExportDiagnosticCode.TARGET_MISSING, f"Export target does not exist: {path}"
        )
    if is_link_or_junction(path) or not path.is_dir():
        raise NCExportSecurityError(
            NCExportDiagnosticCode.TARGET_UNSUPPORTED,
            "Export target must be a real filesystem directory",
        )
    unresolved = path.absolute()
    for component in (unresolved, *unresolved.parents):
        if component.exists() and is_link_or_junction(component):
            raise NCExportSecurityError(
                NCExportDiagnosticCode.PATH_ESCAPE,
                "Export target crosses a symlink or junction",
            )
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise NCExportSecurityError(NCExportDiagnosticCode.TARGET_MISSING, str(error)) from error
    current = resolved
    while current.parent != current:
        if is_link_or_junction(current):
            raise NCExportSecurityError(
                NCExportDiagnosticCode.PATH_ESCAPE,
                "Export target crosses a symlink or junction",
            )
        current = current.parent
    return resolved


def contained_child(root: Path, filename: str) -> Path:
    """Return one direct child and reject canonicalization/root escape."""
    resolved_root = require_real_directory(root)
    candidate = resolved_root / filename
    if candidate.parent.resolve(strict=True) != resolved_root:
        raise NCExportSecurityError(
            NCExportDiagnosticCode.PATH_ESCAPE, "Export destination escaped its target root"
        )
    if candidate.exists() and is_link_or_junction(candidate):
        raise NCExportSecurityError(
            NCExportDiagnosticCode.PATH_ESCAPE,
            "Export destination cannot be a symlink or junction",
        )
    return candidate


def reject_protected_target(path: Path, project_root: Path | None = None) -> None:
    """Reject .git and source-code destinations called out by project policy."""
    resolved = path.resolve(strict=True)
    if any(part.casefold() == ".git" for part in resolved.parts):
        raise NCExportSecurityError(
            NCExportDiagnosticCode.TARGET_UNSUPPORTED,
            "NC export cannot target a .git directory",
        )
    if project_root is None:
        return
    try:
        relative = resolved.relative_to(project_root.resolve(strict=True))
    except ValueError:
        return
    if relative.parts and relative.parts[0].casefold() in {"src", "tests", "source"}:
        raise NCExportSecurityError(
            NCExportDiagnosticCode.TARGET_UNSUPPORTED,
            "NC export cannot target source or test directories inside the project",
        )

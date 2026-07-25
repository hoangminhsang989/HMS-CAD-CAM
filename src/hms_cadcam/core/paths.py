"""Typed Windows storage paths for HMS CAD/CAM.

Production resolution is deliberately independent from the current working
directory and from user-controlled environment variables.  Tests and review
harnesses must opt in to an explicit sandbox mode and provide every root.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import stat
from typing import Mapping, Protocol


APPLICATION_FAMILY = "HMS-CADCAM"
STORAGE_LAYOUT_VERSION = 1
DEFAULT_INSTALL_ROOT = Path("C:/HMS-CADCAM")


class StorageScope(StrEnum):
    INSTALL = "INSTALL"
    MACHINE_SHARED = "MACHINE_SHARED"
    USER_ROAMING = "USER_ROAMING"
    USER_LOCAL = "USER_LOCAL"
    DOCUMENT = "DOCUMENT"
    CAM_PROJECT = "CAM_PROJECT"
    TEST_SANDBOX = "TEST_SANDBOX"
    REVIEW_PRIVATE = "REVIEW_PRIVATE"


class AppPathKind(StrEnum):
    INSTALL_ROOT = "INSTALL_ROOT"
    EXECUTABLE = "EXECUTABLE"
    RUNTIME = "RUNTIME"
    RESOURCES = "RESOURCES"
    PLUGINS = "PLUGINS"
    TRANSLATIONS = "TRANSLATIONS"
    LICENSES = "LICENSES"
    PROGRAM_DATA_ROOT = "PROGRAM_DATA_ROOT"
    TOOL_LIBRARY = "TOOL_LIBRARY"
    PROGRAM_TEMPLATES = "PROGRAM_TEMPLATES"
    POSTS = "POSTS"
    MACHINES = "MACHINES"
    MATERIALS = "MATERIALS"
    MACHINE_CONFIG = "MACHINE_CONFIG"
    SCHEMAS = "SCHEMAS"
    MACHINE_BACKUPS = "MACHINE_BACKUPS"
    USER_ROAMING_ROOT = "USER_ROAMING_ROOT"
    USER_CONFIG = "USER_CONFIG"
    USER_UI_STATE = "USER_UI_STATE"
    USER_PROFILES = "USER_PROFILES"
    USER_LOCAL_ROOT = "USER_LOCAL_ROOT"
    CACHE = "CACHE"
    LOGS = "LOGS"
    TEMP = "TEMP"
    CRASH = "CRASH"
    DOCUMENT_PATH = "DOCUMENT_PATH"
    CAM_PROJECT_ROOT = "CAM_PROJECT_ROOT"


class PathResolutionMode(StrEnum):
    PRODUCTION = "PRODUCTION"
    TEST_SANDBOX = "TEST_SANDBOX"
    REVIEW_SANDBOX = "REVIEW_SANDBOX"


class PathSource(StrEnum):
    PRODUCTION_CONTRACT = "PRODUCTION_CONTRACT"
    WINDOWS_KNOWN_FOLDER = "WINDOWS_KNOWN_FOLDER"
    TEST_INJECTION = "TEST_INJECTION"
    REVIEW_INJECTION = "REVIEW_INJECTION"
    USER_SELECTION = "USER_SELECTION"


class ExpectedOwner(StrEnum):
    INSTALLER = "INSTALLER"
    MACHINE_ADMINISTRATORS = "MACHINE_ADMINISTRATORS"
    CURRENT_USER = "CURRENT_USER"
    USER_SELECTED = "USER_SELECTED"


class PathDiagnosticCode(StrEnum):
    READY = "READY"
    MISSING = "MISSING"
    READ_DENIED = "READ_DENIED"
    WRITE_DENIED = "WRITE_DENIED"
    NOT_CREATABLE = "NOT_CREATABLE"
    FILE_DIRECTORY_COLLISION = "FILE_DIRECTORY_COLLISION"
    UNSAFE_PATH = "UNSAFE_PATH"
    ADMIN_INSTALL_REQUIRED = "ADMIN_INSTALL_REQUIRED"
    USER_SELECTION_REQUIRED = "USER_SELECTION_REQUIRED"


class PathStatus(StrEnum):
    READY = "READY"
    MISSING = "MISSING"
    READ_ONLY = "READ_ONLY"
    READ_DENIED = "READ_DENIED"
    NOT_CREATABLE = "NOT_CREATABLE"
    UNSAFE = "UNSAFE"
    USER_SELECTION_REQUIRED = "USER_SELECTION_REQUIRED"


class KnownFolder(StrEnum):
    PROGRAM_DATA = "PROGRAM_DATA"
    ROAMING_APP_DATA = "ROAMING_APP_DATA"
    LOCAL_APP_DATA = "LOCAL_APP_DATA"
    DOCUMENTS = "DOCUMENTS"


class KnownFolderProvider(Protocol):
    def resolve(self, folder: KnownFolder) -> Path:
        """Return one absolute Windows Known Folder path."""


class WindowsKnownFolderProvider:
    """Resolve Windows Known Folders through the shell API, never CWD."""

    _CSIDL = {
        KnownFolder.PROGRAM_DATA: 0x0023,
        KnownFolder.ROAMING_APP_DATA: 0x001A,
        KnownFolder.LOCAL_APP_DATA: 0x001C,
        KnownFolder.DOCUMENTS: 0x0005,
    }

    def resolve(self, folder: KnownFolder) -> Path:
        if os.name != "nt":
            raise OSError("HMS production storage requires Windows Known Folders")
        buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        result = ctypes.windll.shell32.SHGetFolderPathW(  # type: ignore[attr-defined]
            None,
            self._CSIDL[folder],
            None,
            0,
            buffer,
        )
        if result != 0 or not buffer.value:
            raise OSError(f"Cannot resolve Windows Known Folder: {folder.value}")
        path = Path(buffer.value)
        if not path.is_absolute():
            raise OSError(f"Known Folder is not absolute: {path}")
        return path


@dataclass(frozen=True, slots=True)
class StaticKnownFolderProvider:
    """Deterministic provider intended only for tests and review sandboxes."""

    paths: Mapping[KnownFolder, Path]

    def resolve(self, folder: KnownFolder) -> Path:
        try:
            path = Path(self.paths[folder])
        except KeyError as exc:
            raise OSError(f"Missing injected Known Folder: {folder.value}") from exc
        if not path.is_absolute():
            raise ValueError(f"Injected Known Folder must be absolute: {path}")
        return path


@dataclass(frozen=True, slots=True)
class ResolvedAppPath:
    kind: AppPathKind
    scope: StorageScope
    physical_path: Path
    display_path: str
    exists: bool
    readable: bool
    writable: bool
    creatable: bool
    expected_owner: ExpectedOwner
    source: PathSource
    layout_version: int
    diagnostic_code: PathDiagnosticCode
    status: PathStatus


INSTALL_CHILDREN: Mapping[AppPathKind, str] = {
    AppPathKind.EXECUTABLE: "HMS-CADCAM.exe",
    AppPathKind.RUNTIME: "runtime",
    AppPathKind.RESOURCES: "resources",
    AppPathKind.PLUGINS: "plugins",
    AppPathKind.TRANSLATIONS: "translations",
    AppPathKind.LICENSES: "licenses",
}
PROGRAM_DATA_CHILDREN: Mapping[AppPathKind, str] = {
    AppPathKind.TOOL_LIBRARY: "Tool-Library",
    AppPathKind.PROGRAM_TEMPLATES: "Program-Templates",
    AppPathKind.POSTS: "Posts",
    AppPathKind.MACHINES: "Machines",
    AppPathKind.MATERIALS: "Materials",
    AppPathKind.MACHINE_CONFIG: "Config",
    AppPathKind.SCHEMAS: "Schemas",
    AppPathKind.MACHINE_BACKUPS: "Backups",
}
USER_ROAMING_CHILDREN: Mapping[AppPathKind, str] = {
    AppPathKind.USER_CONFIG: "Config",
    AppPathKind.USER_UI_STATE: "UI-State",
    AppPathKind.USER_PROFILES: "Profiles",
}
USER_LOCAL_CHILDREN: Mapping[AppPathKind, str] = {
    AppPathKind.CACHE: "Cache",
    AppPathKind.LOGS: "Logs",
    AppPathKind.TEMP: "Temp",
    AppPathKind.CRASH: "Crash",
}


class ApplicationPathsService:
    """Resolve every non-project application path through one typed service."""

    def __init__(
        self,
        *,
        mode: PathResolutionMode = PathResolutionMode.PRODUCTION,
        known_folders: KnownFolderProvider | None = None,
        install_root: Path | None = None,
        program_data_root: Path | None = None,
        user_roaming_root: Path | None = None,
        user_local_root: Path | None = None,
    ) -> None:
        self._mode = PathResolutionMode(mode)
        if self._mode is PathResolutionMode.PRODUCTION:
            if any(
                value is not None
                for value in (
                    install_root,
                    program_data_root,
                    user_roaming_root,
                    user_local_root,
                )
            ):
                raise ValueError("Production roots cannot be overridden")
            provider = known_folders or WindowsKnownFolderProvider()
            roots = {
                AppPathKind.INSTALL_ROOT: DEFAULT_INSTALL_ROOT,
                AppPathKind.PROGRAM_DATA_ROOT: (
                    provider.resolve(KnownFolder.PROGRAM_DATA) / APPLICATION_FAMILY
                ),
                AppPathKind.USER_ROAMING_ROOT: (
                    provider.resolve(KnownFolder.ROAMING_APP_DATA) / APPLICATION_FAMILY
                ),
                AppPathKind.USER_LOCAL_ROOT: (
                    provider.resolve(KnownFolder.LOCAL_APP_DATA) / APPLICATION_FAMILY
                ),
            }
            self._documents_root = provider.resolve(KnownFolder.DOCUMENTS)
        else:
            values = {
                AppPathKind.INSTALL_ROOT: install_root,
                AppPathKind.PROGRAM_DATA_ROOT: program_data_root,
                AppPathKind.USER_ROAMING_ROOT: user_roaming_root,
                AppPathKind.USER_LOCAL_ROOT: user_local_root,
            }
            if any(value is None for value in values.values()):
                raise ValueError("Sandbox mode requires all four explicit roots")
            roots = {kind: Path(value) for kind, value in values.items() if value}
            self._documents_root = roots[AppPathKind.USER_ROAMING_ROOT].parent / "Documents"
        for path in (*roots.values(), self._documents_root):
            if not path.is_absolute():
                raise ValueError(f"Application root must be absolute: {path}")
        self._roots = roots

    @classmethod
    def production(
        cls,
        *,
        known_folders: KnownFolderProvider | None = None,
    ) -> "ApplicationPathsService":
        return cls(known_folders=known_folders)

    @classmethod
    def sandbox(
        cls,
        root: Path,
        *,
        review: bool = False,
    ) -> "ApplicationPathsService":
        owner = Path(root)
        if not owner.is_absolute():
            raise ValueError("Sandbox root must be absolute")
        return cls(
            mode=(
                PathResolutionMode.REVIEW_SANDBOX
                if review
                else PathResolutionMode.TEST_SANDBOX
            ),
            install_root=owner / "install",
            program_data_root=owner / "program-data",
            user_roaming_root=owner / "user-roaming",
            user_local_root=owner / "user-local",
        )

    @property
    def mode(self) -> PathResolutionMode:
        return self._mode

    @property
    def documents_root(self) -> Path:
        """Return the Known Folder used only as a Save-dialog suggestion."""
        return self._documents_root

    def path(self, kind: AppPathKind, *, selected_path: Path | None = None) -> Path:
        return self.resolve(kind, selected_path=selected_path).physical_path

    def resolve(
        self,
        kind: AppPathKind,
        *,
        selected_path: Path | None = None,
    ) -> ResolvedAppPath:
        selected_kind = AppPathKind(kind)
        if selected_kind in (AppPathKind.DOCUMENT_PATH, AppPathKind.CAM_PROJECT_ROOT):
            if selected_path is None:
                raise ValueError(f"{selected_kind.value} requires a user-selected path")
            path = Path(selected_path)
            if not path.is_absolute():
                raise ValueError("User-selected document/project path must be absolute")
            scope = (
                StorageScope.DOCUMENT
                if selected_kind is AppPathKind.DOCUMENT_PATH
                else StorageScope.CAM_PROJECT
            )
            return self._inspect(
                selected_kind,
                scope,
                path,
                ExpectedOwner.USER_SELECTED,
                PathSource.USER_SELECTION,
            )
        root_kind, scope, owner = self._metadata(selected_kind)
        root = self._roots[root_kind]
        if selected_kind is root_kind:
            path = root
        elif selected_kind in INSTALL_CHILDREN:
            path = root / INSTALL_CHILDREN[selected_kind]
        elif selected_kind in PROGRAM_DATA_CHILDREN:
            path = root / PROGRAM_DATA_CHILDREN[selected_kind]
        elif selected_kind in USER_ROAMING_CHILDREN:
            path = root / USER_ROAMING_CHILDREN[selected_kind]
        else:
            path = root / USER_LOCAL_CHILDREN[selected_kind]
        source = self._source_for(root_kind)
        return self._inspect(selected_kind, scope, path, owner, source)

    def all_application_paths(self) -> tuple[ResolvedAppPath, ...]:
        return tuple(
            self.resolve(kind)
            for kind in AppPathKind
            if kind not in (AppPathKind.DOCUMENT_PATH, AppPathKind.CAM_PROJECT_ROOT)
        )

    def _metadata(
        self,
        kind: AppPathKind,
    ) -> tuple[AppPathKind, StorageScope, ExpectedOwner]:
        if kind is AppPathKind.INSTALL_ROOT or kind in INSTALL_CHILDREN:
            return AppPathKind.INSTALL_ROOT, StorageScope.INSTALL, ExpectedOwner.INSTALLER
        if kind is AppPathKind.PROGRAM_DATA_ROOT or kind in PROGRAM_DATA_CHILDREN:
            return (
                AppPathKind.PROGRAM_DATA_ROOT,
                StorageScope.MACHINE_SHARED,
                ExpectedOwner.MACHINE_ADMINISTRATORS,
            )
        if kind is AppPathKind.USER_ROAMING_ROOT or kind in USER_ROAMING_CHILDREN:
            return (
                AppPathKind.USER_ROAMING_ROOT,
                StorageScope.USER_ROAMING,
                ExpectedOwner.CURRENT_USER,
            )
        if kind is AppPathKind.USER_LOCAL_ROOT or kind in USER_LOCAL_CHILDREN:
            return (
                AppPathKind.USER_LOCAL_ROOT,
                StorageScope.USER_LOCAL,
                ExpectedOwner.CURRENT_USER,
            )
        raise ValueError(f"Unsupported application path kind: {kind.value}")

    def _source_for(self, root_kind: AppPathKind) -> PathSource:
        if self._mode is PathResolutionMode.TEST_SANDBOX:
            return PathSource.TEST_INJECTION
        if self._mode is PathResolutionMode.REVIEW_SANDBOX:
            return PathSource.REVIEW_INJECTION
        if root_kind is AppPathKind.INSTALL_ROOT:
            return PathSource.PRODUCTION_CONTRACT
        return PathSource.WINDOWS_KNOWN_FOLDER

    @staticmethod
    def _inspect(
        kind: AppPathKind,
        scope: StorageScope,
        path: Path,
        owner: ExpectedOwner,
        source: PathSource,
    ) -> ResolvedAppPath:
        try:
            exists = path.exists()
            collision = exists and not (
                path.is_file() if kind is AppPathKind.EXECUTABLE else path.is_dir()
            )
            readable = exists and os.access(path, os.R_OK)
            writable = exists and os.access(path, os.W_OK)
            creatable = not exists and _nearest_existing_parent_writable(path)
            unsafe = _is_reparse_point(path)
        except OSError:
            exists = readable = writable = creatable = False
            collision = unsafe = False
        if unsafe:
            status = PathStatus.UNSAFE
            diagnostic = PathDiagnosticCode.UNSAFE_PATH
        elif collision:
            status = PathStatus.UNSAFE
            diagnostic = PathDiagnosticCode.FILE_DIRECTORY_COLLISION
        elif exists and not readable:
            status = PathStatus.READ_DENIED
            diagnostic = PathDiagnosticCode.READ_DENIED
        elif exists and not writable:
            status = PathStatus.READ_ONLY
            diagnostic = PathDiagnosticCode.WRITE_DENIED
        elif exists:
            status = PathStatus.READY
            diagnostic = PathDiagnosticCode.READY
        elif creatable:
            status = PathStatus.MISSING
            diagnostic = PathDiagnosticCode.MISSING
        else:
            status = PathStatus.NOT_CREATABLE
            diagnostic = (
                PathDiagnosticCode.ADMIN_INSTALL_REQUIRED
                if scope in (StorageScope.INSTALL, StorageScope.MACHINE_SHARED)
                else PathDiagnosticCode.NOT_CREATABLE
            )
        return ResolvedAppPath(
            kind=kind,
            scope=scope,
            physical_path=path,
            display_path=str(path),
            exists=exists,
            readable=readable,
            writable=writable,
            creatable=creatable,
            expected_owner=owner,
            source=source,
            layout_version=STORAGE_LAYOUT_VERSION,
            diagnostic_code=diagnostic,
            status=status,
        )


def _nearest_existing_parent_writable(path: Path) -> bool:
    current = path.parent
    while current != current.parent and not current.exists():
        current = current.parent
    return current.is_dir() and os.access(current, os.W_OK)


def _is_reparse_point(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Compatibility view for existing startup/project composition."""

    data_dir: Path
    config_dir: Path
    log_dir: Path

    @classmethod
    def for_current_user(cls) -> "AppPaths":
        service = ApplicationPathsService.production()
        return cls(
            data_dir=service.path(AppPathKind.USER_LOCAL_ROOT),
            config_dir=service.path(AppPathKind.USER_CONFIG),
            log_dir=service.path(AppPathKind.LOGS),
        )


__all__ = [
    "APPLICATION_FAMILY",
    "AppPathKind",
    "AppPaths",
    "ApplicationPathsService",
    "DEFAULT_INSTALL_ROOT",
    "ExpectedOwner",
    "INSTALL_CHILDREN",
    "KnownFolder",
    "KnownFolderProvider",
    "PROGRAM_DATA_CHILDREN",
    "PathDiagnosticCode",
    "PathResolutionMode",
    "PathSource",
    "PathStatus",
    "ResolvedAppPath",
    "STORAGE_LAYOUT_VERSION",
    "StaticKnownFolderProvider",
    "StorageScope",
    "USER_LOCAL_CHILDREN",
    "USER_ROAMING_CHILDREN",
    "WindowsKnownFolderProvider",
]

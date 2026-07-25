"""Secure `.BAKUPHMS` creation, validation, preview and selective restore."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import stat
from typing import Any
from uuid import UUID, uuid4
import zipfile

from hms_cadcam.core.paths import (
    APPLICATION_FAMILY,
    PROGRAM_DATA_CHILDREN,
    AppPathKind,
    ApplicationPathsService,
)
from hms_cadcam.core.storage_backup import (
    MachineBackupService,
    PreRestoreBackupRecord,
    PreRestoreBackupService,
)
from hms_cadcam.core.storage_io import (
    AtomicBytesWriter,
    MachineResource,
    ResourceFileLock,
    canonical_json_bytes,
)
from hms_cadcam.core.storage_security import (
    WINDOWS_RESERVED_NAMES,
    validate_storage_write_path,
)
from hms_cadcam.core.user_profiles import (
    PROFILE_FILE_NAMES,
    ProfileError,
    UserProfile,
    UserProfileService,
    profile_from_backup_documents,
)


BACKUP_EXTENSION = ".BAKUPHMS"
BACKUP_SCHEMA_VERSION = 1
BACKUP_FORMAT_VERSION = 1
BACKUP_WRITER_VERSION = "0.1.0"
CHECKSUM_ALGORITHM = "SHA-256"
ZIP_COMPRESSION_METHOD = "DEFLATE"
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


class BackupCategory(StrEnum):
    USER_PROFILES = "USER_PROFILES"
    USER_INTERFACE = "USER_INTERFACE"
    USER_SETTINGS = "USER_SETTINGS"
    KEYBOARD_SHORTCUTS = "KEYBOARD_SHORTCUTS"
    QUICK_ACCESS = "QUICK_ACCESS"
    RECENT_FILES = "RECENT_FILES"
    TOOL_LIBRARY = "TOOL_LIBRARY"
    HOLDER_LIBRARY = "HOLDER_LIBRARY"
    PROGRAM_TEMPLATES = "PROGRAM_TEMPLATES"
    POSTS = "POSTS"
    MACHINES = "MACHINES"
    MATERIALS = "MATERIALS"
    MACHINE_CONFIG = "MACHINE_CONFIG"
    EXPORTABLE_SCHEMAS = "EXPORTABLE_SCHEMAS"


class BackupScope(StrEnum):
    USER_ROAMING = "USER_ROAMING"
    MACHINE_SHARED = "MACHINE_SHARED"


class SelectionState(StrEnum):
    NONE = "NONE"
    PARTIAL = "PARTIAL"
    ALL = "ALL"


class ConflictAction(StrEnum):
    KEEP_EXISTING = "KEEP_EXISTING"
    REPLACE = "REPLACE"
    MERGE = "MERGE"
    IMPORT_AS_COPY = "IMPORT_AS_COPY"
    SKIP = "SKIP"


class CompatibilityState(StrEnum):
    COMPATIBLE = "COMPATIBLE"
    PARTIAL = "PARTIAL"
    MIGRATION_REQUIRED = "MIGRATION_REQUIRED"
    NEWER_UNSUPPORTED = "NEWER_UNSUPPORTED"
    WRONG_PRODUCT = "WRONG_PRODUCT"
    CORRUPT = "CORRUPT"
    MISSING_REQUIRED = "MISSING_REQUIRED"


class BackupError(RuntimeError):
    """Base `.BAKUPHMS` operation error."""


class BackupCancelled(BackupError):
    """The caller cancelled before atomic publication."""


class BackupValidationError(BackupError):
    """The container failed a fail-closed validation gate."""

    def __init__(
        self,
        message: str,
        *,
        compatibility: CompatibilityState = CompatibilityState.CORRUPT,
    ) -> None:
        super().__init__(message)
        self.compatibility = compatibility


@dataclass(frozen=True, slots=True)
class BackupLimits:
    maximum_entry_count: int = 10_000
    maximum_entry_size: int = 512 * 1024 * 1024
    maximum_total_uncompressed_size: int = 2 * 1024 * 1024 * 1024
    maximum_compression_ratio: float = 200.0
    maximum_manifest_size: int = 8 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class CategoryEstimate:
    category: BackupCategory
    scope: BackupScope
    available: bool
    readable: bool
    exportable: bool
    selected_by_default: bool
    resource_count: int
    estimated_size: int
    diagnostic_code: str

    @property
    def selectable(self) -> bool:
        return self.available and self.readable and self.exportable


@dataclass(frozen=True, slots=True)
class BackupResourceEntry:
    logical_resource_id: str
    category: BackupCategory
    scope: BackupScope
    relative_path: str
    container_path: str
    size: int
    checksum: str
    resource_version: str
    required: bool
    dependencies: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "logical_resource_id": self.logical_resource_id,
            "category": self.category.value,
            "scope": self.scope.value,
            "relative_path": self.relative_path,
            "container_path": self.container_path,
            "size": self.size,
            "checksum": self.checksum,
            "resource_version": self.resource_version,
            "required": self.required,
            "dependencies": list(self.dependencies),
        }

    @classmethod
    def from_dict(cls, value: object) -> "BackupResourceEntry":
        if not isinstance(value, dict) or set(value) != {
            "logical_resource_id",
            "category",
            "scope",
            "relative_path",
            "container_path",
            "size",
            "checksum",
            "resource_version",
            "required",
            "dependencies",
        }:
            raise BackupValidationError("Backup resource manifest fields mismatch")
        dependencies = value["dependencies"]
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            raise BackupValidationError("Backup resource dependencies are invalid")
        string_fields = (
            "logical_resource_id",
            "category",
            "scope",
            "relative_path",
            "container_path",
            "checksum",
            "resource_version",
        )
        if (
            not all(isinstance(value[name], str) for name in string_fields)
            or not isinstance(value["size"], int)
            or isinstance(value["size"], bool)
            or not isinstance(value["required"], bool)
        ):
            raise BackupValidationError("Backup resource field type is invalid")
        try:
            entry = cls(
                logical_resource_id=str(value["logical_resource_id"]),
                category=BackupCategory(str(value["category"])),
                scope=BackupScope(str(value["scope"])),
                relative_path=str(value["relative_path"]),
                container_path=str(value["container_path"]),
                size=int(value["size"]),
                checksum=str(value["checksum"]),
                resource_version=str(value["resource_version"]),
                required=bool(value["required"]),
                dependencies=tuple(dependencies),
            )
        except ValueError as exc:
            if bool(value.get("required", False)):
                raise BackupValidationError(
                    "Unknown mandatory backup resource",
                    compatibility=CompatibilityState.NEWER_UNSUPPORTED,
                ) from exc
            raise BackupValidationError("Unknown optional backup resource") from exc
        if (
            not entry.logical_resource_id
            or entry.size < 0
            or len(entry.checksum) != 64
            or any(character not in "0123456789abcdef" for character in entry.checksum)
        ):
            raise BackupValidationError("Backup resource metadata is invalid")
        _validate_archive_path(entry.container_path)
        _validate_relative_resource_path(entry.relative_path)
        return entry


@dataclass(frozen=True, slots=True)
class BackupManifest:
    schema_version: int
    format_version: int
    backup_id: str
    application_family: str
    source_application_version: str
    created_at_utc: str
    created_locale: str
    selected_categories: tuple[BackupCategory, ...]
    selected_profile_ids: tuple[str, ...]
    resource_count: int
    uncompressed_size: int
    compressed_size: int
    compression_method: str
    checksum_algorithm: str
    resource_manifest: tuple[BackupResourceEntry, ...]
    compatibility: Mapping[str, object]
    writer_version: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "format_version": self.format_version,
            "backup_id": self.backup_id,
            "application_family": self.application_family,
            "source_application_version": self.source_application_version,
            "created_at_utc": self.created_at_utc,
            "created_locale": self.created_locale,
            "selected_categories": [item.value for item in self.selected_categories],
            "selected_profile_ids": list(self.selected_profile_ids),
            "resource_count": self.resource_count,
            "uncompressed_size": self.uncompressed_size,
            "compressed_size": self.compressed_size,
            "compression_method": self.compression_method,
            "checksum_algorithm": self.checksum_algorithm,
            "resource_manifest": [item.to_dict() for item in self.resource_manifest],
            "compatibility": dict(self.compatibility),
            "writer_version": self.writer_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> "BackupManifest":
        fields = {
            "schema_version", "format_version", "backup_id",
            "application_family", "source_application_version",
            "created_at_utc", "created_locale", "selected_categories",
            "selected_profile_ids", "resource_count", "uncompressed_size",
            "compressed_size", "compression_method", "checksum_algorithm",
            "resource_manifest", "compatibility", "writer_version",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise BackupValidationError("Backup manifest fields mismatch")
        integer_fields = (
            "schema_version",
            "format_version",
            "resource_count",
            "uncompressed_size",
            "compressed_size",
        )
        string_fields = (
            "backup_id",
            "application_family",
            "source_application_version",
            "created_at_utc",
            "created_locale",
            "compression_method",
            "checksum_algorithm",
            "writer_version",
        )
        if (
            not all(
                isinstance(value[name], int) and not isinstance(value[name], bool)
                for name in integer_fields
            )
            or not all(isinstance(value[name], str) for name in string_fields)
        ):
            raise BackupValidationError("Backup manifest field type is invalid")
        if value["schema_version"] != BACKUP_SCHEMA_VERSION:
            raise BackupValidationError("Unsupported backup schema version")
        if int(value["format_version"]) > BACKUP_FORMAT_VERSION:
            raise BackupValidationError(
                "Backup format is newer than this HMS version",
                compatibility=CompatibilityState.NEWER_UNSUPPORTED,
            )
        if int(value["format_version"]) < BACKUP_FORMAT_VERSION:
            raise BackupValidationError(
                "Backup requires a migration adapter",
                compatibility=CompatibilityState.MIGRATION_REQUIRED,
            )
        if value["application_family"] != APPLICATION_FAMILY:
            raise BackupValidationError(
                "Backup belongs to another product family",
                compatibility=CompatibilityState.WRONG_PRODUCT,
            )
        raw_categories = value["selected_categories"]
        raw_profiles = value["selected_profile_ids"]
        raw_resources = value["resource_manifest"]
        compatibility = value["compatibility"]
        if not isinstance(raw_categories, list) or not isinstance(raw_profiles, list):
            raise BackupValidationError("Backup selection metadata is invalid")
        if not isinstance(raw_resources, list) or not isinstance(compatibility, dict):
            raise BackupValidationError("Backup resource manifest is invalid")
        try:
            categories = tuple(BackupCategory(str(item)) for item in raw_categories)
            profiles = tuple(_canonical_uuid(str(item)) for item in raw_profiles)
            created = datetime.fromisoformat(str(value["created_at_utc"]))
            UUID(str(value["backup_id"]))
        except (ValueError, TypeError) as exc:
            raise BackupValidationError("Backup manifest value is invalid") from exc
        if created.tzinfo is None or len(set(categories)) != len(categories):
            raise BackupValidationError("Backup manifest identity is invalid")
        resources = tuple(BackupResourceEntry.from_dict(item) for item in raw_resources)
        manifest = cls(
            BACKUP_SCHEMA_VERSION,
            BACKUP_FORMAT_VERSION,
            str(value["backup_id"]),
            APPLICATION_FAMILY,
            str(value["source_application_version"]),
            str(value["created_at_utc"]),
            str(value["created_locale"]),
            categories,
            profiles,
            int(value["resource_count"]),
            int(value["uncompressed_size"]),
            int(value["compressed_size"]),
            str(value["compression_method"]),
            str(value["checksum_algorithm"]),
            resources,
            dict(compatibility),
            str(value["writer_version"]),
        )
        if (
            manifest.resource_count != len(resources)
            or manifest.uncompressed_size != sum(item.size for item in resources)
            or manifest.compressed_size < 0
            or manifest.compression_method != ZIP_COMPRESSION_METHOD
            or manifest.checksum_algorithm != CHECKSUM_ALGORITHM
        ):
            raise BackupValidationError("Backup manifest totals are invalid")
        logical_ids = [item.logical_resource_id.casefold() for item in resources]
        paths = [item.container_path.casefold() for item in resources]
        if len(set(logical_ids)) != len(logical_ids) or len(set(paths)) != len(paths):
            raise BackupValidationError("Duplicate backup resource entry")
        selected = set(manifest.selected_categories)
        if any(item.category not in selected for item in resources):
            raise BackupValidationError("Resource category was not selected")
        for resource in resources:
            _validate_resource_contract(resource)
        resource_profile_ids = {
            _canonical_uuid(PurePosixPath(item.relative_path).parts[1])
            for item in resources
            if item.category in PROFILE_CATEGORY_FILES
        }
        if resource_profile_ids - set(manifest.selected_profile_ids):
            raise BackupValidationError("Profile resource was not declared")
        return manifest


@dataclass(frozen=True, slots=True)
class ValidatedBackup:
    path: Path
    manifest: BackupManifest
    checksums: Mapping[str, str]
    compatibility: CompatibilityState


@dataclass(frozen=True, slots=True)
class BackupInspection:
    valid: bool
    compatibility: CompatibilityState
    manifest: BackupManifest | None
    diagnostic: str


@dataclass(frozen=True, slots=True)
class BackupCreationResult:
    path: Path
    manifest: BackupManifest
    file_size: int
    checksum: str


@dataclass(frozen=True, slots=True)
class _CollectedResource:
    entry: BackupResourceEntry
    payload: bytes


@dataclass(frozen=True, slots=True)
class RestorePlanItem:
    entry: BackupResourceEntry
    target_path: Path
    selected: bool
    conflict: bool
    permission_blocked: bool
    action: ConflictAction
    diagnostic_code: str


@dataclass(frozen=True, slots=True)
class RestorePlan:
    source_path: Path
    manifest: BackupManifest
    items: tuple[RestorePlanItem, ...]

    @property
    def conflict_count(self) -> int:
        return sum(item.conflict for item in self.items if item.selected)

    @property
    def unresolved_conflict_count(self) -> int:
        return sum(
            item.conflict and item.action is ConflictAction.KEEP_EXISTING
            for item in self.items if item.selected
        )

    @property
    def permission_blocked_count(self) -> int:
        return sum(item.permission_blocked for item in self.items if item.selected)


@dataclass(frozen=True, slots=True)
class RestoreItemResult:
    logical_resource_id: str
    success: bool
    skipped: bool
    rolled_back: bool
    diagnostic_code: str


@dataclass(frozen=True, slots=True)
class RestoreResult:
    success: bool
    partial: bool
    items: tuple[RestoreItemResult, ...]
    restored_count: int
    skipped_count: int
    permission_blocked_count: int
    rollback_failure_count: int
    source_unchanged: bool
    backup_before_restore_count: int
    pre_restore_backup_checksum_mismatch_count: int
    resource_published_before_failure_count: int
    rollback_attempted_resource_count: int
    rollback_restored_resource_count: int
    rollback_restored_checksum_mismatch_count: int
    previous_data_preserved: bool


PROFILE_CATEGORY_FILES: Mapping[BackupCategory, str] = {
    BackupCategory.USER_PROFILES: "profile.json",
    BackupCategory.USER_INTERFACE: "ui-state.json",
    BackupCategory.USER_SETTINGS: "preferences.json",
    BackupCategory.KEYBOARD_SHORTCUTS: "shortcuts.json",
    BackupCategory.QUICK_ACCESS: "quick-access.json",
    BackupCategory.RECENT_FILES: "recent-files.json",
}
MACHINE_CATEGORY_KINDS: Mapping[BackupCategory, AppPathKind] = {
    BackupCategory.TOOL_LIBRARY: AppPathKind.TOOL_LIBRARY,
    BackupCategory.HOLDER_LIBRARY: AppPathKind.TOOL_LIBRARY,
    BackupCategory.PROGRAM_TEMPLATES: AppPathKind.PROGRAM_TEMPLATES,
    BackupCategory.POSTS: AppPathKind.POSTS,
    BackupCategory.MACHINES: AppPathKind.MACHINES,
    BackupCategory.MATERIALS: AppPathKind.MATERIALS,
    BackupCategory.MACHINE_CONFIG: AppPathKind.MACHINE_CONFIG,
    BackupCategory.EXPORTABLE_SCHEMAS: AppPathKind.SCHEMAS,
}
MACHINE_LOCKS: Mapping[BackupCategory, MachineResource] = {
    BackupCategory.TOOL_LIBRARY: MachineResource.TOOL_LIBRARY,
    BackupCategory.HOLDER_LIBRARY: MachineResource.TOOL_LIBRARY,
    BackupCategory.PROGRAM_TEMPLATES: MachineResource.PROGRAM_TEMPLATES,
    BackupCategory.POSTS: MachineResource.POSTS,
    BackupCategory.MACHINES: MachineResource.MACHINES,
    BackupCategory.MATERIALS: MachineResource.MATERIALS,
    BackupCategory.MACHINE_CONFIG: MachineResource.CONFIG,
    BackupCategory.EXPORTABLE_SCHEMAS: MachineResource.SCHEMAS,
}
MERGE_ALLOWED = frozenset(
    {
        BackupCategory.USER_SETTINGS,
        BackupCategory.QUICK_ACCESS,
        BackupCategory.RECENT_FILES,
        BackupCategory.MACHINE_CONFIG,
    }
)
FORBIDDEN_SUFFIXES = frozenset(
    {".exe", ".dll", ".com", ".msi", ".ttf", ".otf", ".hms", ".nc", ".tap", ".gcode"}
)
FORBIDDEN_NAMES = frozenset(
    {"project.db", "project.hms.json", "autosave.hms.json"}
)
FORBIDDEN_TOKENS = frozenset({"password", "credential", "token", "license-secret", "private-key"})


class BackupSelectionModel:
    """Typed select-all/none/partial state constrained to eligible categories."""

    def __init__(self, estimates: Sequence[CategoryEstimate]) -> None:
        self.estimates = {item.category: item for item in estimates}
        self._selected = {
            item.category
            for item in estimates
            if item.selectable and item.selected_by_default
        }

    @property
    def selected(self) -> tuple[BackupCategory, ...]:
        return tuple(category for category in BackupCategory if category in self._selected)

    @property
    def state(self) -> SelectionState:
        eligible = {key for key, value in self.estimates.items() if value.selectable}
        if not self._selected:
            return SelectionState.NONE
        return SelectionState.ALL if self._selected == eligible else SelectionState.PARTIAL

    def set_selected(self, category: BackupCategory, selected: bool) -> None:
        item = self.estimates[BackupCategory(category)]
        if selected and item.selectable:
            self._selected.add(item.category)
            if item.category in PROFILE_CATEGORY_FILES and item.category is not BackupCategory.USER_PROFILES:
                profile_item = self.estimates.get(BackupCategory.USER_PROFILES)
                if profile_item is not None and profile_item.selectable:
                    self._selected.add(BackupCategory.USER_PROFILES)
        else:
            self._selected.discard(item.category)
            if item.category is BackupCategory.USER_PROFILES:
                self._selected.difference_update(PROFILE_CATEGORY_FILES)

    def select_all(self) -> None:
        self._selected = {
            key for key, value in self.estimates.items() if value.selectable
        }

    def select_none(self) -> None:
        self._selected.clear()

    @property
    def estimated_size(self) -> int:
        return sum(self.estimates[item].estimated_size for item in self._selected)

    @property
    def resource_count(self) -> int:
        return sum(self.estimates[item].resource_count for item in self._selected)


class HmsBackupService:
    """Create and validate one managed ZIP-compatible `.BAKUPHMS` file."""

    def __init__(
        self,
        paths: ApplicationPathsService,
        *,
        profile_service: UserProfileService | None = None,
        application_version: str = "0.1.0",
        limits: BackupLimits | None = None,
    ) -> None:
        self.paths = paths
        self.profile_service = profile_service or UserProfileService(paths)
        self.application_version = str(application_version)
        self.limits = limits or BackupLimits()

    @staticmethod
    def suggested_filename(now: datetime | None = None) -> str:
        timestamp = now or datetime.now()
        return f"HMS-Sao-luu-{timestamp:%Y-%m-%d}{BACKUP_EXTENSION}"

    def suggested_directory(self, previous: Path | None = None) -> Path:
        if previous is not None and previous.is_dir():
            return previous
        if self.paths.documents_root.is_dir():
            return self.paths.documents_root
        return self.paths.path(AppPathKind.USER_ROAMING_ROOT)

    def estimate_categories(
        self,
        *,
        profile_ids: Sequence[str] = (),
    ) -> tuple[CategoryEstimate, ...]:
        selected_profiles = self._selected_profiles(profile_ids)
        estimates: list[CategoryEstimate] = []
        for category in BackupCategory:
            resources = self._collect_category(category, selected_profiles, estimate_only=True)
            scope = _category_scope(category)
            root = (
                self.paths.path(AppPathKind.USER_ROAMING_ROOT)
                if scope is BackupScope.USER_ROAMING
                else self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
            )
            available = bool(resources)
            readable = root.is_dir() and os.access(root, os.R_OK)
            estimates.append(
                CategoryEstimate(
                    category,
                    scope,
                    available,
                    readable,
                    True,
                    available and category is not BackupCategory.RECENT_FILES,
                    len(resources),
                    sum(item.entry.size for item in resources),
                    "READY" if available and readable else "NO_DATA" if not available else "READ_DENIED",
                )
            )
        return tuple(estimates)

    def create(
        self,
        destination: Path,
        categories: Sequence[BackupCategory],
        *,
        profile_ids: Sequence[str] = (),
        created_locale: str = "VI_VN",
        overwrite_confirmed: bool = False,
        cancelled: Callable[[], bool] | None = None,
    ) -> BackupCreationResult:
        target = Path(destination)
        if target.suffix.casefold() != BACKUP_EXTENSION.casefold():
            raise BackupError(f"Backup file must use {BACKUP_EXTENSION}")
        if not target.is_absolute():
            raise BackupError("Backup destination must be absolute")
        if target.exists() and not overwrite_confirmed:
            raise FileExistsError(str(target))
        target.parent.mkdir(parents=True, exist_ok=True)
        if cancelled and cancelled():
            raise BackupCancelled("Backup cancelled before collection")
        selected_set = {BackupCategory(item) for item in categories}
        if selected_set & (set(PROFILE_CATEGORY_FILES) - {BackupCategory.USER_PROFILES}):
            selected_set.add(BackupCategory.USER_PROFILES)
        selected_categories = tuple(
            category for category in BackupCategory if category in selected_set
        )
        selected_profiles = self._selected_profiles(profile_ids)
        resources: list[_CollectedResource] = []
        lock_resources = {
            MACHINE_LOCKS[item]
            for item in selected_categories
            if item in MACHINE_LOCKS
        }
        machine_root = self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
        staging = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with ExitStack() as stack:
                for resource in sorted(lock_resources, key=lambda item: item.value):
                    stack.enter_context(ResourceFileLock(machine_root, resource))
                for category in selected_categories:
                    if cancelled and cancelled():
                        raise BackupCancelled("Backup cancelled during collection")
                    resources.extend(self._collect_category(category, selected_profiles))
            resources.sort(key=lambda item: item.entry.container_path.casefold())
            if not resources:
                raise BackupError("No eligible backup resources were selected")
            manifest = self._manifest(
                resources,
                selected_categories,
                (
                    tuple(profile.profile_id for profile in selected_profiles)
                    if selected_set & set(PROFILE_CATEGORY_FILES)
                    else ()
                ),
                created_locale,
                compressed_size=0,
            )
            self._write_archive(staging, manifest, resources)
            with zipfile.ZipFile(staging, "r") as archive:
                compressed = sum(
                    archive.getinfo(item.entry.container_path).compress_size
                    for item in resources
                )
            manifest = replace(manifest, compressed_size=compressed)
            self._write_archive(staging, manifest, resources)
            validated = self.validate(staging, require_extension=False)
            if validated.manifest != manifest:
                raise BackupValidationError("Backup read-after-write mismatch")
            if cancelled and cancelled():
                raise BackupCancelled("Backup cancelled before publication")
            if target.exists():
                os.replace(staging, target)
            else:
                os.rename(staging, target)
            checksum = _sha256_path(target)
            return BackupCreationResult(target, manifest, target.stat().st_size, checksum)
        finally:
            staging.unlink(missing_ok=True)

    def inspect(self, path: Path) -> BackupInspection:
        try:
            validated = self.validate(path)
            return BackupInspection(True, validated.compatibility, validated.manifest, "VALID")
        except BackupValidationError as exc:
            return BackupInspection(False, exc.compatibility, None, str(exc))

    def validate(self, path: Path, *, require_extension: bool = True) -> ValidatedBackup:
        source = Path(path)
        if require_extension and source.suffix.casefold() != BACKUP_EXTENSION.casefold():
            raise BackupValidationError("Invalid backup extension")
        if not source.is_file() or source.is_symlink():
            raise BackupValidationError("Backup is not a regular file")
        with source.open("rb") as stream:
            if stream.read(4) != b"PK\x03\x04":
                raise BackupValidationError("Backup magic is invalid")
        try:
            with zipfile.ZipFile(source, "r") as archive:
                infos = archive.infolist()
                self._validate_infos(infos)
                manifest_bytes = archive.read("manifest.json")
                checksums_bytes = archive.read("checksums.json")
                if max(len(manifest_bytes), len(checksums_bytes)) > self.limits.maximum_manifest_size:
                    raise BackupValidationError("Backup metadata is too large")
                try:
                    manifest = BackupManifest.from_dict(json.loads(manifest_bytes.decode("utf-8")))
                    checksums = json.loads(checksums_bytes.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise BackupValidationError("Backup metadata JSON is malformed") from exc
                if not isinstance(checksums, dict) or not all(
                    isinstance(key, str) and isinstance(value, str)
                    for key, value in checksums.items()
                ):
                    raise BackupValidationError("Backup checksum index is invalid")
                expected_paths = {item.container_path for item in manifest.resource_manifest}
                actual_paths = {item.filename for item in infos} - {"manifest.json", "checksums.json"}
                if expected_paths != actual_paths or set(checksums) != expected_paths:
                    raise BackupValidationError("Backup manifest/archive entries mismatch")
                total = 0
                compressed = 0
                info_by_name = {item.filename: item for item in infos}
                for resource in manifest.resource_manifest:
                    info = info_by_name[resource.container_path]
                    if info.file_size != resource.size:
                        raise BackupValidationError("Backup resource size mismatch")
                    payload = archive.read(resource.container_path)
                    digest = hashlib.sha256(payload).hexdigest()
                    if digest != resource.checksum or checksums[resource.container_path] != digest:
                        raise BackupValidationError("Backup resource checksum mismatch")
                    total += len(payload)
                    compressed += info.compress_size
                if total != manifest.uncompressed_size or compressed != manifest.compressed_size:
                    raise BackupValidationError("Backup compressed/uncompressed totals mismatch")
                return ValidatedBackup(source, manifest, dict(checksums), CompatibilityState.COMPATIBLE)
        except (OSError, zipfile.BadZipFile, KeyError) as exc:
            raise BackupValidationError("Backup archive is damaged") from exc

    def read_payloads(self, validated: ValidatedBackup) -> Mapping[str, bytes]:
        with zipfile.ZipFile(validated.path, "r") as archive:
            return {
                entry.container_path: archive.read(entry.container_path)
                for entry in validated.manifest.resource_manifest
            }

    def _selected_profiles(self, profile_ids: Sequence[str]) -> tuple[UserProfile, ...]:
        try:
            active = self.profile_service.bootstrap()
            profiles = self.profile_service.profiles()
        except (OSError, ValueError, TypeError, ProfileError):
            return ()
        requested = {_canonical_uuid(item) for item in profile_ids}
        if not requested:
            return profiles
        selected = tuple(item for item in profiles if item.profile_id in requested)
        if {item.profile_id for item in selected} != requested:
            raise BackupError("Selected profile does not exist")
        return selected or (active,)

    def _collect_category(
        self,
        category: BackupCategory,
        profiles: Sequence[UserProfile],
        *,
        estimate_only: bool = False,
    ) -> list[_CollectedResource]:
        if category in PROFILE_CATEGORY_FILES:
            filename = PROFILE_CATEGORY_FILES[category]
            resources: list[_CollectedResource] = []
            for profile in profiles:
                source = self.paths.path(AppPathKind.USER_PROFILES) / profile.profile_id / filename
                if (
                    source.is_file()
                    and not source.is_symlink()
                    and not _json_file_contains_secret(source)
                ):
                    resources.append(self._collected_file(
                        category,
                        BackupScope.USER_ROAMING,
                        self.paths.path(AppPathKind.USER_ROAMING_ROOT),
                        source,
                        required=category is BackupCategory.USER_PROFILES,
                        dependencies=(() if category is BackupCategory.USER_PROFILES else (f"profile:{profile.profile_id}",)),
                        estimate_only=estimate_only,
                    ))
            return resources
        root = self.paths.path(MACHINE_CATEGORY_KINDS[category])
        if not root.is_dir() or root.is_symlink():
            return []
        resources = []
        for source in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
            if (
                not source.is_file()
                or source.is_symlink()
                or _forbidden(source.relative_to(root))
                or _json_file_contains_secret(source)
            ):
                continue
            holder = "holder" in str(source.relative_to(root)).casefold()
            if category is BackupCategory.HOLDER_LIBRARY and not holder:
                continue
            if category is BackupCategory.TOOL_LIBRARY and holder:
                continue
            if category is BackupCategory.MACHINE_CONFIG and source.name == "storage-layout.json":
                continue
            resources.append(self._collected_file(
                category,
                BackupScope.MACHINE_SHARED,
                self.paths.path(AppPathKind.PROGRAM_DATA_ROOT),
                source,
                required=False,
                dependencies=(),
                estimate_only=estimate_only,
            ))
        return resources

    def _collected_file(
        self,
        category: BackupCategory,
        scope: BackupScope,
        scope_root: Path,
        source: Path,
        *,
        required: bool,
        dependencies: tuple[str, ...],
        estimate_only: bool,
    ) -> _CollectedResource:
        relative = source.relative_to(scope_root).as_posix()
        _validate_relative_resource_path(relative)
        if _forbidden(Path(relative)):
            raise BackupError("Forbidden resource cannot enter a backup")
        payload = b"" if estimate_only else source.read_bytes()
        size = source.stat().st_size
        digest = "0" * 64 if estimate_only else hashlib.sha256(payload).hexdigest()
        if scope is BackupScope.USER_ROAMING:
            prefix = (
                "profiles"
                if category is BackupCategory.USER_PROFILES
                else "user-settings"
            )
        else:
            prefix = "machine-resources"
        container_path = f"{prefix}/{category.value.lower()}/{relative}"
        entry = BackupResourceEntry(
            f"{scope.value}:{category.value}:{relative.casefold()}",
            category,
            scope,
            relative,
            container_path,
            size,
            digest,
            _resource_version(payload),
            required,
            dependencies,
        )
        return _CollectedResource(entry, payload)

    def _manifest(
        self,
        resources: Sequence[_CollectedResource],
        categories: tuple[BackupCategory, ...],
        profile_ids: tuple[str, ...],
        locale: str,
        *,
        compressed_size: int,
    ) -> BackupManifest:
        return BackupManifest(
            BACKUP_SCHEMA_VERSION,
            BACKUP_FORMAT_VERSION,
            str(uuid4()),
            APPLICATION_FAMILY,
            self.application_version,
            datetime.now(timezone.utc).isoformat(),
            str(locale),
            categories,
            profile_ids,
            len(resources),
            sum(item.entry.size for item in resources),
            compressed_size,
            ZIP_COMPRESSION_METHOD,
            CHECKSUM_ALGORITHM,
            tuple(item.entry for item in resources),
            {"minimum_reader_format": 1, "maximum_reader_format": 1},
            BACKUP_WRITER_VERSION,
        )

    def _write_archive(
        self,
        target: Path,
        manifest: BackupManifest,
        resources: Sequence[_CollectedResource],
    ) -> None:
        target.unlink(missing_ok=True)
        checksums = {
            item.entry.container_path: item.entry.checksum for item in resources
        }
        with zipfile.ZipFile(
            target,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            strict_timestamps=True,
        ) as archive:
            _write_zip_entry(archive, "manifest.json", canonical_json_bytes(manifest.to_dict()))
            _write_zip_entry(archive, "checksums.json", canonical_json_bytes(checksums))
            for resource in resources:
                _write_zip_entry(archive, resource.entry.container_path, resource.payload)
        with target.open("rb+") as stream:
            os.fsync(stream.fileno())

    def _validate_infos(self, infos: Sequence[zipfile.ZipInfo]) -> None:
        if len(infos) > self.limits.maximum_entry_count:
            raise BackupValidationError("Backup entry count exceeds the limit")
        names = [item.filename for item in infos]
        folded = [item.casefold() for item in names]
        if len(names) != len(set(names)) or len(folded) != len(set(folded)):
            raise BackupValidationError("Duplicate or case-colliding archive entry")
        if "manifest.json" not in names or "checksums.json" not in names:
            raise BackupValidationError(
                "Backup is missing mandatory metadata",
                compatibility=CompatibilityState.MISSING_REQUIRED,
            )
        total = 0
        files = set(folded)
        for info in infos:
            _validate_archive_path(info.filename)
            if info.is_dir():
                raise BackupValidationError("Directory entries are not permitted")
            unix_type = (info.external_attr >> 16) & 0o170000
            if unix_type not in {0, stat.S_IFREG}:
                raise BackupValidationError("Symlink or special archive entry blocked")
            if info.create_system == 0 and (
                info.external_attr & 0x400
                or (info.external_attr >> 16) & 0x400
            ):
                raise BackupValidationError("Junction or reparse archive entry blocked")
            if info.file_size > self.limits.maximum_entry_size:
                raise BackupValidationError("Backup entry is too large")
            total += info.file_size
            if total > self.limits.maximum_total_uncompressed_size:
                raise BackupValidationError("Backup uncompressed size exceeds the limit")
            if info.file_size and info.compress_size == 0:
                raise BackupValidationError("Backup compression ratio is unsafe")
            if info.compress_size and info.file_size / info.compress_size > self.limits.maximum_compression_ratio:
                raise BackupValidationError("Backup compression ratio is unsafe")
            path = PurePosixPath(info.filename.casefold())
            for parent in path.parents:
                if str(parent) not in {".", ""} and str(parent) in files:
                    raise BackupValidationError("Archive file/directory collision")


class HmsRestoreService:
    """Preview and publish selected resources with backup and rollback."""

    def __init__(
        self,
        paths: ApplicationPathsService,
        *,
        backup_service: HmsBackupService | None = None,
        profile_service: UserProfileService | None = None,
        writer: AtomicBytesWriter | None = None,
        machine_backup: MachineBackupService | None = None,
        pre_restore_backup: PreRestoreBackupService | None = None,
    ) -> None:
        self.paths = paths
        self.profile_service = profile_service or UserProfileService(paths)
        self.backup_service = backup_service or HmsBackupService(paths, profile_service=self.profile_service)
        self.writer = writer or AtomicBytesWriter()
        self.machine_backup = machine_backup or MachineBackupService(paths)
        self.pre_restore_backup = (
            pre_restore_backup or PreRestoreBackupService(paths)
        )

    def preview(
        self,
        source: Path,
        *,
        selected_categories: Sequence[BackupCategory] | None = None,
        actions: Mapping[str, ConflictAction] | None = None,
    ) -> RestorePlan:
        validated = self.backup_service.validate(source)
        selected = (
            set(validated.manifest.selected_categories)
            if selected_categories is None
            else {BackupCategory(item) for item in selected_categories}
        )
        action_map = actions or {}
        items: list[RestorePlanItem] = []
        for entry in validated.manifest.resource_manifest:
            target = self._target(entry)
            conflict = target.is_file() and _sha256_path(target) != entry.checksum
            permission_blocked = not self._target_writable(entry, target)
            default_action = (
                ConflictAction.KEEP_EXISTING if conflict else ConflictAction.REPLACE
            )
            requested_action = ConflictAction(
                action_map.get(entry.logical_resource_id, default_action)
            )
            action = (
                ConflictAction.SKIP
                if permission_blocked
                else requested_action
            )
            if action is ConflictAction.MERGE and entry.category not in MERGE_ALLOWED:
                diagnostic = "MERGE_NOT_SUPPORTED"
            elif permission_blocked:
                diagnostic = "PERMISSION_DENIED"
            elif conflict:
                diagnostic = "CONFLICT"
            else:
                diagnostic = "READY"
            items.append(RestorePlanItem(
                entry,
                target,
                entry.category in selected,
                conflict,
                permission_blocked,
                action,
                diagnostic,
            ))
        return RestorePlan(Path(source), validated.manifest, tuple(items))

    def restore(self, plan: RestorePlan) -> RestoreResult:
        source_checksum = _sha256_path(plan.source_path)
        validated = self.backup_service.validate(plan.source_path)
        if validated.manifest.backup_id != plan.manifest.backup_id:
            raise BackupValidationError("Restore plan no longer matches its source")
        payloads = self.backup_service.read_payloads(validated)
        selected = tuple(item for item in plan.items if item.selected)
        permission_blocked_items = tuple(
            item for item in selected if item.permission_blocked
        )
        publishable = tuple(
            item
            for item in selected
            if not item.permission_blocked
        )
        results: list[RestoreItemResult] = [
            RestoreItemResult(
                item.entry.logical_resource_id,
                False,
                True,
                False,
                "PERMISSION_DENIED",
            )
            for item in permission_blocked_items
        ]
        snapshots: list[tuple[RestorePlanItem, bytes | None]] = []
        pre_restore_records: dict[str, PreRestoreBackupRecord] = {}
        profile_snapshots: dict[str, UserProfile] = {}
        imported_profiles: list[str] = []
        profile_journal_ids: set[str] = set()
        backup_count = 0
        backup_checksum_mismatches = 0
        rollback_failures = 0
        rollback_attempted = 0
        rollback_restored = 0
        rollback_checksum_mismatches = 0
        restored_count = 0
        published_ids: set[str] = set()
        transaction_id = self.pre_restore_backup.new_transaction_id()
        machine_locks = {
            MACHINE_LOCKS[item.entry.category]
            for item in publishable
            if item.entry.category in MACHINE_LOCKS
        }
        try:
            with ExitStack() as stack:
                machine_root = self.paths.path(AppPathKind.PROGRAM_DATA_ROOT)
                for resource in sorted(machine_locks, key=lambda item: item.value):
                    stack.enter_context(ResourceFileLock(machine_root, resource))
                profile_items = [
                    item
                    for item in publishable
                    if item.entry.category in PROFILE_CATEGORY_FILES
                ]
                generic_items = [
                    item
                    for item in publishable
                    if item.entry.category not in PROFILE_CATEGORY_FILES
                ]
                for item in generic_items:
                    if item.conflict and item.action in {ConflictAction.KEEP_EXISTING, ConflictAction.SKIP}:
                        results.append(RestoreItemResult(item.entry.logical_resource_id, True, True, False, item.action.value))
                        continue
                    if item.action is ConflictAction.MERGE and item.entry.category not in MERGE_ALLOWED:
                        results.append(RestoreItemResult(item.entry.logical_resource_id, False, False, False, "MERGE_NOT_SUPPORTED"))
                        continue
                    payload = payloads[item.entry.container_path]
                    if item.action is ConflictAction.MERGE and item.target_path.is_file():
                        payload = _merge_json_bytes(item.target_path.read_bytes(), payload)
                    previous = item.target_path.read_bytes() if item.target_path.is_file() else None
                    snapshots.append((item, previous))
                    if previous is not None and item.action in {
                        ConflictAction.REPLACE,
                        ConflictAction.MERGE,
                    }:
                        record = self.pre_restore_backup.create_backup(
                            item.target_path,
                            resource_id=item.entry.logical_resource_id,
                            category=item.entry.category.value,
                            scope=item.entry.scope.value,
                            transaction_id=transaction_id,
                        )
                        if record is None:
                            raise BackupError(
                                "Pre-restore backup was not created"
                            )
                        pre_restore_records[
                            item.entry.logical_resource_id
                        ] = record
                        backup_count += 1
                        if not self.pre_restore_backup.validate(record):
                            backup_checksum_mismatches += 1
                            raise BackupError(
                                "Pre-restore backup checksum mismatch"
                            )
                    root = self._scope_root(item.entry.scope)
                    self.writer.write(root, item.target_path, payload)
                    if hashlib.sha256(item.target_path.read_bytes()).hexdigest() != hashlib.sha256(payload).hexdigest():
                        raise BackupError("Restore read-after-write validation failed")
                    published_ids.add(item.entry.logical_resource_id)
                    restored_count += 1
                    results.append(RestoreItemResult(item.entry.logical_resource_id, True, False, False, "RESTORED"))
                if profile_items:
                    for item in profile_items:
                        if (
                            item.target_path.is_file()
                            and item.action
                            in {
                                ConflictAction.REPLACE,
                                ConflictAction.MERGE,
                            }
                        ):
                            record = self.pre_restore_backup.create_backup(
                                item.target_path,
                                resource_id=item.entry.logical_resource_id,
                                category=item.entry.category.value,
                                scope=item.entry.scope.value,
                                transaction_id=transaction_id,
                            )
                            if record is None:
                                raise BackupError(
                                    "Pre-restore profile backup was not created"
                                )
                            pre_restore_records[
                                item.entry.logical_resource_id
                            ] = record
                            backup_count += 1
                            if not self.pre_restore_backup.validate(record):
                                backup_checksum_mismatches += 1
                                raise BackupError(
                                    "Pre-restore profile backup checksum mismatch"
                                )
                    restored, profile_results = self._restore_profiles(
                        profile_items,
                        payloads,
                        snapshots=profile_snapshots,
                        imported=imported_profiles,
                        journal_ids=profile_journal_ids,
                    )
                    restored_count += restored
                    results.extend(profile_results)
        except (OSError, RuntimeError, ValueError, TypeError, ProfileError, BackupError):
            for profile_id in reversed(imported_profiles):
                try:
                    self.profile_service.delete(profile_id)
                except (OSError, RuntimeError, ValueError, TypeError, ProfileError):
                    rollback_failures += 1
            for profile in profile_snapshots.values():
                try:
                    self.profile_service.save(profile)
                except (OSError, RuntimeError, ValueError, TypeError, ProfileError):
                    rollback_failures += 1
            for logical_id, record in pre_restore_records.items():
                if record.category not in {
                    category.value for category in PROFILE_CATEGORY_FILES
                }:
                    continue
                rollback_attempted += 1
                try:
                    original = Path(record.original_path)
                    if (
                        not original.is_file()
                        or _sha256_path(original)
                        != record.original_checksum
                    ):
                        rollback_checksum_mismatches += 1
                        rollback_failures += 1
                    else:
                        rollback_restored += 1
                except (OSError, RuntimeError, ValueError):
                    rollback_failures += 1
            for item, previous in reversed(snapshots):
                rollback_attempted += 1
                try:
                    if previous is None:
                        item.target_path.unlink(missing_ok=True)
                    else:
                        record = pre_restore_records.get(
                            item.entry.logical_resource_id
                        )
                        rollback_payload = (
                            self.pre_restore_backup.restore_bytes(record)
                            if record is not None
                            else previous
                        )
                        self.writer.write(
                            self._scope_root(item.entry.scope),
                            item.target_path,
                            rollback_payload,
                        )
                        restored_checksum = _sha256_path(item.target_path)
                        expected_checksum = hashlib.sha256(previous).hexdigest()
                        if restored_checksum != expected_checksum:
                            rollback_checksum_mismatches += 1
                            raise BackupError(
                                "Rollback restored checksum mismatch"
                            )
                    rollback_restored += 1
                except (OSError, RuntimeError, ValueError):
                    rollback_failures += 1
            rolled_ids = {
                item.entry.logical_resource_id for item, _ in snapshots
            } | profile_journal_ids
            results = [
                replace(item, success=False, rolled_back=item.logical_resource_id in rolled_ids, diagnostic_code="ROLLED_BACK")
                if item.logical_resource_id in rolled_ids else item
                for item in results
            ]
            reported_ids = {item.logical_resource_id for item in results}
            results.extend(
                RestoreItemResult(
                    item.entry.logical_resource_id,
                    False,
                    False,
                    item.entry.logical_resource_id in rolled_ids,
                    "ROLLED_BACK"
                    if item.entry.logical_resource_id in rolled_ids
                    else "RESTORE_FAILED",
                )
                for item in selected
                if item.entry.logical_resource_id not in reported_ids
                and not item.permission_blocked
            )
        source_unchanged = _sha256_path(plan.source_path) == source_checksum
        blocked = len(permission_blocked_items)
        failures = sum(not item.success and not item.skipped for item in results)
        return RestoreResult(
            success=failures == 0 and rollback_failures == 0,
            partial=blocked > 0 or any(item.skipped for item in results),
            items=tuple(results),
            restored_count=sum(
                item.success and not item.skipped and item.diagnostic_code == "RESTORED"
                for item in results
            ),
            skipped_count=sum(item.skipped for item in results),
            permission_blocked_count=blocked,
            rollback_failure_count=rollback_failures,
            source_unchanged=source_unchanged,
            backup_before_restore_count=backup_count,
            pre_restore_backup_checksum_mismatch_count=(
                backup_checksum_mismatches
            ),
            resource_published_before_failure_count=len(published_ids),
            rollback_attempted_resource_count=rollback_attempted,
            rollback_restored_resource_count=rollback_restored,
            rollback_restored_checksum_mismatch_count=(
                rollback_checksum_mismatches
            ),
            previous_data_preserved=(
                rollback_failures == 0
                and rollback_checksum_mismatches == 0
            ),
        )

    def _restore_profiles(
        self,
        items: Sequence[RestorePlanItem],
        payloads: Mapping[str, bytes],
        *,
        snapshots: dict[str, UserProfile],
        imported: list[str],
        journal_ids: set[str],
    ) -> tuple[int, list[RestoreItemResult]]:
        grouped: dict[str, list[RestorePlanItem]] = {}
        for item in items:
            parts = PurePosixPath(item.entry.relative_path).parts
            if len(parts) != 3 or parts[0] != "Profiles":
                raise BackupError("Profile backup resource path is invalid")
            grouped.setdefault(_canonical_uuid(parts[1]), []).append(item)
        results: list[RestoreItemResult] = []
        restored = 0
        existing_ids = {item.profile_id for item in self.profile_service.profiles()}
        for profile_id, group in grouped.items():
            allowed = [item for item in group if not item.permission_blocked]
            if not allowed:
                results.extend(RestoreItemResult(item.entry.logical_resource_id, False, True, False, "PERMISSION_DENIED") for item in group)
                continue
            action = next((item.action for item in allowed if item.conflict), allowed[0].action)
            if action in {ConflictAction.KEEP_EXISTING, ConflictAction.SKIP} and profile_id in existing_ids:
                results.extend(RestoreItemResult(item.entry.logical_resource_id, True, True, False, action.value) for item in group)
                continue
            docs = {
                PurePosixPath(item.entry.relative_path).name: payloads[item.entry.container_path]
                for item in allowed
            }
            if "profile.json" not in docs:
                raise BackupError("Profile metadata dependency was not selected")
            restored_profile = profile_from_backup_documents(profile_id, docs)
            journal_ids.update(item.entry.logical_resource_id for item in allowed)
            if profile_id in existing_ids:
                current = self.profile_service.load(profile_id)
                snapshots[profile_id] = current
                if action is ConflictAction.IMPORT_AS_COPY:
                    imported_profile = self.profile_service.import_profile(restored_profile, as_copy=True)
                    imported.append(imported_profile.profile_id)
                else:
                    merged = _selective_profile(current, restored_profile, {item.entry.category for item in allowed})
                    self.profile_service.save(merged)
            else:
                imported_profile = self.profile_service.import_profile(
                    restored_profile,
                    as_copy=action is ConflictAction.IMPORT_AS_COPY,
                )
                imported.append(imported_profile.profile_id)
            restored += len(allowed)
            results.extend(RestoreItemResult(item.entry.logical_resource_id, True, False, False, "RESTORED") for item in allowed)
        return restored, results

    def _target(self, entry: BackupResourceEntry) -> Path:
        root = self._scope_root(entry.scope)
        relative = PurePosixPath(entry.relative_path)
        target = root.joinpath(*relative.parts)
        validation = validate_storage_write_path(root, target, expect_directory=False)
        if not validation.safe and validation.code.value not in {"PARENT_NOT_WRITABLE"}:
            raise BackupValidationError("Restore target escaped its scope")
        return target

    def _target_writable(self, entry: BackupResourceEntry, target: Path) -> bool:
        validation = validate_storage_write_path(
            self._scope_root(entry.scope), target, expect_directory=False
        )
        return validation.safe

    def _scope_root(self, scope: BackupScope) -> Path:
        return self.paths.path(
            AppPathKind.USER_ROAMING_ROOT
            if scope is BackupScope.USER_ROAMING
            else AppPathKind.PROGRAM_DATA_ROOT
        )


def _selective_profile(
    current: UserProfile,
    restored: UserProfile,
    categories: set[BackupCategory],
) -> UserProfile:
    values: dict[str, object] = {}
    if BackupCategory.USER_PROFILES in categories:
        values.update(display_name=restored.display_name, locale=restored.locale, layout_description=restored.layout_description)
    if BackupCategory.USER_INTERFACE in categories:
        values["ui_state"] = restored.ui_state
    if BackupCategory.USER_SETTINGS in categories:
        values.update(preferences=restored.preferences, appearance=restored.appearance)
    if BackupCategory.KEYBOARD_SHORTCUTS in categories:
        values["shortcuts"] = restored.shortcuts
    if BackupCategory.QUICK_ACCESS in categories:
        values["quick_access"] = restored.quick_access
    if BackupCategory.RECENT_FILES in categories:
        values["recent_files"] = restored.recent_files
    values["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    return replace(current, **values)


def _category_scope(category: BackupCategory) -> BackupScope:
    return (
        BackupScope.USER_ROAMING
        if category in PROFILE_CATEGORY_FILES
        else BackupScope.MACHINE_SHARED
    )


def _validate_resource_contract(entry: BackupResourceEntry) -> None:
    if entry.scope is not _category_scope(entry.category):
        raise BackupValidationError("Backup resource scope/category mismatch")
    expected_prefix = (
        "profiles"
        if entry.category is BackupCategory.USER_PROFILES
        else "user-settings"
        if entry.scope is BackupScope.USER_ROAMING
        else "machine-resources"
    )
    expected_container = (
        f"{expected_prefix}/{entry.category.value.lower()}/{entry.relative_path}"
    )
    if entry.container_path != expected_container:
        raise BackupValidationError("Backup container namespace mismatch")
    relative = PurePosixPath(entry.relative_path)
    if entry.category in PROFILE_CATEGORY_FILES:
        if (
            len(relative.parts) != 3
            or relative.parts[0] != "Profiles"
            or relative.parts[2] != PROFILE_CATEGORY_FILES[entry.category]
        ):
            raise BackupValidationError("Profile resource path/category mismatch")
        profile_id = _canonical_uuid(relative.parts[1])
        expected_dependencies = (
            ()
            if entry.category is BackupCategory.USER_PROFILES
            else (f"profile:{profile_id}",)
        )
        if entry.dependencies != expected_dependencies:
            raise BackupValidationError("Profile resource dependency mismatch")
        return
    kind = MACHINE_CATEGORY_KINDS[entry.category]
    expected_root = PROGRAM_DATA_CHILDREN[kind]
    if not relative.parts or relative.parts[0] != expected_root:
        raise BackupValidationError("Machine resource path/category mismatch")
    holder_path = "holder" in entry.relative_path.casefold()
    if (
        entry.category is BackupCategory.HOLDER_LIBRARY and not holder_path
        or entry.category is BackupCategory.TOOL_LIBRARY and holder_path
    ):
        raise BackupValidationError("Tool/Holder resource classification mismatch")
    if entry.dependencies:
        raise BackupValidationError("Unexpected machine resource dependency")


def _resource_version(payload: bytes) -> str:
    if not payload:
        return "unknown"
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return "unversioned"
    if isinstance(value, dict):
        for key in ("resource_version", "document_version", "schema_version", "format_version"):
            if key in value:
                return str(value[key])
    return "unversioned"


def _forbidden(path: Path) -> bool:
    folded_parts = {part.casefold() for part in path.parts}
    return (
        path.suffix.casefold() in FORBIDDEN_SUFFIXES
        or path.name.casefold() in FORBIDDEN_NAMES
        or bool(
            folded_parts
            & {
                ".locks",
                "cache",
                "logs",
                "temp",
                "crash",
                "autosave",
                "toolpaths",
                "incoming-geometry",
                "working-geometry",
            }
        )
        or any(token in path.name.casefold() for token in FORBIDDEN_TOKENS)
    )


def _json_file_contains_secret(path: Path) -> bool:
    if path.suffix.casefold() != ".json":
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False

    def contains(item: object) -> bool:
        if isinstance(item, dict):
            return any(
                any(token in str(key).casefold() for token in FORBIDDEN_TOKENS)
                or contains(child)
                for key, child in item.items()
            )
        if isinstance(item, list):
            return any(contains(child) for child in item)
        return False

    return contains(value)


def _validate_archive_path(value: str) -> None:
    if not value or "\\" in value or "\x00" in value:
        raise BackupValidationError("Archive path uses an invalid separator")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupValidationError("Archive path traversal or absolute path blocked")
    _validate_path_parts(path.parts)


def _validate_relative_resource_path(value: str) -> None:
    if not value or "\\" in value:
        raise BackupValidationError("Resource relative path is invalid")
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise BackupValidationError("Absolute resource path blocked")
    if any(part in {"", ".", ".."} for part in posix.parts):
        raise BackupValidationError("Resource path traversal blocked")
    _validate_path_parts(posix.parts)


def _validate_path_parts(parts: Iterable[str]) -> None:
    for part in parts:
        if part.rstrip(". ") != part:
            raise BackupValidationError("Trailing dot or space blocked")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise BackupValidationError("Reserved Windows name blocked")
        if any(character in '<>:"|?*' or ord(character) < 32 for character in part):
            raise BackupValidationError("Invalid Windows path character blocked")


def _write_zip_entry(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, ZIP_EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o600) << 16
    info.flag_bits |= 0x800
    archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)


def _canonical_uuid(value: str) -> str:
    try:
        parsed = UUID(str(value))
    except ValueError as exc:
        raise BackupValidationError("Profile ID is not a UUID") from exc
    return str(parsed)


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _merge_json_bytes(current: bytes, incoming: bytes) -> bytes:
    try:
        left = json.loads(current.decode("utf-8"))
        right = json.loads(incoming.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("MERGE requires JSON object resources") from exc
    if not isinstance(left, dict) or not isinstance(right, dict):
        raise BackupError("MERGE requires JSON object resources")
    # Safe merge adds incoming keys but keeps every current value on a key
    # conflict. Replacing a conflicting key requires an explicit REPLACE plan.
    return canonical_json_bytes({**right, **left})


__all__ = [
    "BACKUP_EXTENSION", "BACKUP_FORMAT_VERSION", "BACKUP_SCHEMA_VERSION",
    "BackupCancelled", "BackupCategory", "BackupCreationResult", "BackupError",
    "BackupInspection", "BackupLimits", "BackupManifest", "BackupResourceEntry",
    "BackupScope", "BackupSelectionModel", "BackupValidationError", "CategoryEstimate",
    "CompatibilityState", "ConflictAction", "HmsBackupService", "HmsRestoreService",
    "RestoreItemResult", "RestorePlan", "RestorePlanItem", "RestoreResult",
    "SelectionState", "ValidatedBackup",
]

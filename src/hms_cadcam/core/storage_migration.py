"""Non-destructive legacy storage scan, preview and verified copy foundation."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import hashlib
import os
from pathlib import Path
import shutil
from typing import Mapping
from uuid import uuid4

from hms_cadcam.core.paths import AppPathKind, ApplicationPathsService
from hms_cadcam.core.storage_security import validate_storage_write_path


class MigrationResourceType(StrEnum):
    TOOL_LIBRARY = "TOOL_LIBRARY"
    POSTS = "POSTS"
    MACHINES = "MACHINES"
    MATERIALS = "MATERIALS"
    MACHINE_CONFIG = "MACHINE_CONFIG"
    USER_CONFIG = "USER_CONFIG"
    UI_STATE = "UI_STATE"


class MigrationConflict(StrEnum):
    NONE = "NONE"
    DUPLICATE = "DUPLICATE"
    TARGET_EXISTS = "TARGET_EXISTS"
    UNSAFE_SOURCE = "UNSAFE_SOURCE"
    EXCLUDED_PROJECT_DATA = "EXCLUDED_PROJECT_DATA"


class MigrationAction(StrEnum):
    COPY = "COPY"
    SKIP = "SKIP"
    BLOCK = "BLOCK"


class MigrationStatus(StrEnum):
    PREVIEW = "PREVIEW"
    COPIED = "COPIED"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MigrationItem:
    source: Path
    target: Path
    resource_type: MigrationResourceType
    size: int
    checksum: str
    conflict: MigrationConflict
    action: MigrationAction
    status: MigrationStatus


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    items: tuple[MigrationItem, ...]
    scanned_file_count: int
    copy_count: int
    duplicate_count: int
    conflict_count: int
    project_data_excluded_count: int


TARGET_KINDS: Mapping[MigrationResourceType, AppPathKind] = {
    MigrationResourceType.TOOL_LIBRARY: AppPathKind.TOOL_LIBRARY,
    MigrationResourceType.POSTS: AppPathKind.POSTS,
    MigrationResourceType.MACHINES: AppPathKind.MACHINES,
    MigrationResourceType.MATERIALS: AppPathKind.MATERIALS,
    MigrationResourceType.MACHINE_CONFIG: AppPathKind.MACHINE_CONFIG,
    MigrationResourceType.USER_CONFIG: AppPathKind.USER_CONFIG,
    MigrationResourceType.UI_STATE: AppPathKind.USER_UI_STATE,
}
PROJECT_NAMES = frozenset(
    {
        "project.db",
        "project.hms.json",
        "autosave.hms.json",
    }
)
PROJECT_DIRECTORIES = frozenset(
    {"autosave", "cam", "nc", "toolpaths", "stock", "fixtures", "source"}
)


class LegacyMigrationService:
    """Copy allowed legacy resources after a typed preview; never delete source."""

    def __init__(self, paths: ApplicationPathsService) -> None:
        self.paths = paths

    def scan(
        self,
        locations: Mapping[MigrationResourceType, tuple[Path, ...]],
    ) -> MigrationPlan:
        items: list[MigrationItem] = []
        excluded = 0
        for resource, roots in locations.items():
            target_root = self.paths.path(TARGET_KINDS[MigrationResourceType(resource)])
            for source_root in roots:
                owner = Path(source_root)
                if owner.is_symlink():
                    candidates = (owner,)
                elif owner.is_file():
                    candidates = (owner,)
                elif owner.is_dir():
                    candidates = tuple(
                        path for path in owner.rglob("*") if path.is_file()
                    )
                else:
                    candidates = ()
                for source in candidates:
                    relative = source.name if owner.is_file() else str(source.relative_to(owner))
                    target = target_root / relative
                    if source.is_symlink():
                        items.append(
                            MigrationItem(
                                source,
                                target,
                                MigrationResourceType(resource),
                                0,
                                "",
                                MigrationConflict.UNSAFE_SOURCE,
                                MigrationAction.BLOCK,
                                MigrationStatus.BLOCKED,
                            )
                        )
                        continue
                    if _is_project_data(source):
                        excluded += 1
                        items.append(
                            MigrationItem(
                                source,
                                target,
                                MigrationResourceType(resource),
                                source.stat().st_size,
                                _sha256(source),
                                MigrationConflict.EXCLUDED_PROJECT_DATA,
                                MigrationAction.SKIP,
                                MigrationStatus.BLOCKED,
                            )
                        )
                        continue
                    checksum = _sha256(source)
                    if target.exists() or target.is_symlink():
                        duplicate = (
                            target.is_file()
                            and not target.is_symlink()
                            and _sha256(target) == checksum
                        )
                        conflict = (
                            MigrationConflict.DUPLICATE
                            if duplicate
                            else MigrationConflict.TARGET_EXISTS
                        )
                        action = MigrationAction.SKIP if duplicate else MigrationAction.BLOCK
                        status = MigrationStatus.PREVIEW if duplicate else MigrationStatus.BLOCKED
                    else:
                        conflict = MigrationConflict.NONE
                        action = MigrationAction.COPY
                        status = MigrationStatus.PREVIEW
                    items.append(
                        MigrationItem(
                            source=source,
                            target=target,
                            resource_type=MigrationResourceType(resource),
                            size=source.stat().st_size,
                            checksum=checksum,
                            conflict=conflict,
                            action=action,
                            status=status,
                        )
                    )
        return MigrationPlan(
            items=tuple(items),
            scanned_file_count=len(items),
            copy_count=sum(item.action is MigrationAction.COPY for item in items),
            duplicate_count=sum(item.conflict is MigrationConflict.DUPLICATE for item in items),
            conflict_count=sum(item.conflict is MigrationConflict.TARGET_EXISTS for item in items),
            project_data_excluded_count=excluded,
        )

    def execute(self, item: MigrationItem) -> MigrationItem:
        if item.action is not MigrationAction.COPY or item.conflict is not MigrationConflict.NONE:
            return replace(item, status=MigrationStatus.BLOCKED)
        target_root = self.paths.path(TARGET_KINDS[item.resource_type])
        validation = validate_storage_write_path(
            target_root,
            item.target,
            expect_directory=False,
        )
        if (
            not validation.safe
            or item.target.exists()
            or item.target.is_symlink()
            or _is_project_data(item.source)
        ):
            return replace(item, status=MigrationStatus.BLOCKED)
        if not item.source.is_file() or item.source.is_symlink():
            return replace(item, status=MigrationStatus.BLOCKED)
        if item.source.stat().st_size != item.size or _sha256(item.source) != item.checksum:
            return replace(item, status=MigrationStatus.FAILED)
        item.target.parent.mkdir(parents=True, exist_ok=True)
        staging = item.target.with_name(f".{item.target.name}.{uuid4().hex}.migration")
        try:
            with item.source.open("rb") as source_stream, staging.open("xb") as target_stream:
                shutil.copyfileobj(source_stream, target_stream)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            if _sha256(staging) != item.checksum:
                raise ValueError("Migration staging checksum mismatch")
            if item.target.exists() or item.target.is_symlink():
                return replace(item, status=MigrationStatus.BLOCKED)
            # On the supported Windows target, rename is atomic and refuses an
            # existing destination.  Unlike replace, it cannot overwrite data
            # created after the preview or validation checks.
            os.rename(staging, item.target)
            if _sha256(item.target) != item.checksum or not item.source.is_file():
                raise ValueError("Migration publish validation failed")
            return replace(item, status=MigrationStatus.VERIFIED)
        except (OSError, ValueError):
            staging.unlink(missing_ok=True)
            return replace(item, status=MigrationStatus.FAILED)
        finally:
            staging.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_project_data(path: Path) -> bool:
    return (
        path.name.casefold() in PROJECT_NAMES
        or path.suffix.casefold() == ".hms"
        or any(part.casefold() in PROJECT_DIRECTORIES for part in path.parts)
        or path.suffix.casefold() in {".nc", ".tap", ".gcode"}
    )


__all__ = [
    "LegacyMigrationService",
    "MigrationAction",
    "MigrationConflict",
    "MigrationItem",
    "MigrationPlan",
    "MigrationResourceType",
    "MigrationStatus",
]

"""Controlled maintenance operations confined to user-local storage."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from hms_cadcam.core.paths import AppPathKind, ApplicationPathsService
from hms_cadcam.core.storage_security import validate_storage_write_path

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CacheCleanupResult:
    removed_file_count: int
    removed_directory_count: int
    released_bytes: int
    blocked_paths: tuple[str, ...]


class UserStorageMaintenanceService:
    """Delete only validated cache entries; logs/temp/crash remain untouched."""

    def __init__(self, paths: ApplicationPathsService) -> None:
        self.paths = paths

    def clear_cache(self) -> CacheCleanupResult:
        cache_root = self.paths.path(AppPathKind.CACHE)
        if not cache_root.exists():
            return CacheCleanupResult(0, 0, 0, ())
        root_validation = validate_storage_write_path(
            self.paths.path(AppPathKind.USER_LOCAL_ROOT),
            cache_root,
            expect_directory=True,
        )
        if not root_validation.safe:
            return CacheCleanupResult(0, 0, 0, (str(cache_root),))
        files_removed = directories_removed = released = 0
        blocked: list[str] = []
        for candidate in sorted(cache_root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
            validation = validate_storage_write_path(
                cache_root,
                candidate,
                expect_directory=candidate.is_dir(),
            )
            if not validation.safe or candidate.is_symlink():
                blocked.append(str(candidate))
                continue
            try:
                if candidate.is_file():
                    size = candidate.stat().st_size
                    candidate.unlink()
                    files_removed += 1
                    released += size
                elif candidate.is_dir():
                    candidate.rmdir()
                    directories_removed += 1
            except OSError as exc:
                LOGGER.warning("Không thể dọn cache %s: %s", candidate, exc)
                blocked.append(str(candidate))
        return CacheCleanupResult(
            files_removed,
            directories_removed,
            released,
            tuple(blocked),
        )


__all__ = ["CacheCleanupResult", "UserStorageMaintenanceService"]

"""Versioned external cache for immutable SimulationResult payloads."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import OperationId, SimulationResultId, ToolpathArtifactId
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint

from .codec import dumps, loads_result
from .model import SimulationResult

logger = logging.getLogger(__name__)

SIMULATION_CACHE_FORMAT = "HMS_SIMULATION_CACHE_ENTRY"
SIMULATION_CACHE_VERSION = 1
SIMULATION_CACHE_DIRECTORY = Path("cache") / "simulation"
_METADATA_SUFFIX = ".metadata.json"
_PAYLOAD_SUFFIX = ".result.json"
_TEMP_SUFFIX = ".writing"
_MAX_STALE_ENTRIES_PER_OPERATION = 3


class SimulationCacheStatus(StrEnum):
    VALID = "valid"
    MISSING = "missing"
    STALE = "stale"
    CHECKSUM_MISMATCH = "checksum_mismatch"
    FUTURE_VERSION = "future_version"
    INVALID = "invalid"
    WRITE_FAILED = "write_failed"


class SimulationCacheError(RuntimeError):
    def __init__(self, status: SimulationCacheStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True, slots=True)
class SimulationCacheMetadata:
    cache_key: str
    project_id: UUID
    operation_id: OperationId
    artifact_id: ToolpathArtifactId
    artifact_fingerprint: ContentFingerprint
    input_fingerprint: DependencyFingerprint
    result_id: SimulationResultId
    result_fingerprint: ContentFingerprint
    payload_filename: str
    payload_size: int
    payload_sha256: str
    format: str = SIMULATION_CACHE_FORMAT
    format_version: int = SIMULATION_CACHE_VERSION

    def __post_init__(self) -> None:
        if self.format != SIMULATION_CACHE_FORMAT:
            raise UnsupportedCamSchemaError("Unsupported simulation cache format")
        if self.format_version != SIMULATION_CACHE_VERSION:
            raise UnsupportedCamSchemaError("Unsupported simulation cache version")
        for value, name in (
            (self.cache_key, "cache key"),
            (self.payload_sha256, "payload checksum"),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise CamValidationError(f"Simulation cache {name} is invalid")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("Simulation cache project identity is invalid")
        if not isinstance(self.operation_id, OperationId):
            raise CamValidationError("Simulation cache operation identity is invalid")
        if not isinstance(self.artifact_id, ToolpathArtifactId):
            raise CamValidationError("Simulation cache artifact identity is invalid")
        if not isinstance(self.result_id, SimulationResultId):
            raise CamValidationError("Simulation cache result identity is invalid")
        if not isinstance(self.artifact_fingerprint, ContentFingerprint):
            raise CamValidationError("Simulation cache artifact fingerprint is invalid")
        if not isinstance(self.input_fingerprint, DependencyFingerprint):
            raise CamValidationError("Simulation cache input fingerprint is invalid")
        if not isinstance(self.result_fingerprint, ContentFingerprint):
            raise CamValidationError("Simulation cache result fingerprint is invalid")
        if self.payload_filename != f"{self.cache_key}{_PAYLOAD_SUFFIX}":
            raise CamValidationError("Simulation cache payload filename is invalid")
        if type(self.payload_size) is not int or self.payload_size < 0:
            raise CamValidationError("Simulation cache payload size is invalid")
        if self.cache_key != _cache_key(
            self.project_id,
            self.operation_id,
            self.artifact_id,
            self.artifact_fingerprint,
            self.input_fingerprint,
            self.result_id,
            self.result_fingerprint,
        ):
            raise CamValidationError("Simulation cache key verification failed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "format_version": self.format_version,
            "cache_key": self.cache_key,
            "project_id": str(self.project_id),
            "operation_id": str(self.operation_id),
            "artifact_id": str(self.artifact_id),
            "artifact_fingerprint": self.artifact_fingerprint.to_dict(),
            "input_fingerprint": self.input_fingerprint.to_dict(),
            "result_id": str(self.result_id),
            "result_fingerprint": self.result_fingerprint.to_dict(),
            "payload_filename": self.payload_filename,
            "payload_size": self.payload_size,
            "payload_sha256": self.payload_sha256,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SimulationCacheMetadata":
        fields = {
            "format", "format_version", "cache_key", "project_id",
            "operation_id", "artifact_id", "artifact_fingerprint",
            "input_fingerprint", "result_id", "result_fingerprint",
            "payload_filename", "payload_size", "payload_sha256",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise CamValidationError("Simulation cache metadata is malformed")
        if value["format"] != SIMULATION_CACHE_FORMAT:
            raise UnsupportedCamSchemaError("Unsupported simulation cache format")
        if type(value["format_version"]) is not int:
            raise CamValidationError("Simulation cache version is invalid")
        if value["format_version"] != SIMULATION_CACHE_VERSION:
            raise UnsupportedCamSchemaError("Unsupported simulation cache version")
        return cls(
            cache_key=value["cache_key"],
            project_id=UUID(value["project_id"]),
            operation_id=OperationId.parse(value["operation_id"]),
            artifact_id=ToolpathArtifactId.parse(value["artifact_id"]),
            artifact_fingerprint=ContentFingerprint.from_dict(value["artifact_fingerprint"]),
            input_fingerprint=DependencyFingerprint.from_dict(value["input_fingerprint"]),
            result_id=SimulationResultId.parse(value["result_id"]),
            result_fingerprint=ContentFingerprint.from_dict(value["result_fingerprint"]),
            payload_filename=value["payload_filename"],
            payload_size=value["payload_size"],
            payload_sha256=value["payload_sha256"],
            format=value["format"],
            format_version=value["format_version"],
        )


@dataclass(frozen=True, slots=True)
class SimulationCacheLoad:
    status: SimulationCacheStatus
    result: SimulationResult | None = None
    metadata: SimulationCacheMetadata | None = None
    message: str | None = None


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _cache_key(
    project_id: UUID,
    operation_id: OperationId,
    artifact_id: ToolpathArtifactId,
    artifact_fingerprint: ContentFingerprint,
    input_fingerprint: DependencyFingerprint,
    result_id: SimulationResultId,
    result_fingerprint: ContentFingerprint,
) -> str:
    payload = {
        "project_id": str(project_id),
        "operation_id": str(operation_id),
        "artifact_id": str(artifact_id),
        "artifact_fingerprint": artifact_fingerprint.to_dict(),
        "input_fingerprint": input_fingerprint.to_dict(),
        "result_id": str(result_id),
        "result_fingerprint": result_fingerprint.to_dict(),
    }
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _operation_directory_name(operation_id: OperationId) -> str:
    return hashlib.sha256(str(operation_id).encode("ascii")).hexdigest()[:32]


class SimulationCacheStore:
    """Fail-closed cache I/O restricted to ``project/cache/simulation``."""

    def cache_root(self, project_root: Path) -> Path:
        return project_root / SIMULATION_CACHE_DIRECTORY

    def _existing_cache_root(self, project_root: Path) -> Path | None:
        """Return a real in-project cache root without following links/junctions."""
        cache_parent = project_root / "cache"
        cache_root = self.cache_root(project_root)
        try:
            if (
                not project_root.is_dir()
                or self._is_link(project_root)
                or not cache_parent.is_dir()
                or self._is_link(cache_parent)
                or not cache_root.is_dir()
                or self._is_link(cache_root)
            ):
                return None
            project_resolved = project_root.resolve(strict=True)
            cache_parent_resolved = cache_parent.resolve(strict=True)
            cache_root_resolved = cache_root.resolve(strict=True)
        except (OSError, RuntimeError):
            return None
        if (
            cache_parent_resolved.parent != project_resolved
            or cache_root_resolved.parent != cache_parent_resolved
        ):
            return None
        return cache_root

    def write(
        self,
        project_root: Path,
        project_id: UUID,
        result: SimulationResult,
    ) -> SimulationCacheMetadata:
        cache_root = self._ensure_cache_root(project_root)
        operation_root = cache_root / _operation_directory_name(result.operation_id)
        self._ensure_real_directory(operation_root, create=True)
        key = _cache_key(
            project_id,
            result.operation_id,
            result.artifact_id,
            result.artifact_fingerprint,
            result.input_fingerprint,
            result.result_id,
            result.result_fingerprint,
        )
        payload = dumps(result).encode("utf-8")
        metadata = SimulationCacheMetadata(
            cache_key=key,
            project_id=project_id,
            operation_id=result.operation_id,
            artifact_id=result.artifact_id,
            artifact_fingerprint=result.artifact_fingerprint,
            input_fingerprint=result.input_fingerprint,
            result_id=result.result_id,
            result_fingerprint=result.result_fingerprint,
            payload_filename=f"{key}{_PAYLOAD_SUFFIX}",
            payload_size=len(payload),
            payload_sha256=hashlib.sha256(payload).hexdigest(),
        )
        metadata_payload = _canonical_bytes(metadata.to_dict())
        payload_path = operation_root / metadata.payload_filename
        metadata_path = operation_root / f"{key}{_METADATA_SUFFIX}"
        try:
            self._atomic_write(payload_path, payload)
            self._atomic_write(metadata_path, metadata_payload)
            self._remove_superseded_current(operation_root, metadata)
            self.cleanup(project_root)
        except OSError as error:
            logger.warning("Không thể ghi simulation cache", exc_info=True)
            raise SimulationCacheError(
                SimulationCacheStatus.WRITE_FAILED,
                f"Simulation cache write failed: {error}",
            ) from error
        return metadata

    def load_current(
        self,
        project_root: Path,
        project_id: UUID,
        operation_id: OperationId,
        artifact_fingerprint: ContentFingerprint,
        input_fingerprint: DependencyFingerprint,
    ) -> SimulationCacheLoad:
        cache_root = self._existing_cache_root(project_root)
        if cache_root is None:
            return SimulationCacheLoad(
                SimulationCacheStatus.MISSING,
                message="cache missing or unsafe",
            )
        operation_root = cache_root / _operation_directory_name(operation_id)
        if not operation_root.is_dir() or self._is_link(operation_root):
            return SimulationCacheLoad(
                SimulationCacheStatus.MISSING,
                message="cache missing",
            )
        saw_entry = False
        saw_stale = False
        first_failure: SimulationCacheLoad | None = None
        for metadata_path in sorted(operation_root.glob(f"*{_METADATA_SUFFIX}")):
            saw_entry = True
            loaded = self._load_entry(metadata_path)
            if loaded.status is not SimulationCacheStatus.VALID:
                first_failure = first_failure or loaded
                continue
            assert loaded.metadata is not None and loaded.result is not None
            metadata = loaded.metadata
            if metadata.project_id != project_id or metadata.operation_id != operation_id:
                saw_stale = True
                continue
            if (
                metadata.artifact_fingerprint != artifact_fingerprint
                or metadata.input_fingerprint != input_fingerprint
            ):
                saw_stale = True
                continue
            return loaded
        if first_failure is not None:
            return first_failure
        if saw_stale or saw_entry:
            return SimulationCacheLoad(
                SimulationCacheStatus.STALE,
                message="cache stale",
            )
        return SimulationCacheLoad(
            SimulationCacheStatus.MISSING,
            message="cache missing",
        )

    def load_latest_for_source(
        self,
        project_root: Path,
        project_id: UUID,
        operation_id: OperationId,
        artifact_fingerprint: ContentFingerprint,
    ) -> SimulationCacheLoad:
        """Discover the newest valid policy/result for one current artifact."""
        cache_root = self._existing_cache_root(project_root)
        if cache_root is None:
            return SimulationCacheLoad(
                SimulationCacheStatus.MISSING,
                message="cache missing or unsafe",
            )
        operation_root = cache_root / _operation_directory_name(operation_id)
        if not operation_root.is_dir() or self._is_link(operation_root):
            return SimulationCacheLoad(
                SimulationCacheStatus.MISSING,
                message="cache missing",
            )
        try:
            paths = sorted(
                operation_root.glob(f"*{_METADATA_SUFFIX}"),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
                reverse=True,
            )
        except OSError as error:
            return SimulationCacheLoad(
                SimulationCacheStatus.INVALID,
                message=f"cache invalid: {error}",
            )
        first_failure: SimulationCacheLoad | None = None
        saw_stale = False
        for path in paths:
            loaded = self._load_entry(path)
            if loaded.status is not SimulationCacheStatus.VALID:
                first_failure = first_failure or loaded
                continue
            assert loaded.metadata is not None
            if (
                loaded.metadata.project_id == project_id
                and loaded.metadata.operation_id == operation_id
                and loaded.metadata.artifact_fingerprint == artifact_fingerprint
            ):
                return loaded
            saw_stale = True
        if first_failure is not None:
            return first_failure
        if saw_stale or paths:
            return SimulationCacheLoad(
                SimulationCacheStatus.STALE,
                message="cache stale",
            )
        return SimulationCacheLoad(
            SimulationCacheStatus.MISSING,
            message="cache missing",
        )

    def copy_valid_entries(
        self,
        source_root: Path,
        target_root: Path,
        source_project_id: UUID,
        target_project_id: UUID,
    ) -> tuple[SimulationCacheMetadata, ...]:
        source_cache = self._existing_cache_root(source_root)
        if source_cache is None:
            return ()
        copied: list[SimulationCacheMetadata] = []
        for metadata_path in sorted(source_cache.glob(f"*/*{_METADATA_SUFFIX}")):
            loaded = self._load_entry(metadata_path)
            if (
                loaded.status is not SimulationCacheStatus.VALID
                or loaded.metadata is None
                or loaded.result is None
                or loaded.metadata.project_id != source_project_id
            ):
                continue
            copied.append(self.write(target_root, target_project_id, loaded.result))
        return tuple(copied)

    def delete_operation(self, project_root: Path, operation_id: OperationId) -> None:
        existing_cache_root = self._existing_cache_root(project_root)
        if existing_cache_root is None:
            return
        operation_root = existing_cache_root / _operation_directory_name(operation_id)
        cache_root = existing_cache_root.resolve(strict=True)
        resolved = operation_root.resolve(strict=False)
        if resolved.parent != cache_root or not operation_root.is_dir() or self._is_link(operation_root):
            return
        for path in operation_root.iterdir():
            if path.is_file() and not self._is_link(path):
                path.unlink(missing_ok=True)
        try:
            operation_root.rmdir()
        except OSError:
            logger.warning("Không thể dọn simulation cache của operation %s", operation_id)

    def cleanup(
        self,
        project_root: Path,
        *,
        maximum_entries_per_operation: int = _MAX_STALE_ENTRIES_PER_OPERATION,
    ) -> None:
        if maximum_entries_per_operation < 1:
            raise ValueError("Simulation cache retention must be positive")
        cache_root = self._existing_cache_root(project_root)
        if cache_root is None:
            return
        for operation_root in cache_root.iterdir():
            if not operation_root.is_dir() or self._is_link(operation_root):
                continue
            for temporary in operation_root.glob(f"*{_TEMP_SUFFIX}"):
                if temporary.is_file() and not self._is_link(temporary):
                    temporary.unlink(missing_ok=True)
            metadata_paths = sorted(
                operation_root.glob(f"*{_METADATA_SUFFIX}"),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
                reverse=True,
            )
            for metadata_path in metadata_paths[maximum_entries_per_operation:]:
                loaded = self._load_metadata(metadata_path)
                if loaded is not None:
                    (operation_root / loaded.payload_filename).unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
            referenced_payloads = {
                loaded.payload_filename
                for path in metadata_paths[:maximum_entries_per_operation]
                if (loaded := self._load_metadata(path)) is not None
            }
            # A crash between payload fsync/replace and metadata replace can
            # leave an unreferenced payload.  It is derived data and safe to
            # remove on the next cache maintenance pass.
            for payload_path in operation_root.glob(f"*{_PAYLOAD_SUFFIX}"):
                if (
                    payload_path.name not in referenced_payloads
                    and payload_path.is_file()
                    and not self._is_link(payload_path)
                ):
                    payload_path.unlink(missing_ok=True)

    def _load_entry(self, metadata_path: Path) -> SimulationCacheLoad:
        try:
            if self._is_link(metadata_path) or not metadata_path.is_file():
                raise CamValidationError("Simulation cache metadata path is invalid")
            raw = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata = SimulationCacheMetadata.from_dict(raw)
            if metadata_path.name != f"{metadata.cache_key}{_METADATA_SUFFIX}":
                raise CamValidationError("Simulation cache metadata filename is invalid")
            payload_path = metadata_path.parent / metadata.payload_filename
            if self._is_link(payload_path) or not payload_path.is_file():
                return SimulationCacheLoad(
                    SimulationCacheStatus.MISSING,
                    metadata=metadata,
                    message="cache payload missing",
                )
            payload = payload_path.read_bytes()
            if (
                len(payload) != metadata.payload_size
                or hashlib.sha256(payload).hexdigest() != metadata.payload_sha256
            ):
                return SimulationCacheLoad(
                    SimulationCacheStatus.CHECKSUM_MISMATCH,
                    metadata=metadata,
                    message="cache checksum mismatch",
                )
            result = loads_result(payload.decode("utf-8"))
            if (
                result.operation_id != metadata.operation_id
                or result.artifact_id != metadata.artifact_id
                or result.artifact_fingerprint != metadata.artifact_fingerprint
                or result.input_fingerprint != metadata.input_fingerprint
                or result.result_id != metadata.result_id
                or result.result_fingerprint != metadata.result_fingerprint
            ):
                raise CamValidationError("Simulation cache provenance mismatch")
            return SimulationCacheLoad(
                SimulationCacheStatus.VALID,
                result=result,
                metadata=metadata,
            )
        except UnsupportedCamSchemaError as error:
            return SimulationCacheLoad(
                SimulationCacheStatus.FUTURE_VERSION,
                message=f"cache future version: {error}",
            )
        except (
            CamValidationError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
        ) as error:
            return SimulationCacheLoad(
                SimulationCacheStatus.INVALID,
                message=f"cache invalid: {error}",
            )

    def _load_metadata(self, path: Path) -> SimulationCacheMetadata | None:
        try:
            return SimulationCacheMetadata.from_dict(
                json.loads(path.read_text(encoding="utf-8"))
            )
        except (
            CamValidationError,
            UnsupportedCamSchemaError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            OSError,
        ):
            return None

    def _remove_superseded_current(
        self,
        operation_root: Path,
        current: SimulationCacheMetadata,
    ) -> None:
        for path in operation_root.glob(f"*{_METADATA_SUFFIX}"):
            if path.name == f"{current.cache_key}{_METADATA_SUFFIX}":
                continue
            metadata = self._load_metadata(path)
            if metadata is None:
                continue
            if (
                metadata.project_id == current.project_id
                and metadata.operation_id == current.operation_id
                and metadata.artifact_fingerprint == current.artifact_fingerprint
                and metadata.input_fingerprint == current.input_fingerprint
            ):
                (operation_root / metadata.payload_filename).unlink(missing_ok=True)
                path.unlink(missing_ok=True)

    def _ensure_cache_root(self, project_root: Path) -> Path:
        if not project_root.is_dir() or self._is_link(project_root):
            raise SimulationCacheError(
                SimulationCacheStatus.WRITE_FAILED,
                "Simulation project root is invalid",
            )
        cache_parent = project_root / "cache"
        self._ensure_real_directory(cache_parent, create=True)
        cache_root = cache_parent / "simulation"
        self._ensure_real_directory(cache_root, create=True)
        if cache_root.resolve().parent != cache_parent.resolve():
            raise SimulationCacheError(
                SimulationCacheStatus.WRITE_FAILED,
                "Simulation cache escaped the project root",
            )
        return cache_root

    @classmethod
    def _ensure_real_directory(cls, path: Path, *, create: bool) -> None:
        if create:
            path.mkdir(exist_ok=True)
        if cls._is_link(path) or not path.is_dir():
            raise SimulationCacheError(
                SimulationCacheStatus.WRITE_FAILED,
                f"Simulation cache directory is invalid: {path.name}",
            )

    @staticmethod
    def _is_link(path: Path) -> bool:
        return path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        )

    @staticmethod
    def _atomic_write(path: Path, payload: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}{_TEMP_SUFFIX}")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

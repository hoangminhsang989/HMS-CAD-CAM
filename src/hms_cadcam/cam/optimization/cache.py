"""Project-local, content-addressed multi-file calculation cache."""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Iterator
from uuid import uuid4


class CacheLookupStatus(StrEnum):
    HIT = "CACHE_HIT"
    MISS = "CACHE_MISS"
    STALE = "CACHE_STALE"
    CORRUPT = "CACHE_CORRUPT"
    INVALID = "CACHE_INVALID"


@dataclass(frozen=True, slots=True)
class CacheLookup:
    status: CacheLookupStatus
    payload: bytes | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class CacheManifest:
    format: str
    format_version: int
    artifact_path: str
    artifact_type: str
    operation_id: str
    phase: str
    fingerprint: str
    dependency_fingerprints: tuple[str, ...]
    size: int
    checksum_sha256: str
    state: str
    engine_version: str
    algorithm_version: str
    operation_references: tuple[str, ...]

    @classmethod
    def create(cls, *, artifact_path: str, artifact_type: str, operation_id: str, phase: str,
               fingerprint: str, dependency_fingerprints: tuple[str, ...], payload: bytes,
               state: str, engine_version: str, algorithm_version: str,
               operation_references: tuple[str, ...] = ()) -> "CacheManifest":
        return cls("HMS_R246_CAM_CACHE", 2, artifact_path, artifact_type, operation_id,
                   phase, fingerprint, dependency_fingerprints, len(payload),
                   hashlib.sha256(payload).hexdigest(), state, engine_version,
                   algorithm_version, tuple(sorted(operation_references)))


class CalculationArtifactStore:
    """Store phase artifacts beneath ``<project>/.hms/cam``.

    Reads verify every manifest and payload field. Any uncertainty is returned
    as a miss-like status so callers must recalculate safely.
    """

    def __init__(self) -> None:
        self._lease_lock = RLock()
        self._leased_paths: set[Path] = set()

    @contextmanager
    def lease(self, path: Path) -> Iterator[None]:
        """Protect one exact artifact path from in-process housekeeping."""
        resolved = path.resolve(strict=False)
        with self._lease_lock:
            self._leased_paths.add(resolved)
        try:
            yield
        finally:
            with self._lease_lock:
                self._leased_paths.discard(resolved)

    def is_leased(self, path: Path) -> bool:
        """Return whether one exact payload or manifest is actively owned."""
        resolved = path.resolve(strict=False)
        with self._lease_lock:
            return resolved in self._leased_paths

    def root(self, project_root: Path) -> Path:
        if not isinstance(project_root, Path) or not project_root.is_dir() or project_root.is_symlink():
            raise ValueError("Project root is invalid")
        return project_root / ".hms" / "cam"

    def _paths(self, project_root: Path, operation_id: str, phase: str, fingerprint: str) -> tuple[Path, Path]:
        if any(not isinstance(item, str) or not item or "/" in item or "\\" in item or item in {".", ".."} for item in (operation_id, phase, fingerprint)):
            raise ValueError("Cache identity is invalid")
        directory = self.root(project_root) / "operations" / operation_id / phase
        return directory / f"{fingerprint}.bin", directory / f"{fingerprint}.manifest.json"

    def _shared_paths(self, project_root: Path, phase: str, fingerprint: str) -> tuple[Path, Path]:
        if any(not isinstance(item, str) or not item or "/" in item or "\\" in item or item in {".", ".."}
               for item in (phase, fingerprint)):
            raise ValueError("Shared cache identity is invalid")
        directory = self.root(project_root) / "shared" / phase
        return directory / f"{fingerprint}.bin", directory / f"{fingerprint}.manifest.json"

    def publish(self, project_root: Path, *, operation_id: str, phase: str, fingerprint: str,
                payload: bytes, artifact_type: str = "phase", dependency_fingerprints: tuple[str, ...] = (),
                state: str = "COMPLETE", engine_version: str = "r246-v1",
                algorithm_version: str = "generic.v1") -> CacheManifest:
        if not isinstance(payload, bytes):
            raise TypeError("Cache payload must be bytes")
        payload_path, manifest_path = self._paths(project_root, operation_id, phase, fingerprint)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = CacheManifest.create(artifact_path=str(payload_path.relative_to(project_root).as_posix()),
            artifact_type=artifact_type, operation_id=operation_id, phase=phase, fingerprint=fingerprint,
            dependency_fingerprints=tuple(sorted(dependency_fingerprints)), payload=payload,
            state=state, engine_version=engine_version,
            algorithm_version=algorithm_version)
        payload_tmp = payload_path.with_name(f".{payload_path.name}.{uuid4().hex}.tmp")
        manifest_tmp = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
        try:
            for temp, data in ((payload_tmp, payload), (manifest_tmp, json.dumps(asdict(manifest), ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"))):
                with temp.open("xb") as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(payload_tmp, payload_path)
            os.replace(manifest_tmp, manifest_path)
        finally:
            payload_tmp.unlink(missing_ok=True); manifest_tmp.unlink(missing_ok=True)
        return manifest

    def publish_shared(self, project_root: Path, *, phase: str, fingerprint: str,
                       payload: bytes, dependency_fingerprints: tuple[str, ...] = (),
                       engine_version: str = "r246-v1",
                       algorithm_version: str = "generic.v1",
                       operation_references: tuple[str, ...] = ()) -> CacheManifest:
        """Atomically publish one content-addressed artifact shared by operations."""
        payload_path, manifest_path = self._shared_paths(project_root, phase, fingerprint)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = CacheManifest.create(
            artifact_path=str(payload_path.relative_to(project_root).as_posix()),
            artifact_type=f"shared.{phase}", operation_id="shared", phase=phase,
            fingerprint=fingerprint, dependency_fingerprints=tuple(sorted(dependency_fingerprints)),
            payload=payload, state="COMPLETE", engine_version=engine_version,
            algorithm_version=algorithm_version,
            operation_references=operation_references,
        )
        self._atomic_pair(payload_path, manifest_path, payload, manifest)
        return manifest

    def lookup_shared(self, project_root: Path, *, phase: str, fingerprint: str,
                      dependency_fingerprints: tuple[str, ...] = (),
                      engine_version: str = "r246-v1",
                      algorithm_version: str = "generic.v1") -> CacheLookup:
        payload_path, manifest_path = self._shared_paths(project_root, phase, fingerprint)
        return self._load_paths(payload_path, manifest_path, operation_id="shared", phase=phase,
                                fingerprint=fingerprint, dependency_fingerprints=dependency_fingerprints,
                                engine_version=engine_version,
                                algorithm_version=algorithm_version)

    def lookup(self, project_root: Path, *, operation_id: str, phase: str, fingerprint: str,
               dependency_fingerprints: tuple[str, ...] = (), engine_version: str = "r246-v1",
               algorithm_version: str = "generic.v1") -> CacheLookup:
        try:
            payload_path, manifest_path = self._paths(project_root, operation_id, phase, fingerprint)
            return self._load_paths(payload_path, manifest_path, operation_id=operation_id,
                                    phase=phase, fingerprint=fingerprint,
                                    dependency_fingerprints=dependency_fingerprints,
                                    engine_version=engine_version,
                                    algorithm_version=algorithm_version)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return CacheLookup(CacheLookupStatus.CORRUPT, message="cache metadata is malformed")

    def _load_paths(self, payload_path: Path, manifest_path: Path, *, operation_id: str,
                    phase: str, fingerprint: str, dependency_fingerprints: tuple[str, ...],
                    engine_version: str, algorithm_version: str) -> CacheLookup:
        try:
            with self.lease(payload_path), self.lease(manifest_path):
                return self._load_paths_leased(
                    payload_path, manifest_path, operation_id=operation_id,
                    phase=phase, fingerprint=fingerprint,
                    dependency_fingerprints=dependency_fingerprints,
                    engine_version=engine_version,
                    algorithm_version=algorithm_version,
                )
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return CacheLookup(CacheLookupStatus.CORRUPT, message="cache metadata is malformed")

    @staticmethod
    def _load_paths_leased(payload_path: Path, manifest_path: Path, *, operation_id: str,
                           phase: str, fingerprint: str,
                           dependency_fingerprints: tuple[str, ...],
                           engine_version: str, algorithm_version: str) -> CacheLookup:
        try:
            if not manifest_path.is_file() or not payload_path.is_file():
                return CacheLookup(CacheLookupStatus.MISS, message="cache entry missing")
            manifest = CacheManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
            payload = payload_path.read_bytes()
            if (manifest.format != "HMS_R246_CAM_CACHE" or manifest.format_version != 2 or
                    manifest.fingerprint != fingerprint or manifest.operation_id != operation_id or
                    manifest.phase != phase or manifest.engine_version != engine_version or
                    manifest.algorithm_version != algorithm_version):
                return CacheLookup(CacheLookupStatus.STALE, message="schema, fingerprint or engine mismatch")
            if tuple(manifest.dependency_fingerprints) != tuple(sorted(dependency_fingerprints)):
                return CacheLookup(CacheLookupStatus.STALE, message="dependency mismatch")
            if manifest.state != "COMPLETE":
                return CacheLookup(CacheLookupStatus.INVALID, message="artifact is not complete")
            if len(payload) != manifest.size or hashlib.sha256(payload).hexdigest() != manifest.checksum_sha256:
                return CacheLookup(CacheLookupStatus.CORRUPT, message="checksum mismatch")
            return CacheLookup(CacheLookupStatus.HIT, payload=payload)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return CacheLookup(CacheLookupStatus.CORRUPT, message="cache metadata is malformed")

    def retain_shared_reference(
        self, project_root: Path, *, phase: str, fingerprint: str, operation_id: str
    ) -> bool:
        """Persist one live operation reference without rewriting the payload."""
        if not operation_id:
            raise ValueError("Shared cache operation reference is invalid")
        payload_path, manifest_path = self._shared_paths(project_root, phase, fingerprint)
        try:
            with self.lease(payload_path), self.lease(manifest_path):
                manifest = CacheManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
                if (
                    not payload_path.is_file()
                    or manifest.format_version != 2
                    or manifest.state != "COMPLETE"
                    or manifest.operation_id != "shared"
                    or manifest.phase != phase
                    or manifest.fingerprint != fingerprint
                ):
                    return False
                references = tuple(sorted({*manifest.operation_references, operation_id}))
                if references == manifest.operation_references:
                    return True
                updated = CacheManifest(
                    **{**asdict(manifest), "operation_references": references}
                )
                self._atomic_manifest(manifest_path, updated)
                return True
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return False

    def release_operation_references(self, project_root: Path, operation_id: str) -> int:
        """Remove one deleted operation from every shared-artifact reference set."""
        if not operation_id:
            raise ValueError("Shared cache operation reference is invalid")
        root = self.root(project_root) / "shared"
        changed = 0
        for manifest_path in root.glob("*/*.manifest.json"):
            payload_path = manifest_path.with_suffix("").with_suffix(".bin")
            try:
                with self.lease(payload_path), self.lease(manifest_path):
                    manifest = CacheManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
                    references = tuple(
                        item for item in manifest.operation_references if item != operation_id
                    )
                    if references == manifest.operation_references:
                        continue
                    updated = CacheManifest(
                        **{**asdict(manifest), "operation_references": references}
                    )
                    self._atomic_manifest(manifest_path, updated)
                    changed += 1
            except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
                continue
        return changed

    @staticmethod
    def _atomic_manifest(manifest_path: Path, manifest: CacheManifest) -> None:
        temp = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
        try:
            encoded = json.dumps(
                asdict(manifest), ensure_ascii=True, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            with temp.open("xb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, manifest_path)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _atomic_pair(payload_path: Path, manifest_path: Path, payload: bytes,
                     manifest: CacheManifest) -> None:
        payload_tmp = payload_path.with_name(f".{payload_path.name}.{uuid4().hex}.tmp")
        manifest_tmp = manifest_path.with_name(f".{manifest_path.name}.{uuid4().hex}.tmp")
        try:
            encoded = json.dumps(asdict(manifest), ensure_ascii=True, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
            for temp, data in ((payload_tmp, payload), (manifest_tmp, encoded)):
                with temp.open("xb") as stream:
                    stream.write(data); stream.flush(); os.fsync(stream.fileno())
            os.replace(payload_tmp, payload_path)
            os.replace(manifest_tmp, manifest_path)
        finally:
            payload_tmp.unlink(missing_ok=True); manifest_tmp.unlink(missing_ok=True)

    def recover_abandoned_scratch(
        self, project_root: Path, *, minimum_age_seconds: float = 0.0
    ) -> int:
        """Remove only abandoned atomic-publication scratch files."""
        if minimum_age_seconds < 0.0:
            raise ValueError("Scratch recovery age must be non-negative")
        root = self.root(project_root)
        if not root.exists():
            return 0
        cutoff_ns = time.time_ns() - int(minimum_age_seconds * 1_000_000_000)
        removed = 0
        for path in root.rglob(".*.tmp"):
            try:
                if path.stat().st_mtime_ns > cutoff_ns or self.is_leased(path):
                    continue
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def cleanup(
        self,
        project_root: Path,
        *,
        max_bytes: int,
        max_age_seconds: float | None = None,
        live_operation_ids: frozenset[str] = frozenset(),
    ) -> int:
        if type(max_bytes) is not int or max_bytes < 0:
            raise ValueError("Cache quota must be non-negative")
        if max_age_seconds is not None and max_age_seconds < 0.0:
            raise ValueError("Cache maximum age must be non-negative")
        if not isinstance(live_operation_ids, frozenset) or any(
            not isinstance(item, str) or not item for item in live_operation_ids
        ):
            raise ValueError("Live operation identities are invalid")
        root = self.root(project_root)
        if not root.exists():
            return 0
        entries = []
        cutoff_ns = None if max_age_seconds is None else (
            time.time_ns() - int(max_age_seconds * 1_000_000_000)
        )
        for path in (*root.glob("operations/*/*/*.bin"), *root.glob("shared/*/*.bin")):
            try:
                stat = path.stat()
                entries.append((stat.st_atime_ns, stat.st_mtime_ns, stat.st_size, path))
            except OSError:
                continue
        total = sum(size for _, _, size, _ in entries)
        removed = 0
        for atime_ns, mtime_ns, size, path in sorted(entries):
            expired = cutoff_ns is not None and mtime_ns <= cutoff_ns
            if total <= max_bytes and not expired:
                continue
            manifest = path.with_suffix(".manifest.json")
            if self.is_leased(path) or self.is_leased(manifest):
                continue
            if "shared" in path.parts and live_operation_ids:
                try:
                    record = CacheManifest(**json.loads(manifest.read_text(encoding="utf-8")))
                    if set(record.operation_references) & set(live_operation_ids):
                        continue
                except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
                    pass
            path.unlink(missing_ok=True); manifest.unlink(missing_ok=True)
            total -= size; removed += 1
        return removed

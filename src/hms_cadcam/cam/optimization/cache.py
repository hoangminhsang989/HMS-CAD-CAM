"""Project-local, content-addressed multi-file calculation cache."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from enum import StrEnum
from pathlib import Path
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

    @classmethod
    def create(cls, *, artifact_path: str, artifact_type: str, operation_id: str, phase: str,
               fingerprint: str, dependency_fingerprints: tuple[str, ...], payload: bytes,
               state: str, engine_version: str) -> "CacheManifest":
        return cls("HMS_R246_CAM_CACHE", 1, artifact_path, artifact_type, operation_id,
                   phase, fingerprint, dependency_fingerprints, len(payload),
                   hashlib.sha256(payload).hexdigest(), state, engine_version)


class CalculationArtifactStore:
    """Store phase artifacts beneath ``<project>/.hms/cam``.

    Reads verify every manifest and payload field. Any uncertainty is returned
    as a miss-like status so callers must recalculate safely.
    """

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
                state: str = "COMPLETE", engine_version: str = "r246-v1") -> CacheManifest:
        if not isinstance(payload, bytes):
            raise TypeError("Cache payload must be bytes")
        payload_path, manifest_path = self._paths(project_root, operation_id, phase, fingerprint)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = CacheManifest.create(artifact_path=str(payload_path.relative_to(project_root).as_posix()),
            artifact_type=artifact_type, operation_id=operation_id, phase=phase, fingerprint=fingerprint,
            dependency_fingerprints=tuple(sorted(dependency_fingerprints)), payload=payload,
            state=state, engine_version=engine_version)
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
                       engine_version: str = "r246-v1") -> CacheManifest:
        """Atomically publish one content-addressed artifact shared by operations."""
        payload_path, manifest_path = self._shared_paths(project_root, phase, fingerprint)
        payload_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = CacheManifest.create(
            artifact_path=str(payload_path.relative_to(project_root).as_posix()),
            artifact_type=f"shared.{phase}", operation_id="shared", phase=phase,
            fingerprint=fingerprint, dependency_fingerprints=tuple(sorted(dependency_fingerprints)),
            payload=payload, state="COMPLETE", engine_version=engine_version,
        )
        self._atomic_pair(payload_path, manifest_path, payload, manifest)
        return manifest

    def lookup_shared(self, project_root: Path, *, phase: str, fingerprint: str,
                      dependency_fingerprints: tuple[str, ...] = (),
                      engine_version: str = "r246-v1") -> CacheLookup:
        payload_path, manifest_path = self._shared_paths(project_root, phase, fingerprint)
        return self._load_paths(payload_path, manifest_path, operation_id="shared", phase=phase,
                                fingerprint=fingerprint, dependency_fingerprints=dependency_fingerprints,
                                engine_version=engine_version)

    def lookup(self, project_root: Path, *, operation_id: str, phase: str, fingerprint: str,
               dependency_fingerprints: tuple[str, ...] = (), engine_version: str = "r246-v1") -> CacheLookup:
        try:
            payload_path, manifest_path = self._paths(project_root, operation_id, phase, fingerprint)
            return self._load_paths(payload_path, manifest_path, operation_id=operation_id,
                                    phase=phase, fingerprint=fingerprint,
                                    dependency_fingerprints=dependency_fingerprints,
                                    engine_version=engine_version)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            return CacheLookup(CacheLookupStatus.CORRUPT, message="cache metadata is malformed")

    def _load_paths(self, payload_path: Path, manifest_path: Path, *, operation_id: str,
                    phase: str, fingerprint: str, dependency_fingerprints: tuple[str, ...],
                    engine_version: str) -> CacheLookup:
        try:
            if not manifest_path.is_file() or not payload_path.is_file():
                return CacheLookup(CacheLookupStatus.MISS, message="cache entry missing")
            manifest = CacheManifest(**json.loads(manifest_path.read_text(encoding="utf-8")))
            payload = payload_path.read_bytes()
            if (manifest.format != "HMS_R246_CAM_CACHE" or manifest.format_version != 1 or
                    manifest.fingerprint != fingerprint or manifest.operation_id != operation_id or
                    manifest.phase != phase or manifest.engine_version != engine_version):
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

    def cleanup(self, project_root: Path, *, max_bytes: int) -> int:
        if type(max_bytes) is not int or max_bytes < 0:
            raise ValueError("Cache quota must be non-negative")
        root = self.root(project_root)
        if not root.exists():
            return 0
        entries = []
        for path in root.glob("operations/*/*/*.bin"):
            try:
                entries.append((path.stat().st_atime_ns, path.stat().st_size, path))
            except OSError:
                continue
        total = sum(size for _, size, _ in entries)
        removed = 0
        for _, size, path in sorted(entries):
            if total <= max_bytes:
                break
            manifest = path.with_suffix(".manifest.json")
            path.unlink(missing_ok=True); manifest.unlink(missing_ok=True)
            total -= size; removed += 1
        return removed

"""Path-safe atomic JSON store for derived Toolpath IR artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import uuid4

from hms_cadcam.cam.domain import ToolpathArtifactId
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import ToolpathArtifactMetadata
from hms_cadcam.cam.toolpath import ToolpathArtifact, artifact_from_dict, artifact_to_dict
MAX_TOOLPATH_ARTIFACT_BYTES = 128 * 1024 * 1024
TOOLPATHS_DIRECTORY = "toolpaths"
_SUFFIX = ".toolpath.json"
_ARTIFACT_FILENAME = re.compile(r"[0-9a-f]{32}\.toolpath\.json")
_STAGING_FILENAME = re.compile(r"\.staging-[0-9a-f]{32}\.tmp")


def _is_link_or_junction(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


class ToolpathArtifactStore:
    """Store only canonical files immediately below the project artifact root."""

    def publish(self, project_root: Path, artifact: ToolpathArtifact) -> ToolpathArtifactMetadata:
        if not isinstance(project_root, Path) or not isinstance(artifact, ToolpathArtifact):
            raise ToolpathArtifactStoreError("Artifact publish inputs are invalid")
        root = project_root / TOOLPATHS_DIRECTORY
        try:
            root.mkdir(exist_ok=True)
            self._require_real_root(root)
            relative_path = self.relative_path_for(artifact.artifact_id)
            target = project_root / Path(relative_path)
            if target.exists() and _is_link_or_junction(target):
                raise ToolpathArtifactStoreError("Artifact target cannot be a link or junction")
            payload = json.dumps(artifact_to_dict(artifact), allow_nan=False, ensure_ascii=False,
                                 separators=(",", ":"), sort_keys=True).encode("utf-8")
            if len(payload) > MAX_TOOLPATH_ARTIFACT_BYTES:
                raise ToolpathArtifactStoreError("Toolpath artifact exceeds size policy")
            # Artifact identifiers are content-derived application authority.
            # A second publisher may prove the exact same immutable bytes, but
            # it must never replace a pre-existing different object.
            if target.exists():
                existing = target.read_bytes()
                if existing != payload:
                    raise ToolpathArtifactStoreError(
                        "Artifact ID collision has different immutable bytes"
                    )
            else:
                temporary = root / f".staging-{uuid4().hex}.tmp"
                try:
                    with temporary.open("xb") as stream:
                        stream.write(payload)
                        stream.flush()
                        os.fsync(stream.fileno())
                    # Another writer can win only with the same bytes.  Do
                    # not use replace(), which would overwrite its authority.
                    try:
                        os.link(temporary, target)
                    except FileExistsError:
                        if target.read_bytes() != payload:
                            raise ToolpathArtifactStoreError(
                                "Artifact ID collision has different immutable bytes"
                            )
                finally:
                    temporary.unlink(missing_ok=True)
            digest = hashlib.sha256(payload).hexdigest()
            if artifact.artifact_fingerprint is None:
                raise ToolpathArtifactStoreError("Artifact content fingerprint is missing")
            metadata = ToolpathArtifactMetadata(artifact.artifact_id, artifact.source_operation_id,
                relative_path, digest, artifact.artifact_fingerprint, artifact.input_fingerprint,
                len(payload), artifact.schema_version, artifact.operation_revision,
                artifact.computation_token.generation, artifact.completion_status.value)
            # The caller supplies a fully validated immutable ToolpathArtifact.
            # Publication still proves durable exact bytes and checksum, while
            # avoiding a second O(events) typed reconstruction in this same
            # transaction. Fresh-context consumers continue through load().
            readback = target.read_bytes()
            if readback != payload or hashlib.sha256(readback).hexdigest() != digest:
                raise ToolpathArtifactStoreError("Toolpath artifact readback mismatch")
            return metadata
        except ToolpathArtifactStoreError:
            raise
        except (OSError, TypeError, ValueError) as error:
            raise ToolpathArtifactStoreError("Atomic toolpath artifact publish failed") from error

    def load(self, project_root: Path, metadata: ToolpathArtifactMetadata) -> ToolpathArtifact:
        path = self.resolve_metadata_path(project_root, metadata)
        try:
            if _is_link_or_junction(path) or not path.is_file():
                raise ToolpathArtifactStoreError("Toolpath artifact file is missing or unsafe")
            size = path.stat().st_size
            if size != metadata.size_bytes or size > MAX_TOOLPATH_ARTIFACT_BYTES:
                raise ToolpathArtifactStoreError("Toolpath artifact size mismatch")
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != metadata.checksum_sha256:
                raise ToolpathArtifactStoreError("Toolpath artifact checksum mismatch")
            data = json.loads(payload.decode("utf-8"),
                parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
            artifact = artifact_from_dict(data)
            if (artifact.artifact_id != metadata.artifact_id or
                    artifact.source_operation_id != metadata.operation_id or
                    artifact.artifact_fingerprint != metadata.artifact_fingerprint or
                    artifact.input_fingerprint != metadata.input_fingerprint or
                    artifact.schema_version != metadata.schema_version or
                    artifact.operation_revision != metadata.expected_operation_revision or
                    artifact.computation_token.generation != metadata.computation_generation or
                    artifact.completion_status.value != metadata.completion_status):
                raise ToolpathArtifactStoreError("Toolpath artifact metadata does not match content")
            return artifact
        except ToolpathArtifactStoreError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise ToolpathArtifactStoreError("Toolpath artifact is malformed") from error

    def copy_referenced(self, source_root: Path, destination_root: Path,
                        metadata_values: tuple[ToolpathArtifactMetadata, ...]) -> tuple[ToolpathArtifactMetadata, ...]:
        copied = []
        for metadata in metadata_values:
            copied.append(self.publish(destination_root, self.load(source_root, metadata)))
        return tuple(copied)

    def cleanup_orphans(self, project_root: Path, referenced: tuple[ToolpathArtifactMetadata, ...]) -> tuple[Path, ...]:
        root = project_root / TOOLPATHS_DIRECTORY
        if not root.exists():
            return ()
        self._require_real_root(root)
        keep = {self.resolve_metadata_path(project_root, item).name for item in referenced}
        removed: list[Path] = []
        for candidate in root.iterdir():
            if _is_link_or_junction(candidate) or not candidate.is_file():
                continue
            if _STAGING_FILENAME.fullmatch(candidate.name):
                candidate.unlink(missing_ok=True)
                removed.append(candidate)
            elif _ARTIFACT_FILENAME.fullmatch(candidate.name) and candidate.name not in keep:
                candidate.unlink(missing_ok=True)
                removed.append(candidate)
        return tuple(removed)

    @staticmethod
    def relative_path_for(artifact_id: ToolpathArtifactId) -> str:
        if not isinstance(artifact_id, ToolpathArtifactId):
            raise ToolpathArtifactStoreError("Artifact ID is invalid")
        return f"{TOOLPATHS_DIRECTORY}/{artifact_id.value.hex}{_SUFFIX}"

    def resolve_metadata_path(self, project_root: Path, metadata: ToolpathArtifactMetadata) -> Path:
        relative = metadata.relative_path
        if (not isinstance(relative, str) or "\\" in relative or ":" in relative or
                PurePosixPath(relative).is_absolute() or PureWindowsPath(relative).is_absolute() or
                PureWindowsPath(relative).drive):
            raise ToolpathArtifactStoreError("Artifact relative path is unsafe")
        parts = PurePosixPath(relative).parts
        expected_name = f"{metadata.artifact_id.value.hex}{_SUFFIX}"
        expected_relative = f"{TOOLPATHS_DIRECTORY}/{expected_name}"
        if (relative != expected_relative or len(parts) != 2 or
                parts[0] != TOOLPATHS_DIRECTORY or parts[1] != expected_name or ".." in parts):
            raise ToolpathArtifactStoreError("Artifact relative path is not canonical")
        root = project_root / TOOLPATHS_DIRECTORY
        self._require_real_root(root)
        candidate = root / expected_name
        if candidate.parent.resolve() != root.resolve():
            raise ToolpathArtifactStoreError("Artifact path escapes its root")
        return candidate

    @staticmethod
    def _require_real_root(root: Path) -> None:
        if _is_link_or_junction(root) or not root.is_dir():
            raise ToolpathArtifactStoreError("Toolpath artifact root must be a real directory")

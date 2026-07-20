"""Atomic filesystem persistence for project-managed production NC artifacts."""

from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

from hms_cadcam.cam.domain.ids import OperationId, PostResultId, ToolpathArtifactId
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.post.export_codec import json_bytes, loads
from hms_cadcam.cam.post.export_model import (
    ExportOverwritePolicy,
    NCArtifactManifest,
    NCArtifactManifestEntry,
    NCArtifactStatus,
    NCExportDiagnosticCode,
)
from hms_cadcam.cam.post.export_security import (
    NCExportSecurityError,
    contained_child,
    is_link_or_junction,
    reject_protected_target,
    require_real_directory,
)


POST_DIRECTORY = "post"
NC_DIRECTORY = "nc"
NC_METADATA_DIRECTORY = "metadata"
NC_MANIFEST_FILENAME = "manifest.json"
_TEMP_SUFFIX = ".hms-nc-exporting"


class NCArtifactStoreError(RuntimeError):
    """Stable storage failure used by the export service and project hooks."""

    def __init__(
        self,
        code: NCExportDiagnosticCode,
        message: str,
        *,
        managed: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.managed = managed


class NCArtifactStore:
    """Own post/manifest.json, post/metadata/, and nc/ below one HMS root."""

    def manifest_path(self, project_root: Path) -> Path:
        return project_root / POST_DIRECTORY / NC_MANIFEST_FILENAME

    def load(self, project_root: Path, project_id: UUID) -> NCArtifactManifest:
        """Strictly load the manifest and every referenced sidecar."""
        manifest_path = self.manifest_path(project_root)
        if not manifest_path.exists():
            return NCArtifactManifest(project_id)
        try:
            root = require_real_directory(project_root)
            post_root = require_real_directory(root / POST_DIRECTORY)
            metadata_root = require_real_directory(post_root / NC_METADATA_DIRECTORY)
            if manifest_path.parent.resolve(strict=True) != post_root:
                raise NCExportSecurityError(
                    NCExportDiagnosticCode.PATH_ESCAPE, "NC manifest escaped post root"
                )
            if is_link_or_junction(manifest_path) or not manifest_path.is_file():
                raise NCExportSecurityError(
                    NCExportDiagnosticCode.MANIFEST_INVALID, "NC manifest path is invalid"
                )
            decoded = loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(decoded, NCArtifactManifest):
                raise ValueError("NC manifest payload type is invalid")
            if decoded.project_id != project_id:
                raise ValueError("NC manifest belongs to another project")
            for entry in decoded.entries:
                sidecar = self._relative_file(root, entry.metadata_relative_path)
                if sidecar.parent.resolve(strict=True) != metadata_root:
                    raise NCExportSecurityError(
                        NCExportDiagnosticCode.PATH_ESCAPE,
                        "NC sidecar escaped metadata root",
                    )
                if is_link_or_junction(sidecar) or not sidecar.is_file():
                    raise NCArtifactStoreError(
                        NCExportDiagnosticCode.SIDECAR_INVALID,
                        f"NC sidecar is missing: {entry.metadata_relative_path}",
                    )
                sidecar_value = loads(sidecar.read_text(encoding="utf-8"))
                if not isinstance(sidecar_value, NCArtifactManifestEntry) or sidecar_value != entry:
                    raise NCArtifactStoreError(
                        NCExportDiagnosticCode.SIDECAR_INVALID,
                        f"NC sidecar differs from manifest: {entry.metadata_relative_path}",
                    )
            return decoded
        except NCArtifactStoreError:
            raise
        except NCExportSecurityError as error:
            raise NCArtifactStoreError(error.code, str(error)) from error
        except PermissionError as error:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.PERMISSION_DENIED, str(error)
            ) from error
        except Exception as error:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.MANIFEST_INVALID, "NC artifact manifest is invalid"
            ) from error

    def inspect(
        self,
        project_root: Path,
        project_id: UUID,
        *,
        current_post_results: tuple[PostResultId, ...] | None = None,
    ) -> NCArtifactManifest:
        """Classify missing/tampered/stale outputs without changing project files."""
        manifest = self.load(project_root, project_id)
        current_ids = None if current_post_results is None else set(current_post_results)
        inspected: list[NCArtifactManifestEntry] = []
        for entry in manifest.entries:
            status = entry.status
            try:
                output = self._relative_file(project_root, entry.output_relative_path)
                if is_link_or_junction(output) or not output.is_file():
                    status = NCArtifactStatus.MISSING
                else:
                    payload = output.read_bytes()
                    if len(payload) != entry.byte_length or hashlib.sha256(payload).hexdigest() != entry.sha256:
                        status = NCArtifactStatus.TAMPERED
                    elif current_ids is not None and entry.post_result_id not in current_ids:
                        status = NCArtifactStatus.STALE
            except (OSError, NCExportSecurityError):
                status = NCArtifactStatus.TAMPERED
            inspected.append(
                entry
                if status is entry.status
                else replace(entry, status=status, artifact_fingerprint=None)
            )
        return NCArtifactManifest(project_id, tuple(inspected))

    def publish(
        self,
        project_root: Path,
        entry: NCArtifactManifestEntry,
        payload: bytes,
        overwrite_policy: ExportOverwritePolicy,
    ) -> NCArtifactManifestEntry:
        """Atomically publish bytes, sidecar, then manifest with rollback on failure."""
        if not isinstance(payload, bytes) or not payload:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.INVALID_REQUEST, "NC payload must be non-empty bytes"
            )
        if len(payload) != entry.byte_length or hashlib.sha256(payload).hexdigest() != entry.sha256:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.CHECKSUM_MISMATCH,
                "NC payload does not match its artifact metadata",
            )
        root = self._project_root(project_root)
        created_directories: list[Path] = []
        touched: dict[Path, bytes | None] = {}
        try:
            post_root = self._ensure_child_directory(root, POST_DIRECTORY, created_directories)
            metadata_root = self._ensure_child_directory(
                post_root, NC_METADATA_DIRECTORY, created_directories
            )
            nc_root = self._ensure_child_directory(root, NC_DIRECTORY, created_directories)
            manifest_path = post_root / NC_MANIFEST_FILENAME
            manifest = (
                self.load(root, entry.project_id)
                if manifest_path.exists()
                else NCArtifactManifest(entry.project_id)
            )
            destination = self._relative_file(root, entry.output_relative_path)
            sidecar = self._relative_file(root, entry.metadata_relative_path)
            if destination.parent != nc_root or sidecar.parent != metadata_root:
                raise NCArtifactStoreError(
                    NCExportDiagnosticCode.PATH_ESCAPE,
                    "NC artifact layout escaped its managed directories",
                )
            existing_for_path = next(
                (
                    item
                    for item in manifest.entries
                    if item.output_relative_path.casefold()
                    == entry.output_relative_path.casefold()
                ),
                None,
            )
            self._validate_managed_overwrite(
                destination, entry, existing_for_path, overwrite_policy
            )
            candidate_entries: list[NCArtifactManifestEntry] = []
            stale_sidecars: list[tuple[Path, NCArtifactManifestEntry]] = []
            for old in manifest.entries:
                if old.output_relative_path.casefold() == entry.output_relative_path.casefold():
                    continue
                if old.operation_id == entry.operation_id and old.status is NCArtifactStatus.CURRENT:
                    stale = replace(old, status=NCArtifactStatus.STALE, artifact_fingerprint=None)
                    candidate_entries.append(stale)
                    stale_sidecars.append(
                        (self._relative_file(root, stale.metadata_relative_path), stale)
                    )
                else:
                    candidate_entries.append(old)
            candidate_entries.append(entry)
            candidate_manifest = NCArtifactManifest(entry.project_id, tuple(candidate_entries))
            paths_to_snapshot = [destination, sidecar, manifest_path]
            paths_to_snapshot.extend(path for path, _ in stale_sidecars)
            for path in paths_to_snapshot:
                touched[path] = path.read_bytes() if path.is_file() else None
            self._atomic_write_verified(destination, payload, expected_sha256=entry.sha256)
            self._atomic_write_verified(sidecar, json_bytes(entry))
            for stale_path, stale_entry in stale_sidecars:
                self._atomic_write_verified(stale_path, json_bytes(stale_entry))
            self._atomic_write_verified(manifest_path, json_bytes(candidate_manifest))
            verified = self.inspect(root, entry.project_id)
            published = next(
                (item for item in verified.entries if item.artifact_id == entry.artifact_id),
                None,
            )
            if published is None or published.status is not NCArtifactStatus.CURRENT:
                raise NCArtifactStoreError(
                    NCExportDiagnosticCode.CHECKSUM_MISMATCH,
                    "Published NC artifact did not pass read-back verification",
                )
            if existing_for_path is not None and existing_for_path.metadata_relative_path != entry.metadata_relative_path:
                old_sidecar = self._relative_file(root, existing_for_path.metadata_relative_path)
                if old_sidecar.is_file() and not is_link_or_junction(old_sidecar):
                    old_sidecar.unlink(missing_ok=True)
            return published
        except NCArtifactStoreError:
            self._rollback(touched)
            self._remove_empty(created_directories)
            raise
        except NCExportSecurityError as error:
            self._rollback(touched)
            self._remove_empty(created_directories)
            raise NCArtifactStoreError(error.code, str(error)) from error
        except PermissionError as error:
            self._rollback(touched)
            self._remove_empty(created_directories)
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.PERMISSION_DENIED, str(error)
            ) from error
        except OSError as error:
            self._rollback(touched)
            self._remove_empty(created_directories)
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.WRITE_FAILED, str(error)
            ) from error

    def export_external(
        self,
        managed_root: Path,
        entry: NCArtifactManifestEntry,
        target_root: Path,
        overwrite_policy: ExportOverwritePolicy,
        *,
        create_target_directory: bool = False,
    ) -> Path:
        """Copy verified managed bytes to one caller-supplied filesystem directory."""
        try:
            source = self._relative_file(managed_root, entry.output_relative_path)
            if is_link_or_junction(source) or not source.is_file():
                raise NCArtifactStoreError(
                    NCExportDiagnosticCode.MISSING,
                    "Project-managed NC artifact is missing",
                    managed=False,
                )
            payload = source.read_bytes()
            if len(payload) != entry.byte_length or hashlib.sha256(payload).hexdigest() != entry.sha256:
                raise NCArtifactStoreError(
                    NCExportDiagnosticCode.TAMPERED,
                    "Project-managed NC artifact is tampered",
                    managed=False,
                )
            root = require_real_directory(target_root, create=create_target_directory)
            reject_protected_target(root, managed_root)
            destination = contained_child(root, Path(entry.output_relative_path).name)
            if destination.exists():
                if overwrite_policy is ExportOverwritePolicy.FAIL_IF_EXISTS:
                    raise NCArtifactStoreError(
                        NCExportDiagnosticCode.FILE_EXISTS,
                        "External NC destination already exists",
                        managed=False,
                    )
                if overwrite_policy is ExportOverwritePolicy.REPLACE_IF_SAME_ARTIFACT:
                    raise NCArtifactStoreError(
                        NCExportDiagnosticCode.OVERWRITE_DENIED,
                        "External destinations do not carry HMS sidecars; explicit replace is required",
                        managed=False,
                    )
            self._atomic_write_verified(destination, payload, expected_sha256=entry.sha256)
            exported = destination.read_bytes()
            if len(exported) != entry.byte_length or hashlib.sha256(exported).hexdigest() != entry.sha256:
                raise NCArtifactStoreError(
                    NCExportDiagnosticCode.CHECKSUM_MISMATCH,
                    "External NC byte verification failed",
                    managed=False,
                )
            return destination
        except NCArtifactStoreError:
            raise
        except NCExportSecurityError as error:
            raise NCArtifactStoreError(error.code, str(error), managed=False) from error
        except PermissionError as error:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.PERMISSION_DENIED,
                str(error),
                managed=False,
            ) from error
        except OSError as error:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.WRITE_FAILED, str(error), managed=False
            ) from error

    def flush(self, project_root: Path, project_id: UUID) -> NCArtifactManifest:
        """Persist current missing/tampered/stale classifications without running Post."""
        manifest_path = self.manifest_path(project_root)
        if not manifest_path.exists():
            return NCArtifactManifest(project_id)
        inspected = self.inspect(project_root, project_id)
        previous = self.load(project_root, project_id)
        if inspected == previous:
            return inspected
        touched: dict[Path, bytes | None] = {}
        try:
            for entry in inspected.entries:
                sidecar = self._relative_file(project_root, entry.metadata_relative_path)
                touched[sidecar] = sidecar.read_bytes() if sidecar.is_file() else None
                self._atomic_write_verified(sidecar, json_bytes(entry))
            touched[manifest_path] = manifest_path.read_bytes()
            self._atomic_write_verified(manifest_path, json_bytes(inspected))
            return self.load(project_root, project_id)
        except Exception:
            self._rollback(touched)
            raise

    def mark_operation_stale(
        self, project_root: Path, project_id: UUID, operation_id: OperationId
    ) -> NCArtifactManifest:
        """Mark managed artifacts stale without deleting local or external output."""
        manifest_path = self.manifest_path(project_root)
        if not manifest_path.exists():
            return NCArtifactManifest(project_id)
        manifest = self.load(project_root, project_id)
        changed = tuple(
            replace(item, status=NCArtifactStatus.STALE, artifact_fingerprint=None)
            if item.operation_id == operation_id and item.status is NCArtifactStatus.CURRENT
            else item
            for item in manifest.entries
        )
        candidate = NCArtifactManifest(project_id, changed)
        if candidate == manifest:
            return manifest
        return self._write_manifest_and_sidecars(project_root, candidate)

    def remove_operation_artifact(
        self, project_root: Path, project_id: UUID, operation_id: OperationId
    ) -> NCArtifactManifest:
        """Explicitly remove managed files for one operation.

        Only files inside the HMS ``nc/`` and ``post/metadata/`` roots are
        touched.  External export targets are deliberately never inspected or
        deleted by this lifecycle operation.
        """
        root = self._project_root(project_root)
        manifest_path = self.manifest_path(root)
        if not manifest_path.exists():
            return NCArtifactManifest(project_id)
        try:
            manifest = self.load(root, project_id)
            removed = tuple(item for item in manifest.entries if item.operation_id == operation_id)
            if not removed:
                return manifest
            remaining = NCArtifactManifest(
                project_id,
                tuple(item for item in manifest.entries if item.operation_id != operation_id),
            )
            # Publish the new manifest/sidecars before deleting payloads so a
            # failed delete leaves a recoverable, manifest-consistent state.
            published = self._write_manifest_and_sidecars(root, remaining)
            for entry in removed:
                for relative in (entry.output_relative_path, entry.metadata_relative_path):
                    path = self._relative_file(root, relative)
                    if is_link_or_junction(path):
                        raise NCArtifactStoreError(
                            NCExportDiagnosticCode.PATH_ESCAPE,
                            "Managed NC artifact path is a link or junction",
                        )
                    if path.is_file():
                        path.unlink()
            return published
        except NCArtifactStoreError:
            raise
        except PermissionError as error:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.PERMISSION_DENIED,
                "Managed NC artifact could not be cleared",
            ) from error
        except OSError as error:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.WRITE_FAILED,
                "Managed NC artifact could not be cleared",
            ) from error

    def reconcile_sources(
        self,
        project_root: Path,
        project_id: UUID,
        current: dict[OperationId, tuple[ToolpathArtifactId, ContentFingerprint]],
    ) -> NCArtifactManifest:
        """Mark entries stale when an operation disappeared or its Toolpath changed."""
        manifest_path = self.manifest_path(project_root)
        if not manifest_path.exists():
            return NCArtifactManifest(project_id)
        manifest = self.load(project_root, project_id)
        changed: list[NCArtifactManifestEntry] = []
        for item in manifest.entries:
            source = current.get(item.operation_id)
            stale = source is None or source != (
                item.source_artifact_id,
                item.source_artifact_fingerprint,
            )
            changed.append(
                replace(item, status=NCArtifactStatus.STALE, artifact_fingerprint=None)
                if stale and item.status is NCArtifactStatus.CURRENT
                else item
            )
        candidate = NCArtifactManifest(project_id, tuple(changed))
        return manifest if candidate == manifest else self._write_manifest_and_sidecars(project_root, candidate)

    def copy_workspace(
        self,
        source_root: Path,
        target_root: Path,
        source_project_id: UUID,
        target_project_id: UUID,
    ) -> NCArtifactManifest:
        """Copy valid managed output to Save As/autosave/recovery workspace."""
        if not self.manifest_path(source_root).exists():
            return NCArtifactManifest(target_project_id)
        source = self.inspect(source_root, source_project_id)
        valid_entries = tuple(
            item
            for item in source.entries
            if item.status not in {NCArtifactStatus.MISSING, NCArtifactStatus.TAMPERED}
        )
        if not valid_entries:
            return NCArtifactManifest(target_project_id)
        root = self._project_root(target_root)
        created: list[Path] = []
        touched: dict[Path, bytes | None] = {}
        try:
            post_root = self._ensure_child_directory(root, POST_DIRECTORY, created)
            metadata_root = self._ensure_child_directory(post_root, NC_METADATA_DIRECTORY, created)
            nc_root = self._ensure_child_directory(root, NC_DIRECTORY, created)
            copied: list[NCArtifactManifestEntry] = []
            seen_outputs: set[str] = set()
            for old in valid_entries:
                key = old.output_relative_path.casefold()
                if key in seen_outputs:
                    continue
                seen_outputs.add(key)
                source_output = self._relative_file(source_root, old.output_relative_path)
                payload = source_output.read_bytes()
                if len(payload) != old.byte_length or hashlib.sha256(payload).hexdigest() != old.sha256:
                    raise NCArtifactStoreError(
                        NCExportDiagnosticCode.CHECKSUM_MISMATCH,
                        "NC workspace source changed during copy",
                    )
                status = (
                    NCArtifactStatus.STALE
                    if source_project_id != target_project_id
                    else old.status
                )
                entry = replace(
                    old,
                    project_id=target_project_id,
                    status=status,
                    artifact_fingerprint=None,
                )
                output = self._relative_file(root, entry.output_relative_path)
                sidecar = self._relative_file(root, entry.metadata_relative_path)
                if output.parent != nc_root or sidecar.parent != metadata_root:
                    raise NCArtifactStoreError(
                        NCExportDiagnosticCode.PATH_ESCAPE,
                        "NC workspace artifact escaped managed layout",
                    )
                touched[output] = output.read_bytes() if output.is_file() else None
                touched[sidecar] = sidecar.read_bytes() if sidecar.is_file() else None
                self._atomic_write_verified(output, payload, expected_sha256=entry.sha256)
                self._atomic_write_verified(sidecar, json_bytes(entry))
                copied.append(entry)
            manifest = NCArtifactManifest(target_project_id, tuple(copied))
            manifest_path = post_root / NC_MANIFEST_FILENAME
            touched[manifest_path] = (
                manifest_path.read_bytes() if manifest_path.is_file() else None
            )
            self._atomic_write_verified(manifest_path, json_bytes(manifest))
            return self.inspect(root, target_project_id)
        except Exception:
            self._rollback(touched)
            self._remove_empty(created)
            raise

    def _write_manifest_and_sidecars(
        self, project_root: Path, manifest: NCArtifactManifest
    ) -> NCArtifactManifest:
        touched: dict[Path, bytes | None] = {}
        manifest_path = self.manifest_path(project_root)
        try:
            for entry in manifest.entries:
                sidecar = self._relative_file(project_root, entry.metadata_relative_path)
                touched[sidecar] = sidecar.read_bytes() if sidecar.is_file() else None
                self._atomic_write_verified(sidecar, json_bytes(entry))
            touched[manifest_path] = manifest_path.read_bytes() if manifest_path.is_file() else None
            self._atomic_write_verified(manifest_path, json_bytes(manifest))
            return self.load(project_root, manifest.project_id)
        except Exception:
            self._rollback(touched)
            raise

    @staticmethod
    def _validate_managed_overwrite(
        destination: Path,
        entry: NCArtifactManifestEntry,
        existing: NCArtifactManifestEntry | None,
        policy: ExportOverwritePolicy,
    ) -> None:
        if not destination.exists():
            return
        if policy is ExportOverwritePolicy.FAIL_IF_EXISTS:
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.FILE_EXISTS,
                "Project-managed NC destination already exists",
            )
        if policy is ExportOverwritePolicy.REPLACE_EXPLICIT:
            return
        if (
            existing is None
            or existing.artifact_id != entry.artifact_id
            or existing.project_id != entry.project_id
            or existing.operation_id != entry.operation_id
            or existing.production_profile_id != entry.production_profile_id
            or existing.production_profile_fingerprint
            != entry.production_profile_fingerprint
        ):
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.OVERWRITE_DENIED,
                "Existing NC file is not the same HMS-managed artifact",
            )

    @staticmethod
    def _project_root(project_root: Path) -> Path:
        try:
            return require_real_directory(project_root)
        except NCExportSecurityError as error:
            raise NCArtifactStoreError(error.code, str(error)) from error

    @staticmethod
    def _ensure_child_directory(
        parent: Path, name: str, created: list[Path]
    ) -> Path:
        path = parent / name
        if not path.exists():
            path.mkdir()
            created.append(path)
        if is_link_or_junction(path) or not path.is_dir():
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.PATH_ESCAPE,
                f"Managed NC directory is unsafe: {name}",
            )
        if path.resolve(strict=True).parent != parent.resolve(strict=True):
            raise NCArtifactStoreError(
                NCExportDiagnosticCode.PATH_ESCAPE,
                f"Managed NC directory escaped project root: {name}",
            )
        return path

    @staticmethod
    def _relative_file(root: Path, relative_path: str) -> Path:
        parts = relative_path.split("/")
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise NCExportSecurityError(
                NCExportDiagnosticCode.PATH_ESCAPE, "Managed NC relative path is invalid"
            )
        candidate = root.joinpath(*parts)
        resolved_root = root.resolve(strict=True)
        try:
            candidate.resolve(strict=False).relative_to(resolved_root)
        except ValueError as error:
            raise NCExportSecurityError(
                NCExportDiagnosticCode.PATH_ESCAPE, "Managed NC path escaped project root"
            ) from error
        current = candidate.parent
        while current != resolved_root:
            if current.exists() and is_link_or_junction(current):
                raise NCExportSecurityError(
                    NCExportDiagnosticCode.PATH_ESCAPE,
                    "Managed NC path crosses a symlink or junction",
                )
            current = current.parent
        return candidate

    @staticmethod
    def _atomic_write_verified(
        destination: Path,
        payload: bytes,
        *,
        expected_sha256: str | None = None,
    ) -> None:
        temporary = destination.with_name(
            f".{destination.name}.{uuid4().hex}{_TEMP_SUFFIX}"
        )
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            candidate = temporary.read_bytes()
            expected = expected_sha256 or hashlib.sha256(payload).hexdigest()
            if len(candidate) != len(payload) or hashlib.sha256(candidate).hexdigest() != expected:
                raise NCArtifactStoreError(
                    NCExportDiagnosticCode.CHECKSUM_MISMATCH,
                    "Temporary NC file failed byte verification",
                )
            try:
                os.replace(temporary, destination)
            except OSError as error:
                raise NCArtifactStoreError(
                    NCExportDiagnosticCode.ATOMIC_REPLACE_FAILED, str(error)
                ) from error
            published = destination.read_bytes()
            if len(published) != len(payload) or hashlib.sha256(published).hexdigest() != expected:
                raise NCArtifactStoreError(
                    NCExportDiagnosticCode.CHECKSUM_MISMATCH,
                    "Published NC file failed byte verification",
                )
        finally:
            temporary.unlink(missing_ok=True)

    def _rollback(self, touched: dict[Path, bytes | None]) -> None:
        for path, previous in reversed(tuple(touched.items())):
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    self._atomic_write_verified(path, previous)
            except (OSError, NCArtifactStoreError):
                # The original failure remains authoritative; recovery on next Open
                # will classify any mismatch as missing/tampered.
                continue

    @staticmethod
    def _remove_empty(paths: list[Path]) -> None:
        for path in reversed(paths):
            try:
                path.rmdir()
            except OSError:
                continue

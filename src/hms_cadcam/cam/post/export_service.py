"""Latest-wins runtime pipeline from validated PostResult to NC file artifacts."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Callable
from uuid import UUID, uuid4, uuid5

from hms_cadcam.cam.domain.ids import (
    NCArtifactId,
    NCExportResultId,
    OperationId,
    PostResultId,
    ProgramAssemblyResultId,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import DependencyFingerprint
from hms_cadcam.cam.post.export_model import (
    ExportTarget,
    NCAssemblyExportRequest,
    NCArtifactManifest,
    NCArtifactManifestEntry,
    NCArtifactStatus,
    NCExportDiagnostic,
    NCExportDiagnosticCode,
    NCExportRequest,
    NCExportResult,
    NCExportStatistics,
    NCExportStatus,
)
from hms_cadcam.cam.post.export_security import (
    NCExportSecurityError,
    sanitize_export_filename,
)
from hms_cadcam.cam.post.export_store import NCArtifactStore, NCArtifactStoreError
from hms_cadcam.cam.post.lowering import PostSourceSnapshot, validate_post_source
from hms_cadcam.cam.post.model import PostRequest, PostResult, PostResultStatus
from hms_cadcam.cam.post.service import build_post_input_fingerprint
from hms_cadcam.cam.post.assembly_model import (
    ProgramAssemblyRequest,
    ProgramAssemblyResult,
    ProgramAssemblyStatus,
)


_ARTIFACT_NAMESPACE = UUID("2792175b-a984-5c1c-8dd1-7d2200000001")


@dataclass(frozen=True, slots=True)
class NCExportSourceSnapshot:
    """Runtime-only source snapshot; callbacks and paths are never persisted."""

    project_generation: int
    post_request: PostRequest
    post_result: PostResult
    source: PostSourceSnapshot

    def __post_init__(self) -> None:
        if type(self.project_generation) is not int or self.project_generation < 0:
            raise ValueError("NC export project generation is invalid")
        if not isinstance(self.post_request, PostRequest):
            raise TypeError("NC export post request is invalid")
        if not isinstance(self.post_result, PostResult):
            raise TypeError("NC export post result is invalid")
        if not isinstance(self.source, PostSourceSnapshot):
            raise TypeError("NC export source snapshot is invalid")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(
            {
                "project_generation": self.project_generation,
                "post_request": self.post_request.input_policy_fingerprint.to_dict(),
                "post_input": build_post_input_fingerprint(
                    self.post_request, self.source
                ).to_dict(),
                "post_result_id": str(self.post_result.result_id),
                "post_result_fingerprint": self.post_result.result_fingerprint.to_dict(),
            }
        )


@dataclass(frozen=True, slots=True)
class NCAssemblyExportSourceSnapshot:
    """Runtime-only export source for one published multi-operation result."""

    project_generation: int
    post_request: ProgramAssemblyRequest
    assembly_result: ProgramAssemblyResult

    def __post_init__(self) -> None:
        if type(self.project_generation) is not int or self.project_generation < 0:
            raise ValueError("NC assembly export project generation is invalid")
        if not isinstance(self.post_request, ProgramAssemblyRequest):
            raise TypeError("NC assembly export request is invalid")
        if not isinstance(self.assembly_result, ProgramAssemblyResult):
            raise TypeError("NC assembly export result is invalid")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        from hms_cadcam.cam.post.assembly_service import (
            build_assembly_input_fingerprint,
        )

        return DependencyFingerprint.from_payload(
            {
                "project_generation": self.project_generation,
                "assembly_request": build_assembly_input_fingerprint(
                    self.post_request
                ).to_dict(),
                "assembly_result_id": str(self.assembly_result.result_id),
                "assembly_result_fingerprint": self.assembly_result.result_fingerprint.to_dict(),
            }
        )


@dataclass(frozen=True, slots=True)
class NCExportToken:
    value: UUID
    generation: int
    request_fingerprint: DependencyFingerprint
    source_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class NCExportExecution:
    accepted: bool
    result: NCExportResult | None
    artifact: NCArtifactManifestEntry | None
    diagnostics: tuple[NCExportDiagnostic, ...]
    status: NCExportStatus


class NCExportService:
    """Validate, persist, verify, and optionally copy one production PostResult."""

    def __init__(self, store: NCArtifactStore | None = None) -> None:
        self._store = store or NCArtifactStore()
        self._lock = RLock()
        self._generation = 0
        self._latest: dict[tuple[UUID, object], NCExportToken] = {}
        self._results: dict[tuple[UUID, object], NCExportResult] = {}
        self._manifest: NCArtifactManifest | None = None
        self._bound_root: Path | None = None
        self._bound_project_id: UUID | None = None
        self._bound_project_generation: int | None = None
        self._load_diagnostics: tuple[NCExportDiagnostic, ...] = ()

    @property
    def store(self) -> NCArtifactStore:
        return self._store

    @property
    def load_diagnostics(self) -> tuple[NCExportDiagnostic, ...]:
        with self._lock:
            return self._load_diagnostics

    def bind_project(
        self,
        project_root: Path | None,
        project_id: UUID | None,
        project_generation: int | None,
        *,
        current_post_result_ids: tuple[object, ...] | None = None,
    ) -> NCArtifactManifest | None:
        """Invalidate old tokens and inspect one project's persisted NC artifacts."""
        with self._lock:
            self._generation += 1
            self._latest.clear()
            self._results.clear()
            self._manifest = None
            self._load_diagnostics = ()
            self._bound_root = project_root
            self._bound_project_id = project_id
            self._bound_project_generation = project_generation
        if project_root is None or project_id is None:
            return None
        try:
            manifest = self._store.inspect(
                project_root,
                project_id,
                current_post_results=current_post_result_ids,  # type: ignore[arg-type]
            )
        except NCArtifactStoreError as error:
            diagnostic = _diagnostic(error.code, "export.open_manifest_invalid")
            with self._lock:
                self._load_diagnostics = (diagnostic,)
            return None
        with self._lock:
            self._manifest = manifest
        return manifest

    def begin(
        self, request: NCExportRequest, source: NCExportSourceSnapshot
    ) -> NCExportToken:
        with self._lock:
            self._generation += 1
            token = NCExportToken(
                uuid4(), self._generation, request.fingerprint, source.fingerprint
            )
            self._latest[(request.project_id, request.operation_id)] = token
            return token

    def current(self, project_id: UUID, operation_id: object) -> NCExportResult | None:
        with self._lock:
            return self._results.get((project_id, operation_id))

    def artifacts(self) -> tuple[NCArtifactManifestEntry, ...]:
        with self._lock:
            return self._manifest.entries if self._manifest else ()

    def invalidate_all(self) -> None:
        with self._lock:
            self._generation += 1
            self._latest.clear()
            self._results.clear()

    def mark_operation_stale(self, operation_id: object) -> None:
        with self._lock:
            keys = tuple(key for key in self._results if key[1] == operation_id)
            for key in keys:
                self._results.pop(key, None)
                self._latest.pop(key, None)
            root = self._bound_root
            project_id = self._bound_project_id
        if root is not None and project_id is not None:
            try:
                manifest = self._store.mark_operation_stale(
                    root, project_id, operation_id  # type: ignore[arg-type]
                )
            except NCArtifactStoreError:
                return
            with self._lock:
                self._manifest = manifest

    def clear_managed_artifact(self, project_root: Path, project_id: UUID, operation_id: object) -> NCArtifactManifest:
        """Explicitly remove one project-managed artifact and its sidecar.

        The operation is intentionally separate from ``mark_operation_stale``;
        callers must invoke it as an explicit destructive UI action.  Runtime
        PostResult and external files are unaffected.
        """
        if not isinstance(operation_id, OperationId):
            raise ValueError("Operation identity is invalid")
        with self._lock:
            project_bound = self._bound_project_id
            root_bound = self._bound_root
        if project_bound is not None and project_bound != project_id:
            raise ValueError("Managed artifact belongs to another project")
        if root_bound is not None and root_bound.resolve() != project_root.resolve():
            raise ValueError("Managed artifact root belongs to another project")
        manifest = self._store.remove_operation_artifact(project_root, project_id, operation_id)
        with self._lock:
            self._results.pop((project_id, operation_id), None)
            self._latest.pop((project_id, operation_id), None)
            self._manifest = manifest
        return manifest

    def clear_managed_assembly_artifact(
        self,
        project_root: Path,
        project_id: UUID,
        assembly_result_id: ProgramAssemblyResultId,
    ) -> NCArtifactManifest:
        """Explicitly remove one assembly artifact without touching external files."""
        if not isinstance(assembly_result_id, ProgramAssemblyResultId):
            raise ValueError("Assembly result identity is invalid")
        with self._lock:
            project_bound = self._bound_project_id
            root_bound = self._bound_root
        if project_bound is not None and project_bound != project_id:
            raise ValueError("Managed assembly artifact belongs to another project")
        if root_bound is not None and root_bound.resolve() != project_root.resolve():
            raise ValueError("Managed assembly artifact root belongs to another project")
        manifest = self._store.remove_assembly_artifact(
            project_root, project_id, assembly_result_id
        )
        with self._lock:
            self._results.pop((project_id, assembly_result_id), None)
            self._latest.pop((project_id, assembly_result_id), None)
            self._manifest = manifest
        return manifest

    def export(
        self,
        project_root: Path,
        request: NCExportRequest,
        source: NCExportSourceSnapshot,
        *,
        current_source: Callable[[], NCExportSourceSnapshot] | None = None,
        current_project_generation: Callable[[], int] | None = None,
        current_post_result: Callable[[], PostResult | None] | None = None,
    ) -> NCExportExecution:
        """Publish managed bytes and optionally copy them to a filesystem target."""
        token = self.begin(request, source)
        try:
            filename, payload = self._validate(request, source)
            entry = self._build_entry(request, source, filename, payload)
        except NCExportSecurityError as error:
            return _failed(error.code, "export.filename_rejected")
        except _NCExportPreflightError as error:
            return _failed(error.code, error.message_key)
        except Exception:
            return _failed(NCExportDiagnosticCode.INVALID_REQUEST, "export.preflight_failed")
        if not self._still_current(
            request,
            source,
            token,
            current_source=current_source,
            current_project_generation=current_project_generation,
            current_post_result=current_post_result,
        ):
            return _failed(
                NCExportDiagnosticCode.POST_STALE,
                "export.prewrite_stale",
                status=NCExportStatus.STALE,
            )
        try:
            published = self._store.publish(
                project_root, entry, payload, request.overwrite_policy
            )
        except NCArtifactStoreError as error:
            diagnostic = _diagnostic(error.code, "export.managed_write_failed")
            result = self._result(
                request,
                source,
                entry,
                NCExportStatus.FAILED,
                (diagnostic,),
                files_written=0,
                verifications=0,
            )
            return NCExportExecution(False, result, None, (diagnostic,), result.status)
        if not self._still_current(
            request,
            source,
            token,
            current_source=current_source,
            current_project_generation=current_project_generation,
            current_post_result=current_post_result,
        ):
            self._store.mark_operation_stale(
                project_root, request.project_id, request.operation_id
            )
            diagnostic = _diagnostic(
                NCExportDiagnosticCode.POST_STALE, "export.postwrite_stale"
            )
            result = self._result(
                request,
                source,
                published,
                NCExportStatus.STALE,
                (diagnostic,),
                files_written=3,
                verifications=3,
            )
            return NCExportExecution(False, result, published, (diagnostic,), result.status)
        result = self._result(
            request,
            source,
            published,
            NCExportStatus.PUBLISHED,
            (),
            files_written=3,
            verifications=3,
        )
        self._publish_runtime(request, token, result, project_root)
        if request.target is ExportTarget.PROJECT_MANAGED:
            return NCExportExecution(True, result, published, (), result.status)
        if request.target_directory is None:
            diagnostic = _diagnostic(
                NCExportDiagnosticCode.TARGET_MISSING, "export.external_target_missing"
            )
            failed = self._result(
                request,
                source,
                published,
                NCExportStatus.EXTERNAL_FAILED,
                (diagnostic,),
                files_written=3,
                verifications=3,
            )
            self._publish_runtime(request, token, failed, project_root)
            return NCExportExecution(False, failed, published, (diagnostic,), failed.status)
        try:
            external_path = self._store.export_external(
                project_root,
                published,
                request.target_directory,
                request.overwrite_policy,
                create_target_directory=request.create_target_directory,
            )
        except NCArtifactStoreError as error:
            diagnostic = _diagnostic(error.code, "export.external_write_failed")
            failed = self._result(
                request,
                source,
                published,
                NCExportStatus.EXTERNAL_FAILED,
                (diagnostic,),
                files_written=3,
                verifications=3,
                target_identifier=_target_identifier(request.target_directory),
            )
            self._publish_runtime(request, token, failed, project_root)
            return NCExportExecution(False, failed, published, (diagnostic,), failed.status)
        completed = self._result(
            request,
            source,
            published,
            NCExportStatus.PUBLISHED_EXTERNAL,
            (),
            files_written=4,
            verifications=4,
            target_identifier=_target_identifier(request.target_directory),
            external_path=external_path,
        )
        self._publish_runtime(request, token, completed, project_root)
        return NCExportExecution(True, completed, published, (), completed.status)

    def export_assembly(
        self,
        project_root: Path,
        request: NCAssemblyExportRequest,
        source: NCAssemblyExportSourceSnapshot,
        *,
        current_source: Callable[[], NCAssemblyExportSourceSnapshot] | None = None,
        current_project_generation: Callable[[], int] | None = None,
    ) -> NCExportExecution:
        """Publish an assembly through the existing managed artifact/export store."""
        key = (request.project_id, request.assembly_result_id)
        with self._lock:
            self._generation += 1
            token = NCExportToken(
                uuid4(), self._generation, request.fingerprint, source.fingerprint
            )
            self._latest[key] = token
        try:
            filename, payload = self._validate_assembly(request, source)
            entry = self._build_assembly_entry(request, source, filename, payload)
        except NCExportSecurityError:
            diagnostic = _diagnostic(
                NCExportDiagnosticCode.FILENAME_INVALID, "export.filename_rejected"
            )
            return NCExportExecution(False, None, None, (diagnostic,), NCExportStatus.FAILED)
        except Exception:
            diagnostic = _diagnostic(
                NCExportDiagnosticCode.INVALID_REQUEST, "export.assembly_preflight_failed"
            )
            return NCExportExecution(False, None, None, (diagnostic,), NCExportStatus.FAILED)
        if not self._assembly_still_current(
            request, source, token, current_source, current_project_generation
        ):
            diagnostic = _diagnostic(NCExportDiagnosticCode.POST_STALE, "export.prewrite_stale")
            return NCExportExecution(False, None, None, (diagnostic,), NCExportStatus.STALE)
        try:
            published = self._store.publish(
                project_root, entry, payload, request.overwrite_policy
            )
        except NCArtifactStoreError as error:
            diagnostic = _diagnostic(error.code, "export.managed_write_failed")
            return NCExportExecution(False, None, None, (diagnostic,), NCExportStatus.FAILED)
        if not self._assembly_still_current(
            request, source, token, current_source, current_project_generation
        ):
            diagnostic = _diagnostic(NCExportDiagnosticCode.POST_STALE, "export.postwrite_stale")
            result = self._assembly_export_result(
                request, source, published, NCExportStatus.STALE, (diagnostic,), 3, 3
            )
            with self._lock:
                self._results[key] = result
            return NCExportExecution(False, result, published, (diagnostic,), result.status)
        managed = self._assembly_export_result(
            request, source, published, NCExportStatus.PUBLISHED, (), 3, 3
        )
        with self._lock:
            self._results[key] = managed
        try:
            manifest = self._store.inspect(project_root, request.project_id)
        except NCArtifactStoreError:
            manifest = None
        if manifest is not None:
            with self._lock:
                self._manifest = manifest
        if request.target is ExportTarget.PROJECT_MANAGED:
            return NCExportExecution(True, managed, published, (), managed.status)
        if request.target_directory is None:
            diagnostic = _diagnostic(NCExportDiagnosticCode.TARGET_MISSING, "export.external_target_missing")
            failed = self._assembly_export_result(
                request, source, published, NCExportStatus.EXTERNAL_FAILED, (diagnostic,), 3, 3
            )
            with self._lock:
                self._results[key] = failed
            return NCExportExecution(False, failed, published, (diagnostic,), failed.status)
        try:
            external_path = self._store.export_external(
                project_root,
                published,
                request.target_directory,
                request.overwrite_policy,
                create_target_directory=request.create_target_directory,
            )
        except NCArtifactStoreError as error:
            diagnostic = _diagnostic(error.code, "export.external_write_failed")
            failed = self._assembly_export_result(
                request,
                source,
                published,
                NCExportStatus.EXTERNAL_FAILED,
                (diagnostic,),
                3,
                3,
                target_identifier=_target_identifier(request.target_directory),
            )
            with self._lock:
                self._results[key] = failed
            return NCExportExecution(False, failed, published, (diagnostic,), failed.status)
        completed = self._assembly_export_result(
            request,
            source,
            published,
            NCExportStatus.PUBLISHED_EXTERNAL,
            (),
            4,
            4,
            target_identifier=_target_identifier(request.target_directory),
            external_path=external_path,
        )
        with self._lock:
            self._results[key] = completed
        return NCExportExecution(True, completed, published, (), completed.status)

    @staticmethod
    def _validate_assembly(
        request: NCAssemblyExportRequest,
        snapshot: NCAssemblyExportSourceSnapshot,
    ) -> tuple[str, bytes]:
        from hms_cadcam.cam.post.assembly_service import (
            build_assembly_input_fingerprint,
        )
        from hms_cadcam.cam.post.assembly_validation import validate_assembly_plan

        result = snapshot.assembly_result
        source_request = snapshot.post_request
        profile = source_request.post_definition.production_profile
        current_input = build_assembly_input_fingerprint(source_request)
        plan_diagnostics = validate_assembly_plan(
            result.plan, source_request.post_definition
        )
        if (
            request.project_id != result.project_id
            or request.assembly_result_id != result.result_id
            or result.status is not ProgramAssemblyStatus.PUBLISHED
            or result.canonical_text is None
            or profile is None
            or result.input_fingerprint != current_input
            or bool(plan_diagnostics)
        ):
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.POST_INVALID, "export.assembly_not_published"
            )
        filename = sanitize_export_filename(request.filename, profile.allowed_extensions)
        context_filename = sanitize_export_filename(
            result.plan.shared_context.file_name, profile.allowed_extensions
        )
        if filename.casefold() != context_filename.casefold():
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.PROFILE_MISMATCH,
                "export.filename_context_mismatch",
            )
        payload = result.canonical_text.encode(profile.encoding)
        if (
            payload.startswith(b"\xef\xbb\xbf")
            or hashlib.sha256(payload).hexdigest() != result.output_checksum
            or len(payload) > profile.maximum_program_size
            or not result.canonical_text.endswith(profile.newline)
        ):
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.POST_INVALID, "export.canonical_bytes_invalid"
            )
        return filename, payload

    @staticmethod
    def _build_assembly_entry(
        request: NCAssemblyExportRequest,
        snapshot: NCAssemblyExportSourceSnapshot,
        filename: str,
        payload: bytes,
    ) -> NCArtifactManifestEntry:
        result = snapshot.assembly_result
        plan = result.plan
        profile = snapshot.post_request.post_definition.production_profile
        assert profile is not None and result.result_fingerprint is not None
        first = plan.sections[0]
        digest = hashlib.sha256(payload).hexdigest()
        artifact_id = NCArtifactId(
            uuid5(
                _ARTIFACT_NAMESPACE,
                "|".join((str(request.project_id), str(result.result_id), filename.casefold(), digest)),
            )
        )
        synthetic_post_result = PostResultId(
            uuid5(_ARTIFACT_NAMESPACE, f"assembly-post-result|{result.result_id}")
        )
        return NCArtifactManifestEntry(
            artifact_id=artifact_id,
            project_id=request.project_id,
            operation_id=first.operation_id,
            source_artifact_id=first.artifact_id,
            source_artifact_fingerprint=first.artifact_fingerprint,
            post_result_id=synthetic_post_result,
            post_input_fingerprint=result.input_fingerprint,
            post_result_fingerprint=result.result_fingerprint,
            post_definition_id=plan.post_definition_id,
            production_profile_id=plan.production_profile_id,
            production_profile_version=plan.production_profile_version,
            production_profile_fingerprint=plan.production_profile_fingerprint,
            tool_binding_fingerprint=first.tool_binding.fingerprint,
            program_context_fingerprint=plan.shared_context.fingerprint,
            output_relative_path=f"nc/{filename}",
            metadata_relative_path=f"post/metadata/{artifact_id.value.hex}.json",
            byte_length=len(payload),
            sha256=digest,
            newline=profile.newline,
            encoding=profile.encoding,
            extension=profile.allowed_extensions[0],
            status=NCArtifactStatus.CURRENT,
            post_diagnostics=(),
            post_statistics=first.program_ir.statistics,
            assembly_result_id=result.result_id,
            assembly_result_fingerprint=result.result_fingerprint,
            assembly_section_count=len(plan.sections),
            assembly_operation_ids=tuple(item.operation_id for item in plan.sections),
            assembly_section_ids=tuple(item.section_id for item in plan.sections),
            assembly_source_artifact_fingerprints=tuple(
                item.artifact_fingerprint for item in plan.sections
            ),
            assembly_tool_binding_fingerprints=tuple(
                item.tool_binding.fingerprint for item in plan.sections
            ),
            assembly_operation_context_fingerprints=tuple(
                item.operation_context_fingerprint for item in plan.sections
            ),
        )

    def _assembly_still_current(
        self,
        request: NCAssemblyExportRequest,
        source: NCAssemblyExportSourceSnapshot,
        token: NCExportToken,
        current_source: Callable[[], NCAssemblyExportSourceSnapshot] | None,
        current_project_generation: Callable[[], int] | None,
    ) -> bool:
        key = (request.project_id, request.assembly_result_id)
        with self._lock:
            if self._latest.get(key) != token:
                return False
        try:
            if current_project_generation is not None and current_project_generation() != source.project_generation:
                return False
            current = current_source() if current_source is not None else source
            return current.fingerprint == token.source_fingerprint
        except Exception:
            return False

    @staticmethod
    def _assembly_export_result(
        request: NCAssemblyExportRequest,
        source: NCAssemblyExportSourceSnapshot,
        entry: NCArtifactManifestEntry,
        status: NCExportStatus,
        diagnostics: tuple[NCExportDiagnostic, ...],
        files_written: int,
        verifications: int,
        *,
        target_identifier: str | None = None,
        external_path: Path | None = None,
    ) -> NCExportResult:
        result = source.assembly_result
        assert result.result_fingerprint is not None
        return NCExportResult(
            request_id=request.request_id,
            result_id=NCExportResultId.new(),
            artifact_id=entry.artifact_id,
            source_post_result_id=entry.post_result_id,
            source_post_input_fingerprint=result.input_fingerprint,
            source_post_result_fingerprint=result.result_fingerprint,
            production_profile_fingerprint=entry.production_profile_fingerprint,
            project_managed_relative_path=entry.output_relative_path,
            target_kind=request.target,
            target_identifier=target_identifier,
            byte_length=entry.byte_length,
            sha256=entry.sha256,
            status=status,
            diagnostics=diagnostics,
            statistics=NCExportStatistics(entry.byte_length, files_written, verifications),
            external_path=external_path,
        )

    @staticmethod
    def _validate(
        request: NCExportRequest, snapshot: NCExportSourceSnapshot
    ) -> tuple[str, bytes]:
        post_request = snapshot.post_request
        result = snapshot.post_result
        source = snapshot.source
        if (
            request.project_id != source.project_id
            or request.project_id != result.project_id
            or request.operation_id != source.operation.operation_id
            or request.operation_id != result.operation_id
            or request.source_artifact_id != source.artifact.artifact_id
            or request.source_artifact_id != result.artifact_id
            or request.post_result_id != result.result_id
        ):
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.INVALID_REQUEST, "export.source_identity_mismatch"
            )
        if result.status is not PostResultStatus.PUBLISHED or result.canonical_text is None:
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.POST_INVALID, "export.post_not_published"
            )
        profile = post_request.post_definition.production_profile
        context = post_request.program_context
        if profile is None or context is None:
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.PROFILE_MISMATCH,
                "export.production_profile_required",
            )
        if (
            result.post_definition_id != post_request.post_definition.definition_id
            or result.post_definition_fingerprint
            != post_request.post_definition.fingerprint
            or result.production_profile_id != profile.profile_id
            or result.production_profile_version != profile.profile_version
            or result.production_profile_fingerprint != profile.fingerprint
            or result.tool_binding_fingerprint != context.tool_binding.fingerprint
            or result.program_context_fingerprint != context.fingerprint
        ):
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.PROFILE_MISMATCH,
                "export.production_provenance_mismatch",
            )
        current_input = build_post_input_fingerprint(post_request, source)
        if result.input_fingerprint != current_input:
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.POST_STALE, "export.post_input_stale"
            )
        diagnostics = validate_post_source(source, post_request.simulation_gate_policy)
        if any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics):
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.POST_STALE, "export.source_or_simulation_stale"
            )
        filename = sanitize_export_filename(request.filename, profile.allowed_extensions)
        context_filename = sanitize_export_filename(
            context.file_name, profile.allowed_extensions
        )
        if filename.casefold() != context_filename.casefold():
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.PROFILE_MISMATCH,
                "export.filename_context_mismatch",
            )
        try:
            payload = result.canonical_text.encode(profile.encoding)
        except (LookupError, UnicodeEncodeError) as error:
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.POST_INVALID, "export.output_encoding_invalid"
            ) from error
        if payload.startswith(b"\xef\xbb\xbf"):
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.POST_INVALID, "export.output_bom_forbidden"
            )
        if (
            len(payload) > profile.maximum_program_size
            or hashlib.sha256(payload).hexdigest() != result.output_checksum
            or not result.canonical_text.endswith(profile.newline)
            or "\n" in result.canonical_text.replace(profile.newline, "")
            or (profile.newline == "\r\n" and "\r" in result.canonical_text.replace("\r\n", ""))
        ):
            raise _NCExportPreflightError(
                NCExportDiagnosticCode.POST_INVALID,
                "export.canonical_bytes_invalid",
            )
        return filename, payload

    @staticmethod
    def _build_entry(
        request: NCExportRequest,
        snapshot: NCExportSourceSnapshot,
        filename: str,
        payload: bytes,
    ) -> NCArtifactManifestEntry:
        result = snapshot.post_result
        profile = snapshot.post_request.post_definition.production_profile
        assert profile is not None
        assert result.result_fingerprint is not None
        assert result.production_profile_id is not None
        assert result.production_profile_version is not None
        assert result.production_profile_fingerprint is not None
        assert result.tool_binding_fingerprint is not None
        assert result.program_context_fingerprint is not None
        digest = hashlib.sha256(payload).hexdigest()
        artifact_id = NCArtifactId(
            uuid5(
                _ARTIFACT_NAMESPACE,
                "|".join(
                    (
                        str(request.project_id),
                        str(request.operation_id),
                        str(request.source_artifact_id),
                        result.result_fingerprint.digest,
                        profile.fingerprint.digest,
                        filename.casefold(),
                        digest,
                    )
                ),
            )
        )
        return NCArtifactManifestEntry(
            artifact_id=artifact_id,
            project_id=request.project_id,
            operation_id=request.operation_id,
            source_artifact_id=request.source_artifact_id,
            source_artifact_fingerprint=result.artifact_fingerprint,
            post_result_id=result.result_id,
            post_input_fingerprint=result.input_fingerprint,
            post_result_fingerprint=result.result_fingerprint,
            post_definition_id=result.post_definition_id,
            production_profile_id=result.production_profile_id,
            production_profile_version=result.production_profile_version,
            production_profile_fingerprint=result.production_profile_fingerprint,
            tool_binding_fingerprint=result.tool_binding_fingerprint,
            program_context_fingerprint=result.program_context_fingerprint,
            output_relative_path=f"nc/{filename}",
            metadata_relative_path=f"post/metadata/{artifact_id.value.hex}.json",
            byte_length=len(payload),
            sha256=digest,
            newline=profile.newline,
            encoding=profile.encoding,
            extension=profile.allowed_extensions[0],
            status=NCArtifactStatus.CURRENT,
            post_diagnostics=result.diagnostics,
            post_statistics=result.statistics,
        )

    def _still_current(
        self,
        request: NCExportRequest,
        source: NCExportSourceSnapshot,
        token: NCExportToken,
        *,
        current_source: Callable[[], NCExportSourceSnapshot] | None,
        current_project_generation: Callable[[], int] | None,
        current_post_result: Callable[[], PostResult | None] | None,
    ) -> bool:
        with self._lock:
            if self._latest.get((request.project_id, request.operation_id)) != token:
                return False
            if self._bound_project_id is not None and self._bound_project_id != request.project_id:
                return False
            if (
                self._bound_project_generation is not None
                and self._bound_project_generation != source.project_generation
            ):
                return False
        try:
            if current_project_generation is not None and (
                current_project_generation() != source.project_generation
            ):
                return False
            if current_post_result is not None:
                current_post = current_post_result()
                if (
                    current_post is None
                    or current_post.result_id != source.post_result.result_id
                    or current_post.result_fingerprint
                    != source.post_result.result_fingerprint
                ):
                    return False
            current = current_source() if current_source is not None else source
            return current.fingerprint == token.source_fingerprint
        except Exception:
            return False

    def _publish_runtime(
        self,
        request: NCExportRequest,
        token: NCExportToken,
        result: NCExportResult,
        project_root: Path,
    ) -> None:
        with self._lock:
            if self._latest.get((request.project_id, request.operation_id)) != token:
                return
            self._results[(request.project_id, request.operation_id)] = result
        try:
            manifest = self._store.inspect(project_root, request.project_id)
        except NCArtifactStoreError:
            return
        with self._lock:
            self._manifest = manifest

    @staticmethod
    def _result(
        request: NCExportRequest,
        source: NCExportSourceSnapshot,
        entry: NCArtifactManifestEntry,
        status: NCExportStatus,
        diagnostics: tuple[NCExportDiagnostic, ...],
        *,
        files_written: int,
        verifications: int,
        target_identifier: str | None = None,
        external_path: Path | None = None,
    ) -> NCExportResult:
        result = source.post_result
        assert result.result_fingerprint is not None
        assert result.production_profile_fingerprint is not None
        return NCExportResult(
            request_id=request.request_id,
            result_id=NCExportResultId.new(),
            artifact_id=entry.artifact_id,
            source_post_result_id=result.result_id,
            source_post_input_fingerprint=result.input_fingerprint,
            source_post_result_fingerprint=result.result_fingerprint,
            production_profile_fingerprint=result.production_profile_fingerprint,
            project_managed_relative_path=entry.output_relative_path,
            target_kind=request.target,
            target_identifier=target_identifier,
            byte_length=entry.byte_length,
            sha256=entry.sha256,
            status=status,
            diagnostics=diagnostics,
            statistics=NCExportStatistics(
                entry.byte_length, files_written, verifications
            ),
            external_path=external_path,
        )


class _NCExportPreflightError(ValueError):
    def __init__(self, code: NCExportDiagnosticCode, message_key: str) -> None:
        super().__init__(message_key)
        self.code = code
        self.message_key = message_key


def _target_identifier(path: Path) -> str:
    name = path.name.strip(" .")
    if not name:
        return "filesystem-root"
    sanitized = "".join(
        char for char in name if ord(char) >= 32 and ord(char) != 127
    )[:128]
    return sanitized or "filesystem-target"


def _diagnostic(code: NCExportDiagnosticCode, key: str) -> NCExportDiagnostic:
    return NCExportDiagnostic(DiagnosticSeverity.ERROR, code, key)


def _failed(
    code: NCExportDiagnosticCode,
    key: str,
    *,
    status: NCExportStatus = NCExportStatus.FAILED,
) -> NCExportExecution:
    diagnostic = _diagnostic(code, key)
    return NCExportExecution(False, None, None, (diagnostic,), status)

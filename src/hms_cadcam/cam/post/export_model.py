"""Immutable contracts for project-managed and filesystem NC export."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

from hms_cadcam.cam.domain.errors import (
    CamInvariantError,
    CamValidationError,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.ids import (
    NCArtifactId,
    NCExportRequestId,
    NCExportResultId,
    OperationId,
    PostProcessorDefinitionId,
    PostResultId,
    ProductionControllerProfileId,
    ProgramAssemblyResultId,
    ProgramOperationSectionId,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.post.model import PostDiagnostic, PostStatistics


NC_EXPORT_FORMAT = "HMS_CAM_NC_EXPORT_REQUEST"
NC_EXPORT_RESULT_FORMAT = "HMS_CAM_NC_EXPORT_RESULT"
NC_ARTIFACT_ENTRY_FORMAT = "HMS_CAM_NC_ARTIFACT"
NC_ARTIFACT_MANIFEST_FORMAT = "HMS_CAM_NC_ARTIFACT_MANIFEST"
NC_EXPORT_VERSION = 1

_KEY = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ExportTarget(StrEnum):
    PROJECT_MANAGED = "project_managed"
    FILESYSTEM_DIRECTORY = "filesystem_directory"
    DATA_SERVER_DIRECTORY = "data_server_directory"


class ExportOverwritePolicy(StrEnum):
    FAIL_IF_EXISTS = "fail_if_exists"
    REPLACE_IF_SAME_ARTIFACT = "replace_if_same_artifact"
    REPLACE_EXPLICIT = "replace_explicit"


class NCExportDiagnosticCode(StrEnum):
    INVALID_REQUEST = "export.invalid_request"
    POST_MISSING = "export.post_missing"
    POST_STALE = "export.post_stale"
    POST_INVALID = "export.post_invalid"
    PROFILE_MISMATCH = "export.profile_mismatch"
    FILENAME_INVALID = "export.filename_invalid"
    EXTENSION_INVALID = "export.extension_invalid"
    TARGET_MISSING = "export.target_missing"
    TARGET_UNSUPPORTED = "export.target_unsupported"
    PERMISSION_DENIED = "export.permission_denied"
    PATH_ESCAPE = "export.path_escape"
    FILE_EXISTS = "export.file_exists"
    OVERWRITE_DENIED = "export.overwrite_denied"
    WRITE_FAILED = "export.write_failed"
    ATOMIC_REPLACE_FAILED = "export.atomic_replace_failed"
    CHECKSUM_MISMATCH = "export.checksum_mismatch"
    SIDECAR_INVALID = "export.sidecar_invalid"
    MANIFEST_INVALID = "export.manifest_invalid"
    TAMPERED = "export.tampered"
    MISSING = "export.missing"
    STALE = "export.stale"
    CANCELLED = "export.cancelled"
    FAILED = "export.failed"


class NCExportStatus(StrEnum):
    PUBLISHED = "published"
    PUBLISHED_EXTERNAL = "published_external"
    EXTERNAL_FAILED = "external_failed"
    FAILED = "failed"
    STALE = "stale"
    CANCELLED = "cancelled"


class NCArtifactStatus(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"
    TAMPERED = "tampered"


def _non_empty_text(value: str, name: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise CamValidationError(f"{name} is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CamValidationError(f"{name} contains control characters")
    return value


def _relative_path(value: str, name: str) -> str:
    value = _non_empty_text(value, name, maximum=512)
    if "\\" in value:
        raise CamValidationError(f"{name} must use project-relative POSIX separators")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CamValidationError(f"{name} must stay relative to the project")
    return path.as_posix()


def _sha256(value: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise CamValidationError("NC artifact SHA-256 is invalid")
    return value


def _evidence(values: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    if not isinstance(values, tuple):
        raise CamValidationError("NC export diagnostic evidence is invalid")
    normalized: list[tuple[str, str]] = []
    for item in values:
        if not isinstance(item, tuple) or len(item) != 2:
            raise CamValidationError("NC export diagnostic evidence is invalid")
        key, value = item
        key = _non_empty_text(key, "Evidence key", maximum=128)
        value = _non_empty_text(value, "Evidence value", maximum=512)
        if _KEY.fullmatch(key) is None:
            raise CamValidationError("NC export diagnostic evidence key is invalid")
        normalized.append((key, value))
    result = tuple(sorted(normalized))
    if len({key for key, _ in result}) != len(result):
        raise CamInvariantError("NC export diagnostic evidence keys must be unique")
    return result


@dataclass(frozen=True, slots=True)
class NCExportDiagnostic:
    severity: DiagnosticSeverity
    code: NCExportDiagnosticCode
    message_key: str
    evidence: tuple[tuple[str, str], ...] = ()
    schema_version: int = NC_EXPORT_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != NC_EXPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported NC export diagnostic version")
        if not isinstance(self.severity, DiagnosticSeverity) or not isinstance(
            self.code, NCExportDiagnosticCode
        ):
            raise CamValidationError("NC export diagnostic enums are invalid")
        message_key = _non_empty_text(self.message_key, "Diagnostic message key", maximum=256)
        if _KEY.fullmatch(message_key) is None:
            raise CamValidationError("NC export diagnostic message key is invalid")
        object.__setattr__(self, "message_key", message_key)
        object.__setattr__(self, "evidence", _evidence(self.evidence))


@dataclass(frozen=True, slots=True)
class NCExportStatistics:
    byte_length: int
    files_written: int
    checksum_verifications: int
    schema_version: int = NC_EXPORT_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != NC_EXPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported NC export statistics version")
        if any(
            type(value) is not int or value < 0
            for value in (self.byte_length, self.files_written, self.checksum_verifications)
        ):
            raise CamValidationError("NC export statistics are invalid")


@dataclass(frozen=True, slots=True)
class NCExportRequest:
    project_id: UUID
    operation_id: OperationId
    source_artifact_id: ToolpathArtifactId
    post_result_id: PostResultId
    filename: str
    target: ExportTarget = ExportTarget.PROJECT_MANAGED
    overwrite_policy: ExportOverwritePolicy = ExportOverwritePolicy.FAIL_IF_EXISTS
    create_target_directory: bool = False
    request_id: NCExportRequestId | None = None
    schema_version: int = NC_EXPORT_VERSION
    target_directory: Path | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != NC_EXPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported NC export request version")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("NC export project ID is invalid")
        for value, typ in (
            (self.operation_id, OperationId),
            (self.source_artifact_id, ToolpathArtifactId),
            (self.post_result_id, PostResultId),
        ):
            if not isinstance(value, typ):
                raise CamValidationError("NC export source identity is invalid")
        object.__setattr__(self, "filename", _non_empty_text(self.filename, "NC export filename", maximum=512))
        if not isinstance(self.target, ExportTarget) or not isinstance(
            self.overwrite_policy, ExportOverwritePolicy
        ):
            raise CamValidationError("NC export policy is invalid")
        if type(self.create_target_directory) is not bool:
            raise CamValidationError("NC export create-directory policy is invalid")
        if self.request_id is None:
            object.__setattr__(self, "request_id", NCExportRequestId.new())
        if not isinstance(self.request_id, NCExportRequestId):
            raise CamValidationError("NC export request ID is invalid")
        if self.target_directory is not None and not isinstance(self.target_directory, Path):
            raise CamValidationError("NC export runtime target must be pathlib.Path")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        """Return semantic identity without request UUID or absolute runtime path."""
        return {
            "format": NC_EXPORT_FORMAT,
            "format_version": self.schema_version,
            "project_id": str(self.project_id),
            "operation_id": str(self.operation_id),
            "source_artifact_id": str(self.source_artifact_id),
            "post_result_id": str(self.post_result_id),
            "filename": self.filename,
            "target": self.target.value,
            "overwrite_policy": self.overwrite_policy.value,
            "create_target_directory": self.create_target_directory,
        }

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.export_codec import request_to_dict

        return request_to_dict(self)


@dataclass(frozen=True, slots=True)
class NCAssemblyExportRequest:
    """Export envelope for a published multi-operation assembly result."""

    project_id: UUID
    assembly_result_id: ProgramAssemblyResultId
    filename: str
    target: ExportTarget = ExportTarget.PROJECT_MANAGED
    overwrite_policy: ExportOverwritePolicy = ExportOverwritePolicy.FAIL_IF_EXISTS
    create_target_directory: bool = False
    request_id: NCExportRequestId | None = None
    schema_version: int = NC_EXPORT_VERSION
    target_directory: Path | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != NC_EXPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported NC assembly export version")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("NC assembly export project ID is invalid")
        if not isinstance(self.assembly_result_id, ProgramAssemblyResultId):
            raise CamValidationError("NC assembly export result ID is invalid")
        object.__setattr__(self, "filename", _non_empty_text(self.filename, "NC assembly filename", maximum=512))
        if not isinstance(self.target, ExportTarget) or not isinstance(self.overwrite_policy, ExportOverwritePolicy):
            raise CamValidationError("NC assembly export policy is invalid")
        if type(self.create_target_directory) is not bool:
            raise CamValidationError("NC assembly export create-directory policy is invalid")
        if self.request_id is None:
            object.__setattr__(self, "request_id", NCExportRequestId.new())
        if not isinstance(self.request_id, NCExportRequestId):
            raise CamValidationError("NC assembly export request ID is invalid")
        if self.target_directory is not None and not isinstance(self.target_directory, Path):
            raise CamValidationError("NC assembly export target must be pathlib.Path")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        return DependencyFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM_NC_ASSEMBLY_EXPORT_REQUEST",
            "format_version": self.schema_version,
            "project_id": str(self.project_id),
            "assembly_result_id": str(self.assembly_result_id),
            "filename": self.filename,
            "target": self.target.value,
            "overwrite_policy": self.overwrite_policy.value,
            "create_target_directory": self.create_target_directory,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.identity_payload(),
            "request_id": str(self.request_id),
        }


@dataclass(frozen=True, slots=True)
class NCArtifactManifestEntry:
    artifact_id: NCArtifactId
    project_id: UUID
    operation_id: OperationId
    source_artifact_id: ToolpathArtifactId
    source_artifact_fingerprint: ContentFingerprint
    post_result_id: PostResultId
    post_input_fingerprint: DependencyFingerprint
    post_result_fingerprint: ContentFingerprint
    post_definition_id: PostProcessorDefinitionId
    production_profile_id: ProductionControllerProfileId
    production_profile_version: int
    production_profile_fingerprint: ContentFingerprint
    tool_binding_fingerprint: ContentFingerprint
    program_context_fingerprint: ContentFingerprint
    output_relative_path: str
    metadata_relative_path: str
    byte_length: int
    sha256: str
    newline: str
    encoding: str
    extension: str
    status: NCArtifactStatus
    post_diagnostics: tuple[PostDiagnostic, ...]
    post_statistics: PostStatistics
    artifact_fingerprint: ContentFingerprint | None = None
    schema_version: int = NC_EXPORT_VERSION
    assembly_result_id: ProgramAssemblyResultId | None = None
    assembly_result_fingerprint: ContentFingerprint | None = None
    assembly_section_count: int | None = None
    assembly_operation_ids: tuple[OperationId, ...] = ()
    assembly_section_ids: tuple[ProgramOperationSectionId, ...] = ()
    assembly_source_artifact_fingerprints: tuple[ContentFingerprint, ...] = ()
    assembly_tool_binding_fingerprints: tuple[ContentFingerprint, ...] = ()
    assembly_operation_context_fingerprints: tuple[ContentFingerprint, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != NC_EXPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported NC artifact entry version")
        if not isinstance(self.artifact_id, NCArtifactId):
            raise CamValidationError("NC artifact identity is invalid")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("NC artifact project identity is invalid")
        for value, typ in (
            (self.operation_id, OperationId),
            (self.source_artifact_id, ToolpathArtifactId),
            (self.post_result_id, PostResultId),
            (self.post_definition_id, PostProcessorDefinitionId),
            (self.production_profile_id, ProductionControllerProfileId),
        ):
            if not isinstance(value, typ):
                raise CamValidationError("NC artifact provenance identity is invalid")
        for value in (
            self.source_artifact_fingerprint,
            self.post_input_fingerprint,
            self.post_result_fingerprint,
            self.production_profile_fingerprint,
            self.tool_binding_fingerprint,
            self.program_context_fingerprint,
        ):
            if not isinstance(value, (ContentFingerprint, DependencyFingerprint)):
                raise CamValidationError("NC artifact provenance fingerprint is invalid")
        if type(self.production_profile_version) is not int or self.production_profile_version <= 0:
            raise CamValidationError("NC artifact profile version is invalid")
        object.__setattr__(self, "output_relative_path", _relative_path(self.output_relative_path, "NC output path"))
        object.__setattr__(self, "metadata_relative_path", _relative_path(self.metadata_relative_path, "NC metadata path"))
        if type(self.byte_length) is not int or self.byte_length <= 0:
            raise CamValidationError("NC artifact byte length is invalid")
        object.__setattr__(self, "sha256", _sha256(self.sha256))
        if self.newline not in {"\n", "\r\n"}:
            raise CamValidationError("NC artifact newline is invalid")
        object.__setattr__(self, "encoding", _non_empty_text(self.encoding, "NC encoding", maximum=64).casefold())
        extension = _non_empty_text(self.extension, "NC extension", maximum=32).casefold()
        if not extension.startswith(".") or extension.count(".") != 1:
            raise CamValidationError("NC artifact extension is invalid")
        object.__setattr__(self, "extension", extension)
        if not isinstance(self.status, NCArtifactStatus):
            raise CamValidationError("NC artifact status is invalid")
        if not isinstance(self.post_diagnostics, tuple) or any(
            not isinstance(item, PostDiagnostic) for item in self.post_diagnostics
        ):
            raise CamValidationError("NC artifact post diagnostics are invalid")
        if not isinstance(self.post_statistics, PostStatistics):
            raise CamValidationError("NC artifact post statistics are invalid")
        if self.assembly_result_id is not None and not isinstance(
            self.assembly_result_id, ProgramAssemblyResultId
        ):
            raise CamValidationError("NC artifact assembly result identity is invalid")
        assembly_fingerprints = tuple(
            value
            for value in (
                self.assembly_result_fingerprint,
                *self.assembly_source_artifact_fingerprints,
                *self.assembly_tool_binding_fingerprints,
                *self.assembly_operation_context_fingerprints,
            )
            if value is not None
        )
        if any(
            not isinstance(value, (ContentFingerprint, DependencyFingerprint))
            for value in assembly_fingerprints
        ):
            raise CamValidationError("NC artifact assembly fingerprints are invalid")
        if any(not isinstance(value, OperationId) for value in self.assembly_operation_ids):
            raise CamValidationError("NC artifact assembly operation IDs are invalid")
        if any(
            not isinstance(value, ProgramOperationSectionId)
            for value in self.assembly_section_ids
        ):
            raise CamValidationError("NC artifact assembly section IDs are invalid")
        if self.assembly_result_id is None and any(
            (
                self.assembly_result_fingerprint,
                self.assembly_section_count,
                self.assembly_operation_ids,
                self.assembly_section_ids,
                self.assembly_source_artifact_fingerprints,
                self.assembly_tool_binding_fingerprints,
                self.assembly_operation_context_fingerprints,
            )
        ):
            raise CamInvariantError("Assembly metadata requires an assembly result ID")
        if self.assembly_result_id is not None:
            if self.assembly_result_fingerprint is None:
                raise CamValidationError("Assembly result fingerprint is required")
            if type(self.assembly_section_count) is not int or self.assembly_section_count <= 0:
                raise CamValidationError("Assembly section count is invalid")
            lengths = {
                len(self.assembly_operation_ids),
                len(self.assembly_section_ids),
                len(self.assembly_source_artifact_fingerprints),
                len(self.assembly_tool_binding_fingerprints),
                len(self.assembly_operation_context_fingerprints),
            }
            if len(lengths) != 1 or not lengths or next(iter(lengths)) == 0:
                raise CamInvariantError("Assembly manifest provenance is incomplete")
            if self.assembly_section_count != next(iter(lengths)):
                raise CamInvariantError("Assembly section count does not match provenance")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.artifact_fingerprint is None:
            object.__setattr__(self, "artifact_fingerprint", calculated)
        elif self.artifact_fingerprint != calculated:
            raise CamInvariantError("NC artifact fingerprint verification failed")

    def identity_payload(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.export_codec import entry_to_dict

        data = entry_to_dict(self, include_fingerprint=False)
        data.pop("status")
        if self.assembly_result_id is None:
            for key in (
                "assembly_result_id",
                "assembly_result_fingerprint",
                "assembly_section_count",
                "assembly_operation_ids",
                "assembly_section_ids",
                "assembly_source_artifact_fingerprints",
                "assembly_tool_binding_fingerprints",
                "assembly_operation_context_fingerprints",
            ):
                data.pop(key, None)
        return data

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.export_codec import entry_to_dict

        return entry_to_dict(self)


@dataclass(frozen=True, slots=True)
class NCArtifactManifest:
    project_id: UUID
    entries: tuple[NCArtifactManifestEntry, ...] = ()
    manifest_fingerprint: ContentFingerprint | None = None
    schema_version: int = NC_EXPORT_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != NC_EXPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported NC artifact manifest version")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("NC artifact manifest project ID is invalid")
        if not isinstance(self.entries, tuple) or any(
            not isinstance(item, NCArtifactManifestEntry) for item in self.entries
        ):
            raise CamValidationError("NC artifact manifest entries are invalid")
        entries = tuple(sorted(self.entries, key=lambda item: str(item.artifact_id)))
        if any(item.project_id != self.project_id for item in entries):
            raise CamInvariantError("NC artifact entry belongs to another project")
        if len({item.artifact_id for item in entries}) != len(entries):
            raise CamInvariantError("NC artifact manifest contains duplicate IDs")
        current_paths = [
            item.output_relative_path.casefold()
            for item in entries
            if item.status is NCArtifactStatus.CURRENT
        ]
        if len(set(current_paths)) != len(current_paths):
            raise CamInvariantError("NC artifact manifest contains duplicate current output paths")
        object.__setattr__(self, "entries", entries)
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.manifest_fingerprint is None:
            object.__setattr__(self, "manifest_fingerprint", calculated)
        elif self.manifest_fingerprint != calculated:
            raise CamInvariantError("NC artifact manifest fingerprint verification failed")

    def identity_payload(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.export_codec import manifest_to_dict

        return manifest_to_dict(self, include_fingerprint=False)

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.export_codec import manifest_to_dict

        return manifest_to_dict(self)


@dataclass(frozen=True, slots=True)
class NCExportResult:
    request_id: NCExportRequestId
    result_id: NCExportResultId
    artifact_id: NCArtifactId
    source_post_result_id: PostResultId
    source_post_input_fingerprint: DependencyFingerprint
    source_post_result_fingerprint: ContentFingerprint
    production_profile_fingerprint: ContentFingerprint
    project_managed_relative_path: str
    target_kind: ExportTarget
    target_identifier: str | None
    byte_length: int
    sha256: str
    status: NCExportStatus
    diagnostics: tuple[NCExportDiagnostic, ...]
    statistics: NCExportStatistics
    result_fingerprint: ContentFingerprint | None = None
    schema_version: int = NC_EXPORT_VERSION
    external_path: Path | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.schema_version != NC_EXPORT_VERSION:
            raise UnsupportedCamSchemaError("Unsupported NC export result version")
        for value, typ in (
            (self.request_id, NCExportRequestId),
            (self.result_id, NCExportResultId),
            (self.artifact_id, NCArtifactId),
            (self.source_post_result_id, PostResultId),
        ):
            if not isinstance(value, typ):
                raise CamValidationError("NC export result identity is invalid")
        if not isinstance(self.source_post_input_fingerprint, DependencyFingerprint):
            raise CamValidationError("NC export input fingerprint is invalid")
        for value in (self.source_post_result_fingerprint, self.production_profile_fingerprint):
            if not isinstance(value, ContentFingerprint):
                raise CamValidationError("NC export result fingerprint provenance is invalid")
        object.__setattr__(
            self,
            "project_managed_relative_path",
            _relative_path(self.project_managed_relative_path, "Managed NC output path"),
        )
        if not isinstance(self.target_kind, ExportTarget):
            raise CamValidationError("NC export result target is invalid")
        if self.target_identifier is not None:
            object.__setattr__(
                self,
                "target_identifier",
                _non_empty_text(self.target_identifier, "NC export target identifier", maximum=128),
            )
        if type(self.byte_length) is not int or self.byte_length <= 0:
            raise CamValidationError("NC export result byte length is invalid")
        object.__setattr__(self, "sha256", _sha256(self.sha256))
        if not isinstance(self.status, NCExportStatus):
            raise CamValidationError("NC export result status is invalid")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, NCExportDiagnostic) for item in self.diagnostics
        ):
            raise CamValidationError("NC export result diagnostics are invalid")
        if not isinstance(self.statistics, NCExportStatistics):
            raise CamValidationError("NC export result statistics are invalid")
        if self.external_path is not None and not isinstance(self.external_path, Path):
            raise CamValidationError("NC export external runtime path is invalid")
        calculated = ContentFingerprint.from_payload(self.identity_payload())
        if self.result_fingerprint is None:
            object.__setattr__(self, "result_fingerprint", calculated)
        elif self.result_fingerprint != calculated:
            raise CamInvariantError("NC export result fingerprint verification failed")

    def identity_payload(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.export_codec import result_to_dict

        data = result_to_dict(self, include_fingerprint=False)
        data.pop("request_id")
        data.pop("result_id")
        data.pop("target_identifier")
        return data

    def to_dict(self) -> dict[str, Any]:
        from hms_cadcam.cam.post.export_codec import result_to_dict

        return result_to_dict(self)

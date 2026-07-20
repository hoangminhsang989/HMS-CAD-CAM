"""Strict deterministic JSON codecs for NC export artifacts and results."""

from __future__ import annotations

import json
from typing import Any, TypeVar
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import (
    NCArtifactId,
    NCExportRequestId,
    NCExportResultId,
    OperationId,
    PostProcessorDefinitionId,
    PostResultId,
    ProductionControllerProfileId,
    ToolpathArtifactId,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.post.codec import (
    diagnostic_from_dict as post_diagnostic_from_dict,
    diagnostic_to_dict as post_diagnostic_to_dict,
    statistics_from_dict as post_statistics_from_dict,
    statistics_to_dict as post_statistics_to_dict,
)
from hms_cadcam.cam.post.export_model import (
    NC_ARTIFACT_ENTRY_FORMAT,
    NC_ARTIFACT_MANIFEST_FORMAT,
    NC_EXPORT_FORMAT,
    NC_EXPORT_RESULT_FORMAT,
    NC_EXPORT_VERSION,
    ExportOverwritePolicy,
    ExportTarget,
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


T = TypeVar("T")


def _strict(data: Any, format_name: str, fields: set[str]) -> None:
    if not isinstance(data, dict) or set(data) != fields | {"format", "format_version"}:
        raise CamValidationError(f"{format_name} payload is malformed")
    if data["format"] != format_name:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} format")
    if type(data["format_version"]) is not int or data["format_version"] != NC_EXPORT_VERSION:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} version")


def _enum(enum_type: type[T], value: Any, name: str) -> T:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise CamValidationError(f"{name} is invalid") from error


def _id(id_type: type[T], value: Any, name: str) -> T:
    try:
        return id_type.parse(value)
    except (TypeError, ValueError, CamValidationError) as error:
        raise CamValidationError(f"{name} is invalid") from error


def _uuid(value: Any, name: str) -> UUID:
    try:
        result = UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise CamValidationError(f"{name} is invalid") from error
    if result.int == 0:
        raise CamValidationError(f"{name} is invalid")
    return result


def diagnostic_to_dict(value: NCExportDiagnostic) -> dict[str, Any]:
    return {
        "format": "HMS_CAM_NC_EXPORT_DIAGNOSTIC",
        "format_version": value.schema_version,
        "severity": value.severity.value,
        "code": value.code.value,
        "message_key": value.message_key,
        "evidence": [list(item) for item in value.evidence],
    }


def diagnostic_from_dict(data: dict[str, Any]) -> NCExportDiagnostic:
    _strict(
        data,
        "HMS_CAM_NC_EXPORT_DIAGNOSTIC",
        {"severity", "code", "message_key", "evidence"},
    )
    if not isinstance(data["evidence"], list):
        raise CamValidationError("NC export diagnostic evidence is malformed")
    try:
        evidence = tuple(tuple(item) for item in data["evidence"])
    except TypeError as error:
        raise CamValidationError("NC export diagnostic evidence is malformed") from error
    return NCExportDiagnostic(
        _enum(DiagnosticSeverity, data["severity"], "Diagnostic severity"),
        _enum(NCExportDiagnosticCode, data["code"], "Diagnostic code"),
        data["message_key"],
        evidence,
        data["format_version"],
    )


def export_statistics_to_dict(value: NCExportStatistics) -> dict[str, Any]:
    return {
        "format": "HMS_CAM_NC_EXPORT_STATISTICS",
        "format_version": value.schema_version,
        "byte_length": value.byte_length,
        "files_written": value.files_written,
        "checksum_verifications": value.checksum_verifications,
    }


def export_statistics_from_dict(data: dict[str, Any]) -> NCExportStatistics:
    _strict(
        data,
        "HMS_CAM_NC_EXPORT_STATISTICS",
        {"byte_length", "files_written", "checksum_verifications"},
    )
    return NCExportStatistics(
        data["byte_length"],
        data["files_written"],
        data["checksum_verifications"],
        data["format_version"],
    )


def request_to_dict(value: NCExportRequest) -> dict[str, Any]:
    return {
        "format": NC_EXPORT_FORMAT,
        "format_version": value.schema_version,
        "request_id": str(value.request_id),
        "project_id": str(value.project_id),
        "operation_id": str(value.operation_id),
        "source_artifact_id": str(value.source_artifact_id),
        "post_result_id": str(value.post_result_id),
        "filename": value.filename,
        "target": value.target.value,
        "overwrite_policy": value.overwrite_policy.value,
        "create_target_directory": value.create_target_directory,
    }


def request_from_dict(data: dict[str, Any]) -> NCExportRequest:
    _strict(
        data,
        NC_EXPORT_FORMAT,
        {
            "request_id",
            "project_id",
            "operation_id",
            "source_artifact_id",
            "post_result_id",
            "filename",
            "target",
            "overwrite_policy",
            "create_target_directory",
        },
    )
    return NCExportRequest(
        project_id=_uuid(data["project_id"], "Project ID"),
        operation_id=_id(OperationId, data["operation_id"], "Operation ID"),
        source_artifact_id=_id(ToolpathArtifactId, data["source_artifact_id"], "Artifact ID"),
        post_result_id=_id(PostResultId, data["post_result_id"], "Post result ID"),
        filename=data["filename"],
        target=_enum(ExportTarget, data["target"], "Export target"),
        overwrite_policy=_enum(
            ExportOverwritePolicy, data["overwrite_policy"], "Overwrite policy"
        ),
        create_target_directory=data["create_target_directory"],
        request_id=_id(NCExportRequestId, data["request_id"], "Export request ID"),
        schema_version=data["format_version"],
    )


def entry_to_dict(
    value: NCArtifactManifestEntry, *, include_fingerprint: bool = True
) -> dict[str, Any]:
    data = {
        "format": NC_ARTIFACT_ENTRY_FORMAT,
        "format_version": value.schema_version,
        "artifact_id": str(value.artifact_id),
        "project_id": str(value.project_id),
        "operation_id": str(value.operation_id),
        "source_artifact_id": str(value.source_artifact_id),
        "source_artifact_fingerprint": value.source_artifact_fingerprint.to_dict(),
        "post_result_id": str(value.post_result_id),
        "post_input_fingerprint": value.post_input_fingerprint.to_dict(),
        "post_result_fingerprint": value.post_result_fingerprint.to_dict(),
        "post_definition_id": str(value.post_definition_id),
        "production_profile_id": str(value.production_profile_id),
        "production_profile_version": value.production_profile_version,
        "production_profile_fingerprint": value.production_profile_fingerprint.to_dict(),
        "tool_binding_fingerprint": value.tool_binding_fingerprint.to_dict(),
        "program_context_fingerprint": value.program_context_fingerprint.to_dict(),
        "output_relative_path": value.output_relative_path,
        "metadata_relative_path": value.metadata_relative_path,
        "byte_length": value.byte_length,
        "sha256": value.sha256,
        "newline": value.newline,
        "encoding": value.encoding,
        "extension": value.extension,
        "status": value.status.value,
        "post_diagnostics": [post_diagnostic_to_dict(item) for item in value.post_diagnostics],
        "post_statistics": post_statistics_to_dict(value.post_statistics),
    }
    if include_fingerprint:
        data["artifact_fingerprint"] = value.artifact_fingerprint.to_dict()
    return data


def entry_from_dict(data: dict[str, Any]) -> NCArtifactManifestEntry:
    fields = {
        "artifact_id",
        "project_id",
        "operation_id",
        "source_artifact_id",
        "source_artifact_fingerprint",
        "post_result_id",
        "post_input_fingerprint",
        "post_result_fingerprint",
        "post_definition_id",
        "production_profile_id",
        "production_profile_version",
        "production_profile_fingerprint",
        "tool_binding_fingerprint",
        "program_context_fingerprint",
        "output_relative_path",
        "metadata_relative_path",
        "byte_length",
        "sha256",
        "newline",
        "encoding",
        "extension",
        "status",
        "post_diagnostics",
        "post_statistics",
        "artifact_fingerprint",
    }
    _strict(data, NC_ARTIFACT_ENTRY_FORMAT, fields)
    if not isinstance(data["post_diagnostics"], list):
        raise CamValidationError("NC artifact diagnostics are malformed")
    return NCArtifactManifestEntry(
        artifact_id=_id(NCArtifactId, data["artifact_id"], "NC artifact ID"),
        project_id=_uuid(data["project_id"], "Project ID"),
        operation_id=_id(OperationId, data["operation_id"], "Operation ID"),
        source_artifact_id=_id(
            ToolpathArtifactId, data["source_artifact_id"], "Source artifact ID"
        ),
        source_artifact_fingerprint=ContentFingerprint.from_dict(
            data["source_artifact_fingerprint"]
        ),
        post_result_id=_id(PostResultId, data["post_result_id"], "Post result ID"),
        post_input_fingerprint=DependencyFingerprint.from_dict(data["post_input_fingerprint"]),
        post_result_fingerprint=ContentFingerprint.from_dict(data["post_result_fingerprint"]),
        post_definition_id=_id(
            PostProcessorDefinitionId, data["post_definition_id"], "Post definition ID"
        ),
        production_profile_id=_id(
            ProductionControllerProfileId,
            data["production_profile_id"],
            "Production profile ID",
        ),
        production_profile_version=data["production_profile_version"],
        production_profile_fingerprint=ContentFingerprint.from_dict(
            data["production_profile_fingerprint"]
        ),
        tool_binding_fingerprint=ContentFingerprint.from_dict(data["tool_binding_fingerprint"]),
        program_context_fingerprint=ContentFingerprint.from_dict(
            data["program_context_fingerprint"]
        ),
        output_relative_path=data["output_relative_path"],
        metadata_relative_path=data["metadata_relative_path"],
        byte_length=data["byte_length"],
        sha256=data["sha256"],
        newline=data["newline"],
        encoding=data["encoding"],
        extension=data["extension"],
        status=_enum(NCArtifactStatus, data["status"], "NC artifact status"),
        post_diagnostics=tuple(post_diagnostic_from_dict(item) for item in data["post_diagnostics"]),
        post_statistics=post_statistics_from_dict(data["post_statistics"]),
        artifact_fingerprint=ContentFingerprint.from_dict(data["artifact_fingerprint"]),
        schema_version=data["format_version"],
    )


def manifest_to_dict(
    value: NCArtifactManifest, *, include_fingerprint: bool = True
) -> dict[str, Any]:
    data = {
        "format": NC_ARTIFACT_MANIFEST_FORMAT,
        "format_version": value.schema_version,
        "project_id": str(value.project_id),
        "entries": [entry_to_dict(item) for item in value.entries],
    }
    if include_fingerprint:
        data["manifest_fingerprint"] = value.manifest_fingerprint.to_dict()
    return data


def manifest_from_dict(data: dict[str, Any]) -> NCArtifactManifest:
    _strict(
        data,
        NC_ARTIFACT_MANIFEST_FORMAT,
        {"project_id", "entries", "manifest_fingerprint"},
    )
    if not isinstance(data["entries"], list):
        raise CamValidationError("NC artifact manifest entries are malformed")
    return NCArtifactManifest(
        project_id=_uuid(data["project_id"], "Manifest project ID"),
        entries=tuple(entry_from_dict(item) for item in data["entries"]),
        manifest_fingerprint=ContentFingerprint.from_dict(data["manifest_fingerprint"]),
        schema_version=data["format_version"],
    )


def result_to_dict(
    value: NCExportResult, *, include_fingerprint: bool = True
) -> dict[str, Any]:
    data = {
        "format": NC_EXPORT_RESULT_FORMAT,
        "format_version": value.schema_version,
        "request_id": str(value.request_id),
        "result_id": str(value.result_id),
        "artifact_id": str(value.artifact_id),
        "source_post_result_id": str(value.source_post_result_id),
        "source_post_input_fingerprint": value.source_post_input_fingerprint.to_dict(),
        "source_post_result_fingerprint": value.source_post_result_fingerprint.to_dict(),
        "production_profile_fingerprint": value.production_profile_fingerprint.to_dict(),
        "project_managed_relative_path": value.project_managed_relative_path,
        "target_kind": value.target_kind.value,
        "target_identifier": value.target_identifier,
        "byte_length": value.byte_length,
        "sha256": value.sha256,
        "status": value.status.value,
        "diagnostics": [diagnostic_to_dict(item) for item in value.diagnostics],
        "statistics": export_statistics_to_dict(value.statistics),
    }
    if include_fingerprint:
        data["result_fingerprint"] = value.result_fingerprint.to_dict()
    return data


def result_from_dict(data: dict[str, Any]) -> NCExportResult:
    _strict(
        data,
        NC_EXPORT_RESULT_FORMAT,
        {
            "request_id",
            "result_id",
            "artifact_id",
            "source_post_result_id",
            "source_post_input_fingerprint",
            "source_post_result_fingerprint",
            "production_profile_fingerprint",
            "project_managed_relative_path",
            "target_kind",
            "target_identifier",
            "byte_length",
            "sha256",
            "status",
            "diagnostics",
            "statistics",
            "result_fingerprint",
        },
    )
    if not isinstance(data["diagnostics"], list):
        raise CamValidationError("NC export result diagnostics are malformed")
    return NCExportResult(
        request_id=_id(NCExportRequestId, data["request_id"], "Export request ID"),
        result_id=_id(NCExportResultId, data["result_id"], "Export result ID"),
        artifact_id=_id(NCArtifactId, data["artifact_id"], "NC artifact ID"),
        source_post_result_id=_id(
            PostResultId, data["source_post_result_id"], "Source post result ID"
        ),
        source_post_input_fingerprint=DependencyFingerprint.from_dict(
            data["source_post_input_fingerprint"]
        ),
        source_post_result_fingerprint=ContentFingerprint.from_dict(
            data["source_post_result_fingerprint"]
        ),
        production_profile_fingerprint=ContentFingerprint.from_dict(
            data["production_profile_fingerprint"]
        ),
        project_managed_relative_path=data["project_managed_relative_path"],
        target_kind=_enum(ExportTarget, data["target_kind"], "Export target"),
        target_identifier=data["target_identifier"],
        byte_length=data["byte_length"],
        sha256=data["sha256"],
        status=_enum(NCExportStatus, data["status"], "Export status"),
        diagnostics=tuple(diagnostic_from_dict(item) for item in data["diagnostics"]),
        statistics=export_statistics_from_dict(data["statistics"]),
        result_fingerprint=ContentFingerprint.from_dict(data["result_fingerprint"]),
        schema_version=data["format_version"],
    )


ExportCodecValue = (
    NCExportRequest
    | NCExportResult
    | NCArtifactManifestEntry
    | NCArtifactManifest
)


def dumps(value: ExportCodecValue) -> str:
    if isinstance(value, NCExportRequest):
        payload = request_to_dict(value)
    elif isinstance(value, NCExportResult):
        payload = result_to_dict(value)
    elif isinstance(value, NCArtifactManifestEntry):
        payload = entry_to_dict(value)
    elif isinstance(value, NCArtifactManifest):
        payload = manifest_to_dict(value)
    else:
        raise TypeError(f"Unsupported NC export codec value: {type(value)!r}")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def loads(text: str) -> ExportCodecValue:
    if not isinstance(text, str):
        raise CamValidationError("NC export JSON must be text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CamValidationError("NC export JSON is malformed") from error
    if not isinstance(data, dict):
        raise CamValidationError("NC export JSON root must be an object")
    format_name = data.get("format")
    if format_name == NC_EXPORT_FORMAT:
        return request_from_dict(data)
    if format_name == NC_EXPORT_RESULT_FORMAT:
        return result_from_dict(data)
    if format_name == NC_ARTIFACT_ENTRY_FORMAT:
        return entry_from_dict(data)
    if format_name == NC_ARTIFACT_MANIFEST_FORMAT:
        return manifest_from_dict(data)
    raise UnsupportedCamSchemaError("Unsupported NC export JSON format")


def json_bytes(value: ExportCodecValue) -> bytes:
    """Return canonical UTF-8 JSON bytes used by manifest and sidecars."""
    return (dumps(value) + "\n").encode("utf-8")

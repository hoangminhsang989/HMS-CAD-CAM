"""Strict JSON codec for simulation request/result models."""

from __future__ import annotations

from typing import Any, TypeVar

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import (
    FixtureInstanceId, MachineDefinitionId, OperationId, SetupId,
    SimulationRequestId, SimulationResultId, ToolAssemblyId,
    ToolDefinitionId, HolderDefinitionId, ToolpathArtifactId,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.spatial import Point3
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.toolpath.geometry import Bounds3
from .model import (
    SIMULATION_FORMAT, SIMULATION_VERSION, SimulationIssue, SimulationIssueCategory,
    SimulationIssueCode, SimulationRequest, SimulationResult, SimulationSamplingPolicy,
    SimulationStatistics, SimulationStatus,
)

T = TypeVar("T")


def _exact(data: Any, fields: set[str], *, format_name: str = SIMULATION_FORMAT, version: int = SIMULATION_VERSION) -> dict[str, Any]:
    if not isinstance(data, dict) or set(data) != fields | {"format", "format_version"}:
        raise CamValidationError("Simulation payload is malformed")
    if data["format"] != format_name:
        raise UnsupportedCamSchemaError("Unsupported simulation format")
    if type(data["format_version"]) is not int or data["format_version"] != version:
        raise UnsupportedCamSchemaError("Unsupported simulation version")
    return data


def _id(cls: type[T], value: Any) -> T:
    return cls.parse(value)


def _fp(data: Any, cls: type[ContentFingerprint] = ContentFingerprint) -> ContentFingerprint:
    if not isinstance(data, dict):
        raise CamValidationError("Fingerprint payload is malformed")
    return cls.from_dict(data)


def policy_from_dict(data: dict[str, Any]) -> SimulationSamplingPolicy:
    payload = _exact(data, {"max_linear_step", "chord_tolerance", "max_arc_angle", "geometric_tolerance", "maximum_samples", "chunk_size", "cancellation_check_interval", "maximum_issues", "memory_budget_bytes"})
    return SimulationSamplingPolicy(**{key: payload[key] for key in ("max_linear_step", "chord_tolerance", "max_arc_angle", "geometric_tolerance", "maximum_samples", "chunk_size", "cancellation_check_interval", "maximum_issues", "memory_budget_bytes")})


def issue_from_dict(data: dict[str, Any]) -> SimulationIssue:
    fields = {"severity", "category", "code", "message_key", "operation_id", "artifact_id", "segment_index", "event_index", "sample_index", "world_point", "bounds", "involved_entities", "evidence"}
    if not isinstance(data, dict) or set(data) != fields:
        raise CamValidationError("Simulation issue payload is malformed")
    try:
        severity = DiagnosticSeverity(data["severity"])
        category = SimulationIssueCategory(data["category"])
        code = SimulationIssueCode(data["code"])
    except (TypeError, ValueError) as error:
        raise CamValidationError("Simulation issue enum is invalid") from error
    entities = data["involved_entities"]
    evidence = data["evidence"]
    if not isinstance(entities, list) or not isinstance(evidence, list) or any(not isinstance(pair, list) or len(pair) != 2 for pair in evidence):
        raise CamValidationError("Simulation issue evidence payload is malformed")
    return SimulationIssue(
        severity=severity, category=category, code=code, message_key=data["message_key"],
        operation_id=_id(OperationId, data["operation_id"]), artifact_id=_id(ToolpathArtifactId, data["artifact_id"]),
        segment_index=data["segment_index"], event_index=data["event_index"], sample_index=data["sample_index"],
        world_point=Point3.from_dict(data["world_point"]) if data["world_point"] is not None else None,
        bounds=Bounds3.from_dict(data["bounds"]) if data["bounds"] is not None else None,
        involved_entities=tuple(entities), evidence=tuple(tuple(pair) for pair in evidence),
    )


def statistics_from_dict(data: dict[str, Any]) -> SimulationStatistics:
    payload = _exact(data, {"sampled_point_count", "sampled_segment_count", "collision_count", "warning_count", "error_count", "bounds"})
    return SimulationStatistics(payload["sampled_point_count"], payload["sampled_segment_count"], payload["collision_count"], payload["warning_count"], payload["error_count"], Bounds3.from_dict(payload["bounds"]))


def request_from_dict(data: dict[str, Any]) -> SimulationRequest:
    fields = {"request_id", "algorithm_version", "operation_id", "operation_revision", "artifact_id", "artifact_fingerprint", "input_fingerprint", "setup_id", "setup_revision", "wcs_fingerprint", "stock_fingerprint", "fixtures", "tool_assembly_id", "tool_assembly_fingerprint", "tool_id", "tool_fingerprint", "holder_id", "holder_fingerprint", "machine_id", "machine_fingerprint", "unit", "sampling_policy", "safe_height"}
    payload = _exact(data, fields)
    fixtures = payload["fixtures"]
    if not isinstance(fixtures, list) or any(not isinstance(item, list) or len(item) != 2 for item in fixtures):
        raise CamValidationError("Fixture provenance payload is malformed")
    try:
        unit = LengthUnit(payload["unit"])
    except (TypeError, ValueError) as error:
        raise CamValidationError("Simulation request unit is invalid") from error
    return SimulationRequest(
        request_id=_id(SimulationRequestId, payload["request_id"]), operation_id=_id(OperationId, payload["operation_id"]), operation_revision=Revision(payload["operation_revision"]), artifact_id=_id(ToolpathArtifactId, payload["artifact_id"]), artifact_fingerprint=_fp(payload["artifact_fingerprint"]), input_fingerprint=_fp(payload["input_fingerprint"], DependencyFingerprint), setup_id=_id(SetupId, payload["setup_id"]), setup_revision=Revision(payload["setup_revision"]), wcs_fingerprint=_fp(payload["wcs_fingerprint"]), stock_fingerprint=_fp(payload["stock_fingerprint"]), fixture_fingerprints=tuple((_id(FixtureInstanceId, item[0]), _fp(item[1])) for item in fixtures), tool_assembly_id=_id(ToolAssemblyId, payload["tool_assembly_id"]), tool_assembly_fingerprint=_fp(payload["tool_assembly_fingerprint"]), tool_id=_id(ToolDefinitionId, payload["tool_id"]), tool_fingerprint=_fp(payload["tool_fingerprint"]), holder_id=_id(HolderDefinitionId, payload["holder_id"]) if payload["holder_id"] is not None else None, holder_fingerprint=_fp(payload["holder_fingerprint"]) if payload["holder_fingerprint"] is not None else None, machine_id=_id(MachineDefinitionId, payload["machine_id"]) if payload["machine_id"] is not None else None, machine_fingerprint=_fp(payload["machine_fingerprint"]) if payload["machine_fingerprint"] is not None else None, unit=unit, sampling_policy=policy_from_dict(payload["sampling_policy"]), safe_height=payload["safe_height"], algorithm_version=payload["algorithm_version"],
    )


def result_from_dict(data: dict[str, Any]) -> SimulationResult:
    fields = {"algorithm_version", "result_id", "request_id", "operation_id", "artifact_id", "artifact_fingerprint", "input_fingerprint", "sampling_policy", "status", "issues", "statistics", "result_fingerprint"}
    payload = _exact(data, fields)
    try:
        status = SimulationStatus(payload["status"])
    except (TypeError, ValueError) as error:
        raise CamValidationError("Simulation status is invalid") from error
    issues = payload["issues"]
    if not isinstance(issues, list):
        raise CamValidationError("Simulation issues payload is malformed")
    result = SimulationResult(
        result_id=_id(SimulationResultId, payload["result_id"]), request_id=_id(SimulationRequestId, payload["request_id"]), operation_id=_id(OperationId, payload["operation_id"]), artifact_id=_id(ToolpathArtifactId, payload["artifact_id"]), artifact_fingerprint=_fp(payload["artifact_fingerprint"]), input_fingerprint=_fp(payload["input_fingerprint"], DependencyFingerprint), sampling_policy=policy_from_dict(payload["sampling_policy"]), status=status, issues=tuple(issue_from_dict(item) for item in issues), statistics=statistics_from_dict(payload["statistics"]), result_fingerprint=_fp(payload["result_fingerprint"]), algorithm_version=payload["algorithm_version"],
    )
    return result


def dumps(value: SimulationRequest | SimulationResult) -> str:
    import json
    return json.dumps(value.to_dict(), ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"))


def loads_request(text: str) -> SimulationRequest:
    import json
    return request_from_dict(json.loads(text))


def loads_result(text: str) -> SimulationResult:
    import json
    return result_from_dict(json.loads(text))

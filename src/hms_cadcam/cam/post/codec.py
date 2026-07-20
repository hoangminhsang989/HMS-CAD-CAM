"""Strict deterministic codecs for the 7D.1 post-processing contracts."""

from __future__ import annotations

import json
from typing import Any, TypeVar
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import (
    HolderDefinitionId, MachineDefinitionId, NCProgramId, OperationId,
    PostProcessorDefinitionId, PostRequestId, PostResultId, SetupId,
    ProductionControllerProfileId, ToolAssemblyId, ToolDefinitionId, ToolpathArtifactId,
)
from hms_cadcam.cam.domain.machine import MachineKind, OperationCapability, SpindleDirection, TappingMode
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.setup import WorkOffset
from hms_cadcam.cam.domain.spatial import Point3, Vector3, WcsFrame
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, LengthUnit, SpindleSpeed, SpindleSpeedUnit
from hms_cadcam.cam.toolpath.events import CoolantState, FeedMode, MotionClass, SpindleState
from hms_cadcam.cam.toolpath.geometry import Pose
from hms_cadcam.cam.post.model import (
    ArcCenterFormat, ArcMotionRecord, CoordinateMode, CoordinateModeRecord, CoolantRecord,
    DwellRecord, FeedModeRecord, FeedValueRecord, LinearMotionRecord, LoweringPolicy,
    NCProgramIR, NC_PROGRAM_FORMAT, NC_PROGRAM_VERSION, NCRecord, NCRecordKind,
    Plane, PlaneRecord, PostDiagnostic, PostDiagnosticCode, PostProcessorCapabilities,
    PostProcessorDefinition, PostRequest, PostResult, PostResultStatus, PostStatistics,
    POST_FORMAT, POST_RESULT_FORMAT, POST_VERSION, ProgramBeginRecord, ProgramEndRecord,
    RapidMotionRecord, SemanticMarkerRecord, SimulationGateMode, SimulationGatePolicy,
    SpindleDirectionRecord, SpindleStartRecord, SpindleStopRecord, ToolActivationRecord,
    UnitsRecord, WorkOffsetRecord, NCRecordUnion,
)
from hms_cadcam.cam.post.profile import (
    PROGRAM_CONTEXT_FORMAT, PRODUCTION_PROFILE_FORMAT, TOOL_BINDING_FORMAT,
    ControllerToolBinding, ProductionControllerProfile, ProductionProgramContext,
    profile_from_dict, profile_to_dict,
)

T = TypeVar("T")


def _strict(data: Any, *, format_name: str, version: int, fields: set[str]) -> None:
    if not isinstance(data, dict) or set(data) != fields | {"format", "format_version"}:
        raise CamValidationError(f"{format_name} payload is malformed")
    if data["format"] != format_name:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} format")
    if type(data["format_version"]) is not int or data["format_version"] != version:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} version")


def _enum(enum_type: type[T], value: Any, name: str) -> T:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as error:
        raise CamValidationError(f"{name} payload is invalid") from error


def _id(cls: type[T], value: Any, name: str) -> T:
    try:
        return cls.parse(value)
    except (TypeError, ValueError, CamValidationError) as error:
        raise CamValidationError(f"{name} payload is invalid") from error


def _fp(data: Any, cls: type[ContentFingerprint] = ContentFingerprint) -> ContentFingerprint:
    return cls.from_dict(data)


def _typed_fp(data: Any) -> ContentFingerprint:
    if not isinstance(data, dict):
        raise CamValidationError("Fingerprint payload is malformed")
    return DependencyFingerprint.from_dict(data) if data.get("kind") == DependencyFingerprint.KIND else ContentFingerprint.from_dict(data)


def _length(value: Any) -> dict[str, Any]:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CamValidationError("Numeric quantity is invalid")
    return {"value": value}


def _feed_to_dict(value: FeedRate | None) -> dict[str, Any] | None:
    return None if value is None else {"value": value.value, "unit": value.unit.value}


def _feed_from_dict(value: Any) -> FeedRate | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"value", "unit"}:
        raise CamValidationError("Feed payload is malformed")
    return FeedRate(value["value"], _enum(FeedUnit, value["unit"], "Feed unit"))


def _spindle_to_dict(value: SpindleSpeed) -> dict[str, Any]:
    return {"value": value.value, "unit": value.unit.value}


def _spindle_from_dict(value: Any) -> SpindleSpeed:
    if not isinstance(value, dict) or set(value) != {"value", "unit"}:
        raise CamValidationError("Spindle payload is malformed")
    return SpindleSpeed(value["value"], _enum(SpindleSpeedUnit, value["unit"], "Spindle unit"))


def _evidence_to_dict(value: tuple[tuple[str, str], ...]) -> list[list[str]]:
    return [[key, item] for key, item in value]


def _evidence_from_dict(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or any(not isinstance(item, list) or len(item) != 2 or not all(isinstance(part, str) and part for part in item) for item in value):
        raise CamValidationError("Evidence payload is malformed")
    return tuple((item[0], item[1]) for item in value)


def diagnostic_to_dict(value: PostDiagnostic) -> dict[str, Any]:
    return {"format": POST_FORMAT, "format_version": POST_VERSION, "severity": value.severity.value,
            "code": value.code.value, "message_key": value.message_key,
            "operation_id": str(value.operation_id) if value.operation_id else None,
            "artifact_id": str(value.artifact_id) if value.artifact_id else None,
            "event_index": value.event_index, "record_index": value.record_index,
            "evidence": _evidence_to_dict(value.evidence), "schema_version": value.schema_version}


def diagnostic_from_dict(data: dict[str, Any]) -> PostDiagnostic:
    _strict(data, format_name=POST_FORMAT, version=POST_VERSION,
            fields={"severity", "code", "message_key", "operation_id", "artifact_id", "event_index", "record_index", "evidence", "schema_version"})
    return PostDiagnostic(_enum(DiagnosticSeverity, data["severity"], "Diagnostic severity"),
        _enum(PostDiagnosticCode, data["code"], "Diagnostic code"), data["message_key"],
        _id(OperationId, data["operation_id"], "Operation") if data["operation_id"] else None,
        _id(ToolpathArtifactId, data["artifact_id"], "Artifact") if data["artifact_id"] else None,
        data["event_index"], data["record_index"], _evidence_from_dict(data["evidence"]), data["schema_version"])


def statistics_to_dict(value: PostStatistics) -> dict[str, Any]:
    return {"format": POST_FORMAT, "format_version": POST_VERSION, "record_count": value.record_count,
            "motion_count": value.motion_count, "rapid_count": value.rapid_count,
            "linear_count": value.linear_count, "arc_count": value.arc_count,
            "dwell_count": value.dwell_count, "total_rapid_length": value.total_rapid_length,
            "total_cutting_length": value.total_cutting_length, "total_link_length": value.total_link_length,
            "total_retract_length": value.total_retract_length, "total_arc_length": value.total_arc_length,
            "dwell_seconds": value.dwell_seconds, "schema_version": value.schema_version}


def statistics_from_dict(data: dict[str, Any]) -> PostStatistics:
    _strict(data, format_name=POST_FORMAT, version=POST_VERSION,
            fields={"record_count", "motion_count", "rapid_count", "linear_count", "arc_count", "dwell_count", "total_rapid_length", "total_cutting_length", "total_link_length", "total_retract_length", "total_arc_length", "dwell_seconds", "schema_version"})
    return PostStatistics(data["record_count"], data["motion_count"], data["rapid_count"], data["linear_count"], data["arc_count"], data["dwell_count"], data["total_rapid_length"], data["total_cutting_length"], data["total_link_length"], data["total_retract_length"], data["total_arc_length"], data["dwell_seconds"], data["schema_version"])


def policy_to_dict(value: LoweringPolicy) -> dict[str, Any]:
    return value.to_dict()


def policy_from_dict(data: dict[str, Any]) -> LoweringPolicy:
    _strict(data, format_name="HMS_CAM_POST_LOWERING_POLICY", version=POST_VERSION,
            fields={"preserve_motion", "preserve_semantic_markers", "allow_canned_cycles", "allow_arc_to_line"})
    return LoweringPolicy(data["preserve_motion"], data["preserve_semantic_markers"], data["allow_canned_cycles"], data["allow_arc_to_line"], data["format_version"])


def simulation_gate_policy_to_dict(value: SimulationGatePolicy) -> dict[str, Any]:
    return value.to_dict()


def simulation_gate_policy_from_dict(data: dict[str, Any]) -> SimulationGatePolicy:
    _strict(data, format_name="HMS_CAM_POST_SIMULATION_GATE", version=POST_VERSION, fields={"mode"})
    return SimulationGatePolicy(_enum(SimulationGateMode, data["mode"], "Simulation gate mode"), data["format_version"])


def capabilities_to_dict(value: PostProcessorCapabilities) -> dict[str, Any]:
    return {"format": POST_FORMAT, "format_version": POST_VERSION,
            "supported_machine_kinds": [item.value for item in value.supported_machine_kinds],
            "supported_axes": list(value.supported_axes), "supported_units": [item.value for item in value.supported_units],
            "supported_feed_modes": [item.value for item in value.supported_feed_modes],
            "supported_spindle_directions": [item.value for item in value.supported_spindle_directions],
            "supported_coolant_modes": [item.value for item in value.supported_coolant_modes],
            "supported_arc_planes": [item.value for item in value.supported_arc_planes],
            "arc_center_formats": [item.value for item in value.arc_center_formats],
            "supported_operation_strategies": list(value.supported_operation_strategies),
            "supported_operation_capabilities": [item.value for item in value.supported_operation_capabilities],
            "work_offset_supported": value.work_offset_supported, "tool_activation_supported": value.tool_activation_supported,
            "tapping_synchronization": value.tapping_synchronization, "tapping_modes": [item.value for item in value.tapping_modes],
            "minimum_rpm": value.minimum_rpm, "maximum_rpm": value.maximum_rpm, "maximum_feed": value.maximum_feed,
            "schema_version": value.schema_version}


def capabilities_from_dict(data: dict[str, Any]) -> PostProcessorCapabilities:
    _strict(data, format_name=POST_FORMAT, version=POST_VERSION,
            fields={"supported_machine_kinds", "supported_axes", "supported_units", "supported_feed_modes", "supported_spindle_directions", "supported_coolant_modes", "supported_arc_planes", "arc_center_formats", "supported_operation_strategies", "supported_operation_capabilities", "work_offset_supported", "tool_activation_supported", "tapping_synchronization", "tapping_modes", "minimum_rpm", "maximum_rpm", "maximum_feed", "schema_version"})
    lists = ("supported_machine_kinds", "supported_axes", "supported_units", "supported_feed_modes", "supported_spindle_directions", "supported_coolant_modes", "supported_arc_planes", "arc_center_formats", "supported_operation_strategies", "supported_operation_capabilities", "tapping_modes")
    if any(not isinstance(data[key], list) for key in lists):
        raise CamValidationError("Post capability collections must be lists")
    return PostProcessorCapabilities(
        tuple(_enum(MachineKind, item, "Machine kind") for item in data["supported_machine_kinds"]),
        tuple(data["supported_axes"]), tuple(_enum(LengthUnit, item, "Length unit") for item in data["supported_units"]),
        tuple(_enum(FeedMode, item, "Feed mode") for item in data["supported_feed_modes"]),
        tuple(_enum(SpindleDirection, item, "Spindle direction") for item in data["supported_spindle_directions"]),
        tuple(_enum(CoolantState, item, "Coolant mode") for item in data["supported_coolant_modes"]),
        tuple(_enum(Plane, item, "Arc plane") for item in data["supported_arc_planes"]),
        tuple(_enum(ArcCenterFormat, item, "Arc center format") for item in data["arc_center_formats"]),
        tuple(data["supported_operation_strategies"]), tuple(_enum(OperationCapability, item, "Operation capability") for item in data["supported_operation_capabilities"]),
        data["work_offset_supported"], data["tool_activation_supported"], data["tapping_synchronization"], tuple(_enum(TappingMode, item, "Tapping mode") for item in data["tapping_modes"]),
        data["minimum_rpm"], data["maximum_rpm"], data["maximum_feed"], data["schema_version"])


def definition_to_dict(value: PostProcessorDefinition) -> dict[str, Any]:
    data = {"format": POST_FORMAT, "format_version": POST_VERSION, "definition_id": str(value.definition_id),
            "definition_version": value.definition_version, "adapter_key": value.adapter_key, "adapter_version": value.adapter_version,
            "capabilities": capabilities_to_dict(value.capabilities), "numeric_precision": value.numeric_precision,
            "newline": value.newline, "encoding": value.encoding, "maximum_line_length": value.maximum_line_length,
            "maximum_program_size": value.maximum_program_size, "allow_comments": value.allow_comments,
            "comment_prefix": value.comment_prefix, "display_name": value.display_name, "schema_version": value.schema_version}
    if value.production_profile is not None:
        data["production_profile"] = profile_to_dict(value.production_profile)
    return data


def definition_from_dict(data: dict[str, Any]) -> PostProcessorDefinition:
    fields = {"definition_id", "definition_version", "adapter_key", "adapter_version", "capabilities", "numeric_precision", "newline", "encoding", "maximum_line_length", "maximum_program_size", "allow_comments", "comment_prefix", "display_name", "schema_version"}
    if "production_profile" in data:
        fields.add("production_profile")
    _strict(data, format_name=POST_FORMAT, version=POST_VERSION,
            fields=fields)
    return PostProcessorDefinition(_id(PostProcessorDefinitionId, data["definition_id"], "Post definition"), data["definition_version"], data["adapter_key"], data["adapter_version"], capabilities_from_dict(data["capabilities"]), data["numeric_precision"], data["newline"], data["encoding"], data["maximum_line_length"], data["maximum_program_size"], data["allow_comments"], data["comment_prefix"], data["display_name"], data["schema_version"], profile_from_dict(data["production_profile"]) if "production_profile" in data else None)


def request_to_dict(value: PostRequest) -> dict[str, Any]:
    data = {"format": POST_FORMAT, "format_version": POST_VERSION, "request_id": str(value.request_id), "project_id": str(value.project_id), "operation_id": str(value.operation_id), "artifact_id": str(value.artifact_id), "post_definition": definition_to_dict(value.post_definition), "lowering_policy": policy_to_dict(value.lowering_policy), "simulation_gate_policy": simulation_gate_policy_to_dict(value.simulation_gate_policy), "algorithm_version": value.algorithm_version}
    if value.program_context is not None:
        data["program_context"] = value.program_context.to_dict()
    return data


def request_from_dict(data: dict[str, Any]) -> PostRequest:
    fields = {"request_id", "project_id", "operation_id", "artifact_id", "post_definition", "lowering_policy", "simulation_gate_policy", "algorithm_version"}
    if "program_context" in data:
        fields.add("program_context")
    _strict(data, format_name=POST_FORMAT, version=POST_VERSION, fields=fields)
    try:
        project_id = UUID(data["project_id"])
    except (TypeError, ValueError) as error:
        raise CamValidationError("Project ID payload is invalid") from error
    return PostRequest(project_id, _id(OperationId, data["operation_id"], "Operation"), _id(ToolpathArtifactId, data["artifact_id"], "Artifact"), definition_from_dict(data["post_definition"]), policy_from_dict(data["lowering_policy"]), simulation_gate_policy_from_dict(data["simulation_gate_policy"]), _id(PostRequestId, data["request_id"], "Post request"), data["algorithm_version"], ProductionProgramContext.from_dict(data["program_context"]) if "program_context" in data else None)


def _common_record(value: NCRecord) -> dict[str, Any]:
    return {"sequence_index": value.sequence_index, "kind": value.kind.value}


def record_to_dict(value: NCRecordUnion) -> dict[str, Any]:
    data = _common_record(value)
    if isinstance(value, ProgramBeginRecord):
        data["metadata"] = _evidence_to_dict(value.metadata)
    elif isinstance(value, UnitsRecord):
        data["unit"] = value.unit.value
    elif isinstance(value, CoordinateModeRecord):
        data["mode"] = value.mode.value
    elif isinstance(value, PlaneRecord):
        data["plane"] = value.plane.value
    elif isinstance(value, WorkOffsetRecord):
        data["work_offset"] = value.work_offset.to_dict() if value.work_offset else None
    elif isinstance(value, ToolActivationRecord):
        data.update(tool_assembly_id=str(value.tool_assembly_id), tool_assembly_fingerprint=value.tool_assembly_fingerprint.to_dict(), tool_id=str(value.tool_id) if value.tool_id else None, holder_id=str(value.holder_id) if value.holder_id else None)
    elif isinstance(value, FeedModeRecord):
        data["mode"] = value.mode.value
    elif isinstance(value, FeedValueRecord):
        data["feed_rate"] = _feed_to_dict(value.feed_rate)
    elif isinstance(value, SpindleDirectionRecord):
        data["direction"] = value.direction.value
    elif isinstance(value, SpindleStartRecord):
        data.update(direction=value.direction.value, speed=_spindle_to_dict(value.speed))
    elif isinstance(value, SpindleStopRecord):
        pass
    elif isinstance(value, CoolantRecord):
        data["state"] = value.state.value
    elif isinstance(value, RapidMotionRecord):
        data.update(start=value.start.to_dict(), end=value.end.to_dict(), motion_class=value.motion_class.value, rapid_rate=_feed_to_dict(value.rapid_rate), provenance=value.provenance)
    elif isinstance(value, LinearMotionRecord):
        data.update(start=value.start.to_dict(), end=value.end.to_dict(), feed_rate=_feed_to_dict(value.feed_rate), motion_class=value.motion_class.value, provenance=value.provenance, engagement=_evidence_to_dict(value.engagement))
    elif isinstance(value, ArcMotionRecord):
        data.update(start=value.start.to_dict(), end=value.end.to_dict(), center=value.center.to_dict(), plane_normal=value.plane_normal.to_dict(), sweep_radians=value.sweep_radians, feed_rate=_feed_to_dict(value.feed_rate), motion_class=value.motion_class.value, provenance=value.provenance)
    elif isinstance(value, DwellRecord):
        data.update(duration_seconds=value.duration_seconds, provenance=value.provenance)
    elif isinstance(value, SemanticMarkerRecord):
        data.update(semantic_key=value.semantic_key, message=value.message, metadata=_evidence_to_dict(value.metadata), provenance=value.provenance)
    elif isinstance(value, ProgramEndRecord):
        pass
    else:
        raise CamValidationError("Unknown NC record type")
    return data


def record_from_dict(data: dict[str, Any]) -> NCRecordUnion:
    if not isinstance(data, dict) or set(data) < {"sequence_index", "kind"}:
        raise CamValidationError("NC record payload is malformed")
    kind = _enum(NCRecordKind, data["kind"], "NC record kind")
    sequence = data["sequence_index"]
    try:
        if kind is NCRecordKind.PROGRAM_BEGIN:
            _strict_record(data, {"sequence_index", "kind", "metadata"}); return ProgramBeginRecord(sequence, _evidence_from_dict(data["metadata"]))
        if kind is NCRecordKind.UNITS:
            _strict_record(data, {"sequence_index", "kind", "unit"}); return UnitsRecord(sequence, _enum(LengthUnit, data["unit"], "Unit"))
        if kind is NCRecordKind.COORDINATE_MODE:
            _strict_record(data, {"sequence_index", "kind", "mode"}); return CoordinateModeRecord(sequence, _enum(CoordinateMode, data["mode"], "Coordinate mode"))
        if kind is NCRecordKind.PLANE:
            _strict_record(data, {"sequence_index", "kind", "plane"}); return PlaneRecord(sequence, _enum(Plane, data["plane"], "Plane"))
        if kind is NCRecordKind.WORK_OFFSET:
            _strict_record(data, {"sequence_index", "kind", "work_offset"}); return WorkOffsetRecord(sequence, WorkOffset.from_dict(data["work_offset"]) if data["work_offset"] else None)
        if kind is NCRecordKind.TOOL_ACTIVATION:
            _strict_record(data, {"sequence_index", "kind", "tool_assembly_id", "tool_assembly_fingerprint", "tool_id", "holder_id"}); return ToolActivationRecord(sequence, _id(ToolAssemblyId, data["tool_assembly_id"], "Tool assembly"), _typed_fp(data["tool_assembly_fingerprint"]), _id(ToolDefinitionId, data["tool_id"], "Tool") if data["tool_id"] else None, _id(HolderDefinitionId, data["holder_id"], "Holder") if data["holder_id"] else None)
        if kind is NCRecordKind.FEED_MODE:
            _strict_record(data, {"sequence_index", "kind", "mode"}); return FeedModeRecord(sequence, _enum(FeedMode, data["mode"], "Feed mode"))
        if kind is NCRecordKind.FEED_VALUE:
            _strict_record(data, {"sequence_index", "kind", "feed_rate"}); return FeedValueRecord(sequence, _feed_from_dict(data["feed_rate"]))
        if kind is NCRecordKind.SPINDLE_DIRECTION:
            _strict_record(data, {"sequence_index", "kind", "direction"}); return SpindleDirectionRecord(sequence, _enum(SpindleDirection, data["direction"], "Spindle direction"))
        if kind is NCRecordKind.SPINDLE_START:
            _strict_record(data, {"sequence_index", "kind", "direction", "speed"}); return SpindleStartRecord(sequence, _enum(SpindleDirection, data["direction"], "Spindle direction"), _spindle_from_dict(data["speed"]))
        if kind is NCRecordKind.SPINDLE_STOP:
            _strict_record(data, {"sequence_index", "kind"}); return SpindleStopRecord(sequence)
        if kind is NCRecordKind.COOLANT:
            _strict_record(data, {"sequence_index", "kind", "state"}); return CoolantRecord(sequence, _enum(CoolantState, data["state"], "Coolant state"))
        if kind is NCRecordKind.RAPID:
            _strict_record(data, {"sequence_index", "kind", "start", "end", "motion_class", "rapid_rate", "provenance"}); return RapidMotionRecord(sequence, Pose.from_dict(data["start"]), Pose.from_dict(data["end"]), _enum(MotionClass, data["motion_class"], "Motion class"), _feed_from_dict(data["rapid_rate"]), data["provenance"])
        if kind is NCRecordKind.LINEAR:
            _strict_record(data, {"sequence_index", "kind", "start", "end", "feed_rate", "motion_class", "provenance", "engagement"}); return LinearMotionRecord(sequence, Pose.from_dict(data["start"]), Pose.from_dict(data["end"]), _feed_from_dict(data["feed_rate"]), _enum(MotionClass, data["motion_class"], "Motion class"), data["provenance"], _evidence_from_dict(data["engagement"]))
        if kind is NCRecordKind.ARC:
            _strict_record(data, {"sequence_index", "kind", "start", "end", "center", "plane_normal", "sweep_radians", "feed_rate", "motion_class", "provenance"}); return ArcMotionRecord(sequence, Pose.from_dict(data["start"]), Pose.from_dict(data["end"]), Point3.from_dict(data["center"]), Vector3.from_dict(data["plane_normal"]), data["sweep_radians"], _feed_from_dict(data["feed_rate"]), _enum(MotionClass, data["motion_class"], "Motion class"), data["provenance"])
        if kind is NCRecordKind.DWELL:
            _strict_record(data, {"sequence_index", "kind", "duration_seconds", "provenance"}); return DwellRecord(sequence, data["duration_seconds"], data["provenance"])
        if kind is NCRecordKind.SEMANTIC_MARKER:
            _strict_record(data, {"sequence_index", "kind", "semantic_key", "message", "metadata", "provenance"}); return SemanticMarkerRecord(sequence, data["semantic_key"], data["message"], _evidence_from_dict(data["metadata"]), data["provenance"])
        if kind is NCRecordKind.PROGRAM_END:
            _strict_record(data, {"sequence_index", "kind"}); return ProgramEndRecord(sequence)
    except (TypeError, ValueError, KeyError) as error:
        raise CamValidationError("NC record payload is invalid") from error
    raise CamValidationError("Unsupported NC record kind")


def _strict_record(data: dict[str, Any], fields: set[str]) -> None:
    if set(data) != fields:
        raise CamValidationError("NC record fields are malformed")


def program_to_dict(value: NCProgramIR) -> dict[str, Any]:
    data = {"format": NC_PROGRAM_FORMAT, "format_version": NC_PROGRAM_VERSION, "program_id": str(value.program_id), "project_id": str(value.project_id), "operation_id": str(value.operation_id), "artifact_id": str(value.artifact_id), "artifact_fingerprint": value.artifact_fingerprint.to_dict(), "strategy_key": value.strategy_key, "strategy_version": value.strategy_version, "unit": value.unit.value, "coordinate_mode": value.coordinate_mode.value, "plane": value.plane.value, "setup_id": str(value.setup_id), "setup_revision": value.setup_revision.to_dict(), "wcs": value.wcs.to_dict(), "work_offset": value.work_offset.to_dict() if value.work_offset else None, "tool_assembly_id": str(value.tool_assembly_id), "tool_assembly_fingerprint": value.tool_assembly_fingerprint.to_dict(), "records": [record_to_dict(item) for item in value.records], "diagnostics": [diagnostic_to_dict(item) for item in value.diagnostics], "statistics": statistics_to_dict(value.statistics), "program_fingerprint": value.program_fingerprint.to_dict() if value.program_fingerprint else None}
    if value.production_context is not None:
        data["production_context"] = value.production_context.to_dict()
    return data


def program_from_dict(data: dict[str, Any]) -> NCProgramIR:
    fields = {"program_id", "project_id", "operation_id", "artifact_id", "artifact_fingerprint", "strategy_key", "strategy_version", "unit", "coordinate_mode", "plane", "setup_id", "setup_revision", "wcs", "work_offset", "tool_assembly_id", "tool_assembly_fingerprint", "records", "diagnostics", "statistics", "program_fingerprint"}
    if "production_context" in data:
        fields.add("production_context")
    _strict(data, format_name=NC_PROGRAM_FORMAT, version=NC_PROGRAM_VERSION, fields=fields)
    if not isinstance(data["records"], list) or not isinstance(data["diagnostics"], list):
        raise CamValidationError("Program collections must be lists")
    try:
        project_id = UUID(data["project_id"])
    except (TypeError, ValueError) as error:
        raise CamValidationError("Program project ID is invalid") from error
    return NCProgramIR.create(program_id=_id(NCProgramId, data["program_id"], "Program"), project_id=project_id, operation_id=_id(OperationId, data["operation_id"], "Operation"), artifact_id=_id(ToolpathArtifactId, data["artifact_id"], "Artifact"), artifact_fingerprint=_fp(data["artifact_fingerprint"]), strategy_key=data["strategy_key"], strategy_version=data["strategy_version"], unit=_enum(LengthUnit, data["unit"], "Unit"), coordinate_mode=_enum(CoordinateMode, data["coordinate_mode"], "Coordinate mode"), plane=_enum(Plane, data["plane"], "Plane"), setup_id=_id(SetupId, data["setup_id"], "Setup"), setup_revision=Revision.from_dict(data["setup_revision"]), wcs=WcsFrame.from_dict(data["wcs"]), work_offset=WorkOffset.from_dict(data["work_offset"]) if data["work_offset"] else None, tool_assembly_id=_id(ToolAssemblyId, data["tool_assembly_id"], "Tool assembly"), tool_assembly_fingerprint=_typed_fp(data["tool_assembly_fingerprint"]), records=tuple(record_from_dict(item) for item in data["records"]), diagnostics=tuple(diagnostic_from_dict(item) for item in data["diagnostics"]), statistics=statistics_from_dict(data["statistics"]), program_fingerprint=_fp(data["program_fingerprint"]) if data["program_fingerprint"] else None, production_context=ProductionProgramContext.from_dict(data["production_context"]) if "production_context" in data else None)


def result_to_dict(value: PostResult) -> dict[str, Any]:
    data = {"format": POST_RESULT_FORMAT, "format_version": POST_VERSION, "result_id": str(value.result_id), "project_id": str(value.project_id), "operation_id": str(value.operation_id), "artifact_id": str(value.artifact_id), "artifact_fingerprint": value.artifact_fingerprint.to_dict(), "input_fingerprint": value.input_fingerprint.to_dict(), "post_definition_id": str(value.post_definition_id), "post_definition_version": value.post_definition_version, "post_definition_fingerprint": value.post_definition_fingerprint.to_dict(), "setup_id": str(value.setup_id), "setup_revision": value.setup_revision.to_dict(), "setup_fingerprint": value.setup_fingerprint.to_dict(), "tool_assembly_id": str(value.tool_assembly_id), "tool_assembly_fingerprint": value.tool_assembly_fingerprint.to_dict(), "tool_fingerprint": value.tool_fingerprint.to_dict() if value.tool_fingerprint else None, "holder_id": str(value.holder_id) if value.holder_id else None, "holder_fingerprint": value.holder_fingerprint.to_dict() if value.holder_fingerprint else None, "machine_id": str(value.machine_id) if value.machine_id else None, "machine_fingerprint": value.machine_fingerprint.to_dict() if value.machine_fingerprint else None, "simulation_fingerprint": value.simulation_fingerprint.to_dict() if value.simulation_fingerprint else None, "program_ir_fingerprint": value.program_ir_fingerprint.to_dict() if value.program_ir_fingerprint else None, "output_checksum": value.output_checksum, "canonical_text": value.canonical_text, "status": value.status.value, "diagnostics": [diagnostic_to_dict(item) for item in value.diagnostics], "statistics": statistics_to_dict(value.statistics), "result_fingerprint": value.result_fingerprint.to_dict() if value.result_fingerprint else None, "schema_version": value.schema_version}
    if value.production_profile_id is not None:
        data.update(production_profile_id=str(value.production_profile_id), production_profile_version=value.production_profile_version, production_profile_fingerprint=value.production_profile_fingerprint.to_dict(), tool_binding_fingerprint=value.tool_binding_fingerprint.to_dict(), program_context_fingerprint=value.program_context_fingerprint.to_dict(), validated_unit=value.validated_unit.value, validated_feed_modes=[item.value for item in value.validated_feed_modes])
    return data


def result_from_dict(data: dict[str, Any]) -> PostResult:
    fields = {"result_id", "project_id", "operation_id", "artifact_id", "artifact_fingerprint", "input_fingerprint", "post_definition_id", "post_definition_version", "post_definition_fingerprint", "setup_id", "setup_revision", "setup_fingerprint", "tool_assembly_id", "tool_assembly_fingerprint", "tool_fingerprint", "holder_id", "holder_fingerprint", "machine_id", "machine_fingerprint", "simulation_fingerprint", "program_ir_fingerprint", "output_checksum", "canonical_text", "status", "diagnostics", "statistics", "result_fingerprint", "schema_version"}
    production_fields = {"production_profile_id", "production_profile_version", "production_profile_fingerprint", "tool_binding_fingerprint", "program_context_fingerprint", "validated_unit", "validated_feed_modes"}
    if "production_profile_id" in data:
        fields |= production_fields
    _strict(data, format_name=POST_RESULT_FORMAT, version=POST_VERSION, fields=fields)
    if not isinstance(data["diagnostics"], list):
        raise CamValidationError("Result diagnostics must be a list")
    try:
        project_id = UUID(data["project_id"])
    except (TypeError, ValueError) as error:
        raise CamValidationError("Result project ID is invalid") from error
    if "validated_feed_modes" in data and not isinstance(data["validated_feed_modes"], list):
        raise CamValidationError("Validated feed-mode payload is invalid")
    return PostResult.create(result_id=_id(PostResultId, data["result_id"], "Result"), project_id=project_id, operation_id=_id(OperationId, data["operation_id"], "Operation"), artifact_id=_id(ToolpathArtifactId, data["artifact_id"], "Artifact"), artifact_fingerprint=_fp(data["artifact_fingerprint"]), input_fingerprint=DependencyFingerprint.from_dict(data["input_fingerprint"]), post_definition_id=_id(PostProcessorDefinitionId, data["post_definition_id"], "Post definition"), post_definition_version=data["post_definition_version"], post_definition_fingerprint=_fp(data["post_definition_fingerprint"]), setup_id=_id(SetupId, data["setup_id"], "Setup"), setup_revision=Revision.from_dict(data["setup_revision"]), setup_fingerprint=_fp(data["setup_fingerprint"]), tool_assembly_id=_id(ToolAssemblyId, data["tool_assembly_id"], "Tool assembly"), tool_assembly_fingerprint=_typed_fp(data["tool_assembly_fingerprint"]), tool_fingerprint=_fp(data["tool_fingerprint"]) if data["tool_fingerprint"] else None, holder_id=_id(HolderDefinitionId, data["holder_id"], "Holder") if data["holder_id"] else None, holder_fingerprint=_fp(data["holder_fingerprint"]) if data["holder_fingerprint"] else None, machine_id=_id(MachineDefinitionId, data["machine_id"], "Machine") if data["machine_id"] else None, machine_fingerprint=_fp(data["machine_fingerprint"]) if data["machine_fingerprint"] else None, simulation_fingerprint=_fp(data["simulation_fingerprint"]) if data["simulation_fingerprint"] else None, program_ir_fingerprint=_fp(data["program_ir_fingerprint"]) if data["program_ir_fingerprint"] else None, output_checksum=data["output_checksum"], canonical_text=data["canonical_text"], status=_enum(PostResultStatus, data["status"], "Result status"), diagnostics=tuple(diagnostic_from_dict(item) for item in data["diagnostics"]), statistics=statistics_from_dict(data["statistics"]), result_fingerprint=_fp(data["result_fingerprint"]) if data["result_fingerprint"] else None, schema_version=data["schema_version"], production_profile_id=_id(ProductionControllerProfileId, data["production_profile_id"], "Production profile") if "production_profile_id" in data else None, production_profile_version=data.get("production_profile_version"), production_profile_fingerprint=_fp(data["production_profile_fingerprint"]) if "production_profile_fingerprint" in data else None, tool_binding_fingerprint=_fp(data["tool_binding_fingerprint"]) if "tool_binding_fingerprint" in data else None, program_context_fingerprint=_fp(data["program_context_fingerprint"]) if "program_context_fingerprint" in data else None, validated_unit=_enum(LengthUnit, data["validated_unit"], "Validated unit") if "validated_unit" in data else None, validated_feed_modes=tuple(_enum(FeedMode, item, "Validated feed mode") for item in data.get("validated_feed_modes", [])))


def _program_payload(value: NCProgramIR) -> dict[str, Any]:
    data = program_to_dict(value).copy()
    data.pop("program_id", None)
    data.pop("program_fingerprint", None)
    return data


def compute_program_fingerprint(value: NCProgramIR) -> ContentFingerprint:
    return ContentFingerprint.from_payload(_program_payload(value))


def _result_payload(value: PostResult) -> dict[str, Any]:
    data = result_to_dict(value).copy()
    for key in ("result_id", "result_fingerprint", "schema_version"):
        data.pop(key, None)
    return data


def compute_result_fingerprint(value: PostResult) -> ContentFingerprint:
    return ContentFingerprint.from_payload(_result_payload(value))


def dumps(value: PostRequest | NCProgramIR | PostResult | PostProcessorDefinition | ProductionControllerProfile | ControllerToolBinding | ProductionProgramContext) -> str:
    if isinstance(value, PostRequest):
        payload = request_to_dict(value)
    elif isinstance(value, NCProgramIR):
        payload = program_to_dict(value)
    elif isinstance(value, PostResult):
        payload = result_to_dict(value)
    elif isinstance(value, PostProcessorDefinition):
        payload = definition_to_dict(value)
    elif isinstance(value, ProductionControllerProfile):
        payload = profile_to_dict(value)
    elif isinstance(value, ControllerToolBinding):
        payload = value.to_dict()
    elif isinstance(value, ProductionProgramContext):
        payload = value.to_dict()
    else:
        raise CamValidationError("Unsupported post codec value")
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def loads(text: str) -> PostRequest | NCProgramIR | PostResult | PostProcessorDefinition | ProductionControllerProfile | ControllerToolBinding | ProductionProgramContext:
    if not isinstance(text, str):
        raise CamValidationError("Post codec input must be text")
    try:
        data = json.loads(text, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CamValidationError("Post codec JSON is invalid") from error
    if not isinstance(data, dict):
        raise CamValidationError("Post codec root must be an object")
    format_name = data.get("format")
    if format_name == NC_PROGRAM_FORMAT:
        return program_from_dict(data)
    if format_name == POST_RESULT_FORMAT:
        return result_from_dict(data)
    if format_name == PRODUCTION_PROFILE_FORMAT:
        return profile_from_dict(data)
    if format_name == TOOL_BINDING_FORMAT:
        return ControllerToolBinding.from_dict(data)
    if format_name == PROGRAM_CONTEXT_FORMAT:
        return ProductionProgramContext.from_dict(data)
    if format_name == POST_FORMAT:
        if "request_id" in data:
            return request_from_dict(data)
        if "definition_id" in data:
            return definition_from_dict(data)
    raise UnsupportedCamSchemaError("Unsupported post codec format")

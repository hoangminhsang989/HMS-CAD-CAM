"""Strict deterministic JSON-compatible codecs for Toolpath IR v1."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import (
    MachineDefinitionId, OperationId, SetupId, ToolAssemblyId,
    ToolpathArtifactId, ToolpathEventId,
)
from hms_cadcam.cam.domain.operation import ComputationToken, DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint, Revision
from hms_cadcam.cam.domain.spatial import Point3, Vector3
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, LengthUnit, SpindleSpeed, SpindleSpeedUnit
from hms_cadcam.cam.toolpath.events import (
    AnyToolpathEvent, ArcMove, CoolantState, CoolantStateEvent, DwellEvent,
    FeedMode, FeedModeEvent, LinearMove, MarkerEvent, MotionClass, RapidMove,
    SpindleState, SpindleStateEvent, ToolContextEvent, ToolpathEventKind,
)
from hms_cadcam.cam.toolpath.geometry import Bounds3, CoordinateSpace, Pose
from hms_cadcam.cam.toolpath.model import (
    TOOLPATH_FORMAT, TOOLPATH_VERSION, ToolpathArtifact, ToolpathCompletionStatus,
    ToolpathDiagnostic, ToolpathDiagnosticCode, ToolpathStatistics,
)

_EVENT_FORMAT = "HMS_CAM_TOOLPATH_EVENT"
_POSE_FORMAT = "HMS_CAM_TOOLPATH_POSE"
_BOUNDS_FORMAT = "HMS_CAM_TOOLPATH_BOUNDS"
_STATISTICS_FORMAT = "HMS_CAM_TOOLPATH_STATISTICS"
_DIAGNOSTIC_FORMAT = "HMS_CAM_TOOLPATH_DIAGNOSTIC"


def _strict(data: Any, format_name: str, fields: set[str]) -> None:
    if not isinstance(data, dict) or set(data) != {"format", "format_version", *fields}:
        raise CamValidationError(f"{format_name} payload is malformed")
    if data["format"] != format_name:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} format")
    if type(data["format_version"]) is not int or data["format_version"] != TOOLPATH_VERSION:
        raise UnsupportedCamSchemaError(f"Unsupported {format_name} version")


def pose_to_dict(value: Pose) -> dict[str, Any]:
    return {"format": _POSE_FORMAT, "format_version": TOOLPATH_VERSION,
            "position": value.position.to_dict(), "tool_axis": value.tool_axis.to_dict()}


def pose_from_dict(data: dict[str, Any]) -> Pose:
    _strict(data, _POSE_FORMAT, {"position", "tool_axis"})
    return Pose(Point3.from_dict(data["position"]), Vector3.from_dict(data["tool_axis"]))


def bounds_to_dict(value: Bounds3) -> dict[str, Any]:
    return {"format": _BOUNDS_FORMAT, "format_version": TOOLPATH_VERSION,
            "minimum": value.minimum.to_dict(), "maximum": value.maximum.to_dict()}


def bounds_from_dict(data: dict[str, Any]) -> Bounds3:
    _strict(data, _BOUNDS_FORMAT, {"minimum", "maximum"})
    return Bounds3(Point3.from_dict(data["minimum"]), Point3.from_dict(data["maximum"]))


def diagnostic_to_dict(value: ToolpathDiagnostic) -> dict[str, Any]:
    return {"format": _DIAGNOSTIC_FORMAT, "format_version": TOOLPATH_VERSION,
            "severity": value.severity.value, "code": value.code.value, "message": value.message,
            "context": [{"key": key, "value": item} for key, item in value.context]}


def diagnostic_from_dict(data: dict[str, Any]) -> ToolpathDiagnostic:
    _strict(data, _DIAGNOSTIC_FORMAT, {"severity", "code", "message", "context"})
    context = data["context"]
    if not isinstance(context, list) or any(not isinstance(item, dict) or set(item) != {"key", "value"} for item in context):
        raise CamValidationError("Toolpath diagnostic context payload is malformed")
    try:
        return ToolpathDiagnostic(DiagnosticSeverity(data["severity"]), ToolpathDiagnosticCode(data["code"]),
                                  data["message"], tuple((item["key"], item["value"]) for item in context))
    except (TypeError, ValueError) as error:
        raise CamValidationError("Toolpath diagnostic payload is invalid") from error


def statistics_to_dict(value: ToolpathStatistics) -> dict[str, Any]:
    return {"format": _STATISTICS_FORMAT, "format_version": TOOLPATH_VERSION,
            "total_rapid_length": value.total_rapid_length,
            "total_cutting_length": value.total_cutting_length,
            "total_link_length": value.total_link_length,
            "total_retract_length": value.total_retract_length,
            "total_arc_length": value.total_arc_length,
            "estimated_duration_seconds": value.estimated_duration_seconds,
            "duration_is_partial": value.duration_is_partial,
            "event_counts": [{"kind": kind.value, "count": count} for kind, count in value.event_counts]}


def statistics_from_dict(data: dict[str, Any]) -> ToolpathStatistics:
    _strict(data, _STATISTICS_FORMAT, {"total_rapid_length", "total_cutting_length", "total_link_length",
        "total_retract_length", "total_arc_length", "estimated_duration_seconds", "duration_is_partial", "event_counts"})
    counts = data["event_counts"]
    if not isinstance(counts, list) or any(not isinstance(item, dict) or set(item) != {"kind", "count"} for item in counts):
        raise CamValidationError("Toolpath event counts payload is malformed")
    try:
        return ToolpathStatistics(data["total_rapid_length"], data["total_cutting_length"], data["total_link_length"],
            data["total_retract_length"], data["total_arc_length"], data["estimated_duration_seconds"],
            data["duration_is_partial"], tuple((ToolpathEventKind(item["kind"]), item["count"]) for item in counts))
    except (TypeError, ValueError) as error:
        raise CamValidationError("Toolpath statistics payload is invalid") from error


def _common(event: AnyToolpathEvent) -> dict[str, Any]:
    return {"format": _EVENT_FORMAT, "format_version": TOOLPATH_VERSION, "kind": event.kind.value,
            "event_id": str(event.event_id), "sequence_index": event.sequence_index,
            "source_operation_id": str(event.source_operation_id), "provenance": event.provenance,
            "metadata": [{"key": key, "value": value} for key, value in event.metadata]}


def _feed(value: FeedRate | None) -> dict[str, Any] | None:
    return None if value is None else {"value": value.value, "unit": value.unit.value}


def _feed_from(data: Any) -> FeedRate | None:
    if data is None:
        return None
    if not isinstance(data, dict) or set(data) != {"value", "unit"}:
        raise CamValidationError("Feed payload is malformed")
    try:
        return FeedRate(data["value"], FeedUnit(data["unit"]))
    except (TypeError, ValueError) as error:
        raise CamValidationError("Feed payload is invalid") from error


def event_to_dict(event: AnyToolpathEvent) -> dict[str, Any]:
    data = _common(event)
    if isinstance(event, RapidMove):
        data.update(start=pose_to_dict(event.start), end=pose_to_dict(event.end),
                    motion_class=event.motion_class.value, rapid_rate=_feed(event.rapid_rate))
    elif isinstance(event, LinearMove):
        data.update(start=pose_to_dict(event.start), end=pose_to_dict(event.end), feed_rate=_feed(event.feed_rate),
                    motion_class=event.motion_class.value,
                    engagement=[{"key": key, "value": value} for key, value in event.engagement])
    elif isinstance(event, ArcMove):
        data.update(start=pose_to_dict(event.start), end=pose_to_dict(event.end), center=event.center.to_dict(),
                    plane_normal=event.plane_normal.to_dict(), sweep_radians=event.sweep_radians,
                    feed_rate=_feed(event.feed_rate), motion_class=event.motion_class.value)
    elif isinstance(event, DwellEvent):
        data["duration_seconds"] = event.duration_seconds
    elif isinstance(event, SpindleStateEvent):
        data.update(state=event.state.value, speed=(None if event.speed is None else
            {"value": event.speed.value, "unit": event.speed.unit.value}))
    elif isinstance(event, CoolantStateEvent):
        data["state"] = event.state.value
    elif isinstance(event, FeedModeEvent):
        data["mode"] = event.mode.value
    elif isinstance(event, ToolContextEvent):
        data["tool_assembly_id"] = str(event.tool_assembly_id)
    elif isinstance(event, MarkerEvent):
        data.update(semantic_key=event.semantic_key, message=event.message)
    else:
        raise CamValidationError("Unknown critical toolpath event type")
    return data


def event_from_dict(data: dict[str, Any]) -> AnyToolpathEvent:
    if not isinstance(data, dict) or data.get("format") != _EVENT_FORMAT:
        raise UnsupportedCamSchemaError("Unsupported toolpath event format")
    if type(data.get("format_version")) is not int or data["format_version"] != TOOLPATH_VERSION:
        raise UnsupportedCamSchemaError("Unsupported toolpath event version")
    try:
        kind = ToolpathEventKind(data["kind"])
    except (KeyError, TypeError, ValueError) as error:
        raise UnsupportedCamSchemaError("Unknown critical toolpath event kind") from error
    common_fields = {"format", "format_version", "kind", "event_id", "sequence_index", "source_operation_id", "provenance", "metadata"}
    variants = {
        ToolpathEventKind.RAPID: {"start", "end", "motion_class", "rapid_rate"},
        ToolpathEventKind.LINEAR: {"start", "end", "feed_rate", "motion_class", "engagement"},
        ToolpathEventKind.ARC: {"start", "end", "center", "plane_normal", "sweep_radians", "feed_rate", "motion_class"},
        ToolpathEventKind.DWELL: {"duration_seconds"},
        ToolpathEventKind.SPINDLE_STATE: {"state", "speed"},
        ToolpathEventKind.COOLANT_STATE: {"state"}, ToolpathEventKind.FEED_MODE: {"mode"},
        ToolpathEventKind.TOOL_CONTEXT: {"tool_assembly_id"},
        ToolpathEventKind.MARKER: {"semantic_key", "message"},
    }
    if set(data) != common_fields | variants[kind]:
        raise CamValidationError("Toolpath event payload fields are malformed")
    metadata = data["metadata"]
    if not isinstance(metadata, list) or any(not isinstance(item, dict) or set(item) != {"key", "value"} for item in metadata):
        raise CamValidationError("Toolpath event metadata payload is malformed")
    common = dict(event_id=ToolpathEventId.parse(data["event_id"]), sequence_index=data["sequence_index"],
                  source_operation_id=OperationId.parse(data["source_operation_id"]), provenance=data["provenance"],
                  metadata=tuple((item["key"], item["value"]) for item in metadata))
    try:
        if kind is ToolpathEventKind.RAPID:
            return RapidMove(**common, start=pose_from_dict(data["start"]), end=pose_from_dict(data["end"]),
                             motion_class=MotionClass(data["motion_class"]), rapid_rate=_feed_from(data["rapid_rate"]))
        if kind is ToolpathEventKind.LINEAR:
            engagement = data["engagement"]
            if not isinstance(engagement, list) or any(not isinstance(item, dict) or set(item) != {"key", "value"} for item in engagement):
                raise CamValidationError("Engagement payload is malformed")
            return LinearMove(**common, start=pose_from_dict(data["start"]), end=pose_from_dict(data["end"]),
                feed_rate=_feed_from(data["feed_rate"]), motion_class=MotionClass(data["motion_class"]),
                engagement=tuple((item["key"], item["value"]) for item in engagement))  # type: ignore[arg-type]
        if kind is ToolpathEventKind.ARC:
            return ArcMove(**common, start=pose_from_dict(data["start"]), end=pose_from_dict(data["end"]),
                center=Point3.from_dict(data["center"]), plane_normal=Vector3.from_dict(data["plane_normal"]),
                sweep_radians=data["sweep_radians"], feed_rate=_feed_from(data["feed_rate"]),
                motion_class=MotionClass(data["motion_class"]))  # type: ignore[arg-type]
        if kind is ToolpathEventKind.DWELL:
            return DwellEvent(**common, duration_seconds=data["duration_seconds"])
        if kind is ToolpathEventKind.SPINDLE_STATE:
            speed = data["speed"]
            if speed is not None and (not isinstance(speed, dict) or set(speed) != {"value", "unit"}):
                raise CamValidationError("Spindle speed payload is malformed")
            parsed_speed = None if speed is None else SpindleSpeed(speed["value"], SpindleSpeedUnit(speed["unit"]))
            return SpindleStateEvent(**common, state=SpindleState(data["state"]), speed=parsed_speed)
        if kind is ToolpathEventKind.COOLANT_STATE:
            return CoolantStateEvent(**common, state=CoolantState(data["state"]))
        if kind is ToolpathEventKind.FEED_MODE:
            return FeedModeEvent(**common, mode=FeedMode(data["mode"]))
        if kind is ToolpathEventKind.TOOL_CONTEXT:
            return ToolContextEvent(**common, tool_assembly_id=ToolAssemblyId.parse(data["tool_assembly_id"]))
        return MarkerEvent(**common, semantic_key=data["semantic_key"], message=data["message"])
    except UnsupportedCamSchemaError:
        raise
    except (TypeError, ValueError) as error:
        raise CamValidationError("Toolpath event payload is invalid") from error


def artifact_to_dict(value: ToolpathArtifact) -> dict[str, Any]:
    if value.artifact_fingerprint is None:
        raise CamValidationError("Published toolpath fingerprint is missing")
    return {"format": TOOLPATH_FORMAT, "format_version": TOOLPATH_VERSION,
        "artifact_id": str(value.artifact_id), "schema_version": value.schema_version,
        "source_operation_id": str(value.source_operation_id), "operation_revision": value.operation_revision.to_dict(),
        "computation_token": {"value": str(value.computation_token.value), "generation": value.computation_token.generation},
        "input_fingerprint": value.input_fingerprint.to_dict(), "coordinate_space": value.coordinate_space.value,
        "unit": value.unit.value, "setup_id": str(value.setup_id), "setup_revision": value.setup_revision.to_dict(),
        "wcs_fingerprint": value.wcs_fingerprint.to_dict(), "tool_assembly_id": str(value.tool_assembly_id),
        "tool_assembly_fingerprint": value.tool_assembly_fingerprint.to_dict(),
        "machine_id": str(value.machine_id) if value.machine_id else None,
        "machine_fingerprint": value.machine_fingerprint.to_dict() if value.machine_fingerprint else None,
        "initial_pose": pose_to_dict(value.initial_pose), "events": [event_to_dict(item) for item in value.events],
        "bounds": bounds_to_dict(value.bounds), "statistics": statistics_to_dict(value.statistics),
        "diagnostics": [diagnostic_to_dict(item) for item in value.diagnostics],
        "completion_status": value.completion_status.value, "artifact_fingerprint": value.artifact_fingerprint.to_dict(),
        "created_at": value.created_at}


def artifact_from_dict(data: dict[str, Any], *, max_events: int | None = None) -> ToolpathArtifact:
    fields = {"artifact_id", "schema_version", "source_operation_id", "operation_revision", "computation_token",
        "input_fingerprint", "coordinate_space", "unit", "setup_id", "setup_revision", "wcs_fingerprint",
        "tool_assembly_id", "tool_assembly_fingerprint", "machine_id", "machine_fingerprint", "initial_pose",
        "events", "bounds", "statistics", "diagnostics", "completion_status", "artifact_fingerprint", "created_at"}
    _strict(data, TOOLPATH_FORMAT, fields)
    events = data["events"]
    diagnostics = data["diagnostics"]
    token = data["computation_token"]
    if not isinstance(events, list) or not isinstance(diagnostics, list):
        raise CamValidationError("Toolpath artifact child payloads must be lists")
    if max_events is not None and (type(max_events) is not int or max_events < 0 or len(events) > max_events):
        raise CamValidationError("Toolpath event validation limit exceeded")
    if not isinstance(token, dict) or set(token) != {"value", "generation"}:
        raise CamValidationError("Toolpath computation token payload is malformed")
    try:
        return ToolpathArtifact(ToolpathArtifactId.parse(data["artifact_id"]), OperationId.parse(data["source_operation_id"]),
            Revision.from_dict(data["operation_revision"]), ComputationToken(UUID(token["value"]), token["generation"]),
            DependencyFingerprint.from_dict(data["input_fingerprint"]), CoordinateSpace(data["coordinate_space"]),
            LengthUnit(data["unit"]), SetupId.parse(data["setup_id"]), Revision.from_dict(data["setup_revision"]),
            ContentFingerprint.from_dict(data["wcs_fingerprint"]), ToolAssemblyId.parse(data["tool_assembly_id"]),
            ContentFingerprint.from_dict(data["tool_assembly_fingerprint"]),
            MachineDefinitionId.parse(data["machine_id"]) if data["machine_id"] else None,
            ContentFingerprint.from_dict(data["machine_fingerprint"]) if data["machine_fingerprint"] else None,
            pose_from_dict(data["initial_pose"]), tuple(event_from_dict(item) for item in events),
            bounds_from_dict(data["bounds"]), statistics_from_dict(data["statistics"]),
            tuple(diagnostic_from_dict(item) for item in diagnostics), ToolpathCompletionStatus(data["completion_status"]),
            ContentFingerprint.from_dict(data["artifact_fingerprint"]), data["created_at"], data["schema_version"])
    except UnsupportedCamSchemaError:
        raise
    except CamValidationError:
        raise
    except (TypeError, ValueError) as error:
        raise CamValidationError("Toolpath artifact payload is invalid") from error

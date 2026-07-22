"""Pure, single-operation lowering from the published ToolpathArtifact."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.machine import (
    MachineAxisType, MachineCompatibilityStatus, MachineCoolantCapability, MachineDefinition,
    MachineEvidence, OperationCapability, SpindleDirection, TappingMode,
    assess_machine_compatibility,
)
from hms_cadcam.cam.domain.ids import NCProgramId
from hms_cadcam.cam.domain.operation import ArtifactStatus, DiagnosticSeverity, Operation
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint
from hms_cadcam.cam.domain.setup import Setup
from hms_cadcam.cam.domain.tooling import HolderDefinition, ToolAssembly, ToolDefinition
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.simulation.model import SimulationResult, SimulationStatus
from hms_cadcam.cam.toolpath.events import (
    ArcMove, CoolantState, CoolantStateEvent, DwellEvent, FeedMode, FeedModeEvent,
    LinearMove, MarkerEvent, MotionClass, RapidMove, SpindleState, SpindleStateEvent,
    ToolContextEvent,
)
from hms_cadcam.cam.toolpath.model import ToolpathArtifact, ToolpathCompletionStatus
from hms_cadcam.cam.post.model import (
    ArcCenterFormat, ArcMotionRecord, CoordinateMode, CoordinateModeRecord, CoolantRecord, DwellRecord,
    FeedModeRecord, FeedValueRecord, LinearMotionRecord, LoweringPolicy, NCProgramIR,
    Plane, PlaneRecord, PostDiagnostic, PostDiagnosticCode, PostRequest, ProgramBeginRecord,
    ProgramEndRecord, RapidMotionRecord, SemanticMarkerRecord, SimulationGateMode,
    SimulationGatePolicy, SpindleDirectionRecord, SpindleStartRecord, SpindleStopRecord,
    ToolActivationRecord, UnitsRecord, WorkOffsetRecord,
)


@dataclass(frozen=True, slots=True)
class PostSourceSnapshot:
    """Immutable application snapshot consumed by the post service."""

    project_id: UUID
    operation: Operation
    artifact: ToolpathArtifact
    setup: Setup
    assembly: ToolAssembly
    tool: ToolDefinition | None = None
    holder: HolderDefinition | None = None
    machine: MachineDefinition | None = None
    simulation_result: SimulationResult | None = None
    expected_simulation_input_fingerprint: DependencyFingerprint | None = None


def _diagnostic(code: PostDiagnosticCode, message_key: str, source: PostSourceSnapshot, *, event_index: int | None = None, severity: DiagnosticSeverity = DiagnosticSeverity.ERROR) -> PostDiagnostic:
    return PostDiagnostic(severity, code, message_key,
                          source.operation.operation_id, source.artifact.artifact_id, event_index, None)


def _markers(source: PostSourceSnapshot, policy: LoweringPolicy) -> tuple[PostDiagnostic, ...]:
    strategy = source.operation.strategy_key
    expected = {
        "tapping_v1": "hms_tapping_sync_v1",
        "reaming_v1": "hms_reaming_process_v1",
        "boring_v1": "hms_boring_process_v1",
    }.get(strategy)
    if expected is None:
        return ()
    markers = [event for event in source.artifact.events if isinstance(event, MarkerEvent)]
    if not markers:
        return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.semantic_markers_missing", source),)
    formats = {dict(event.metadata)["format"] for event in markers if "format" in dict(event.metadata)}
    if formats != {expected}:
        return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.semantic_marker_schema_invalid", source),)
    keys = [event.semantic_key for event in markers]
    events = source.artifact.events
    format_markers = [event for event in markers if dict(event.metadata).get("format") == expected]
    metadata = dict(format_markers[0].metadata) if format_markers else {}
    def positive_number(key: str) -> bool:
        try:
            value = float(metadata[key])
            return math.isfinite(value) and value > 0.0
        except (KeyError, TypeError, ValueError):
            return False
    if strategy == "tapping_v1":
        required = ("tap.synchronization_begin", "tap.synchronization_end", "tap.hole_complete")
        if any(key not in keys for key in required) or not keys.index(required[0]) < keys.index(required[1]):
            return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.tapping_markers_invalid", source),)
        if metadata.get("hand") not in {"right_hand_tap", "left_hand_tap"} or metadata.get("policy") not in {"rigid", "floating"}:
            return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.tapping_metadata_invalid", source),)
        if metadata.get("metadata_version") != "1" or metadata.get("pitch_unit") != source.artifact.unit.value or not all(positive_number(key) for key in ("pitch", "rpm", "thread_depth")):
            return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.tapping_numeric_metadata_invalid", source),)
        for begin_index, event in enumerate(events):
            if not isinstance(event, MarkerEvent) or event.semantic_key != required[0]:
                continue
            end_index = next((index for index in range(begin_index + 1, len(events)) if isinstance(events[index], MarkerEvent) and events[index].semantic_key == required[1]), -1)
            section = events[begin_index + 1:end_index] if end_index > begin_index else ()
            spindle = [item for item in section if isinstance(item, SpindleStateEvent) and item.state is not SpindleState.OFF]
            cutting = [item for item in section if isinstance(item, LinearMove) and item.motion_class is MotionClass.CUTTING]
            retract = [item for item in section if isinstance(item, LinearMove) and item.motion_class is MotionClass.RETRACT]
            completed = any(isinstance(item, MarkerEvent) and item.semantic_key == required[2] for item in section)
            if len(spindle) < 2 or spindle[0].state is spindle[1].state or len(cutting) != 1 or len(retract) != 1 or not completed:
                return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.tapping_synchronization_invalid", source),)
            expected_cutting = SpindleState.CLOCKWISE if metadata["hand"] == "right_hand_tap" else SpindleState.COUNTERCLOCKWISE
            if spindle[0].state is not expected_cutting:
                return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.tapping_spindle_direction_invalid", source),)
            if any(not item.feed_rate.unit.value.endswith("per_revolution") or not math.isclose(item.feed_rate.value, float(metadata["pitch"]), rel_tol=0.0, abs_tol=1.0e-12) for item in (cutting[0], retract[0])):
                return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.tapping_feed_sync_invalid", source),)
    elif strategy in {"reaming_v1", "boring_v1"}:
        prefix = "ream" if strategy == "reaming_v1" else "bore"
        required = (f"{prefix}.process_begin", f"{prefix}.hole_complete", f"{prefix}.process_end")
        if any(key not in keys for key in required) or not keys.index(required[0]) < keys.index(required[1]) < keys.index(required[2]):
            return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, f"post.{prefix}_markers_invalid", source),)
        if metadata.get("metadata_version") != "1" or metadata.get("strategy_key") != strategy or metadata.get("strategy_version") != "1" or not all(positive_number(key) for key in ("feed_per_revolution", "rpm")):
            return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, f"post.{prefix}_metadata_invalid", source),)
        begin_index = next(index for index, event in enumerate(events) if isinstance(event, MarkerEvent) and event.semantic_key == required[0])
        end_index = next((index for index in range(begin_index + 1, len(events)) if isinstance(events[index], MarkerEvent) and events[index].semantic_key == required[2]), -1)
        section = events[begin_index + 1:end_index] if end_index > begin_index else ()
        cutting = [item for item in section if isinstance(item, LinearMove) and item.motion_class is MotionClass.CUTTING]
        retract = [item for item in section if isinstance(item, LinearMove) and item.motion_class is MotionClass.RETRACT]
        if len(cutting) != 1 or len(retract) != 1 or any(not item.feed_rate.unit.value.endswith("per_revolution") for item in (cutting[0], retract[0])):
            return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, f"post.{prefix}_controlled_retract_invalid", source),)
        if any(not math.isclose(item.feed_rate.value, float(metadata["feed_per_revolution"]), rel_tol=0.0, abs_tol=1.0e-12) for item in (cutting[0], retract[0])):
            return (_diagnostic(PostDiagnosticCode.UNSUPPORTED_CYCLE, f"post.{prefix}_feed_sync_invalid", source),)
        if strategy == "boring_v1":
            expected_context = ContentFingerprint.from_payload({"assembly": source.assembly.to_dict(), "tool": source.tool.to_dict() if source.tool else None, "holder": source.holder.to_dict() if source.holder else None})
            if metadata.get("tool_context_fingerprint") != expected_context.digest or metadata.get("assembly_fingerprint") != source.assembly.content_fingerprint.digest:
                return (_diagnostic(PostDiagnosticCode.TOOL_STALE, "post.bore_tool_context_invalid", source),)
    return ()


def validate_post_source(source: PostSourceSnapshot, gate: SimulationGatePolicy) -> tuple[PostDiagnostic, ...]:
    """Validate provenance and safety preconditions before any lowering."""
    operation, artifact, setup, assembly = source.operation, source.artifact, source.setup, source.assembly
    diagnostics: list[PostDiagnostic] = []
    if artifact.completion_status is not ToolpathCompletionStatus.COMPLETE:
        diagnostics.append(_diagnostic(PostDiagnosticCode.SOURCE_STALE, "post.artifact_not_complete", source))
    if artifact.source_operation_id != operation.operation_id or artifact.operation_revision != operation.revision:
        diagnostics.append(_diagnostic(PostDiagnosticCode.MIXED_PROVENANCE, "post.operation_provenance_mismatch", source))
    if operation.artifact_state.status is not ArtifactStatus.VALID or not operation.enabled or not setup.enabled:
        diagnostics.append(_diagnostic(PostDiagnosticCode.SOURCE_INVALID, "post.operation_not_valid", source))
    if artifact.artifact_fingerprint is None or artifact.input_fingerprint != operation.artifact_state.input_fingerprint or (operation.artifact_state.artifact_fingerprint and artifact.artifact_fingerprint != operation.artifact_state.artifact_fingerprint):
        diagnostics.append(_diagnostic(PostDiagnosticCode.SOURCE_STALE, "post.artifact_fingerprint_mismatch", source))
    wcs_fingerprint = ContentFingerprint.from_payload(setup.wcs.to_dict())
    if artifact.setup_id != setup.setup_id or artifact.setup_revision != setup.revision or artifact.wcs_fingerprint != wcs_fingerprint:
        diagnostics.append(_diagnostic(PostDiagnosticCode.SETUP_INVALID, "post.setup_provenance_mismatch", source))
    boring_context = ContentFingerprint.from_payload({
        "assembly": assembly.to_dict(),
        "tool": source.tool.to_dict() if source.tool else None,
        "holder": source.holder.to_dict() if source.holder else None,
    })
    assembly_reference_fingerprint = ContentFingerprint.from_payload(assembly.to_dict())
    accepted_tool_contexts = {assembly.content_fingerprint, assembly_reference_fingerprint}
    if operation.strategy_key == "boring_v1" and source.tool is not None and source.holder is not None:
        accepted_tool_contexts.add(boring_context)
    if artifact.tool_assembly_id != assembly.assembly_id or artifact.tool_assembly_fingerprint not in accepted_tool_contexts:
        diagnostics.append(_diagnostic(PostDiagnosticCode.MIXED_PROVENANCE, "post.tool_assembly_mismatch", source))
    if assembly.unit is not artifact.unit or setup.wcs.origin.unit is not artifact.unit:
        diagnostics.append(_diagnostic(PostDiagnosticCode.UNIT_MISMATCH, "post.unit_mismatch", source))
    if operation.tool_assembly.assembly_id != assembly.assembly_id or operation.tool_assembly.expected_revision != assembly.revision or operation.tool_assembly.expected_fingerprint != assembly_reference_fingerprint or operation.tool_assembly.unit is not assembly.unit:
        diagnostics.append(_diagnostic(PostDiagnosticCode.TOOL_STALE, "post.operation_tool_reference_mismatch", source))
    if source.tool is None:
        diagnostics.append(_diagnostic(PostDiagnosticCode.TOOL_MISSING, "post.tool_missing", source))
    elif source.tool.tool_id != assembly.tool_id or source.tool.revision != assembly.expected_tool_revision or source.tool.content_fingerprint != assembly.expected_tool_fingerprint:
        diagnostics.append(_diagnostic(PostDiagnosticCode.TOOL_STALE, "post.tool_provenance_mismatch", source))
    if assembly.holder_id is not None:
        if source.holder is None or source.holder.holder_id != assembly.holder_id or source.holder.revision != assembly.expected_holder_revision or source.holder.content_fingerprint != assembly.expected_holder_fingerprint:
            diagnostics.append(_diagnostic(PostDiagnosticCode.TOOL_STALE, "post.holder_provenance_mismatch", source))
    requirement = operation.machine_requirement
    if requirement is not None:
        evidence = MachineEvidence(source.machine is not None,
                                  source.machine.revision if source.machine else None,
                                  source.machine.content_fingerprint if source.machine else None,
                                  source.machine.unit if source.machine else None,
                                  source.machine.capabilities.operations if source.machine else ())
        if assess_machine_compatibility(requirement, evidence) is not MachineCompatibilityStatus.COMPATIBLE:
            diagnostics.append(_diagnostic(PostDiagnosticCode.MACHINE_INCOMPATIBLE, "post.machine_requirement_mismatch", source))
    if artifact.machine_id != (source.machine.machine_id if source.machine else None) or artifact.machine_fingerprint != (source.machine.content_fingerprint if source.machine else None):
        diagnostics.append(_diagnostic(PostDiagnosticCode.MACHINE_INCOMPATIBLE, "post.machine_provenance_mismatch", source))
    if source.simulation_result is None:
        if gate.mode is not SimulationGateMode.OPTIONAL:
            diagnostics.append(_diagnostic(PostDiagnosticCode.SIMULATION_MISSING, "post.simulation_missing", source))
        else:
            diagnostics.append(_diagnostic(PostDiagnosticCode.SIMULATION_MISSING, "post.simulation_optional_missing", source, severity=DiagnosticSeverity.WARNING))
    else:
        simulation = source.simulation_result
        expected_input = source.expected_simulation_input_fingerprint
        if simulation.operation_id != operation.operation_id or simulation.artifact_id != artifact.artifact_id or simulation.artifact_fingerprint != artifact.artifact_fingerprint or (expected_input is not None and simulation.input_fingerprint != expected_input):
            diagnostics.append(_diagnostic(PostDiagnosticCode.SIMULATION_STALE, "post.simulation_stale", source))
        elif simulation.status is SimulationStatus.FAIL:
            diagnostics.append(_diagnostic(PostDiagnosticCode.SIMULATION_FAILED, "post.simulation_failed", source))
        elif simulation.status is SimulationStatus.WARN and gate.mode is SimulationGateMode.REQUIRE_PASS:
            diagnostics.append(_diagnostic(PostDiagnosticCode.SIMULATION_FAILED, "post.simulation_warning_not_allowed", source))
        elif simulation.status is SimulationStatus.WARN:
            diagnostics.append(_diagnostic(PostDiagnosticCode.SIMULATION_FAILED, "post.simulation_warning_allowed", source, severity=DiagnosticSeverity.WARNING))
    movements = [event for event in artifact.events if isinstance(event, (RapidMove, LinearMove, ArcMove))]
    if movements and getattr(movements[-1], "motion_class", MotionClass.NON_CUTTING) is MotionClass.CUTTING:
        diagnostics.append(_diagnostic(PostDiagnosticCode.RAPID_UNSAFE, "post.final_retract_missing", source))
    diagnostics.extend(_markers(source, LoweringPolicy()))
    return tuple(sorted(diagnostics, key=lambda item: (item.code.value, item.event_index or -1, item.evidence)))


def lower_toolpath(request: PostRequest, source: PostSourceSnapshot, *, policy: LoweringPolicy | None = None) -> NCProgramIR:
    """Lower events one-for-one; no generator re-entry or motion optimization."""
    policy = policy or request.lowering_policy
    diagnostics = validate_post_source(source, request.simulation_gate_policy)
    if any(item.severity is DiagnosticSeverity.ERROR for item in diagnostics):
        raise CamValidationError("Post source preflight failed")
    capabilities = request.post_definition.capabilities
    production_profile = request.post_definition.production_profile
    if production_profile is not None:
        machine = source.machine
        if machine is None:
            raise CamValidationError("Production post requires an explicit machine definition")
        if machine.kind is not production_profile.machine_type or machine.unit not in production_profile.supported_units:
            raise CamValidationError("Machine kind/unit is incompatible with production profile")
        linear_axes = tuple(axis for axis in machine.axes if axis.axis_type is MachineAxisType.LINEAR)
        if len(linear_axes) != 3 or {axis.name.upper() for axis in linear_axes} != set(production_profile.axes):
            raise CamValidationError("Production profile requires exact X/Y/Z linear axes")
        if machine.manufacturer is None or machine.manufacturer.casefold() != "fanuc":
            raise CamValidationError("Production profile requires a FANUC machine definition")
        if machine.model is None or production_profile.machine_family.casefold() not in machine.model.casefold():
            raise CamValidationError("Machine model is incompatible with production profile")
        context = request.program_context
        if context is None or context.tool_binding.tool_assembly_fingerprint != source.assembly.content_fingerprint:
            raise CamValidationError("Production tool binding is missing or stale")
    if source.operation.strategy_key not in capabilities.supported_operation_strategies:
        raise CamValidationError("Operation strategy is unsupported by post definition")
    if source.operation.strategy_key == "parallel_finishing_3d":
        from hms_cadcam.cam.cam3d.parallel import parallel_artifact_has_safe_contract

        if not parallel_artifact_has_safe_contract(
            source.artifact,
            require_holder_verified=True,
        ):
            raise CamValidationError(
                "Parallel artifact is not SAFE with verified holder scope for post-processing"
            )
    if source.artifact.unit not in capabilities.supported_units:
        raise CamValidationError("Artifact unit is unsupported by post definition")
    required_capability = {
        "facing_2_5d": OperationCapability.MILLING,
        "contour_2d": OperationCapability.MILLING,
        "pocket_2_5d": OperationCapability.MILLING,
        "drilling_v1": OperationCapability.DRILLING,
        "tapping_v1": OperationCapability.TAPPING,
        "reaming_v1": OperationCapability.DRILLING,
        "boring_v1": OperationCapability.DRILLING,
        "parallel_finishing_3d": OperationCapability.MILLING,
    }[source.operation.strategy_key]
    if required_capability not in capabilities.supported_operation_capabilities:
        raise CamValidationError("Operation capability is unsupported by post definition")
    if not capabilities.work_offset_supported or not capabilities.tool_activation_supported:
        raise CamValidationError("Post definition lacks mandatory WCS/tool activation support")
    if source.machine is not None:
        if source.machine.kind not in capabilities.supported_machine_kinds:
            raise CamValidationError("Machine kind is unsupported by post definition")
        if any(axis.name not in capabilities.supported_axes for axis in source.machine.axes):
            raise CamValidationError("Machine axes are unsupported by post definition")
        if required_capability not in source.machine.capabilities.operations:
            raise CamValidationError("Machine operation capability is unsupported")
    if source.operation.strategy_key == "tapping_v1":
        tapping_metadata = next((dict(event.metadata) for event in source.artifact.events
                                 if isinstance(event, MarkerEvent) and dict(event.metadata).get("format") == "hms_tapping_sync_v1"), None)
        if not capabilities.tapping_synchronization or tapping_metadata is None:
            raise CamValidationError("Tapping synchronization is unsupported")
        if TappingMode(tapping_metadata["policy"]) not in capabilities.tapping_modes:
            raise CamValidationError("Tapping mode is unsupported by post definition")
        if source.machine is not None:
            if not any(spindle.synchronized_feed for spindle in source.machine.spindles):
                raise CamValidationError("Machine lacks synchronized tapping feed")
            if TappingMode(tapping_metadata["policy"]) not in source.machine.capabilities.tapping_modes:
                raise CamValidationError("Machine tapping mode is unsupported")
    events = source.artifact.events
    records = [ProgramBeginRecord(0, (("format", "hms_post_neutral_v1"), ("strategy_key", source.operation.strategy_key), ("strategy_version", str(source.operation.strategy_version))))]
    records.extend((UnitsRecord(1, source.artifact.unit), CoordinateModeRecord(2, CoordinateMode.ABSOLUTE), PlaneRecord(3, Plane.XY), WorkOffsetRecord(4, source.setup.work_offset), ToolActivationRecord(5, source.assembly.assembly_id, source.assembly.content_fingerprint, source.tool.tool_id if source.tool else None, source.holder.holder_id if source.holder else None)))
    active_feed: FeedMode | None = None
    for event_index, event in enumerate(events):
        sequence = len(records)
        if isinstance(event, ToolContextEvent):
            if event.tool_assembly_id != source.assembly.assembly_id:
                raise CamValidationError("Tool context changed within one operation")
        elif isinstance(event, FeedModeEvent):
            if event.mode is FeedMode.INVERSE_TIME:
                raise CamValidationError("Inverse-time feed is unsupported")
            if event.mode not in capabilities.supported_feed_modes:
                raise CamValidationError("Feed mode is unsupported by post definition")
            records.append(FeedModeRecord(sequence, event.mode))
            active_feed = event.mode
        elif isinstance(event, RapidMove):
            records.append(RapidMotionRecord(sequence, event.start, event.end, event.motion_class, event.rapid_rate, event.provenance))
        elif isinstance(event, LinearMove):
            required_mode = FeedMode.UNITS_PER_REVOLUTION if event.feed_rate.unit.value.endswith("per_revolution") else FeedMode.UNITS_PER_MINUTE
            if required_mode not in capabilities.supported_feed_modes:
                raise CamValidationError("Motion feed mode is unsupported by post definition")
            expected_feed_unit = ("mm_per_revolution" if event.feed_rate.unit.value.endswith("per_revolution") and source.artifact.unit is LengthUnit.MM else
                                  "inch_per_revolution" if event.feed_rate.unit.value.endswith("per_revolution") else
                                  "mm_per_minute" if source.artifact.unit is LengthUnit.MM else "inch_per_minute")
            if event.feed_rate.unit.value != expected_feed_unit:
                raise CamValidationError("Motion feed unit does not match artifact unit")
            if capabilities.maximum_feed is not None and event.feed_rate.value > capabilities.maximum_feed:
                raise CamValidationError("Motion feed exceeds post limit")
            if source.machine is not None and event.feed_rate.unit is source.machine.capabilities.maximum_feed.unit and event.feed_rate.value > source.machine.capabilities.maximum_feed.value:
                raise CamValidationError("Motion feed exceeds machine limit")
            if active_feed is not required_mode:
                records.append(FeedModeRecord(sequence, required_mode))
                active_feed = required_mode
                sequence += 1
            records.extend((FeedValueRecord(sequence, event.feed_rate), LinearMotionRecord(sequence + 1, event.start, event.end, event.feed_rate, event.motion_class, event.provenance, event.engagement)))
        elif isinstance(event, ArcMove):
            required_mode = FeedMode.UNITS_PER_REVOLUTION if event.feed_rate.unit.value.endswith("per_revolution") else FeedMode.UNITS_PER_MINUTE
            if required_mode not in capabilities.supported_feed_modes or Plane.XY not in capabilities.supported_arc_planes:
                raise CamValidationError("Arc semantics are unsupported by post definition")
            if capabilities.arc_center_formats and ArcCenterFormat.IJK not in capabilities.arc_center_formats:
                raise CamValidationError("Arc center format is unsupported by post definition")
            expected_feed_unit = ("mm_per_revolution" if event.feed_rate.unit.value.endswith("per_revolution") and source.artifact.unit is LengthUnit.MM else
                                  "inch_per_revolution" if event.feed_rate.unit.value.endswith("per_revolution") else
                                  "mm_per_minute" if source.artifact.unit is LengthUnit.MM else "inch_per_minute")
            if event.feed_rate.unit.value != expected_feed_unit:
                raise CamValidationError("Arc feed unit does not match artifact unit")
            if capabilities.maximum_feed is not None and event.feed_rate.value > capabilities.maximum_feed:
                raise CamValidationError("Arc feed exceeds post limit")
            if active_feed is not required_mode:
                records.append(FeedModeRecord(sequence, required_mode))
                active_feed = required_mode
                sequence += 1
            normal = event.plane_normal
            if abs(normal.x) > 1.0e-8 or abs(normal.y) > 1.0e-8 or abs(abs(normal.z) - normal.magnitude) > 1.0e-8:
                raise CamValidationError("Only planar XY arcs are supported")
            records.extend((FeedValueRecord(sequence, event.feed_rate), ArcMotionRecord(sequence + 1, event.start, event.end, event.center, event.plane_normal, event.sweep_radians, event.feed_rate, event.motion_class, event.provenance)))
        elif isinstance(event, DwellEvent):
            records.append(DwellRecord(sequence, event.duration_seconds, event.provenance))
        elif isinstance(event, SpindleStateEvent):
            if event.state is SpindleState.OFF:
                records.append(SpindleStopRecord(sequence))
            else:
                direction = SpindleDirection.CLOCKWISE if event.state is SpindleState.CLOCKWISE else SpindleDirection.COUNTERCLOCKWISE
                if direction not in capabilities.supported_spindle_directions:
                    raise CamValidationError("Spindle direction is unsupported by post definition")
                if capabilities.minimum_rpm is not None and event.speed.value < capabilities.minimum_rpm:
                    raise CamValidationError("Spindle speed is below post limit")
                if capabilities.maximum_rpm is not None and event.speed.value > capabilities.maximum_rpm:
                    raise CamValidationError("Spindle speed exceeds post limit")
                if source.machine is not None and not any(direction in spindle.directions for spindle in source.machine.spindles):
                    raise CamValidationError("Machine spindle direction is unsupported")
                if source.machine is not None and not any(spindle.minimum_speed.value <= event.speed.value <= spindle.maximum_speed.value for spindle in source.machine.spindles):
                    raise CamValidationError("Machine spindle speed is outside range")
                records.extend((SpindleDirectionRecord(sequence, direction), SpindleStartRecord(sequence + 1, direction, event.speed)))
        elif isinstance(event, CoolantStateEvent):
            if event.state not in capabilities.supported_coolant_modes:
                raise CamValidationError("Coolant state is unsupported by post definition")
            if source.machine is not None and event.state is not CoolantState.OFF:
                machine_coolant = {
                    CoolantState.FLOOD: MachineCoolantCapability.FLOOD,
                    CoolantState.MIST: MachineCoolantCapability.MIST,
                    CoolantState.THROUGH_TOOL: MachineCoolantCapability.THROUGH_SPINDLE,
                }[event.state]
                if machine_coolant not in source.machine.capabilities.coolant:
                    raise CamValidationError("Machine coolant capability is unsupported")
            records.append(CoolantRecord(sequence, event.state))
        elif isinstance(event, MarkerEvent) and policy.preserve_semantic_markers:
            records.append(SemanticMarkerRecord(sequence, event.semantic_key, event.message, event.metadata, event.provenance))
        else:
            raise CamValidationError(f"Unsupported toolpath event at index {event_index}")
    records.append(ProgramEndRecord(len(records)))
    return NCProgramIR.create(program_id=NCProgramId.new(), project_id=source.project_id, operation_id=source.operation.operation_id, artifact_id=source.artifact.artifact_id, artifact_fingerprint=source.artifact.artifact_fingerprint, strategy_key=source.operation.strategy_key, strategy_version=source.operation.strategy_version, unit=source.artifact.unit, coordinate_mode=CoordinateMode.ABSOLUTE, plane=Plane.XY, setup_id=source.setup.setup_id, setup_revision=source.setup.revision, wcs=source.setup.wcs, work_offset=source.setup.work_offset, tool_assembly_id=source.assembly.assembly_id, tool_assembly_fingerprint=source.assembly.content_fingerprint, records=tuple(records), diagnostics=diagnostics, production_context=request.program_context)

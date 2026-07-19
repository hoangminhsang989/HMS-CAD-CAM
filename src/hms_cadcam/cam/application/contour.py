"""Validation, offset and deterministic Toolpath generation for 2D Contour v1."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    CamInvariantError,
    ComputationToken,
    ContentFingerprint,
    ContourCurveKind,
    ContourCutDirection,
    ContourLoop,
    ContourOrientation,
    ContourParameters,
    ContourProfileDescriptor,
    ContourProfileSource,
    ContourSegment,
    ContourSide,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    GeometryInputRole,
    GeometryReferenceKind,
    GeometryResolutionStatus,
    MachineDefinition,
    MachineKind,
    Operation,
    OperationCapability,
    OperationInputSnapshot,
    Point3,
    ResolvedContourProfile,
    Setup,
    ToolAssembly,
    ToolDefinition,
    ToolFamily,
    ToolReferenceStatus,
    ToolpathArtifactId,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.cam.toolpath import (
    FeedMode,
    MotionClass,
    Pose,
    SpindleState,
    ToolpathArtifact,
    ToolpathBuilder,
)

_ARTIFACT_NAMESPACE = UUID("160982c4-90fb-4f1e-9e8c-eab5fd140535")
_TOLERANCE = 1.0e-8
_MAX_EVENTS_ESTIMATE = 100_000


class ContourGenerationError(ValueError):
    """Generation failed with a stable user-facing diagnostic."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))


@dataclass(frozen=True, slots=True)
class ContourPath:
    """One exact closed cutter-center loop in Setup WCS."""

    loop: ContourLoop
    source_fingerprint: ContentFingerprint


@dataclass(frozen=True, slots=True)
class ContourInputs:
    operation: Operation
    setup: Setup
    parameters: ContourParameters
    path: ContourPath
    source_polygon: tuple[tuple[float, float], ...]
    assembly: ToolAssembly
    tool: ToolDefinition
    machine: MachineDefinition
    tool_diameter: float
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class ContourComputeResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()


def resolve_profile_in_setup(descriptor: ContourProfileDescriptor, setup: Setup) -> ContourPath:
    """Transform one verified world/model descriptor into Setup WCS exactly once."""
    if descriptor.unit is not setup.wcs.origin.unit:
        raise ContourGenerationError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                     "Đơn vị profile và Setup WCS không khớp.")
    if descriptor.inner_loops:
        raise ContourGenerationError(DiagnosticCode.CONTOUR_UNSUPPORTED_INNER_LOOPS,
                                     "2D Contour v1 không hỗ trợ inner loop hoặc island.")
    alignment = descriptor.normal.dot(setup.wcs.z_axis)
    if abs(alignment) < 1.0 - 1.0e-9:
        raise ContourGenerationError(DiagnosticCode.CONTOUR_NON_PLANAR_PROFILE,
                                     "Mặt phẳng profile không song song mặt phẳng XY của Setup WCS.")

    def setup_point(value: Point3) -> Point3:
        delta = Vector3(value.x - setup.wcs.origin.x, value.y - setup.wcs.origin.y,
                        value.z - setup.wcs.origin.z)
        return Point3(delta.dot(setup.wcs.x_axis), delta.dot(setup.wcs.y_axis),
                      delta.dot(setup.wcs.z_axis), value.unit)

    segments = tuple(ContourSegment(segment.kind, setup_point(segment.start), setup_point(segment.end),
                                    None if segment.center is None else setup_point(segment.center),
                                    None if segment.sweep_radians is None else segment.sweep_radians * (1.0 if alignment > 0 else -1.0))
                     for segment in descriptor.outer_loop.segments)
    z_values = tuple(value for segment in segments for value in (segment.start.z, segment.end.z))
    if max(z_values) - min(z_values) > _TOLERANCE:
        raise ContourGenerationError(DiagnosticCode.CONTOUR_NON_PLANAR_PROFILE,
                                     "Profile không phẳng trong Setup WCS.")
    if _has_self_intersection(_sample_segments(segments)):
        raise ContourGenerationError(DiagnosticCode.CONTOUR_SELF_INTERSECTION,
                                     "Resolved profile bị tự giao.")
    orientation = _orientation(segments)
    loop = ContourLoop(segments, orientation)
    if loop.orientation is ContourOrientation.CLOCKWISE:
        loop = loop.reversed()
    loop = _canonical_start(loop)
    fingerprint = ContentFingerprint.from_payload({
        "reference": descriptor.reference.to_dict(),
        "resolved_profile": descriptor.geometry_fingerprint.to_dict(),
        "occurrence_transform": descriptor.provenance.occurrence_transform.absolute_transform,
        "occurrence_path": descriptor.provenance.occurrence_transform.occurrence_path,
        "setup_wcs": setup.wcs.to_dict(),
        "loop": loop.to_dict(),
    })
    return ContourPath(loop, fingerprint)


def offset_contour(loop: ContourLoop, side: ContourSide, distance: float) -> ContourLoop:
    """Offset one canonical CCW LINE/ARC loop; unsafe topology changes fail closed."""
    if not isinstance(side, ContourSide) or not math.isfinite(distance) or distance < 0.0:
        raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_FAILED, "Khoảng offset không hợp lệ.")
    if side is ContourSide.ON or distance <= _TOLERANCE:
        return loop
    signed = distance if side is ContourSide.INSIDE else -distance
    raw = tuple(_offset_support(segment, signed) for segment in loop.segments)
    joints: list[Point3] = []
    for previous, following in zip(raw, (*raw[1:], raw[0]), strict=True):
        candidates = _support_intersections(previous, following)
        if not candidates:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_FAILED,
                                         "Không thể tạo join offset liên tục.")
        target = Point3((previous.end.x + following.start.x) / 2.0,
                        (previous.end.y + following.start.y) / 2.0,
                        previous.end.z, previous.unit)
        joints.append(min(candidates, key=lambda point: (_distance(point, target), point.x, point.y)))
    rebuilt: list[ContourSegment] = []
    for index, segment in enumerate(raw):
        start = joints[index - 1]
        end = joints[index]
        try:
            if segment.kind is ContourCurveKind.LINE:
                raw_dx, raw_dy = segment.end.x - segment.start.x, segment.end.y - segment.start.y
                new_dx, new_dy = end.x - start.x, end.y - start.y
                if raw_dx * new_dx + raw_dy * new_dy <= _TOLERANCE:
                    raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_COLLAPSED,
                                                 "LINE offset co sập hoặc đảo hướng.")
                rebuilt.append(ContourSegment(segment.kind, start, end))
            else:
                assert segment.center is not None and segment.sweep_radians is not None
                sweep = _directed_sweep(start, end, segment.center, 1 if segment.sweep_radians > 0 else -1)
                if abs(sweep - segment.sweep_radians) > math.pi:
                    raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_FAILED,
                                                 "ARC offset join đảo nhánh hình học.")
                rebuilt.append(ContourSegment(segment.kind, start, end, segment.center, sweep))
        except CamInvariantError as error:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_COLLAPSED, str(error)) from error
    candidate = ContourLoop(tuple(rebuilt), _orientation(tuple(rebuilt)))
    sampled = _sample_loop(candidate)
    area = _polygon_area(sampled)
    if candidate.orientation is not ContourOrientation.COUNTERCLOCKWISE or area <= _TOLERANCE:
        raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_COLLAPSED,
                                     "Offset làm contour co sập hoặc đảo topology.")
    if _has_self_intersection(sampled):
        raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_FAILED,
                                     "Contour sau offset bị tự giao.")
    return _canonical_start(candidate)


class ContourGenerator:
    """Controller-neutral 2D Contour generator using ToolpathBuilder v1."""

    def resolve_inputs(
        self,
        operation: Operation,
        setup: Setup,
        *,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
        machine: MachineDefinition | None,
        resolved_profile: ResolvedContourProfile | ContourProfileDescriptor | None,
    ) -> ContourInputs:
        try:
            parameters = ContourParameters.from_operation_parameters(operation.parameters)
        except (TypeError, ValueError) as error:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_INVALID_PARAMETERS, str(error)) from error
        if operation.family.value != "milling" or operation.setup_id != setup.setup_id:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_INVALID_PARAMETERS,
                                         "2D Contour phải thuộc family MILLING và đúng Setup.")
        inputs = tuple(value for value in operation.geometry_inputs if value.role is GeometryInputRole.PROFILE)
        if len(inputs) != 1:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_PROFILE_MISSING,
                                         "2D Contour cần đúng một persistent profile reference.")
        reference = inputs[0].reference
        if reference.source_id not in setup.source_scope.allowed_source_ids:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                         "Profile reference nằm ngoài source scope của Setup.")
        allowed_kind = (GeometryReferenceKind.FACE if parameters.profile_source is ContourProfileSource.PLANAR_FACE_OUTER
                        else GeometryReferenceKind.SKETCH_OR_PROFILE)
        if inputs[0].expected_kind is not allowed_kind or reference.kind is not allowed_kind:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                         "Loại GeometryReference không khớp nguồn profile.")
        descriptor = self._resolved_descriptor(resolved_profile)
        if descriptor.reference.reference_id != reference.reference_id or descriptor.reference.source_id != reference.source_id:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                         "Resolved profile không khớp GeometryReference của operation.")
        if descriptor.provenance.source_kind is not parameters.profile_source:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                                         "Provenance profile không khớp parameter source.")
        path = resolve_profile_in_setup(descriptor, setup)
        tool_status = operation.tool_assembly.assess(assembly)
        if tool_status is ToolReferenceStatus.MISSING:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_TOOL_MISSING, "Không tìm thấy Tool Assembly đã chọn.")
        if tool_status is not ToolReferenceStatus.VALID:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_TOOL_STALE, "Tool Assembly đã stale hoặc sai đơn vị.")
        assert assembly is not None
        if tool is None or tool.tool_id != assembly.tool_id:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_TOOL_MISSING, "Không tìm thấy Tool Definition.")
        if tool.revision != assembly.expected_tool_revision or tool.content_fingerprint != assembly.expected_tool_fingerprint:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_TOOL_STALE, "Tool Definition không khớp assembly snapshot.")
        if tool.family not in {ToolFamily.END_MILL, ToolFamily.BULL_NOSE_END_MILL}:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_UNSUPPORTED_TOOL,
                                         "2D Contour v1 chỉ hỗ trợ END_MILL và BULL_NOSE_END_MILL.")
        diameter = getattr(tool.cutting_geometry, "diameter", None)
        if diameter is None or diameter.unit is not parameters.unit or diameter.value <= 0.0:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_UNSUPPORTED_TOOL, "Đường kính dao không hợp lệ.")
        cutting_length = tool.cutting_geometry.axial_cutting_length
        required_depth = parameters.top_height.value - parameters.final_cut_depth
        if (cutting_length.unit is not parameters.unit or cutting_length.value + _TOLERANCE < required_depth or
                assembly.stickout.unit is not parameters.unit or assembly.stickout.value + _TOLERANCE < required_depth):
            raise ContourGenerationError(DiagnosticCode.CONTOUR_UNSUPPORTED_TOOL,
                                         "Chiều dài cắt hoặc stickout không đủ cho chiều sâu Contour.")
        requirement = operation.machine_requirement
        if requirement is None or machine is None:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_MACHINE_INCOMPATIBLE, "2D Contour cần máy MILL.")
        if (machine.machine_id != requirement.machine_id or machine.revision != requirement.expected_revision or
                machine.content_fingerprint != requirement.expected_fingerprint or machine.unit is not requirement.unit or
                machine.kind not in {MachineKind.MILL, MachineKind.MILL_TURN} or not machine.capabilities.milling or
                OperationCapability.MILLING not in machine.capabilities.operations):
            raise ContourGenerationError(DiagnosticCode.CONTOUR_MACHINE_INCOMPATIBLE,
                                         "Máy thiếu, stale hoặc không hỗ trợ milling.")
        maximum_feed = machine.capabilities.maximum_feed.to(parameters.cutting_feed_rate.unit).value
        if max(parameters.cutting_feed_rate.value, parameters.plunge_feed_rate.value) > maximum_feed:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_MACHINE_INCOMPATIBLE, "Feed vượt giới hạn máy.")
        if not any(spindle.minimum_speed.value <= parameters.spindle_speed.value <= spindle.maximum_speed.value
                   for spindle in machine.spindles):
            raise ContourGenerationError(DiagnosticCode.CONTOUR_MACHINE_INCOMPATIBLE, "Spindle speed vượt giới hạn máy.")
        offset_distance = diameter.value / 2.0 + parameters.radial_stock_allowance.value
        offset = offset_contour(path.loop, parameters.side, offset_distance)
        desired = _desired_orientation(parameters.side, parameters.direction)
        if offset.orientation is not desired:
            offset = _canonical_start(offset.reversed())
        source_polygon = _sample_loop(path.loop)
        _safe_lead_point(offset, source_polygon, parameters.side, parameters.lead_length.value)
        levels = _depth_levels(parameters)
        event_estimate = len(levels) * (len(offset.segments) + 5) + 8
        if event_estimate > _MAX_EVENTS_ESTIMATE:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_INVALID_PARAMETERS,
                                         "2D Contour vượt giới hạn event an toàn.")
        tool_fp = ContentFingerprint.from_payload({"assembly": assembly.to_dict(), "tool": tool.to_dict()})
        geometry_fp = ContentFingerprint.from_payload({
            "reference": reference.to_dict(),
            "profile_fingerprint": descriptor.geometry_fingerprint.to_dict(),
            "path_fingerprint": path.source_fingerprint.to_dict(),
            "offset_loop": offset.to_dict(),
        })
        snapshot = OperationInputSnapshot(
            operation.strategy_key, operation.strategy_version, parameters.fingerprint,
            (("profile", geometry_fp),),
            (("setup", ContentFingerprint.from_payload({"revision": setup.revision.to_dict()})),
             ("operation", ContentFingerprint.from_payload({"revision": operation.revision.to_dict(),
                                                             "enabled": operation.enabled})),
             ("wcs", ContentFingerprint.from_payload(setup.wcs.to_dict()))),
            tool_fp, machine.content_fingerprint,
        )
        return ContourInputs(operation, setup, parameters, ContourPath(offset, geometry_fp),
                             source_polygon, assembly, tool, machine, diameter.value, snapshot.fingerprint)

    @staticmethod
    def _resolved_descriptor(
        value: ResolvedContourProfile | ContourProfileDescriptor | None,
    ) -> ContourProfileDescriptor:
        if value is None:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_PROFILE_MISSING, "Profile chưa được resolve.")
        if isinstance(value, ContourProfileDescriptor):
            return value
        if not isinstance(value, ResolvedContourProfile):
            raise ContourGenerationError(DiagnosticCode.CONTOUR_PROFILE_MISSING, "Profile resolver trả dữ liệu không hợp lệ.")
        if value.status is not GeometryResolutionStatus.RESOLVED:
            default = {
                GeometryResolutionStatus.MISSING: DiagnosticCode.CONTOUR_PROFILE_MISSING,
                GeometryResolutionStatus.STALE: DiagnosticCode.CONTOUR_PROFILE_STALE,
                GeometryResolutionStatus.AMBIGUOUS: DiagnosticCode.CONTOUR_PROFILE_AMBIGUOUS,
                GeometryResolutionStatus.SOURCE_MISMATCH: DiagnosticCode.CONTOUR_SOURCE_MISMATCH,
                GeometryResolutionStatus.TOPOLOGY_CHANGED: DiagnosticCode.CONTOUR_TOPOLOGY_CHANGED,
            }.get(value.status, DiagnosticCode.CONTOUR_PROFILE_MISSING)
            raise ContourGenerationError(value.diagnostic_code or default,
                                         value.message or f"Profile resolution failed: {value.status.value}")
        assert value.profile is not None
        return value.profile

    def begin(self, inputs: ContourInputs) -> tuple[ContourInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(inputs, operation=replace(inputs.operation, artifact_state=state)), token

    def generate(self, inputs: ContourInputs) -> ToolpathArtifact:
        operation, parameters = inputs.operation, inputs.parameters
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise ContourGenerationError(DiagnosticCode.CONTOUR_GENERATION_FAILED,
                                         "2D Contour generation chưa có computation token hiện hành.")
        artifact_uuid = uuid5(_ARTIFACT_NAMESPACE,
                              f"{operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}")
        builder = ToolpathBuilder(
            artifact_id=ToolpathArtifactId(artifact_uuid), operation_id=operation.operation_id,
            operation_revision=operation.revision, computation_token=token,
            input_fingerprint=inputs.input_fingerprint, unit=parameters.unit,
            setup_id=inputs.setup.setup_id, setup_revision=inputs.setup.revision,
            wcs_fingerprint=ContentFingerprint.from_payload(inputs.setup.wcs.to_dict()),
            tool_assembly_id=inputs.assembly.assembly_id,
            tool_assembly_fingerprint=ContentFingerprint.from_payload(inputs.assembly.to_dict()),
            machine_id=inputs.machine.machine_id, machine_fingerprint=inputs.machine.content_fingerprint,
        )
        try:
            loop = inputs.path.loop
            lead_xy = _safe_lead_point(loop, inputs.source_polygon, parameters.side,
                                       parameters.lead_length.value)
            start = loop.segments[0].start
            axis = Vector3(0.0, 0.0, 1.0)
            builder.set_initial_pose(Pose(Point3(lead_xy[0], lead_xy[1], parameters.clearance_height.value,
                                                       parameters.unit), axis))
            builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
            builder.set_spindle(SpindleState.CLOCKWISE, parameters.spindle_speed,
                                provenance="contour.spindle.on")
            levels = _depth_levels(parameters)
            for pass_index, depth in enumerate(levels):
                lead_clearance = Pose(Point3(lead_xy[0], lead_xy[1], parameters.clearance_height.value,
                                                   parameters.unit), axis)
                _rapid_if_needed(builder, lead_clearance, inputs.machine.capabilities.maximum_rapid,
                                 f"contour.pass.{pass_index}.position")
                lead_at_depth = Pose(Point3(lead_xy[0], lead_xy[1], depth, parameters.unit), axis)
                builder.linear_to(lead_at_depth, parameters.plunge_feed_rate, motion_class=MotionClass.LINK,
                                  provenance=f"contour.pass.{pass_index}.plunge")
                contour_start = Pose(Point3(start.x, start.y, depth, parameters.unit), axis)
                builder.linear_to(contour_start, parameters.cutting_feed_rate, motion_class=MotionClass.LINK,
                                  provenance=f"contour.pass.{pass_index}.lead_in")
                for segment_index, segment in enumerate(loop.segments):
                    end = Pose(Point3(segment.end.x, segment.end.y, depth, parameters.unit), axis)
                    provenance = f"contour.pass.{pass_index}.segment.{segment_index}.cut"
                    if segment.kind is ContourCurveKind.LINE:
                        builder.linear_to(end, parameters.cutting_feed_rate,
                                          motion_class=MotionClass.CUTTING, provenance=provenance)
                    else:
                        assert segment.center is not None and segment.sweep_radians is not None
                        builder.arc_to(end, center=Point3(segment.center.x, segment.center.y, depth, parameters.unit),
                                       plane_normal=axis, sweep_radians=segment.sweep_radians,
                                       feed_rate=parameters.cutting_feed_rate,
                                       motion_class=MotionClass.CUTTING, provenance=provenance)
                builder.linear_to(lead_at_depth, parameters.cutting_feed_rate, motion_class=MotionClass.LINK,
                                  provenance=f"contour.pass.{pass_index}.lead_out")
                retract = Pose(Point3(lead_xy[0], lead_xy[1], parameters.retract_height.value,
                                           parameters.unit), axis)
                builder.linear_to(retract, parameters.plunge_feed_rate, motion_class=MotionClass.RETRACT,
                                  provenance=f"contour.pass.{pass_index}.retract")
            final = builder.current_pose
            assert final is not None
            _rapid_if_needed(builder, Pose(Point3(final.position.x, final.position.y,
                parameters.clearance_height.value, parameters.unit), axis),
                inputs.machine.capabilities.maximum_rapid, "contour.final.clearance")
            builder.set_spindle(SpindleState.OFF, provenance="contour.spindle.off")
            return builder.finalize()
        except ContourGenerationError:
            builder.abort()
            raise
        except Exception as error:
            builder.abort()
            raise ContourGenerationError(DiagnosticCode.CONTOUR_GENERATION_FAILED, str(error)) from error


def _depth_levels(parameters: ContourParameters) -> tuple[float, ...]:
    target = parameters.final_cut_depth
    if not parameters.multiple_depth_passes:
        levels = [target]
    else:
        count = max(1, math.ceil((parameters.top_height.value - target) / parameters.stepdown.value))
        levels = []
        for index in range(1, count + 1):
            value = max(target, parameters.top_height.value - parameters.stepdown.value * index)
            if not levels or abs(value - levels[-1]) > _TOLERANCE:
                levels.append(value)
        if abs(levels[-1] - target) <= _TOLERANCE:
            levels[-1] = target
        elif levels[-1] > target:
            levels.append(target)
    if parameters.finishing_pass:
        levels.append(target)
    return tuple(levels)


def _desired_orientation(side: ContourSide, direction: ContourCutDirection) -> ContourOrientation:
    climb_ccw = side is ContourSide.INSIDE
    ccw = climb_ccw if direction is ContourCutDirection.CLIMB else not climb_ccw
    return ContourOrientation.COUNTERCLOCKWISE if ccw else ContourOrientation.CLOCKWISE


def _canonical_start(loop: ContourLoop) -> ContourLoop:
    """Split the lexicographically lowest segment at its midpoint for a smooth deterministic start."""
    candidates = [(_segment_midpoint(segment).x, _segment_midpoint(segment).y, index)
                  for index, segment in enumerate(loop.segments)]
    index = min(candidates)[2]
    segment = loop.segments[index]
    first, second = _split_segment(segment)
    ordered = (second, *loop.segments[index + 1:], *loop.segments[:index], first)
    return ContourLoop(tuple(ordered), loop.orientation)


def _split_segment(segment: ContourSegment) -> tuple[ContourSegment, ContourSegment]:
    midpoint = _segment_midpoint(segment)
    if segment.kind is ContourCurveKind.LINE:
        return ContourSegment(segment.kind, segment.start, midpoint), ContourSegment(segment.kind, midpoint, segment.end)
    assert segment.center is not None and segment.sweep_radians is not None
    half = segment.sweep_radians / 2.0
    return (ContourSegment(segment.kind, segment.start, midpoint, segment.center, half),
            ContourSegment(segment.kind, midpoint, segment.end, segment.center, half))


def _segment_midpoint(segment: ContourSegment) -> Point3:
    if segment.kind is ContourCurveKind.LINE:
        return Point3((segment.start.x + segment.end.x) / 2.0,
                      (segment.start.y + segment.end.y) / 2.0,
                      (segment.start.z + segment.end.z) / 2.0, segment.unit)
    assert segment.center is not None and segment.sweep_radians is not None
    angle = math.atan2(segment.start.y - segment.center.y, segment.start.x - segment.center.x)
    radius = segment.radius
    assert radius is not None
    return Point3(segment.center.x + radius * math.cos(angle + segment.sweep_radians / 2.0),
                  segment.center.y + radius * math.sin(angle + segment.sweep_radians / 2.0),
                  segment.start.z, segment.unit)


def _offset_support(segment: ContourSegment, signed: float) -> ContourSegment:
    if segment.kind is ContourCurveKind.LINE:
        dx, dy = segment.end.x - segment.start.x, segment.end.y - segment.start.y
        length = math.hypot(dx, dy)
        shift_x, shift_y = -dy / length * signed, dx / length * signed
        return ContourSegment(segment.kind,
            Point3(segment.start.x + shift_x, segment.start.y + shift_y, segment.start.z, segment.unit),
            Point3(segment.end.x + shift_x, segment.end.y + shift_y, segment.end.z, segment.unit))
    assert segment.center is not None and segment.sweep_radians is not None and segment.radius is not None
    radius = segment.radius - math.copysign(signed, segment.sweep_radians)
    if radius <= _TOLERANCE:
        raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_COLLAPSED, "ARC radius co sập sau offset.")
    scale = radius / segment.radius
    point = lambda value: Point3(segment.center.x + (value.x - segment.center.x) * scale,
                                 segment.center.y + (value.y - segment.center.y) * scale,
                                 value.z, value.unit)
    return ContourSegment(segment.kind, point(segment.start), point(segment.end), segment.center,
                          segment.sweep_radians)


def _support_intersections(first: ContourSegment, second: ContourSegment) -> tuple[Point3, ...]:
    if _distance(first.end, second.start) <= _TOLERANCE:
        return (Point3((first.end.x + second.start.x) / 2.0,
                       (first.end.y + second.start.y) / 2.0, first.end.z, first.unit),)
    if first.kind is ContourCurveKind.LINE and second.kind is ContourCurveKind.LINE:
        point = _line_line(first, second)
        return () if point is None else (point,)
    if first.kind is ContourCurveKind.LINE:
        return _line_circle(first, second)
    if second.kind is ContourCurveKind.LINE:
        return _line_circle(second, first)
    return _circle_circle(first, second)


def _line_line(first: ContourSegment, second: ContourSegment) -> Point3 | None:
    px, py = first.start.x, first.start.y
    rx, ry = first.end.x - px, first.end.y - py
    qx, qy = second.start.x, second.start.y
    sx, sy = second.end.x - qx, second.end.y - qy
    denominator = rx * sy - ry * sx
    if abs(denominator) <= _TOLERANCE:
        return None
    t = ((qx - px) * sy - (qy - py) * sx) / denominator
    return Point3(px + t * rx, py + t * ry, first.start.z, first.unit)


def _line_circle(line: ContourSegment, arc: ContourSegment) -> tuple[Point3, ...]:
    assert arc.center is not None and arc.radius is not None
    dx, dy = line.end.x - line.start.x, line.end.y - line.start.y
    fx, fy = line.start.x - arc.center.x, line.start.y - arc.center.y
    a = dx * dx + dy * dy
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - arc.radius * arc.radius
    discriminant = b * b - 4.0 * a * c
    if discriminant < -_TOLERANCE:
        return ()
    root = math.sqrt(max(0.0, discriminant))
    values = ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a))
    return tuple(Point3(line.start.x + value * dx, line.start.y + value * dy,
                        line.start.z, line.unit) for value in values)


def _circle_circle(first: ContourSegment, second: ContourSegment) -> tuple[Point3, ...]:
    assert first.center is not None and second.center is not None
    assert first.radius is not None and second.radius is not None
    dx, dy = second.center.x - first.center.x, second.center.y - first.center.y
    distance = math.hypot(dx, dy)
    if distance <= _TOLERANCE or distance > first.radius + second.radius + _TOLERANCE:
        return ()
    if distance < abs(first.radius - second.radius) - _TOLERANCE:
        return ()
    along = (first.radius ** 2 - second.radius ** 2 + distance ** 2) / (2.0 * distance)
    height = math.sqrt(max(0.0, first.radius ** 2 - along ** 2))
    base_x = first.center.x + along * dx / distance
    base_y = first.center.y + along * dy / distance
    offset_x, offset_y = -dy / distance * height, dx / distance * height
    values = {(round(base_x + offset_x, 14), round(base_y + offset_y, 14)),
              (round(base_x - offset_x, 14), round(base_y - offset_y, 14))}
    return tuple(Point3(x, y, first.start.z, first.unit) for x, y in sorted(values))


def _directed_sweep(start: Point3, end: Point3, center: Point3, sign: int) -> float:
    first = math.atan2(start.y - center.y, start.x - center.x)
    second = math.atan2(end.y - center.y, end.x - center.x)
    if sign > 0:
        return (second - first) % math.tau
    return -((first - second) % math.tau)


def _orientation(segments: tuple[ContourSegment, ...]) -> ContourOrientation:
    area = _polygon_area(_sample_segments(segments))
    if abs(area) <= _TOLERANCE:
        raise ContourGenerationError(DiagnosticCode.CONTOUR_OFFSET_COLLAPSED,
                                     "Contour có diện tích bằng zero.")
    return ContourOrientation.COUNTERCLOCKWISE if area > 0.0 else ContourOrientation.CLOCKWISE


def _sample_loop(loop: ContourLoop) -> tuple[tuple[float, float], ...]:
    return _sample_segments(loop.segments)


def _sample_segments(segments: tuple[ContourSegment, ...]) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for segment in segments:
        if not points:
            points.append((segment.start.x, segment.start.y))
        if segment.kind is ContourCurveKind.LINE:
            points.append((segment.end.x, segment.end.y))
        else:
            assert segment.center is not None and segment.sweep_radians is not None and segment.radius is not None
            count = max(2, math.ceil(abs(segment.sweep_radians) / math.radians(5.0)))
            start = math.atan2(segment.start.y - segment.center.y, segment.start.x - segment.center.x)
            points.extend((segment.center.x + segment.radius * math.cos(start + segment.sweep_radians * index / count),
                           segment.center.y + segment.radius * math.sin(start + segment.sweep_radians * index / count))
                          for index in range(1, count + 1))
    if _distance_xy(points[0], points[-1]) > _TOLERANCE:
        points.append(points[0])
    else:
        points[-1] = points[0]
    return tuple(points)


def _polygon_area(points: tuple[tuple[float, float], ...]) -> float:
    return 0.5 * sum(first[0] * second[1] - second[0] * first[1]
                     for first, second in zip(points, points[1:]))


def _has_self_intersection(points: tuple[tuple[float, float], ...]) -> bool:
    count = len(points) - 1
    for first_index in range(count):
        a1, a2 = points[first_index], points[first_index + 1]
        for second_index in range(first_index + 1, count):
            if second_index in {first_index, first_index + 1} or (first_index == 0 and second_index == count - 1):
                continue
            if _segments_intersect(a1, a2, points[second_index], points[second_index + 1]):
                return True
    return False


def _segments_intersect(a1, a2, b1, b2) -> bool:
    cross = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    values = cross(a1, a2, b1), cross(a1, a2, b2), cross(b1, b2, a1), cross(b1, b2, a2)
    if values[0] * values[1] < -_TOLERANCE and values[2] * values[3] < -_TOLERANCE:
        return True
    on_segment = lambda p, q, r: (min(p[0], q[0]) - _TOLERANCE <= r[0] <= max(p[0], q[0]) + _TOLERANCE and
                                  min(p[1], q[1]) - _TOLERANCE <= r[1] <= max(p[1], q[1]) + _TOLERANCE)
    return any(abs(value) <= _TOLERANCE and on_segment(first, second, point)
               for value, first, second, point in (
                   (values[0], a1, a2, b1), (values[1], a1, a2, b2),
                   (values[2], b1, b2, a1), (values[3], b1, b2, a2)))


def _safe_lead_point(
    loop: ContourLoop,
    source_polygon: tuple[tuple[float, float], ...],
    side: ContourSide,
    length: float,
) -> tuple[float, float]:
    start = loop.segments[0].start
    tangent = _start_tangent(loop.segments[0])
    normals = ((-tangent[1], tangent[0]), (tangent[1], -tangent[0]))
    want_inside = side is ContourSide.INSIDE
    for normal in normals:
        candidate = (start.x + normal[0] * length, start.y + normal[1] * length)
        samples = tuple((start.x + normal[0] * length * ratio,
                         start.y + normal[1] * length * ratio) for ratio in (0.25, 0.5, 0.75, 1.0))
        if all(_point_in_polygon(point, source_polygon) is want_inside for point in samples):
            return candidate
    raise ContourGenerationError(DiagnosticCode.CONTOUR_UNSAFE_LEAD,
                                 "Không thể tạo linear lead-in/lead-out ở đúng phía profile.")


def _start_tangent(segment: ContourSegment) -> tuple[float, float]:
    if segment.kind is ContourCurveKind.LINE:
        dx, dy = segment.end.x - segment.start.x, segment.end.y - segment.start.y
    else:
        assert segment.center is not None and segment.sweep_radians is not None
        radial_x, radial_y = segment.start.x - segment.center.x, segment.start.y - segment.center.y
        dx, dy = (-radial_y, radial_x) if segment.sweep_radians > 0 else (radial_y, -radial_x)
    length = math.hypot(dx, dy)
    return dx / length, dy / length


def _point_in_polygon(point: tuple[float, float], polygon: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    x, y = point
    for first, second in zip(polygon, polygon[1:]):
        if ((first[1] > y) != (second[1] > y)):
            crossing = (second[0] - first[0]) * (y - first[1]) / (second[1] - first[1]) + first[0]
            if x < crossing:
                inside = not inside
    return inside


def _rapid_if_needed(builder: ToolpathBuilder, end: Pose, rapid_rate, provenance: str) -> None:
    current = builder.current_pose
    assert current is not None
    distance = math.sqrt((current.position.x - end.position.x) ** 2 +
                         (current.position.y - end.position.y) ** 2 +
                         (current.position.z - end.position.z) ** 2)
    if distance > _TOLERANCE:
        builder.rapid_to(end, rapid_rate=rapid_rate, provenance=provenance)


def _distance(first: Point3, second: Point3) -> float:
    return math.sqrt((first.x - second.x) ** 2 + (first.y - second.y) ** 2 + (first.z - second.z) ** 2)


def _distance_xy(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])

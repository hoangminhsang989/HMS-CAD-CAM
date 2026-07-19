"""Validation, geometry resolution and deterministic Facing 2.5D generation."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from hms_cadcam.cam.domain import (
    ArtifactStatus, BoxStock, CamInvariantError, ComputationToken, ContentFingerprint, DependencyFingerprint,
    DiagnosticCode, DiagnosticSeverity, FacingBoundarySource, FacingCutDirection,
    FacingParameters, FacingRegion, GeometryFingerprint, GeometryInputRole,
    GeometryReferenceKind, GeometryResolutionStatus, LengthUnit, MachineDefinition,
    MachineKind, Operation, OperationCapability, OperationInputSnapshot, PlanarFaceDescriptor, Point3, Setup,
    ToolAssembly, ToolDefinition, ToolFamily, ToolReferenceStatus, ToolpathArtifactId,
    ResolvedMachiningGeometry, ValidationDiagnostic, Vector3,
)
from hms_cadcam.cam.toolpath import (
    CoolantState, FeedMode, MotionClass, Pose, SpindleState, ToolpathArtifact,
    ToolpathBuilder,
)

_ARTIFACT_NAMESPACE = UUID("f1f13ec6-7ed7-4d12-a920-1816de0abe0a")
_TOLERANCE = 1.0e-8
_MAX_CUTTING_PASSES = 20_000


class FacingGenerationError(ValueError):
    """Generation failed with one stable user-facing diagnostic code."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))


@dataclass(frozen=True, slots=True)
class FacingInputs:
    operation: Operation
    setup: Setup
    parameters: FacingParameters
    region: FacingRegion
    assembly: ToolAssembly
    tool: ToolDefinition
    machine: MachineDefinition
    tool_diameter: float
    input_fingerprint: DependencyFingerprint
    planar_boundary: bool = False


@dataclass(frozen=True, slots=True)
class FacingComputeResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()


def resolve_box_facing_region(setup: Setup) -> FacingRegion:
    """Resolve BOX top boundary into Setup WCS without native CAD objects."""
    stock = setup.stock
    if not isinstance(stock, BoxStock):
        raise FacingGenerationError(DiagnosticCode.FACING_UNSUPPORTED_STOCK,
                                    "Facing v1 chỉ hỗ trợ Stock BOX hoặc mặt phẳng đã resolve.")
    if stock.frame.origin.unit is not setup.wcs.origin.unit:
        raise FacingGenerationError(DiagnosticCode.FACING_UNSUPPORTED_STOCK, "Đơn vị Stock và Setup WCS không khớp.")
    alignment = stock.frame.z_axis.dot(setup.wcs.z_axis)
    if alignment < 1.0 - _TOLERANCE:
        raise FacingGenerationError(DiagnosticCode.FACING_AXIS_MISMATCH,
                                    "Trục Z của Stock không cùng hướng với trục dao Setup WCS.")

    def world_point(x: float, y: float, z: float) -> Point3:
        origin, frame = stock.frame.origin, stock.frame
        return Point3(origin.x + frame.x_axis.x * x + frame.y_axis.x * y + frame.z_axis.x * z,
                      origin.y + frame.x_axis.y * x + frame.y_axis.y * y + frame.z_axis.y * z,
                      origin.z + frame.x_axis.z * x + frame.y_axis.z * y + frame.z_axis.z * z,
                      origin.unit)

    def setup_point(value: Point3) -> Point3:
        delta = Vector3(value.x - setup.wcs.origin.x, value.y - setup.wcs.origin.y,
                        value.z - setup.wcs.origin.z)
        return Point3(delta.dot(setup.wcs.x_axis), delta.dot(setup.wcs.y_axis),
                      delta.dot(setup.wcs.z_axis), value.unit)

    z = stock.size_z.value
    boundary = tuple(setup_point(world_point(x, y, z)) for x, y in (
        (0.0, 0.0), (stock.size_x.value, 0.0),
        (stock.size_x.value, stock.size_y.value), (0.0, stock.size_y.value)))
    fingerprint = GeometryFingerprint.from_payload({"source": "stock_box", "stock": stock.to_dict(),
                                                     "setup_wcs": setup.wcs.to_dict()})
    return FacingRegion(boundary, Vector3(0.0, 0.0, 1.0), fingerprint)


def resolve_planar_face_region(descriptor: PlanarFaceDescriptor, setup: Setup) -> FacingRegion:
    """Transform a verified world/model descriptor into Setup WCS."""
    if descriptor.unit is not setup.wcs.origin.unit:
        raise FacingGenerationError(
            DiagnosticCode.FACING_GEOMETRY_RESOLUTION_FAILED,
            "Planar FACE and Setup WCS must use the same declared unit.",
        )
    if descriptor.inner_boundaries:
        raise FacingGenerationError(
            DiagnosticCode.FACING_UNSUPPORTED_INNER_LOOPS,
            "Planar Facing v1 does not support inner loops.",
        )

    def setup_point(value: Point3) -> Point3:
        delta = Vector3(value.x - setup.wcs.origin.x, value.y - setup.wcs.origin.y,
                        value.z - setup.wcs.origin.z)
        return Point3(delta.dot(setup.wcs.x_axis), delta.dot(setup.wcs.y_axis),
                      delta.dot(setup.wcs.z_axis), value.unit)

    normal = Vector3(descriptor.normal.dot(setup.wcs.x_axis),
                     descriptor.normal.dot(setup.wcs.y_axis),
                     descriptor.normal.dot(setup.wcs.z_axis))
    magnitude = normal.magnitude
    if magnitude <= _TOLERANCE or abs(normal.z / magnitude) < 1.0 - _TOLERANCE:
        raise FacingGenerationError(
            DiagnosticCode.FACING_AXIS_MISMATCH,
            "Planar FACE normal must be parallel or opposite to the Setup tool axis.",
        )
    points = tuple(setup_point(point) for point in descriptor.outer_boundary.points[:-1])
    if normal.z < 0.0:
        normal = Vector3(-normal.x, -normal.y, -normal.z)
        points = tuple(reversed(points))
    fingerprint = GeometryFingerprint.from_payload({
        "descriptor": descriptor.geometry_fingerprint.to_dict(),
        "reference_id": str(descriptor.reference_id),
        "setup_wcs": setup.wcs.to_dict(),
        "boundary": [point.to_dict() for point in points],
    })
    return FacingRegion(points, normal, fingerprint)


class FacingGenerator:
    """Controller-neutral raster generator using ToolpathBuilder 7A.5."""

    def resolve_inputs(self, operation: Operation, setup: Setup, *, assembly: ToolAssembly | None,
                       tool: ToolDefinition | None, machine: MachineDefinition | None,
                       resolved_face: ResolvedMachiningGeometry | FacingRegion | None = None) -> FacingInputs:
        try:
            parameters = FacingParameters.from_operation_parameters(operation.parameters)
        except Exception as error:
            raise FacingGenerationError(DiagnosticCode.FACING_INVALID_PARAMETERS, str(error)) from error
        if operation.family.value != "milling" or operation.setup_id != setup.setup_id:
            raise FacingGenerationError(DiagnosticCode.FACING_INVALID_PARAMETERS,
                                        "Facing phải thuộc family MILLING và đúng Setup.")
        tool_status = operation.tool_assembly.assess(assembly)
        if tool_status is ToolReferenceStatus.MISSING:
            raise FacingGenerationError(DiagnosticCode.FACING_TOOL_MISSING, "Không tìm thấy Tool Assembly đã chọn.")
        if tool_status is not ToolReferenceStatus.VALID:
            raise FacingGenerationError(DiagnosticCode.FACING_TOOL_STALE, "Tool Assembly đã stale hoặc sai đơn vị.")
        assert assembly is not None
        if tool is None or tool.tool_id != assembly.tool_id:
            raise FacingGenerationError(DiagnosticCode.FACING_TOOL_MISSING, "Không tìm thấy Tool Definition của assembly.")
        if (tool.revision != assembly.expected_tool_revision or
                tool.content_fingerprint != assembly.expected_tool_fingerprint):
            raise FacingGenerationError(DiagnosticCode.FACING_TOOL_STALE, "Tool Definition không khớp snapshot của assembly.")
        if tool.family not in {ToolFamily.FACE_MILL, ToolFamily.END_MILL, ToolFamily.BULL_NOSE_END_MILL}:
            raise FacingGenerationError(DiagnosticCode.FACING_UNSUPPORTED_TOOL,
                                        "Facing v1 chỉ nhận face mill, end mill hoặc bull-nose end mill.")
        diameter = getattr(tool.cutting_geometry, "diameter", None)
        if diameter is None or diameter.unit is not parameters.unit or diameter.value <= 0.0:
            raise FacingGenerationError(DiagnosticCode.FACING_UNSUPPORTED_TOOL, "Đường kính dao Facing không hợp lệ.")
        requirement = operation.machine_requirement
        if requirement is None or machine is None:
            raise FacingGenerationError(DiagnosticCode.FACING_MACHINE_INCOMPATIBLE, "Facing cần một máy MILL đã chọn.")
        if (machine.machine_id != requirement.machine_id or machine.revision != requirement.expected_revision or
                machine.content_fingerprint != requirement.expected_fingerprint or machine.unit is not requirement.unit or
                machine.kind not in {MachineKind.MILL, MachineKind.MILL_TURN} or not machine.capabilities.milling or
                OperationCapability.MILLING not in machine.capabilities.operations):
            raise FacingGenerationError(DiagnosticCode.FACING_MACHINE_INCOMPATIBLE,
                                        "Máy không tồn tại, stale hoặc không hỗ trợ milling.")
        maximum_feed = machine.capabilities.maximum_feed.to(parameters.feed_rate.unit).value
        if max(parameters.feed_rate.value, parameters.plunge_feed_rate.value) > maximum_feed:
            raise FacingGenerationError(DiagnosticCode.FACING_MACHINE_INCOMPATIBLE, "Feed vượt giới hạn máy.")
        if not any(spindle.minimum_speed.value <= parameters.spindle_speed.value <= spindle.maximum_speed.value
                   for spindle in machine.spindles):
            raise FacingGenerationError(DiagnosticCode.FACING_MACHINE_INCOMPATIBLE, "Spindle speed vượt giới hạn máy.")
        if parameters.stepover.value > diameter.value:
            raise FacingGenerationError(DiagnosticCode.FACING_INVALID_PARAMETERS,
                                        "Stepover không được lớn hơn đường kính dao.")
        if parameters.boundary_source is FacingBoundarySource.STOCK_BOX:
            region = resolve_box_facing_region(setup)
        elif resolved_face is None:
            raise FacingGenerationError(DiagnosticCode.FACING_GEOMETRY_UNRESOLVED,
                                        "Mặt phẳng Facing chưa được resolve an toàn.")
        else:
            if isinstance(resolved_face, ResolvedMachiningGeometry):
                if resolved_face.status is not GeometryResolutionStatus.RESOLVED:
                    code = resolved_face.diagnostic_code or (
                        DiagnosticCode.FACING_GEOMETRY_STALE
                        if resolved_face.status in {GeometryResolutionStatus.STALE,
                                                   GeometryResolutionStatus.TOPOLOGY_CHANGED}
                        else DiagnosticCode.FACING_GEOMETRY_UNRESOLVED
                    )
                    raise FacingGenerationError(code, resolved_face.message or
                                                f"Face resolution failed: {resolved_face.status.value}")
                try:
                    assert resolved_face.planar_face is not None
                    geometry_inputs = tuple(
                        item for item in operation.geometry_inputs
                        if item.role is GeometryInputRole.BOUNDARY
                    )
                    if len(geometry_inputs) != 1 or (
                        geometry_inputs[0].expected_kind is not GeometryReferenceKind.FACE
                    ):
                        raise FacingGenerationError(
                            DiagnosticCode.FACING_FACE_REFERENCE_MISSING,
                            "Planar Facing requires exactly one persistent FACE boundary reference.",
                        )
                    reference = geometry_inputs[0].reference
                    descriptor = resolved_face.planar_face
                    if descriptor.reference_id != reference.reference_id or descriptor.source_id != reference.source_id:
                        raise FacingGenerationError(
                            DiagnosticCode.FACING_FACE_SOURCE_MISMATCH,
                            "Resolved planar FACE does not match the operation reference.",
                        )
                    region = resolve_planar_face_region(descriptor, setup)
                except FacingGenerationError:
                    raise
                except CamInvariantError as error:
                    code = (DiagnosticCode.FACING_NON_PLANAR_FACE if "not planar" in str(error)
                            else DiagnosticCode.FACING_AXIS_MISMATCH)
                    raise FacingGenerationError(code, str(error)) from error
            elif isinstance(resolved_face, FacingRegion):
                region = resolved_face
            else:
                raise FacingGenerationError(DiagnosticCode.FACING_GEOMETRY_UNRESOLVED,
                                            "Face resolver returned an invalid descriptor.")
        stock_region = resolve_box_facing_region(setup)
        stock_top = stock_region.boundary[0].z
        region_z = region.boundary[0].z
        if abs(stock_top - parameters.top_height.value) > _TOLERANCE:
            raise FacingGenerationError(DiagnosticCode.FACING_INVALID_PARAMETERS,
                                        "Top height must equal Stock BOX top in Setup WCS.")
        if parameters.boundary_source is FacingBoundarySource.PLANAR_FACE:
            if region_z > stock_top + _TOLERANCE:
                raise FacingGenerationError(DiagnosticCode.FACING_TARGET_ABOVE_STOCK,
                                            "Selected planar FACE is above Stock BOX top.")
            if abs(region_z - parameters.target_height.value) > _TOLERANCE:
                raise FacingGenerationError(DiagnosticCode.FACING_INVALID_PARAMETERS,
                                            "Target height must equal the selected planar FACE plane.")
        depth_ratio = ((parameters.top_height.value - parameters.final_cut_height) /
                       parameters.stepdown.value)
        lane_count = _estimated_raster_lane_count(
            region, parameters.raster_angle_degrees, parameters.stepover.value)
        if (not math.isfinite(depth_ratio) or not math.isfinite(lane_count) or
                max(1, math.ceil(depth_ratio)) * int(lane_count) > _MAX_CUTTING_PASSES):
            raise FacingGenerationError(DiagnosticCode.FACING_INVALID_PARAMETERS,
                                        "Facing vượt giới hạn 20.000 cutting passes an toàn.")
        tool_fp = ContentFingerprint.from_payload({"assembly": assembly.to_dict(), "tool": tool.to_dict()})
        machine_fp = machine.content_fingerprint
        snapshot = OperationInputSnapshot(operation.strategy_key, operation.strategy_version,
            parameters.fingerprint, (("boundary", region.fingerprint),),
            (("setup", ContentFingerprint.from_payload({"revision": setup.revision.to_dict()})),
             ("stock", ContentFingerprint.from_payload(setup.stock.to_dict())),
             ("wcs", ContentFingerprint.from_payload(setup.wcs.to_dict()))),
            tool_fp, machine_fp)
        return FacingInputs(operation, setup, parameters, region, assembly, tool, machine,
                            diameter.value, snapshot.fingerprint,
                            parameters.boundary_source is FacingBoundarySource.PLANAR_FACE)

    def begin(self, inputs: FacingInputs) -> tuple[FacingInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(inputs, operation=replace(inputs.operation, artifact_state=state)), token

    def generate(self, inputs: FacingInputs) -> ToolpathArtifact:
        operation, parameters = inputs.operation, inputs.parameters
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise FacingGenerationError(DiagnosticCode.FACING_GENERATION_FAILED,
                                        "Facing generation chưa có computation token hiện hành.")
        artifact_uuid = uuid5(_ARTIFACT_NAMESPACE,
            f"{operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}")
        builder = ToolpathBuilder(artifact_id=ToolpathArtifactId(artifact_uuid),
            operation_id=operation.operation_id, operation_revision=operation.revision,
            computation_token=token, input_fingerprint=inputs.input_fingerprint,
            unit=parameters.unit, setup_id=inputs.setup.setup_id, setup_revision=inputs.setup.revision,
            wcs_fingerprint=ContentFingerprint.from_payload(inputs.setup.wcs.to_dict()),
            tool_assembly_id=inputs.assembly.assembly_id,
            tool_assembly_fingerprint=ContentFingerprint.from_payload(inputs.assembly.to_dict()),
            machine_id=inputs.machine.machine_id, machine_fingerprint=inputs.machine.content_fingerprint)
        try:
            extension = (0.0 if inputs.planar_boundary else
                         inputs.tool_diameter / 2.0 + parameters.overtravel.value)
            lanes = _raster_lanes(inputs.region, parameters.raster_angle_degrees,
                                  parameters.stepover.value, extension)
            if not lanes:
                raise CamInvariantError("Facing boundary does not produce any cutting lanes")
            levels = _depth_levels(parameters.top_height.value, parameters.final_cut_height,
                                   parameters.stepdown.value)
            if len(lanes) * len(levels) > _MAX_CUTTING_PASSES:
                raise FacingGenerationError(DiagnosticCode.FACING_INVALID_PARAMETERS,
                                            "Facing exceeds the 20,000 cutting-pass limit.")
            first = _oriented_lane(lanes[0], parameters.direction, 0)
            initial = Pose(Point3(first[0][0], first[0][1], parameters.clearance_height.value, parameters.unit),
                           Vector3(0.0, 0.0, 1.0))
            builder.set_initial_pose(initial)
            builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
            builder.set_spindle(SpindleState.CLOCKWISE, parameters.spindle_speed,
                                provenance="facing.spindle.on")
            for depth_index, depth in enumerate(levels):
                for lane_index, lane in enumerate(lanes):
                    start, end = _oriented_lane(lane, parameters.direction, lane_index)
                    clearance_pose = Pose(Point3(start[0], start[1], parameters.clearance_height.value, parameters.unit),
                                          Vector3(0.0, 0.0, 1.0))
                    _rapid_if_needed(builder, clearance_pose, inputs.machine.capabilities.maximum_rapid,
                                     f"facing.level.{depth_index}.lane.{lane_index}.position")
                    cut_start = Pose(Point3(start[0], start[1], depth, parameters.unit), Vector3(0.0, 0.0, 1.0))
                    builder.linear_to(cut_start, parameters.plunge_feed_rate, motion_class=MotionClass.LINK,
                                      provenance=f"facing.level.{depth_index}.lane.{lane_index}.approach")
                    cut_end = Pose(Point3(end[0], end[1], depth, parameters.unit), Vector3(0.0, 0.0, 1.0))
                    builder.linear_to(cut_end, parameters.feed_rate, motion_class=MotionClass.CUTTING,
                                      provenance=f"facing.level.{depth_index}.lane.{lane_index}.cut")
                    retract = Pose(Point3(end[0], end[1], parameters.retract_height.value, parameters.unit),
                                   Vector3(0.0, 0.0, 1.0))
                    builder.linear_to(retract, parameters.plunge_feed_rate, motion_class=MotionClass.RETRACT,
                                      provenance=f"facing.level.{depth_index}.lane.{lane_index}.retract")
            final = builder.current_pose
            assert final is not None
            _rapid_if_needed(builder, Pose(Point3(final.position.x, final.position.y,
                parameters.clearance_height.value, parameters.unit), Vector3(0.0, 0.0, 1.0)),
                inputs.machine.capabilities.maximum_rapid, "facing.final.clearance")
            builder.set_spindle(SpindleState.OFF, provenance="facing.spindle.off")
            return builder.finalize()
        except FacingGenerationError:
            builder.abort()
            raise
        except Exception as error:
            builder.abort()
            raise FacingGenerationError(DiagnosticCode.FACING_GENERATION_FAILED, str(error)) from error


def _depth_levels(top: float, target: float, stepdown: float) -> tuple[float, ...]:
    count = max(1, math.ceil((top - target) / stepdown))
    levels: list[float] = []
    for index in range(1, count + 1):
        depth = max(target, top - stepdown * index)
        if not levels or depth != levels[-1]:
            levels.append(depth)
    if abs(levels[-1] - target) <= _TOLERANCE:
        levels[-1] = target
    elif levels[-1] > target:
        levels.append(target)
    return tuple(levels)


def _raster_lanes(region: FacingRegion, angle_degrees: float, step: float,
                  extension: float) -> tuple[tuple[tuple[float, float], tuple[float, float]], ...]:
    angle = math.radians(angle_degrees)
    u_axis, v_axis = (math.cos(angle), math.sin(angle)), (-math.sin(angle), math.cos(angle))
    transformed = tuple((point.x * u_axis[0] + point.y * u_axis[1],
                         point.x * v_axis[0] + point.y * v_axis[1]) for point in region.boundary)
    v_min, v_max = min(value[1] for value in transformed), max(value[1] for value in transformed)
    positions = [v_min]
    while positions[-1] + step < v_max - _TOLERANCE:
        positions.append(positions[-1] + step)
    if v_max - positions[-1] > _TOLERANCE:
        positions.append(v_max)
    lanes = []
    for v in positions:
        intersections: list[float] = []
        for index, first in enumerate(transformed):
            second = transformed[(index + 1) % len(transformed)]
            if extension > _TOLERANCE and abs(first[1] - v) <= _TOLERANCE:
                intersections.append(first[0])
            delta = second[1] - first[1]
            if extension <= _TOLERANCE and abs(delta) > _TOLERANCE and (
                (first[1] <= v < second[1]) or (second[1] <= v < first[1])
            ):
                ratio = (v - first[1]) / delta
                intersections.append(first[0] + ratio * (second[0] - first[0]))
            elif extension > _TOLERANCE and abs(delta) > _TOLERANCE and (
                -_TOLERANCE <= (v - first[1]) / delta <= 1.0 + _TOLERANCE
            ):
                ratio = (v - first[1]) / delta
                intersections.append(first[0] + ratio * (second[0] - first[0]))
        crossings = (sorted(round(value, 12) for value in intersections)
                     if extension <= _TOLERANCE else
                     sorted({round(value, 12) for value in intersections}))
        if not crossings:
            continue
        if len(crossings) % 2:
            if len(crossings) == 1 and extension > _TOLERANCE:
                epsilon = max(_TOLERANCE, extension)
                crossings = [crossings[0] - epsilon, crossings[0] + epsilon]
            else:
                continue
        to_xy = lambda u: (u * u_axis[0] + v * v_axis[0],
                           u * u_axis[1] + v * v_axis[1])
        for index in range(0, len(crossings), 2):
            start_u = crossings[index] - extension
            end_u = crossings[index + 1] + extension
            if end_u - start_u > _TOLERANCE:
                lanes.append((to_xy(start_u), to_xy(end_u)))
    return tuple(lanes)


def _estimated_raster_lane_count(region: FacingRegion, angle_degrees: float,
                                  step: float) -> float:
    angle = math.radians(angle_degrees)
    v_axis = (-math.sin(angle), math.cos(angle))
    positions = tuple(point.x * v_axis[0] + point.y * v_axis[1]
                      for point in region.boundary)
    ratio = (max(positions) - min(positions)) / step
    return math.ceil(ratio) + 1 if math.isfinite(ratio) else math.inf


def _oriented_lane(lane: tuple[tuple[float, float], tuple[float, float]],
                   direction: FacingCutDirection, index: int) -> tuple[tuple[float, float], tuple[float, float]]:
    reverse = direction is FacingCutDirection.CONVENTIONAL or (
        direction is FacingCutDirection.BIDIRECTIONAL and index % 2 == 1)
    return (lane[1], lane[0]) if reverse else lane


def _rapid_if_needed(builder: ToolpathBuilder, end: Pose, rapid_rate, provenance: str) -> None:
    current = builder.current_pose
    assert current is not None
    distance = math.sqrt((current.position.x - end.position.x) ** 2 +
                         (current.position.y - end.position.y) ** 2 +
                         (current.position.z - end.position.z) ** 2)
    if distance > _TOLERANCE:
        builder.rapid_to(end, rapid_rate=rapid_rate, provenance=provenance)

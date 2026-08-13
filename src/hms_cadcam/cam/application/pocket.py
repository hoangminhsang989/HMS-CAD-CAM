"""Deterministic Pocket offset-clearing core using Toolpath IR v1."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable
from uuid import UUID, uuid5

from hms_cadcam.cam.application.contour import (
    ContourPath,
    ContourGenerationError,
    _sample_loop,
    offset_contour,
    resolve_profile_in_setup,
)
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
)
from hms_cadcam.cam.automatic_pocket import (
    POCKET_AUTOMATIC_POLICY_KEY,
    POCKET_AUTOMATIC_POLICY_VERSION,
    PocketAutomaticContext,
    PocketAutomaticEntryPlacement,
    pocket_automatic_entry_loops,
    resolve_pocket_automatic_contract,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ComputationToken,
    ContentFingerprint,
    ContourCurveKind,
    ContourLoop,
    ContourOrientation,
    ContourProfileDescriptor,
    ContourSide,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    GeometryInputRole,
    GeometryResolutionStatus,
    MachineDefinition,
    MachineKind,
    Operation,
    OperationCapability,
    OperationInputSnapshot,
    PocketCuttingDirection,
    PocketEntryPolicy,
    PocketRegion,
    PocketStrategy,
    PocketValidationError,
    Point3,
    ResolvedPocketGeometry,
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
    ArcMove,
    FeedMode,
    LinearMove,
    MotionClass,
    Pose,
    SpindleState,
    ToolpathArtifact,
    ToolpathBuilder,
)

_ARTIFACT_NAMESPACE = UUID("82836e44-6d71-44f4-963e-91be25ffbfb1")
_MAX_OFFSET_LOOPS = 10_000
_MAX_EVENTS_ESTIMATE = 100_000


class PocketGenerationError(ValueError):
    """Pocket validation or generation failed with one stable diagnostic."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))


@dataclass(frozen=True, slots=True)
class PocketInputs:
    operation: Operation
    setup: Setup
    strategy: PocketStrategy
    region: PocketRegion
    offset_loops: tuple[ContourLoop, ...]
    depth_levels: tuple[float, ...]
    assembly: ToolAssembly
    tool: ToolDefinition
    machine: MachineDefinition
    tool_diameter: float
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class PocketComputeResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()
    no_rest_material: bool = False


def pocket_feed_independent_fingerprint(inputs: PocketInputs) -> ContentFingerprint:
    """Identify every Pocket dependency except cutting and plunge feed values."""
    if not isinstance(inputs, PocketInputs):
        raise TypeError("Pocket incremental input is invalid")
    strategy = inputs.strategy.to_dict()
    strategy.pop("cutting_feed_rate")
    strategy.pop("plunge_feed_rate")
    return ContentFingerprint.from_payload({
        "format": "HMS_R251_POCKET_FEED_INDEPENDENT_INPUT",
        "format_version": 1,
        "operation_id": str(inputs.operation.operation_id),
        "operation_enabled": inputs.operation.enabled,
        "geometry_inputs": [item.to_dict() for item in inputs.operation.geometry_inputs],
        "strategy": strategy,
        "region": inputs.region.fingerprint.to_dict(),
        "offset_loops": [loop.to_dict() for loop in inputs.offset_loops],
        "depth_levels": list(inputs.depth_levels),
        "setup_id": str(inputs.setup.setup_id),
        "stock": inputs.setup.stock.to_dict(),
        "wcs": inputs.setup.wcs.to_dict(),
        "assembly": inputs.assembly.to_dict(),
        "tool": inputs.tool.to_dict(),
        "machine": inputs.machine.to_dict(),
    })


def pocket_lead_independent_fingerprint(inputs: PocketInputs) -> ContentFingerprint:
    """Identify Pocket cutting geometry independently from Lead-In length."""
    if not isinstance(inputs, PocketInputs):
        raise TypeError("Pocket lead incremental input is invalid")
    strategy = inputs.strategy.to_dict()
    strategy.pop("lead_in_length")
    return ContentFingerprint.from_payload({
        "format": "HMS_R266_POCKET_LEAD_INDEPENDENT_INPUT",
        "format_version": 1,
        "operation_id": str(inputs.operation.operation_id),
        "operation_enabled": inputs.operation.enabled,
        "geometry_inputs": [item.to_dict() for item in inputs.operation.geometry_inputs],
        "strategy": strategy,
        "region": inputs.region.fingerprint.to_dict(),
        "offset_loops": [loop.to_dict() for loop in inputs.offset_loops],
        "depth_levels": list(inputs.depth_levels),
        "setup_id": str(inputs.setup.setup_id),
        "stock": inputs.setup.stock.to_dict(),
        "wcs": inputs.setup.wcs.to_dict(),
        "assembly": inputs.assembly.to_dict(),
        "tool": inputs.tool.to_dict(),
        "machine": inputs.machine.to_dict(),
    })


def build_pocket_offset_loops(
    boundary: ContourLoop,
    initial_offset: float,
    stepover: float,
    tolerance: float,
    *,
    terminal_coverage_radius: float | None = None,
) -> tuple[ContourLoop, ...]:
    """Build inward loops until the remaining core is proven tool-covered."""
    coverage_radius = (initial_offset if terminal_coverage_radius is None
                       else terminal_coverage_radius)
    if (not isinstance(boundary, ContourLoop)
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value)
                   for value in (initial_offset, stepover, tolerance, coverage_radius))):
        raise PocketGenerationError(DiagnosticCode.POCKET_OFFSET_FAILED,
                                    "Pocket offset input is invalid")
    if initial_offset <= 0.0 or tolerance <= 0.0 or coverage_radius <= 0.0:
        raise PocketGenerationError(DiagnosticCode.POCKET_OFFSET_FAILED,
                                    "Pocket offset, coverage radius, and tolerance must be positive")
    if stepover <= 0.0:
        raise PocketGenerationError(DiagnosticCode.POCKET_INVALID_STEPOVER,
                                    "Pocket stepover must be positive")
    try:
        current = offset_contour(_merge_canonical_split(boundary, tolerance),
                                 ContourSide.INSIDE, initial_offset)
    except ContourGenerationError as error:
        code = (DiagnosticCode.POCKET_OFFSET_COLLAPSED
                if error.code is DiagnosticCode.CONTOUR_OFFSET_COLLAPSED
                else DiagnosticCode.POCKET_OFFSET_FAILED)
        raise PocketGenerationError(code, str(error)) from error
    loops = [current]
    while len(loops) < _MAX_OFFSET_LOOPS:
        try:
            candidate = offset_contour(_merge_canonical_split(current, tolerance),
                                       ContourSide.INSIDE, stepover)
        except ContourGenerationError as error:
            exhausted = _offset_is_proven_exhausted(current, coverage_radius, tolerance)
            if error.code is DiagnosticCode.CONTOUR_OFFSET_COLLAPSED and exhausted:
                return tuple(loops)
            if (error.code is DiagnosticCode.CONTOUR_OFFSET_FAILED
                    and exhausted):
                return tuple(loops)
            code = (DiagnosticCode.POCKET_OFFSET_COLLAPSED
                    if error.code is DiagnosticCode.CONTOUR_OFFSET_COLLAPSED
                    else DiagnosticCode.POCKET_OFFSET_FAILED)
            raise PocketGenerationError(code, str(error)) from error
        previous_start = current.segments[0].start
        candidate_start = candidate.segments[0].start
        if math.hypot(previous_start.x - candidate_start.x,
                      previous_start.y - candidate_start.y) <= tolerance:
            raise PocketGenerationError(DiagnosticCode.POCKET_OFFSET_COLLAPSED,
                                        "Pocket offset did not make geometric progress")
        loops.append(candidate)
        current = candidate
    raise PocketGenerationError(DiagnosticCode.POCKET_OFFSET_FAILED,
                                "Pocket exceeds the deterministic offset-loop limit")


def _merge_canonical_split(loop: ContourLoop, tolerance: float) -> ContourLoop:
    """Rejoin only the first/last support split introduced by Contour canonicalization."""
    if len(loop.segments) < 3:
        return loop
    first, last = loop.segments[0], loop.segments[-1]
    if first.kind is not last.kind:
        return loop
    if first.kind is ContourCurveKind.LINE:
        first_vector = (first.end.x - first.start.x, first.end.y - first.start.y)
        last_vector = (last.end.x - last.start.x, last.end.y - last.start.y)
        cross = first_vector[0] * last_vector[1] - first_vector[1] * last_vector[0]
        dot = first_vector[0] * last_vector[0] + first_vector[1] * last_vector[1]
        if abs(cross) > tolerance or dot <= 0.0:
            return loop
        merged = type(first)(first.kind, last.start, first.end)
    else:
        if (first.center is None or last.center is None
                or first.sweep_radians is None or last.sweep_radians is None
                or math.dist((first.center.x, first.center.y),
                             (last.center.x, last.center.y)) > tolerance
                or first.sweep_radians * last.sweep_radians <= 0.0
                or abs(first.sweep_radians + last.sweep_radians) >= math.tau - tolerance):
            return loop
        merged = type(first)(first.kind, last.start, first.end, first.center,
                             first.sweep_radians + last.sweep_radians)
    return ContourLoop((merged, *loop.segments[1:-1]), loop.orientation)


def _offset_is_proven_exhausted(
    loop: ContourLoop,
    coverage_radius: float,
    tolerance: float,
) -> bool:
    """Prove the cutter sweep covers a convex residual core; never use bounds."""
    points = _sample_loop(loop)
    signs = []
    for index in range(len(points) - 1):
        first = points[index - 1 if index else len(points) - 2]
        current = points[index]
        following = points[index + 1]
        cross = ((current[0] - first[0]) * (following[1] - current[1])
                 - (current[1] - first[1]) * (following[0] - current[0]))
        if abs(cross) > tolerance:
            signs.append(cross)
    if not signs or any(value < 0.0 for value in signs):
        return False
    widths = []
    for first, second in zip(points, points[1:]):
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = math.hypot(dx, dy)
        if length <= tolerance:
            continue
        normal = (-dy / length, dx / length)
        projections = tuple(point[0] * normal[0] + point[1] * normal[1]
                            for point in points[:-1])
        widths.append(max(projections) - min(projections))
    return bool(widths) and min(widths) <= 2.0 * coverage_radius + tolerance


def pocket_depth_levels(
    top_z: float,
    final_depth: float,
    stepdown: float,
    tolerance: float,
) -> tuple[float, ...]:
    """Return unique descending Z layers with one exact final layer."""
    values = (top_z, final_depth, stepdown, tolerance)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in values):
        raise PocketGenerationError(DiagnosticCode.POCKET_INVALID_STEPDOWN,
                                    "Pocket depth-layer input must be finite")
    if stepdown <= 0.0 or tolerance <= 0.0:
        raise PocketGenerationError(DiagnosticCode.POCKET_INVALID_STEPDOWN,
                                    "Pocket stepdown and tolerance must be positive")
    if final_depth >= top_z - tolerance:
        raise PocketGenerationError(DiagnosticCode.POCKET_INVALID_STEPDOWN,
                                    "Pocket final depth must be below top Z")
    count = max(1, math.ceil((top_z - final_depth) / stepdown))
    levels: list[float] = []
    for index in range(1, count + 1):
        level = max(final_depth, top_z - stepdown * index)
        if not levels or abs(level - levels[-1]) > tolerance:
            levels.append(level)
    if abs(levels[-1] - final_depth) <= tolerance:
        levels[-1] = final_depth
    elif levels[-1] > final_depth:
        levels.append(final_depth)
    return tuple(levels)


def prepare_pocket_machining_geometry(
    region: PocketRegion,
    setup: Setup,
    *,
    tool_diameter: float,
    radial_stock_allowance: float,
    stepover: float,
    tolerance: float,
    cutting_direction: PocketCuttingDirection,
) -> tuple[ContourPath, tuple[ContourLoop, ...]]:
    """Return the exact cutter-centre loops shared by AUTO and generation."""
    if not isinstance(region, PocketRegion) or not isinstance(setup, Setup):
        raise PocketGenerationError(
            DiagnosticCode.POCKET_PROFILE_INVALID,
            "Pocket machining region or Setup is invalid",
        )
    if not isinstance(cutting_direction, PocketCuttingDirection):
        raise PocketGenerationError(
            DiagnosticCode.POCKET_PROFILE_INVALID,
            "Pocket cutting direction is invalid",
        )
    values = (tool_diameter, radial_stock_allowance, stepover, tolerance)
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values
    ):
        raise PocketGenerationError(
            DiagnosticCode.POCKET_OFFSET_FAILED,
            "Pocket machining geometry inputs must be finite",
        )
    if (
        tool_diameter <= 0.0
        or radial_stock_allowance < 0.0
        or stepover <= 0.0
        or tolerance <= 0.0
    ):
        raise PocketGenerationError(
            DiagnosticCode.POCKET_OFFSET_FAILED,
            "Pocket machining geometry inputs are outside safe bounds",
        )
    descriptor = ContourProfileDescriptor(
        region.reference,
        region.plane_origin,
        region.x_axis,
        region.y_axis,
        region.normal,
        region.boundary.outer_loop,
        (),
        region.bounds,
        region.unit,
        region.source_fingerprint,
        region.provenance,
    )
    try:
        path = resolve_profile_in_setup(descriptor, setup)
    except ContourGenerationError as error:
        raise PocketGenerationError(
            DiagnosticCode.POCKET_PROFILE_INVALID, str(error)
        ) from error
    loops = build_pocket_offset_loops(
        path.loop,
        tool_diameter / 2.0 + radial_stock_allowance,
        stepover,
        tolerance,
        terminal_coverage_radius=tool_diameter / 2.0,
    )
    if cutting_direction is PocketCuttingDirection.CONVENTIONAL:
        loops = tuple(loop.reversed() for loop in loops)
    elif any(
        loop.orientation is not ContourOrientation.COUNTERCLOCKWISE for loop in loops
    ):
        raise PocketGenerationError(
            DiagnosticCode.POCKET_OFFSET_FAILED,
            "Pocket climb loops are not counterclockwise",
        )
    return path, loops


def _stored_automatic_contract(
    operation: Operation,
) -> AutomaticParameterContract | None:
    raw = dict(operation.parameters.values).get(AUTOMATIC_PARAMETER_CONTRACT_KEY)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise PocketGenerationError(
            DiagnosticCode.POCKET_PROFILE_INVALID,
            "Pocket automatic metadata is invalid",
        )
    try:
        contract = AutomaticParameterContract.from_json(raw)
    except ValueError as error:
        raise PocketGenerationError(
            DiagnosticCode.POCKET_PROFILE_INVALID,
            "Pocket automatic metadata is malformed",
        ) from error
    if contract.policy_key != POCKET_AUTOMATIC_POLICY_KEY:
        raise PocketGenerationError(
            DiagnosticCode.POCKET_PROFILE_INVALID,
            "Pocket automatic policy identity is invalid",
        )
    return contract


def _automatic_entry_placement(
    contract: AutomaticParameterContract,
    current: AutomaticParameterContract,
    strategy: PocketStrategy,
) -> PocketAutomaticEntryPlacement | None:
    if contract.policy_version != POCKET_AUTOMATIC_POLICY_VERSION:
        raise PocketGenerationError(
            DiagnosticCode.POCKET_PROFILE_INVALID,
            "Pocket automatic policy version is unsupported",
        )
    for key, expected in (
        ("stepdown", strategy.stepdown.value),
        ("stepover", strategy.stepover.value),
    ):
        try:
            item = contract.value(key)
        except KeyError:
            continue
        if item.mode is AutomaticParameterMode.AUTO:
            current_item = current.value(key)
            if (
                item.source != POCKET_AUTOMATIC_POLICY_KEY
                or item.policy_version != POCKET_AUTOMATIC_POLICY_VERSION
                or item.status is not AutomaticParameterStatus.RESOLVED
                or current_item.status is not AutomaticParameterStatus.RESOLVED
                or item.dependency_fingerprint
                != current_item.dependency_fingerprint
                or item.inputs != current_item.inputs
                or not isinstance(item.effective_value, (int, float))
                or isinstance(item.effective_value, bool)
                or not math.isfinite(float(item.effective_value))
                or not math.isclose(
                    float(item.effective_value),
                    expected,
                    rel_tol=0.0,
                    abs_tol=strategy.tolerance.value,
                )
                or not math.isclose(
                    float(item.effective_value),
                    float(current_item.effective_value),
                    rel_tol=0.0,
                    abs_tol=strategy.tolerance.value,
                )
            ):
                raise PocketGenerationError(
                    DiagnosticCode.POCKET_PROFILE_INVALID,
                    f"Pocket AUTO {key} does not match current authoritative evidence",
                )
    keys = (
        "entry_loop_index",
        "entry_segment_index",
        "entry_point_x",
        "entry_point_y",
        "entry_clearance",
    )
    try:
        entries = tuple(contract.value(key) for key in keys)
    except KeyError:
        # Earlier additive contracts remain executable with canonical starts.
        return None
    if all(item.status is not AutomaticParameterStatus.RESOLVED for item in entries):
        return None
    current_entries = tuple(current.value(key) for key in keys)
    if (
        any(
            item.mode is not AutomaticParameterMode.AUTO
            or item.source != POCKET_AUTOMATIC_POLICY_KEY
            or item.policy_version != POCKET_AUTOMATIC_POLICY_VERSION
            or item.status is not AutomaticParameterStatus.RESOLVED
            or item.dependency_fingerprint
            != current_item.dependency_fingerprint
            or item.inputs != current_item.inputs
            for item, current_item in zip(entries, current_entries)
        )
        or entries[0].status is not AutomaticParameterStatus.RESOLVED
        or type(entries[0].effective_value) is not int
        or entries[1].status is not AutomaticParameterStatus.RESOLVED
        or type(entries[1].effective_value) is not int
        or any(
            item.status is not AutomaticParameterStatus.RESOLVED
            or not isinstance(item.effective_value, (int, float))
            or isinstance(item.effective_value, bool)
            or not math.isfinite(float(item.effective_value))
            for item in entries[2:]
        )
    ):
        raise PocketGenerationError(
            DiagnosticCode.POCKET_ENTRY_UNSAFE,
            "Pocket automatic entry placement is unresolved",
        )
    for item, current_item in zip(entries, current_entries):
        if isinstance(item.effective_value, int) and not isinstance(
            item.effective_value, bool
        ):
            matches = item.effective_value == current_item.effective_value
        else:
            matches = math.isclose(
                float(item.effective_value),
                float(current_item.effective_value),
                rel_tol=0.0,
                abs_tol=strategy.tolerance.value,
            )
        if not matches:
            raise PocketGenerationError(
                DiagnosticCode.POCKET_ENTRY_UNSAFE,
                "Pocket automatic entry placement is stale",
            )
    return PocketAutomaticEntryPlacement(
        entries[0].effective_value,
        entries[1].effective_value,
        float(entries[2].effective_value),
        float(entries[3].effective_value),
        float(entries[4].effective_value),
    )


class PocketGenerator:
    """Controller-neutral Pocket generator with fail-closed validation."""

    def resolve_inputs(
        self,
        operation: Operation,
        setup: Setup,
        *,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
        machine: MachineDefinition | None,
        resolved_geometry: ResolvedPocketGeometry | None,
        geometry_provider: Callable[[
            PocketRegion, Setup, float, float, float, float, PocketCuttingDirection
        ], tuple[ContourPath, tuple[ContourLoop, ...]]] | None = None,
    ) -> PocketInputs:
        geometry_inputs = tuple(value for value in operation.geometry_inputs
                                if value.role is GeometryInputRole.BOUNDARY)
        if len(geometry_inputs) != 1:
            raise PocketGenerationError(DiagnosticCode.POCKET_PROFILE_MISSING,
                                        "Pocket requires one persistent boundary reference")
        if len(operation.geometry_inputs) != 1:
            raise PocketGenerationError(
                DiagnosticCode.POCKET_PROFILE_INVALID,
                "Pocket v1 does not support additional geometry inputs",
            )
        if geometry_inputs[0].reference.source_id not in setup.source_scope.allowed_source_ids:
            raise PocketGenerationError(
                DiagnosticCode.POCKET_PROFILE_INVALID,
                "Pocket boundary reference is outside the Setup source scope",
            )
        try:
            strategy = PocketStrategy.from_operation_parameters(
                operation.parameters, geometry_inputs[0].reference)
        except PocketValidationError as error:
            raise PocketGenerationError(error.code, str(error)) from error
        if operation.family.value != "milling" or operation.setup_id != setup.setup_id:
            raise PocketGenerationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket must belong to the matching milling Setup")
        if resolved_geometry is None:
            raise PocketGenerationError(DiagnosticCode.POCKET_PROFILE_MISSING,
                                        "Pocket geometry has not been resolved")
        if not isinstance(resolved_geometry, ResolvedPocketGeometry):
            raise PocketGenerationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Pocket geometry resolver returned an invalid result")
        if resolved_geometry.status is not GeometryResolutionStatus.RESOLVED:
            diagnostic = resolved_geometry.diagnostics[0]
            raise PocketGenerationError(diagnostic.code, diagnostic.message)
        region = resolved_geometry.region
        assert region is not None
        if region.reference != geometry_inputs[0].reference:
            raise PocketGenerationError(DiagnosticCode.POCKET_PROFILE_INVALID,
                                        "Resolved Pocket region does not match the operation reference")

        tool_status = operation.tool_assembly.assess(assembly)
        if tool_status is ToolReferenceStatus.MISSING:
            raise PocketGenerationError(DiagnosticCode.POCKET_TOOL_MISSING,
                                        "Pocket Tool Assembly is missing")
        if tool_status is not ToolReferenceStatus.VALID:
            raise PocketGenerationError(DiagnosticCode.POCKET_TOOL_STALE,
                                        "Pocket Tool Assembly is stale or has the wrong unit")
        assert assembly is not None
        if tool is None or tool.tool_id != assembly.tool_id:
            raise PocketGenerationError(DiagnosticCode.POCKET_TOOL_MISSING,
                                        "Pocket Tool Definition is missing")
        if (tool.revision != assembly.expected_tool_revision
                or tool.content_fingerprint != assembly.expected_tool_fingerprint):
            raise PocketGenerationError(DiagnosticCode.POCKET_TOOL_STALE,
                                        "Pocket Tool Definition does not match the assembly snapshot")
        if tool.family is not ToolFamily.END_MILL:
            raise PocketGenerationError(DiagnosticCode.POCKET_UNSUPPORTED_TOOL,
                                        "Pocket v1 supports END_MILL only")
        diameter = getattr(tool.cutting_geometry, "diameter", None)
        if diameter is None or diameter.unit is not strategy.unit or diameter.value <= 0.0:
            raise PocketGenerationError(DiagnosticCode.POCKET_UNSUPPORTED_TOOL,
                                        "Pocket tool diameter is invalid")
        if strategy.stepover.value >= diameter.value - strategy.tolerance.value:
            raise PocketGenerationError(DiagnosticCode.POCKET_INVALID_STEPOVER,
                                        "Pocket stepover must be smaller than tool diameter")
        required_depth = strategy.top_z.value - strategy.final_depth.value
        cutting_length = tool.cutting_geometry.axial_cutting_length
        if (cutting_length.unit is not strategy.unit
                or cutting_length.value + strategy.tolerance.value < required_depth
                or assembly.stickout.unit is not strategy.unit
                or assembly.stickout.value + strategy.tolerance.value < required_depth):
            raise PocketGenerationError(DiagnosticCode.POCKET_UNSUPPORTED_TOOL,
                                        "Pocket tool cutting length or stickout is insufficient")

        requirement = operation.machine_requirement
        if requirement is None or machine is None:
            raise PocketGenerationError(DiagnosticCode.POCKET_MACHINE_INCOMPATIBLE,
                                        "Pocket requires a selected milling machine")
        if (machine.machine_id != requirement.machine_id
                or machine.revision != requirement.expected_revision
                or machine.content_fingerprint != requirement.expected_fingerprint
                or machine.unit is not requirement.unit
                or machine.unit is not strategy.unit
                or machine.kind not in {MachineKind.MILL, MachineKind.MILL_TURN}
                or not machine.capabilities.milling
                or OperationCapability.MILLING not in machine.capabilities.operations):
            raise PocketGenerationError(DiagnosticCode.POCKET_MACHINE_INCOMPATIBLE,
                                        "Pocket machine is missing, stale, or cannot mill")
        maximum_feed = machine.capabilities.maximum_feed.to(strategy.cutting_feed_rate.unit).value
        if max(strategy.cutting_feed_rate.value, strategy.plunge_feed_rate.value) > maximum_feed:
            raise PocketGenerationError(DiagnosticCode.POCKET_MACHINE_INCOMPATIBLE,
                                        "Pocket feed exceeds the machine limit")
        if not any(spindle.minimum_speed.value <= strategy.spindle_speed.value <= spindle.maximum_speed.value
                   for spindle in machine.spindles):
            raise PocketGenerationError(DiagnosticCode.POCKET_MACHINE_INCOMPATIBLE,
                                        "Pocket spindle speed is outside the machine range")
        if strategy.entry_policy is not PocketEntryPolicy.VERTICAL_PLUNGE:
            raise PocketGenerationError(DiagnosticCode.POCKET_ENTRY_UNSAFE,
                                        "Pocket entry policy is not supported safely")

        if geometry_provider is None:
            path, loops = prepare_pocket_machining_geometry(
                region,
                setup,
                tool_diameter=diameter.value,
                radial_stock_allowance=strategy.radial_stock_allowance.value,
                stepover=strategy.stepover.value,
                tolerance=strategy.tolerance.value,
                cutting_direction=strategy.cutting_direction,
            )
        else:
            path, loops = geometry_provider(
                region, setup, diameter.value, strategy.radial_stock_allowance.value,
                strategy.stepover.value, strategy.tolerance.value,
                strategy.cutting_direction,
            )
        if any(abs(segment.start.z - strategy.top_z.value) > strategy.tolerance.value
               for segment in path.loop.segments):
            raise PocketGenerationError(DiagnosticCode.POCKET_INVALID_DEPTH,
                                        "Pocket top Z must match the resolved boundary plane")
        automatic = _stored_automatic_contract(operation)
        if automatic is not None:
            current_automatic = resolve_pocket_automatic_contract(
                PocketAutomaticContext(
                    strategy.unit,
                    tool.family,
                    diameter.value,
                    cutting_length.value,
                    assembly.stickout.value,
                    required_depth,
                    strategy.tolerance.value,
                    path.loop,
                    loops,
                    region.fingerprint.digest,
                    region.boundary.fingerprint.digest,
                    None,
                    tool.content_fingerprint.digest,
                    "reachable",
                ),
                quality_profile=automatic.quality_profile,
            )
            placement = _automatic_entry_placement(
                automatic,
                current_automatic,
                strategy,
            )
            if placement is not None:
                try:
                    loops = pocket_automatic_entry_loops(
                        path.loop,
                        loops,
                        placement,
                        cutter_radius=diameter.value / 2.0,
                        tolerance=strategy.tolerance.value,
                    )
                except ValueError as error:
                    raise PocketGenerationError(
                        DiagnosticCode.POCKET_ENTRY_UNSAFE,
                        str(error),
                    ) from error
        levels = pocket_depth_levels(strategy.top_z.value, strategy.final_depth.value,
                                     strategy.stepdown.value, strategy.tolerance.value)
        lead_events = 1 if strategy.lead_in_length.value > 0.0 else 0
        event_estimate = len(levels) * sum(
            len(loop.segments) + 3 + lead_events for loop in loops
        ) + 4
        if event_estimate > _MAX_EVENTS_ESTIMATE:
            raise PocketGenerationError(DiagnosticCode.POCKET_GENERATION_FAILED,
                                        "Pocket exceeds the safe toolpath event limit")
        tool_fingerprint = ContentFingerprint.from_payload({
            "assembly": assembly.to_dict(), "tool": tool.to_dict(),
        })
        geometry_fingerprint = ContentFingerprint.from_payload({
            "region": region.fingerprint.to_dict(),
            "setup_path": path.source_fingerprint.to_dict(),
            "offset_loops": [loop.to_dict() for loop in loops],
        })
        snapshot = OperationInputSnapshot(
            operation.strategy_key,
            operation.strategy_version,
            strategy.fingerprint,
            (("pocket", geometry_fingerprint),),
            (("setup", ContentFingerprint.from_payload({"revision": setup.revision.to_dict()})),
             ("operation", ContentFingerprint.from_payload({
                 "revision": operation.revision.to_dict(), "enabled": operation.enabled,
             })),
             ("stock", ContentFingerprint.from_payload(setup.stock.to_dict())),
             ("wcs", ContentFingerprint.from_payload(setup.wcs.to_dict()))),
            tool_fingerprint,
            machine.content_fingerprint,
        )
        return PocketInputs(operation, setup, strategy, region, loops, levels, assembly, tool,
                            machine, diameter.value, snapshot.fingerprint)

    def begin(self, inputs: PocketInputs) -> tuple[PocketInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(inputs, operation=replace(inputs.operation, artifact_state=state)), token

    def generate(self, inputs: PocketInputs) -> ToolpathArtifact:
        return self._assemble(inputs)

    def regenerate_lead_only(self, inputs: PocketInputs) -> ToolpathArtifact:
        """Reuse validated cut loops/depths while rebuilding Lead-In and assembly."""
        return self._assemble(inputs)

    def _assemble(self, inputs: PocketInputs) -> ToolpathArtifact:
        operation, strategy = inputs.operation, inputs.strategy
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise PocketGenerationError(DiagnosticCode.POCKET_GENERATION_FAILED,
                                        "Pocket generation requires a current computation token")
        artifact_uuid = uuid5(_ARTIFACT_NAMESPACE,
                              f"{operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}")
        builder = ToolpathBuilder(
            artifact_id=ToolpathArtifactId(artifact_uuid),
            operation_id=operation.operation_id,
            operation_revision=operation.revision,
            computation_token=token,
            input_fingerprint=inputs.input_fingerprint,
            unit=strategy.unit,
            setup_id=inputs.setup.setup_id,
            setup_revision=inputs.setup.revision,
            wcs_fingerprint=ContentFingerprint.from_payload(inputs.setup.wcs.to_dict()),
            tool_assembly_id=inputs.assembly.assembly_id,
            tool_assembly_fingerprint=ContentFingerprint.from_payload(inputs.assembly.to_dict()),
            machine_id=inputs.machine.machine_id,
            machine_fingerprint=inputs.machine.content_fingerprint,
        )
        try:
            axis = Vector3(0.0, 0.0, 1.0)
            first = _lead_start(inputs.offset_loops[0], strategy.lead_in_length.value)
            builder.set_initial_pose(Pose(Point3(first.x, first.y, strategy.clearance_height.value,
                                                  strategy.unit), axis))
            builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
            builder.set_spindle(SpindleState.CLOCKWISE, strategy.spindle_speed,
                                provenance="pocket.spindle.on")
            for depth_index, depth in enumerate(inputs.depth_levels):
                for loop_index, loop in enumerate(inputs.offset_loops):
                    start = loop.segments[0].start
                    lead_start = _lead_start(loop, strategy.lead_in_length.value)
                    clearance = Pose(Point3(lead_start.x, lead_start.y, strategy.clearance_height.value,
                                            strategy.unit), axis)
                    _rapid_if_needed(builder, clearance, inputs.machine.capabilities.maximum_rapid,
                                     f"pocket.depth.{depth_index}.loop.{loop_index}.position")
                    entry = Pose(Point3(lead_start.x, lead_start.y, depth, strategy.unit), axis)
                    builder.linear_to(entry, strategy.plunge_feed_rate, motion_class=MotionClass.LINK,
                                      provenance=f"pocket.depth.{depth_index}.loop.{loop_index}.plunge")
                    if strategy.lead_in_length.value > 0.0:
                        builder.linear_to(
                            Pose(Point3(start.x, start.y, depth, strategy.unit), axis),
                            strategy.cutting_feed_rate,
                            motion_class=MotionClass.CUTTING,
                            provenance=(
                                f"pocket.depth.{depth_index}.loop.{loop_index}.lead_in"
                            ),
                        )
                    for segment_index, segment in enumerate(loop.segments):
                        end = Pose(Point3(segment.end.x, segment.end.y, depth, strategy.unit), axis)
                        provenance = (f"pocket.depth.{depth_index}.loop.{loop_index}."
                                      f"segment.{segment_index}.cut")
                        if segment.kind is ContourCurveKind.LINE:
                            builder.linear_to(end, strategy.cutting_feed_rate,
                                              motion_class=MotionClass.CUTTING,
                                              provenance=provenance)
                        else:
                            assert segment.center is not None and segment.sweep_radians is not None
                            builder.arc_to(
                                end,
                                center=Point3(segment.center.x, segment.center.y, depth, strategy.unit),
                                plane_normal=axis,
                                sweep_radians=segment.sweep_radians,
                                feed_rate=strategy.cutting_feed_rate,
                                motion_class=MotionClass.CUTTING,
                                provenance=provenance,
                            )
                    retract = Pose(Point3(start.x, start.y, strategy.retract_height.value,
                                          strategy.unit), axis)
                    builder.linear_to(retract, strategy.plunge_feed_rate,
                                      motion_class=MotionClass.RETRACT,
                                      provenance=f"pocket.depth.{depth_index}.loop.{loop_index}.retract")
            current = builder.current_pose
            assert current is not None
            _rapid_if_needed(
                builder,
                Pose(Point3(current.position.x, current.position.y,
                            strategy.clearance_height.value, strategy.unit), axis),
                inputs.machine.capabilities.maximum_rapid,
                "pocket.final.clearance",
            )
            builder.set_spindle(SpindleState.OFF, provenance="pocket.spindle.off")
            return builder.finalize()
        except PocketGenerationError:
            builder.abort()
            raise
        except Exception as error:
            builder.abort()
            raise PocketGenerationError(DiagnosticCode.POCKET_GENERATION_FAILED, str(error)) from error

    def regenerate_feed_only(
        self,
        inputs: PocketInputs,
        template: ToolpathArtifact,
    ) -> ToolpathArtifact:
        """Reuse one validated Pocket event topology while replacing feed semantics."""
        operation, strategy = inputs.operation, inputs.strategy
        token = operation.artifact_state.token
        if (
            operation.artifact_state.status is not ArtifactStatus.COMPUTING
            or token is None
            or not isinstance(template, ToolpathArtifact)
            or template.source_operation_id != operation.operation_id
            or template.unit is not strategy.unit
        ):
            raise PocketGenerationError(
                DiagnosticCode.POCKET_GENERATION_FAILED,
                "Pocket feed-only template is stale or invalid",
            )
        events = []
        for event in template.events:
            if isinstance(event, (LinearMove, ArcMove)):
                if event.motion_class is MotionClass.CUTTING:
                    events.append(replace(event, feed_rate=strategy.cutting_feed_rate))
                elif isinstance(event, LinearMove) and event.motion_class in {
                    MotionClass.LINK,
                    MotionClass.RETRACT,
                }:
                    events.append(replace(event, feed_rate=strategy.plunge_feed_rate))
                else:
                    raise PocketGenerationError(
                        DiagnosticCode.POCKET_GENERATION_FAILED,
                        "Pocket feed-only template contains an unsupported motion",
                    )
            else:
                events.append(event)
        artifact_uuid = uuid5(
            _ARTIFACT_NAMESPACE,
            f"{operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}",
        )
        try:
            return ToolpathArtifact.create(
                artifact_id=ToolpathArtifactId(artifact_uuid),
                source_operation_id=operation.operation_id,
                operation_revision=operation.revision,
                computation_token=token,
                input_fingerprint=inputs.input_fingerprint,
                coordinate_space=template.coordinate_space,
                unit=strategy.unit,
                setup_id=inputs.setup.setup_id,
                setup_revision=inputs.setup.revision,
                wcs_fingerprint=ContentFingerprint.from_payload(inputs.setup.wcs.to_dict()),
                tool_assembly_id=inputs.assembly.assembly_id,
                tool_assembly_fingerprint=ContentFingerprint.from_payload(
                    inputs.assembly.to_dict()
                ),
                machine_id=inputs.machine.machine_id,
                machine_fingerprint=inputs.machine.content_fingerprint,
                initial_pose=template.initial_pose,
                events=tuple(events),
                diagnostics=template.diagnostics,
                completion_status=template.completion_status,
            )
        except (TypeError, ValueError) as error:
            raise PocketGenerationError(
                DiagnosticCode.POCKET_GENERATION_FAILED,
                "Pocket feed-only artifact validation failed",
            ) from error


def _rapid_if_needed(builder: ToolpathBuilder, end: Pose, rapid_rate, provenance: str) -> None:
    current = builder.current_pose
    assert current is not None
    distance = math.sqrt((current.position.x - end.position.x) ** 2
                         + (current.position.y - end.position.y) ** 2
                         + (current.position.z - end.position.z) ** 2)
    if distance > 1.0e-8:
        builder.rapid_to(end, rapid_rate=rapid_rate, provenance=provenance)


def _lead_start(loop: ContourLoop, length: float) -> Point3:
    """Return a tangent point on the final LINE segment ending at loop start."""
    start = loop.segments[0].start
    if length <= 0.0:
        return start
    last = loop.segments[-1]
    if last.kind is not ContourCurveKind.LINE or last.end != start:
        raise PocketGenerationError(
            DiagnosticCode.POCKET_ENTRY_UNSAFE,
            "Pocket Lead-In requires a terminal LINE segment",
        )
    dx = last.end.x - last.start.x
    dy = last.end.y - last.start.y
    available = math.hypot(dx, dy)
    if length >= available:
        raise PocketGenerationError(
            DiagnosticCode.POCKET_ENTRY_UNSAFE,
            "Pocket Lead-In must be shorter than the terminal segment",
        )
    ratio = length / available
    return Point3(
        start.x - dx * ratio,
        start.y - dy * ratio,
        start.z,
        start.unit,
    )

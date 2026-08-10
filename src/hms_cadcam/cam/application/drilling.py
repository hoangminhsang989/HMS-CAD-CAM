"""Deterministic controller-neutral drilling strategy core."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from hms_cadcam.cam.automatic_drilling import (
    DRILLING_AUTOMATIC_POLICY_KEY,
    DrillingAutomaticContext,
    resolve_drilling_automatic_contract,
    validate_drilling_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
)
from hms_cadcam.cam.domain import (
    AngleUnit,
    ArtifactStatus,
    ComputationToken,
    ContentFingerprint,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    DrillRetractPolicy,
    DrillingCycle,
    DrillingRegion,
    DrillingStrategy,
    GeometryInputRole,
    GeometryReference,
    GeometryResolutionStatus,
    HolePattern,
    HoleReference,
    Length,
    MachineDefinition,
    MachineKind,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationInputSnapshot,
    Point3,
    ResolvedDrillingGeometry,
    Setup,
    SetupKind,
    ToolAssembly,
    ToolDefinition,
    ToolFamily,
    ToolReferenceStatus,
    ToolpathArtifactId,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.cam.domain.drilling import DrillValidationError
from hms_cadcam.cam.toolpath import (
    FeedMode,
    MotionClass,
    Pose,
    SpindleState,
    ToolpathArtifact,
    ToolpathBuilder,
)

_ARTIFACT_NAMESPACE = UUID("56d68bb4-1792-4e85-964e-94bf0c7bc247")
_MAX_EVENTS_ESTIMATE = 100_000


class DrillingGenerationError(ValueError):
    """Drilling validation or generation failed with a stable diagnostic."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))


@dataclass(frozen=True, slots=True)
class DrillingHole:
    """One normalized hole in Setup WCS."""

    position: Point3
    axis: Vector3
    depth: Length
    diameter: Length | None


@dataclass(frozen=True, slots=True)
class DrillingInputs:
    operation: Operation
    setup: Setup
    strategy: DrillingStrategy
    region: DrillingRegion
    holes: tuple[DrillingHole, ...]
    peck_levels: tuple[float, ...]
    assembly: ToolAssembly
    tool: ToolDefinition
    machine: MachineDefinition
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class DrillingComputeResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()


def drilling_peck_levels(
    top_z: float,
    final_depth: float,
    peck_depth: float,
    tolerance: float,
) -> tuple[float, ...]:
    """Return exact descending peck targets with one final depth."""
    values = (top_z, final_depth, peck_depth, tolerance)
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(float(value)) for value in values):
        raise DrillingGenerationError(
            DiagnosticCode.DRILL_INVALID_PECK, "Peck parameters must be finite"
        )
    if (
        tolerance <= 0.0
        or final_depth >= top_z - tolerance
        or peck_depth <= tolerance
        or peck_depth >= top_z - final_depth
    ):
        raise DrillingGenerationError(
            DiagnosticCode.DRILL_INVALID_PECK, "Peck depth policy is invalid"
        )
    levels: list[float] = []
    current = float(top_z)
    while current - peck_depth > final_depth + tolerance:
        current -= peck_depth
        levels.append(current)
        if len(levels) > _MAX_EVENTS_ESTIMATE:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_GENERATION_FAILED,
                "Peck sequence exceeds the safe event limit",
            )
    levels.append(float(final_depth))
    return tuple(levels)


class DrillingGenerator:
    """Validate inputs and generate semantic SPOT/DRILL/PECK Toolpath IR."""

    def resolve_inputs(
        self,
        operation: Operation,
        setup: Setup,
        *,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
        machine: MachineDefinition | None,
        resolved_geometry: ResolvedDrillingGeometry | None,
    ) -> DrillingInputs:
        try:
            strategy = DrillingStrategy.from_operation_parameters(operation.parameters)
        except DrillValidationError as error:
            raise DrillingGenerationError(error.code, str(error)) from error
        if (
            operation.family is not OperationFamily.DRILLING
            or operation.setup_id != setup.setup_id
            or setup.kind not in {SetupKind.MILL, SetupKind.MILL_TURN}
            or not operation.enabled
        ):
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling requires the matching milling Setup",
            )
        if strategy.unit is not setup.wcs.origin.unit:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_UNIT_MISSING,
                "Drilling strategy and Setup WCS units do not match",
            )
        self._validate_operation_geometry(operation, setup, strategy)
        if resolved_geometry is None:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "Drilling geometry has not been resolved",
            )
        if not isinstance(resolved_geometry, ResolvedDrillingGeometry):
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                "Drilling resolver returned an invalid result",
            )
        if resolved_geometry.status is not GeometryResolutionStatus.RESOLVED:
            diagnostic = resolved_geometry.diagnostics[0]
            raise DrillingGenerationError(diagnostic.code, diagnostic.message)
        region = resolved_geometry.region
        assert region is not None
        if region.geometry_input != strategy.geometry:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_SOURCE_MISMATCH,
                "Resolved drilling geometry does not match the operation input",
            )
        if region.depth != strategy.depth:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_DEPTH_INVALID,
                "Resolved drilling depth does not match the strategy",
            )
        holes = self._holes_in_setup(region, setup, strategy)
        assembly_value, tool_value = self._validate_tool(
            operation, strategy, holes, assembly, tool
        )
        self._validate_automatic_setup(
            operation, strategy, region, assembly_value, tool_value
        )
        machine_value = self._validate_machine(operation, strategy, machine)
        pecks = (
            drilling_peck_levels(
                strategy.top_z.value,
                strategy.final_depth.value,
                strategy.peck_depth.value,
                strategy.tolerance.value,
            )
            if strategy.cycle is DrillingCycle.PECK_DRILL
            else (strategy.final_depth.value,)
        )
        event_estimate = len(holes) * (8 + len(pecks) * 3) + 6
        if event_estimate > _MAX_EVENTS_ESTIMATE:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_GENERATION_FAILED,
                "Drilling operation exceeds the safe event limit",
            )
        tool_fingerprint = ContentFingerprint.from_payload({
            "assembly": assembly_value.to_dict(), "tool": tool_value.to_dict(),
        })
        geometry_fingerprint = ContentFingerprint.from_payload({
            "region": region.fingerprint.to_dict(),
            "setup_holes": [
                {
                    "position": hole.position.to_dict(),
                    "axis": hole.axis.to_dict(),
                    "depth": hole.depth.value,
                    "diameter": None if hole.diameter is None else hole.diameter.value,
                }
                for hole in holes
            ],
        })
        snapshot = OperationInputSnapshot(
            operation.strategy_key,
            operation.strategy_version,
            strategy.fingerprint,
            (("drilling", geometry_fingerprint),),
            (
                ("operation", ContentFingerprint.from_payload({
                    "revision": operation.revision.to_dict(), "enabled": operation.enabled,
                })),
                ("setup", ContentFingerprint.from_payload({
                    "revision": setup.revision.to_dict(),
                })),
                ("stock", ContentFingerprint.from_payload(setup.stock.to_dict())),
                ("wcs", ContentFingerprint.from_payload(setup.wcs.to_dict())),
            ),
            tool_fingerprint,
            machine_value.content_fingerprint,
        )
        return DrillingInputs(
            operation, setup, strategy, region, holes, pecks, assembly_value,
            tool_value, machine_value, snapshot.fingerprint,
        )

    def begin(self, inputs: DrillingInputs) -> tuple[DrillingInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(inputs, operation=replace(inputs.operation, artifact_state=state)), token

    def generate(self, inputs: DrillingInputs) -> ToolpathArtifact:
        operation, strategy = inputs.operation, inputs.strategy
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_GENERATION_FAILED,
                "Drilling generation requires a current computation token",
            )
        artifact_uuid = uuid5(
            _ARTIFACT_NAMESPACE,
            f"{operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}",
        )
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
            tool_assembly_fingerprint=ContentFingerprint.from_payload(
                inputs.assembly.to_dict()
            ),
            machine_id=inputs.machine.machine_id,
            machine_fingerprint=inputs.machine.content_fingerprint,
        )
        try:
            axis = Vector3(0.0, 0.0, 1.0)
            first = inputs.holes[0].position
            builder.set_initial_pose(Pose(Point3(
                first.x, first.y, strategy.clearance_height.value, strategy.unit
            ), axis))
            builder.set_initial_process_state(feed_mode=FeedMode.UNITS_PER_MINUTE)
            builder.set_spindle(
                SpindleState.CLOCKWISE,
                strategy.spindle_speed,
                provenance="drill.spindle.on",
            )
            for hole_index, hole in enumerate(inputs.holes):
                clearance = Pose(Point3(
                    hole.position.x, hole.position.y,
                    strategy.clearance_height.value, strategy.unit,
                ), axis)
                _rapid_if_needed(
                    builder, clearance, inputs.machine.capabilities.maximum_rapid,
                    f"drill.hole.{hole_index}.rapid",
                )
                retract = Pose(Point3(
                    hole.position.x, hole.position.y,
                    strategy.retract_height.value, strategy.unit,
                ), axis)
                builder.linear_to(
                    retract, strategy.feed_rate, motion_class=MotionClass.LINK,
                    provenance=f"drill.hole.{hole_index}.approach",
                )
                previous_depth = strategy.top_z.value
                for peck_index, target_depth in enumerate(inputs.peck_levels):
                    if peck_index:
                        resume = Pose(Point3(
                            hole.position.x, hole.position.y, previous_depth, strategy.unit
                        ), axis)
                        builder.linear_to(
                            resume, strategy.feed_rate, motion_class=MotionClass.LINK,
                            provenance=f"drill.hole.{hole_index}.peck.{peck_index}.resume",
                        )
                    target = Pose(Point3(
                        hole.position.x, hole.position.y, target_depth, strategy.unit
                    ), axis)
                    builder.linear_to(
                        target, strategy.feed_rate, motion_class=MotionClass.CUTTING,
                        provenance=f"drill.hole.{hole_index}.peck.{peck_index}.plunge",
                    )
                    if strategy.dwell_seconds > 0.0 and peck_index == len(inputs.peck_levels) - 1:
                        builder.dwell(
                            strategy.dwell_seconds,
                            provenance=f"drill.hole.{hole_index}.dwell",
                        )
                    retract_z = (
                        strategy.clearance_height.value
                        if strategy.cycle is DrillingCycle.PECK_DRILL
                        and strategy.retract_policy is DrillRetractPolicy.CLEARANCE_HEIGHT
                        else strategy.retract_height.value
                    )
                    peck_retract = Pose(Point3(
                        hole.position.x, hole.position.y, retract_z, strategy.unit
                    ), axis)
                    builder.linear_to(
                        peck_retract, strategy.feed_rate,
                        motion_class=MotionClass.RETRACT,
                        provenance=f"drill.hole.{hole_index}.peck.{peck_index}.retract",
                    )
                    previous_depth = target_depth
                _rapid_if_needed(
                    builder, clearance, inputs.machine.capabilities.maximum_rapid,
                    f"drill.hole.{hole_index}.clearance",
                )
                builder.marker(
                    "drill.hole_complete",
                    provenance=f"drill.hole.{hole_index}.complete",
                )
            builder.set_spindle(SpindleState.OFF, provenance="drill.spindle.off")
            return builder.finalize()
        except DrillingGenerationError:
            builder.abort()
            raise
        except Exception as error:
            builder.abort()
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_GENERATION_FAILED, str(error)
            ) from error

    @staticmethod
    def _validate_operation_geometry(
        operation: Operation, setup: Setup, strategy: DrillingStrategy
    ) -> None:
        expected = _persistent_references(strategy)
        supplied = tuple(
            value for value in operation.geometry_inputs
            if value.role is GeometryInputRole.DRIVE_GEOMETRY
        )
        if len(supplied) != len(operation.geometry_inputs) or len(supplied) != len(expected):
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_GEOMETRY_MISSING,
                "Operation persistent hole references do not match drilling geometry",
            )
        remaining = list(supplied)
        for reference in expected:
            match_index = next((
                index for index, operation_input in enumerate(remaining)
                if operation_input.reference == reference
                and operation_input.expected_kind is reference.kind
            ), None)
            if (
                match_index is None
                or reference.source_id not in setup.source_scope.allowed_source_ids
            ):
                raise DrillingGenerationError(
                    DiagnosticCode.DRILL_SOURCE_MISMATCH,
                    "Drilling reference is missing, mismatched, or outside Setup scope",
                )
            remaining.pop(match_index)

    @staticmethod
    def _holes_in_setup(
        region: DrillingRegion, setup: Setup, strategy: DrillingStrategy
    ) -> tuple[DrillingHole, ...]:
        def setup_point(value: Point3) -> Point3:
            delta = Vector3(
                value.x - setup.wcs.origin.x,
                value.y - setup.wcs.origin.y,
                value.z - setup.wcs.origin.z,
            )
            return Point3(
                delta.dot(setup.wcs.x_axis), delta.dot(setup.wcs.y_axis),
                delta.dot(setup.wcs.z_axis), value.unit,
            )

        holes: list[DrillingHole] = []
        for location in region.pattern.locations:
            position = setup_point(location.position)
            axis = Vector3(
                location.axis.dot(setup.wcs.x_axis),
                location.axis.dot(setup.wcs.y_axis),
                location.axis.dot(setup.wcs.z_axis),
            )
            if axis.dot(Vector3(0.0, 0.0, 1.0)) < 1.0 - strategy.tolerance.value:
                raise DrillingGenerationError(
                    DiagnosticCode.DRILL_UNSUPPORTED_GEOMETRY,
                    "Drilling v1 requires hole axes aligned with Setup +Z",
                )
            if abs(position.z - strategy.top_z.value) > strategy.tolerance.value:
                raise DrillingGenerationError(
                    DiagnosticCode.DRILL_DEPTH_INVALID,
                    "Hole plane must match drilling top Z in Setup WCS",
                )
            holes.append(DrillingHole(
                position, axis, strategy.depth.depth, location.diameter,
            ))
        return tuple(holes)

    @staticmethod
    def _validate_tool(
        operation: Operation,
        strategy: DrillingStrategy,
        holes: tuple[DrillingHole, ...],
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
    ) -> tuple[ToolAssembly, ToolDefinition]:
        status = operation.tool_assembly.assess(assembly)
        if status is ToolReferenceStatus.MISSING:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_TOOL_MISSING, "Drilling Tool Assembly is missing"
            )
        if status is ToolReferenceStatus.STALE:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_TOOL_STALE, "Drilling Tool Assembly is stale"
            )
        if status is not ToolReferenceStatus.VALID:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_TOOL_INVALID,
                "Drilling Tool Assembly has an incompatible unit",
            )
        assert assembly is not None
        if tool is None or tool.tool_id != assembly.tool_id:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_TOOL_MISSING, "Drilling Tool Definition is missing"
            )
        if (
            tool.revision != assembly.expected_tool_revision
            or tool.content_fingerprint != assembly.expected_tool_fingerprint
        ):
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_TOOL_STALE,
                "Drilling Tool Definition does not match the assembly snapshot",
            )
        expected_family = (
            ToolFamily.CENTER_DRILL
            if strategy.cycle is DrillingCycle.SPOT_DRILL
            else ToolFamily.DRILL
        )
        diameter = getattr(tool.cutting_geometry, "diameter", None)
        cutting_length = tool.cutting_geometry.axial_cutting_length
        if (
            tool.family is not expected_family
            or tool.unit is not strategy.unit
            or diameter is None
            or diameter.unit is not strategy.unit
            or diameter.value <= 0.0
            or cutting_length.unit is not strategy.unit
            or cutting_length.value + strategy.tolerance.value < strategy.depth.depth.value
            or assembly.stickout.unit is not strategy.unit
            or assembly.stickout.value + strategy.tolerance.value < strategy.depth.depth.value
        ):
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_TOOL_INVALID,
                f"{strategy.cycle.value} requires a compatible {expected_family.value} tool",
            )
        for hole in holes:
            if (
                hole.diameter is not None
                and abs(hole.diameter.value - diameter.value) > strategy.tolerance.value
            ):
                raise DrillingGenerationError(
                    DiagnosticCode.DRILL_TOOL_INVALID,
                    "Drill diameter does not match the known hole diameter",
                )
        return assembly, tool

    @staticmethod
    def _validate_automatic_setup(
        operation: Operation,
        strategy: DrillingStrategy,
        region: DrillingRegion,
        assembly: ToolAssembly,
        tool: ToolDefinition,
    ) -> None:
        """Recompute every persisted AUTO dependency before toolpath emission."""
        raw = dict(operation.parameters.values).get(AUTOMATIC_PARAMETER_CONTRACT_KEY)
        if raw is None:
            return
        if not isinstance(raw, str):
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling automatic metadata is invalid",
            )
        try:
            stored = AutomaticParameterContract.from_json(raw)
            if stored.policy_key != DRILLING_AUTOMATIC_POLICY_KEY:
                raise ValueError("wrong policy")
            geometry = tool.cutting_geometry
            diameter = getattr(geometry, "diameter", None)
            angle = getattr(geometry, "point_angle", None)
            current = resolve_drilling_automatic_contract(
                DrillingAutomaticContext(
                    strategy.unit,
                    strategy.cycle,
                    region.pattern.locations,
                    strategy.geometry.source.fingerprint.digest,
                    True,
                    tool.family,
                    tool.content_fingerprint.digest,
                    None if diameter is None else diameter.to(strategy.unit).value,
                    geometry.axial_cutting_length.to(strategy.unit).value,
                    assembly.stickout.to(strategy.unit).value,
                    None if angle is None else angle.to(AngleUnit.DEGREE).value,
                    strategy.top_z.value,
                    strategy.final_depth.value,
                    strategy.clearance_height.value,
                    strategy.retract_height.value,
                    None if strategy.peck_depth is None else strategy.peck_depth.value,
                    strategy.tolerance.value,
                ),
                quality_profile=stored.quality_profile,
            )
            validate_drilling_automatic_contract(stored, current)
        except (TypeError, ValueError) as error:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Persisted Drilling Auto Setup is stale or malformed",
            ) from error

    @staticmethod
    def _validate_machine(
        operation: Operation,
        strategy: DrillingStrategy,
        machine: MachineDefinition | None,
    ) -> MachineDefinition:
        requirement = operation.machine_requirement
        if requirement is None or machine is None:
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling requires a selected milling machine",
            )
        if (
            machine.machine_id != requirement.machine_id
            or machine.revision != requirement.expected_revision
            or machine.content_fingerprint != requirement.expected_fingerprint
        ):
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Drilling machine snapshot is stale",
            )
        if (
            machine.unit is not requirement.unit
            or machine.unit is not strategy.unit
            or machine.kind not in {MachineKind.MILL, MachineKind.MILL_TURN}
            or OperationCapability.DRILLING not in requirement.required_capabilities
            or OperationCapability.DRILLING not in machine.capabilities.operations
            or strategy.feed_rate.value > machine.capabilities.maximum_feed.value
            or not machine.spindles
            or not any(
                spindle.minimum_speed.value <= strategy.spindle_speed.value
                <= spindle.maximum_speed.value
                for spindle in machine.spindles
            )
        ):
            raise DrillingGenerationError(
                DiagnosticCode.DRILL_INVALID_PARAMETER,
                "Machine unit, capability, feed, or spindle range is incompatible",
            )
        return machine


def _persistent_references(strategy: DrillingStrategy) -> tuple[GeometryReference, ...]:
    source = strategy.geometry.source
    if isinstance(source, HoleReference):
        return (source.reference,)
    assert isinstance(source, HolePattern)
    return tuple(
        location.reference.reference
        for location in source.locations
        if location.reference is not None
    )


def _rapid_if_needed(
    builder: ToolpathBuilder, end: Pose, rapid_rate, provenance: str
) -> None:
    current = builder.current_pose
    assert current is not None
    distance = math.dist(
        (current.position.x, current.position.y, current.position.z),
        (end.position.x, end.position.y, end.position.z),
    )
    if distance > 1.0e-8:
        builder.rapid_to(end, rapid_rate=rapid_rate, provenance=provenance)

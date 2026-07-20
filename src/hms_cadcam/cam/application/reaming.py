"""Deterministic controller-neutral reaming strategy core."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ComputationToken,
    ContentFingerprint,
    CylindricalGeometry,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    DrillGeometryInput,
    DrillingRegion,
    GeometryInputRole,
    GeometryReference,
    GeometryResolutionStatus,
    HolePattern,
    HoleReference,
    HoleSourceKind,
    Length,
    LengthUnit,
    MachineCoolantCapability,
    MachineDefinition,
    MachineKind,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationInputSnapshot,
    Point3,
    ReamingCoolantMode,
    ReamingStrategy,
    ReamingValidationError,
    ResolvedDrillingGeometry,
    Setup,
    SetupKind,
    SpindleDirection,
    ToolAssembly,
    ToolCoolantCapability,
    ToolDefinition,
    ToolFamily,
    ToolReferenceStatus,
    ToolpathArtifactId,
    ValidationDiagnostic,
    Vector3,
)
from hms_cadcam.cam.toolpath import (
    CoolantState,
    FeedMode,
    MotionClass,
    Pose,
    SpindleState,
    ToolpathArtifact,
    ToolpathBuilder,
)

_ARTIFACT_NAMESPACE = UUID("b91ebc04-aee8-4ad7-9bd3-0d70b8f0ab31")
_MAX_EVENTS_ESTIMATE = 100_000


class ReamingGenerationError(ValueError):
    """Reaming validation or generation failed with a stable diagnostic."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))


@dataclass(frozen=True, slots=True)
class ReamingHole:
    """One normalized reaming location in Setup WCS."""

    position: Point3
    axis: Vector3
    depth: Length
    diameter: Length | None
    source_kind: HoleSourceKind


@dataclass(frozen=True, slots=True)
class ReamingInputs:
    operation: Operation
    setup: Setup
    strategy: ReamingStrategy
    region: DrillingRegion
    holes: tuple[ReamingHole, ...]
    assembly: ToolAssembly
    tool: ToolDefinition
    machine: MachineDefinition
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class ReamingComputeResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()


class ReamingGenerator:
    """Validate inputs and generate controlled-retract semantic Reaming IR."""

    def resolve_inputs(
        self,
        operation: Operation,
        setup: Setup,
        *,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
        machine: MachineDefinition | None,
        resolved_geometry: ResolvedDrillingGeometry | None,
    ) -> ReamingInputs:
        try:
            strategy = ReamingStrategy.from_operation_parameters(operation.parameters)
        except ReamingValidationError as error:
            raise ReamingGenerationError(error.code, str(error)) from error
        if (
            operation.family is not OperationFamily.DRILLING
            or operation.setup_id != setup.setup_id
            or setup.kind not in {SetupKind.MILL, SetupKind.MILL_TURN}
            or not operation.enabled
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming requires the matching enabled milling Setup",
            )
        if strategy.unit is not setup.wcs.origin.unit:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming strategy and Setup WCS units do not match",
            )
        self._validate_operation_geometry(operation, setup, strategy)
        region = self._validate_resolved_geometry(resolved_geometry, strategy)
        holes = self._holes_in_setup(region, setup, strategy)
        assembly_value, tool_value = self._validate_tool(
            operation, strategy, holes, assembly, tool
        )
        machine_value = self._validate_machine(
            operation, strategy, tool_value, machine
        )
        if len(holes) * 14 + 6 > _MAX_EVENTS_ESTIMATE:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_GENERATION_FAILED,
                "Reaming operation exceeds the safe event limit",
            )
        tool_fingerprint = ContentFingerprint.from_payload({
            "assembly": assembly_value.to_dict(),
            "tool": tool_value.to_dict(),
        })
        geometry_fingerprint = ContentFingerprint.from_payload({
            "region": region.fingerprint.to_dict(),
            "setup_holes": [
                {
                    "position": hole.position.to_dict(),
                    "axis": hole.axis.to_dict(),
                    "depth": hole.depth.value,
                    "diameter": (
                        None if hole.diameter is None else hole.diameter.value
                    ),
                    "source_kind": hole.source_kind.value,
                }
                for hole in holes
            ],
        })
        snapshot = OperationInputSnapshot(
            operation.strategy_key,
            operation.strategy_version,
            strategy.fingerprint,
            (("reaming", geometry_fingerprint),),
            (
                ("operation", ContentFingerprint.from_payload({
                    "revision": operation.revision.to_dict(),
                    "enabled": operation.enabled,
                })),
                ("setup", ContentFingerprint.from_payload({
                    "revision": setup.revision.to_dict(),
                })),
                ("wcs", ContentFingerprint.from_payload(setup.wcs.to_dict())),
            ),
            tool_fingerprint,
            machine_value.content_fingerprint,
        )
        return ReamingInputs(
            operation,
            setup,
            strategy,
            region,
            holes,
            assembly_value,
            tool_value,
            machine_value,
            snapshot.fingerprint,
        )

    def begin(self, inputs: ReamingInputs) -> tuple[ReamingInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(
            inputs,
            operation=replace(inputs.operation, artifact_state=state),
        ), token

    def generate(self, inputs: ReamingInputs) -> ToolpathArtifact:
        operation, strategy = inputs.operation, inputs.strategy
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_GENERATION_FAILED,
                "Reaming generation requires a current computation token",
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
            initial_z = strategy.clearance_height.value + (
                strategy.clearance_height.value - strategy.retract_height.value
            )
            builder.set_initial_pose(Pose(Point3(
                first.x, first.y, initial_z, strategy.unit
            ), axis))
            builder.set_initial_process_state(
                feed_mode=FeedMode.UNITS_PER_REVOLUTION
            )
            spindle_state = (
                SpindleState.CLOCKWISE
                if strategy.spindle_direction is SpindleDirection.CLOCKWISE
                else SpindleState.COUNTERCLOCKWISE
            )
            coolant_state = _coolant_state(strategy.coolant)
            marker_metadata = (
                ("clearance_height", format(
                    strategy.clearance_height.value, ".17g"
                )),
                ("coolant", strategy.coolant.value),
                ("dwell_seconds", format(strategy.dwell_seconds, ".17g")),
                ("feed_per_revolution", format(
                    strategy.feed_per_revolution.value, ".17g"
                )),
                ("feed_unit", strategy.feed_per_revolution.unit.value),
                ("final_depth", format(strategy.final_depth.value, ".17g")),
                ("format", "hms_reaming_process_v1"),
                ("length_unit", strategy.unit.value),
                ("metadata_version", "1"),
                ("nominal_diameter", format(
                    strategy.nominal_diameter.value, ".17g"
                )),
                ("pre_hole_diameter", format(
                    strategy.pre_hole_diameter.value, ".17g"
                )),
                ("retract_policy", strategy.retract_policy.value),
                ("retract_height", format(
                    strategy.retract_height.value, ".17g"
                )),
                ("rpm", format(strategy.spindle_speed.value, ".17g")),
                ("spindle_direction", strategy.spindle_direction.value),
                ("stock_per_side", format(
                    strategy.stock_per_side.value, ".17g"
                )),
                ("strategy_key", "reaming_v1"),
                ("strategy_version", str(strategy.strategy_version)),
                ("top_z", format(strategy.top_z.value, ".17g")),
            )
            for hole_index, hole in enumerate(inputs.holes):
                clearance = Pose(Point3(
                    hole.position.x,
                    hole.position.y,
                    strategy.clearance_height.value,
                    strategy.unit,
                ), axis)
                _rapid_if_needed(
                    builder,
                    clearance,
                    inputs.machine.capabilities.maximum_rapid,
                    f"ream.hole.{hole_index}.rapid",
                )
                retract = Pose(Point3(
                    hole.position.x,
                    hole.position.y,
                    strategy.retract_height.value,
                    strategy.unit,
                ), axis)
                builder.rapid_to(
                    retract,
                    motion_class=MotionClass.LINK,
                    rapid_rate=inputs.machine.capabilities.maximum_rapid,
                    provenance=f"ream.hole.{hole_index}.approach",
                )
                builder.marker(
                    "ream.process_begin",
                    metadata=marker_metadata,
                    provenance=f"ream.hole.{hole_index}.process.begin",
                )
                builder.set_spindle(
                    spindle_state,
                    strategy.spindle_speed,
                    provenance=f"ream.hole.{hole_index}.spindle.begin",
                )
                if coolant_state is not CoolantState.OFF:
                    builder.set_coolant(
                        coolant_state,
                        provenance=f"ream.hole.{hole_index}.coolant.begin",
                    )
                target = Pose(Point3(
                    hole.position.x,
                    hole.position.y,
                    strategy.final_depth.value,
                    strategy.unit,
                ), axis)
                builder.linear_to(
                    target,
                    strategy.feed_per_revolution,
                    motion_class=MotionClass.CUTTING,
                    provenance=f"ream.hole.{hole_index}.descent",
                )
                if strategy.dwell_seconds > 0.0:
                    builder.dwell(
                        strategy.dwell_seconds,
                        provenance=f"ream.hole.{hole_index}.dwell",
                    )
                builder.linear_to(
                    retract,
                    strategy.feed_per_revolution,
                    motion_class=MotionClass.RETRACT,
                    provenance=f"ream.hole.{hole_index}.controlled_retract",
                )
                builder.marker(
                    "ream.hole_complete",
                    provenance=f"ream.hole.{hole_index}.complete",
                )
                builder.rapid_to(
                    clearance,
                    rapid_rate=inputs.machine.capabilities.maximum_rapid,
                    provenance=f"ream.hole.{hole_index}.final_retract",
                )
                if coolant_state is not CoolantState.OFF:
                    builder.set_coolant(
                        CoolantState.OFF,
                        provenance=f"ream.hole.{hole_index}.coolant.end",
                    )
                builder.set_spindle(
                    SpindleState.OFF,
                    provenance=f"ream.hole.{hole_index}.spindle.end",
                )
                builder.marker(
                    "ream.process_end",
                    metadata=marker_metadata,
                    provenance=f"ream.hole.{hole_index}.process.end",
                )
            return builder.finalize()
        except ReamingGenerationError:
            builder.abort()
            raise
        except Exception as error:
            builder.abort()
            raise ReamingGenerationError(
                DiagnosticCode.REAM_GENERATION_FAILED,
                str(error) or "Reaming generation failed",
            ) from error

    @staticmethod
    def _validate_operation_geometry(
        operation: Operation,
        setup: Setup,
        strategy: ReamingStrategy,
    ) -> None:
        expected = _persistent_references(strategy)
        supplied = tuple(
            value for value in operation.geometry_inputs
            if value.role is GeometryInputRole.DRIVE_GEOMETRY
        )
        if len(supplied) != len(operation.geometry_inputs) or len(supplied) != len(expected):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_GEOMETRY_MISSING,
                "Operation persistent hole references do not match reaming geometry",
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
                raise ReamingGenerationError(
                    DiagnosticCode.REAM_SOURCE_MISMATCH,
                    "Reaming reference is missing, mismatched, or outside Setup scope",
                )
            remaining.pop(match_index)

    @staticmethod
    def _validate_resolved_geometry(
        resolved: ResolvedDrillingGeometry | None,
        strategy: ReamingStrategy,
    ) -> DrillingRegion:
        if resolved is None:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_GEOMETRY_MISSING,
                "Reaming geometry has not been resolved",
            )
        if not isinstance(resolved, ResolvedDrillingGeometry):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_INVALID_PARAMETERS,
                "Reaming resolver returned an invalid result",
            )
        if resolved.status is not GeometryResolutionStatus.RESOLVED:
            code = {
                GeometryResolutionStatus.MISSING: DiagnosticCode.REAM_GEOMETRY_MISSING,
                GeometryResolutionStatus.STALE: DiagnosticCode.REAM_GEOMETRY_STALE,
                GeometryResolutionStatus.TOPOLOGY_CHANGED: DiagnosticCode.REAM_GEOMETRY_STALE,
                GeometryResolutionStatus.AMBIGUOUS: DiagnosticCode.REAM_GEOMETRY_AMBIGUOUS,
                GeometryResolutionStatus.SOURCE_MISMATCH: DiagnosticCode.REAM_SOURCE_MISMATCH,
            }.get(resolved.status, DiagnosticCode.REAM_INVALID_PARAMETERS)
            message = (
                resolved.diagnostics[0].message
                if resolved.diagnostics
                else "Reaming geometry could not be resolved"
            )
            raise ReamingGenerationError(code, message)
        region = resolved.region
        assert region is not None
        if region.geometry_input != strategy.geometry:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_SOURCE_MISMATCH,
                "Resolved reaming geometry does not match the operation input",
            )
        if region.depth != strategy.depth:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_DEPTH_INVALID,
                "Resolved reaming depth does not match the strategy",
            )
        return region

    @staticmethod
    def _holes_in_setup(
        region: DrillingRegion,
        setup: Setup,
        strategy: ReamingStrategy,
    ) -> tuple[ReamingHole, ...]:
        def setup_point(value: Point3) -> Point3:
            delta = Vector3(
                value.x - setup.wcs.origin.x,
                value.y - setup.wcs.origin.y,
                value.z - setup.wcs.origin.z,
            )
            return Point3(
                delta.dot(setup.wcs.x_axis),
                delta.dot(setup.wcs.y_axis),
                delta.dot(setup.wcs.z_axis),
                value.unit,
            )

        holes: list[ReamingHole] = []
        for location in region.pattern.locations:
            position = setup_point(location.position)
            axis = Vector3(
                location.axis.dot(setup.wcs.x_axis),
                location.axis.dot(setup.wcs.y_axis),
                location.axis.dot(setup.wcs.z_axis),
            )
            if axis.dot(Vector3(0.0, 0.0, 1.0)) < 1.0 - strategy.tolerance.value:
                raise ReamingGenerationError(
                    DiagnosticCode.REAM_INVALID_PARAMETERS,
                    "Reaming v1 requires hole axes aligned with Setup +Z",
                )
            if abs(position.z - strategy.top_z.value) > strategy.tolerance.value:
                raise ReamingGenerationError(
                    DiagnosticCode.REAM_DEPTH_INVALID,
                    "Hole plane must match reaming top Z in Setup WCS",
                )
            if (
                location.source_kind is HoleSourceKind.CIRCULAR_EDGE
                and location.diameter is not None
                and abs(
                    location.diameter.value - strategy.nominal_diameter.value
                ) > strategy.tolerance.value
            ):
                raise ReamingGenerationError(
                    DiagnosticCode.REAM_DIAMETER_MISMATCH,
                    "Circular EDGE diameter does not match finished nominal diameter",
                )
            holes.append(ReamingHole(
                position,
                axis,
                strategy.cutting_depth,
                location.diameter,
                location.source_kind,
            ))
        return tuple(holes)

    @staticmethod
    def _validate_tool(
        operation: Operation,
        strategy: ReamingStrategy,
        holes: tuple[ReamingHole, ...],
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
    ) -> tuple[ToolAssembly, ToolDefinition]:
        del holes
        status = operation.tool_assembly.assess(assembly)
        if status is ToolReferenceStatus.MISSING:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_TOOL_MISSING,
                "Reaming Tool Assembly is missing",
            )
        if status is ToolReferenceStatus.STALE:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_TOOL_STALE,
                "Reaming Tool Assembly is stale",
            )
        if status is not ToolReferenceStatus.VALID:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_UNSUPPORTED_TOOL,
                "Reaming Tool Assembly has an incompatible unit",
            )
        assert assembly is not None
        if tool is None or tool.tool_id != assembly.tool_id:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_TOOL_MISSING,
                "Reaming Tool Definition is missing",
            )
        if (
            tool.revision != assembly.expected_tool_revision
            or tool.content_fingerprint != assembly.expected_tool_fingerprint
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_TOOL_STALE,
                "Reaming Tool Definition does not match the assembly snapshot",
            )
        if (
            tool.family is not ToolFamily.REAMER
            or not isinstance(tool.cutting_geometry, CylindricalGeometry)
            or tool.unit is not strategy.unit
            or assembly.unit is not strategy.unit
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_UNSUPPORTED_TOOL,
                "Reaming requires a compatible REAMER tool",
            )
        geometry = tool.cutting_geometry
        tolerance = strategy.tolerance.value
        if abs(geometry.diameter.value - strategy.nominal_diameter.value) > tolerance:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_DIAMETER_MISMATCH,
                "Reamer diameter does not match finished nominal diameter",
            )
        required_depth = strategy.cutting_depth.value
        if (
            geometry.flute_length.value + tolerance < required_depth
            or tool.usable_length.value + tolerance < required_depth
            or assembly.stickout.value + tolerance < required_depth
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_UNSUPPORTED_TOOL,
                "Reamer cutting length, usable length, or stickout is insufficient",
            )
        required_tool_coolant = {
            ReamingCoolantMode.FLOOD: ToolCoolantCapability.FLOOD,
            ReamingCoolantMode.MIST: ToolCoolantCapability.MIST,
            ReamingCoolantMode.THROUGH_TOOL: ToolCoolantCapability.THROUGH_TOOL,
        }.get(strategy.coolant)
        if (
            required_tool_coolant is not None
            and required_tool_coolant not in tool.coolant_capabilities
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_UNSUPPORTED_TOOL,
                "Reamer does not support the selected coolant mode",
            )
        return assembly, tool

    @staticmethod
    def _validate_machine(
        operation: Operation,
        strategy: ReamingStrategy,
        tool: ToolDefinition,
        machine: MachineDefinition | None,
    ) -> MachineDefinition:
        del tool
        requirement = operation.machine_requirement
        if requirement is None or machine is None:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_MACHINE_INCOMPATIBLE,
                "Reaming requires a selected milling machine",
            )
        if (
            machine.machine_id != requirement.machine_id
            or machine.revision != requirement.expected_revision
            or machine.content_fingerprint != requirement.expected_fingerprint
            or machine.unit is not requirement.unit
            or machine.unit is not strategy.unit
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_MACHINE_INCOMPATIBLE,
                "Reaming machine snapshot or unit is incompatible",
            )
        if (
            machine.kind not in {MachineKind.MILL, MachineKind.MILL_TURN}
            or OperationCapability.DRILLING not in requirement.required_capabilities
            or OperationCapability.DRILLING not in machine.capabilities.operations
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_MACHINE_INCOMPATIBLE,
                "Machine does not declare the drilling operation capability",
            )
        speed = strategy.spindle_speed.value
        if not any(
            spindle.minimum_speed.value <= speed <= spindle.maximum_speed.value
            and strategy.spindle_direction in spindle.directions
            for spindle in machine.spindles
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_MACHINE_INCOMPATIBLE,
                "Machine spindle range or direction is incompatible",
            )
        derived_feed = strategy.feed_per_minute.to(
            machine.capabilities.maximum_feed.unit
        )
        if derived_feed.value > machine.capabilities.maximum_feed.value:
            raise ReamingGenerationError(
                DiagnosticCode.REAM_MACHINE_INCOMPATIBLE,
                "Derived reaming feed exceeds the machine maximum feed",
            )
        required_machine_coolant = {
            ReamingCoolantMode.FLOOD: MachineCoolantCapability.FLOOD,
            ReamingCoolantMode.MIST: MachineCoolantCapability.MIST,
            ReamingCoolantMode.THROUGH_TOOL: (
                MachineCoolantCapability.THROUGH_SPINDLE
            ),
        }.get(strategy.coolant)
        if (
            required_machine_coolant is not None
            and required_machine_coolant not in machine.capabilities.coolant
        ):
            raise ReamingGenerationError(
                DiagnosticCode.REAM_MACHINE_INCOMPATIBLE,
                "Machine does not support the selected coolant mode",
            )
        return machine


def _persistent_references(strategy: ReamingStrategy) -> tuple[GeometryReference, ...]:
    source = strategy.geometry.source
    if isinstance(source, HoleReference):
        return (source.reference,)
    assert isinstance(source, HolePattern)
    return tuple(
        location.reference.reference
        for location in source.locations
        if location.reference is not None
    )


def _coolant_state(mode: ReamingCoolantMode) -> CoolantState:
    return {
        ReamingCoolantMode.OFF: CoolantState.OFF,
        ReamingCoolantMode.FLOOD: CoolantState.FLOOD,
        ReamingCoolantMode.MIST: CoolantState.MIST,
        ReamingCoolantMode.THROUGH_TOOL: CoolantState.THROUGH_TOOL,
    }[mode]


def _rapid_if_needed(
    builder: ToolpathBuilder,
    end: Pose,
    rapid_rate,
    provenance: str,
) -> None:
    current = builder.current_pose
    assert current is not None
    distance = math.dist(
        (current.position.x, current.position.y, current.position.z),
        (end.position.x, end.position.y, end.position.z),
    )
    if distance > 1.0e-8:
        builder.rapid_to(end, rapid_rate=rapid_rate, provenance=provenance)

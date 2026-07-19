"""Deterministic controller-neutral tapping strategy core."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ComputationToken,
    ContentFingerprint,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    DrillGeometryInput,
    DrillingRegion,
    FeedRate,
    FeedUnit,
    GeometryInputRole,
    GeometryReference,
    GeometryResolutionStatus,
    HolePattern,
    HoleReference,
    Length,
    LengthUnit,
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
    SpindleDirection,
    TapGeometry,
    TappingHand,
    TappingMode,
    TappingStrategy,
    TappingSynchronizationPolicy,
    TappingValidationError,
    ToolAssembly,
    ToolDefinition,
    ToolFamily,
    ToolHand,
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

_ARTIFACT_NAMESPACE = UUID("2699e718-15f1-4ec4-9353-4443db805adc")
_MAX_EVENTS_ESTIMATE = 100_000


class TappingGenerationError(ValueError):
    """Tapping validation or generation failed with a stable diagnostic."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))


@dataclass(frozen=True, slots=True)
class TappingHole:
    """One normalized tapping location in Setup WCS."""

    position: Point3
    axis: Vector3
    depth: Length
    diameter: Length | None


@dataclass(frozen=True, slots=True)
class TappingInputs:
    operation: Operation
    setup: Setup
    strategy: TappingStrategy
    region: DrillingRegion
    holes: tuple[TappingHole, ...]
    assembly: ToolAssembly
    tool: ToolDefinition
    machine: MachineDefinition
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class TappingComputeResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()


class TappingGenerator:
    """Validate inputs and generate synchronized semantic tapping Toolpath IR."""

    def resolve_inputs(
        self,
        operation: Operation,
        setup: Setup,
        *,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
        machine: MachineDefinition | None,
        resolved_geometry: ResolvedDrillingGeometry | None,
    ) -> TappingInputs:
        try:
            strategy = TappingStrategy.from_operation_parameters(operation.parameters)
        except TappingValidationError as error:
            raise TappingGenerationError(error.code, str(error)) from error
        if (
            operation.family is not OperationFamily.DRILLING
            or operation.setup_id != setup.setup_id
            or setup.kind not in {SetupKind.MILL, SetupKind.MILL_TURN}
            or not operation.enabled
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping requires the matching enabled milling Setup",
            )
        if strategy.unit is not setup.wcs.origin.unit:
            raise TappingGenerationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping strategy and Setup WCS units do not match",
            )
        self._validate_operation_geometry(operation, setup, strategy)
        region = self._validate_resolved_geometry(resolved_geometry, strategy)
        holes = self._holes_in_setup(region, setup, strategy)
        assembly_value, tool_value = self._validate_tool(
            operation, strategy, holes, assembly, tool
        )
        machine_value = self._validate_machine(operation, strategy, machine)
        event_estimate = len(holes) * 12 + 6
        if event_estimate > _MAX_EVENTS_ESTIMATE:
            raise TappingGenerationError(
                DiagnosticCode.TAP_GENERATION_FAILED,
                "Tapping operation exceeds the safe event limit",
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
                }
                for hole in holes
            ],
        })
        snapshot = OperationInputSnapshot(
            operation.strategy_key,
            operation.strategy_version,
            strategy.fingerprint,
            (("tapping", geometry_fingerprint),),
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
        return TappingInputs(
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

    def begin(self, inputs: TappingInputs) -> tuple[TappingInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(
            inputs,
            operation=replace(inputs.operation, artifact_state=state),
        ), token

    def generate(self, inputs: TappingInputs) -> ToolpathArtifact:
        operation, strategy = inputs.operation, inputs.strategy
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise TappingGenerationError(
                DiagnosticCode.TAP_GENERATION_FAILED,
                "Tapping generation requires a current computation token",
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
                first.x,
                first.y,
                strategy.clearance_height.value
                + (strategy.clearance_height.value - strategy.retract_height.value),
                strategy.unit,
            ), axis))
            builder.set_initial_process_state(
                feed_mode=FeedMode.UNITS_PER_REVOLUTION
            )
            feed_per_revolution = FeedRate(
                strategy.pitch.value,
                FeedUnit.MM_PER_REVOLUTION
                if strategy.unit is LengthUnit.MM
                else FeedUnit.INCH_PER_REVOLUTION,
            )
            cutting_direction, retract_direction = _spindle_semantics(strategy.hand)
            sync_metadata = (
                ("format", "hms_tapping_sync_v1"),
                ("pitch", format(strategy.pitch.value, ".17g")),
                ("pitch_unit", strategy.unit.value),
                ("policy", strategy.synchronization_policy.value),
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
                    f"tap.hole.{hole_index}.rapid",
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
                    provenance=f"tap.hole.{hole_index}.approach",
                )
                builder.marker(
                    "tap.synchronization_begin",
                    metadata=sync_metadata,
                    provenance=f"tap.hole.{hole_index}.synchronization.begin",
                )
                builder.set_spindle(
                    cutting_direction,
                    strategy.spindle_speed,
                    provenance=f"tap.hole.{hole_index}.spindle.cutting",
                )
                target = Pose(Point3(
                    hole.position.x,
                    hole.position.y,
                    strategy.final_depth.value,
                    strategy.unit,
                ), axis)
                builder.linear_to(
                    target,
                    feed_per_revolution,
                    motion_class=MotionClass.CUTTING,
                    provenance=f"tap.hole.{hole_index}.descent",
                )
                if strategy.dwell_seconds > 0.0:
                    builder.dwell(
                        strategy.dwell_seconds,
                        provenance=f"tap.hole.{hole_index}.dwell",
                    )
                builder.set_spindle(
                    retract_direction,
                    strategy.spindle_speed,
                    provenance=f"tap.hole.{hole_index}.spindle.reversal",
                )
                builder.linear_to(
                    retract,
                    feed_per_revolution,
                    motion_class=MotionClass.RETRACT,
                    provenance=f"tap.hole.{hole_index}.synchronized_retract",
                )
                builder.marker(
                    "tap.hole_complete",
                    provenance=f"tap.hole.{hole_index}.complete",
                )
                builder.marker(
                    "tap.synchronization_end",
                    metadata=sync_metadata,
                    provenance=f"tap.hole.{hole_index}.synchronization.end",
                )
                builder.rapid_to(
                    clearance,
                    rapid_rate=inputs.machine.capabilities.maximum_rapid,
                    provenance=f"tap.hole.{hole_index}.final_retract",
                )
            builder.set_spindle(SpindleState.OFF, provenance="tap.spindle.off")
            return builder.finalize()
        except TappingGenerationError:
            builder.abort()
            raise
        except Exception as error:
            builder.abort()
            raise TappingGenerationError(
                DiagnosticCode.TAP_GENERATION_FAILED,
                str(error) or "Tapping generation failed",
            ) from error

    @staticmethod
    def _validate_operation_geometry(
        operation: Operation,
        setup: Setup,
        strategy: TappingStrategy,
    ) -> None:
        expected = _persistent_references(strategy)
        supplied = tuple(
            value for value in operation.geometry_inputs
            if value.role is GeometryInputRole.DRIVE_GEOMETRY
        )
        if len(supplied) != len(operation.geometry_inputs) or len(supplied) != len(expected):
            raise TappingGenerationError(
                DiagnosticCode.TAP_GEOMETRY_MISSING,
                "Operation persistent hole references do not match tapping geometry",
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
                raise TappingGenerationError(
                    DiagnosticCode.TAP_SOURCE_MISMATCH,
                    "Tapping reference is missing, mismatched, or outside Setup scope",
                )
            remaining.pop(match_index)

    @staticmethod
    def _validate_resolved_geometry(
        resolved: ResolvedDrillingGeometry | None,
        strategy: TappingStrategy,
    ) -> DrillingRegion:
        if resolved is None:
            raise TappingGenerationError(
                DiagnosticCode.TAP_GEOMETRY_MISSING,
                "Tapping geometry has not been resolved",
            )
        if not isinstance(resolved, ResolvedDrillingGeometry):
            raise TappingGenerationError(
                DiagnosticCode.TAP_INVALID_PARAMETERS,
                "Tapping resolver returned an invalid result",
            )
        if resolved.status is not GeometryResolutionStatus.RESOLVED:
            code = {
                GeometryResolutionStatus.MISSING: DiagnosticCode.TAP_GEOMETRY_MISSING,
                GeometryResolutionStatus.STALE: DiagnosticCode.TAP_GEOMETRY_STALE,
                GeometryResolutionStatus.AMBIGUOUS: DiagnosticCode.TAP_GEOMETRY_AMBIGUOUS,
                GeometryResolutionStatus.SOURCE_MISMATCH: DiagnosticCode.TAP_SOURCE_MISMATCH,
            }.get(resolved.status, DiagnosticCode.TAP_INVALID_PARAMETERS)
            message = (
                resolved.diagnostics[0].message
                if resolved.diagnostics
                else "Tapping geometry could not be resolved"
            )
            raise TappingGenerationError(code, message)
        region = resolved.region
        assert region is not None
        if region.geometry_input != strategy.geometry:
            raise TappingGenerationError(
                DiagnosticCode.TAP_SOURCE_MISMATCH,
                "Resolved tapping geometry does not match the operation input",
            )
        if region.depth != strategy.depth:
            raise TappingGenerationError(
                DiagnosticCode.TAP_DEPTH_INVALID,
                "Resolved tapping depth does not match the strategy",
            )
        return region

    @staticmethod
    def _holes_in_setup(
        region: DrillingRegion,
        setup: Setup,
        strategy: TappingStrategy,
    ) -> tuple[TappingHole, ...]:
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

        holes: list[TappingHole] = []
        for location in region.pattern.locations:
            position = setup_point(location.position)
            axis = Vector3(
                location.axis.dot(setup.wcs.x_axis),
                location.axis.dot(setup.wcs.y_axis),
                location.axis.dot(setup.wcs.z_axis),
            )
            if axis.dot(Vector3(0.0, 0.0, 1.0)) < 1.0 - strategy.tolerance.value:
                raise TappingGenerationError(
                    DiagnosticCode.TAP_INVALID_PARAMETERS,
                    "Tapping v1 requires hole axes aligned with Setup +Z",
                )
            if abs(position.z - strategy.top_z.value) > strategy.tolerance.value:
                raise TappingGenerationError(
                    DiagnosticCode.TAP_DEPTH_INVALID,
                    "Hole plane must match tapping top Z in Setup WCS",
                )
            holes.append(TappingHole(
                position,
                axis,
                strategy.depth.depth,
                location.diameter,
            ))
        return tuple(holes)

    @staticmethod
    def _validate_tool(
        operation: Operation,
        strategy: TappingStrategy,
        holes: tuple[TappingHole, ...],
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
    ) -> tuple[ToolAssembly, ToolDefinition]:
        status = operation.tool_assembly.assess(assembly)
        if status is ToolReferenceStatus.MISSING:
            raise TappingGenerationError(
                DiagnosticCode.TAP_TOOL_MISSING,
                "Tapping Tool Assembly is missing",
            )
        if status is ToolReferenceStatus.STALE:
            raise TappingGenerationError(
                DiagnosticCode.TAP_TOOL_STALE,
                "Tapping Tool Assembly is stale",
            )
        if status is not ToolReferenceStatus.VALID:
            raise TappingGenerationError(
                DiagnosticCode.TAP_UNSUPPORTED_TOOL,
                "Tapping Tool Assembly has an incompatible unit",
            )
        assert assembly is not None
        if tool is None or tool.tool_id != assembly.tool_id:
            raise TappingGenerationError(
                DiagnosticCode.TAP_TOOL_MISSING,
                "Tapping Tool Definition is missing",
            )
        if (
            tool.revision != assembly.expected_tool_revision
            or tool.content_fingerprint != assembly.expected_tool_fingerprint
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_TOOL_STALE,
                "Tapping Tool Definition does not match the assembly snapshot",
            )
        if (
            tool.family is not ToolFamily.TAP
            or not isinstance(tool.cutting_geometry, TapGeometry)
            or tool.unit is not strategy.unit
            or assembly.unit is not strategy.unit
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_UNSUPPORTED_TOOL,
                "Tapping requires a compatible TAP tool",
            )
        geometry = tool.cutting_geometry
        tolerance = strategy.tolerance.value
        if abs(geometry.nominal_diameter.value - strategy.nominal_diameter.value) > tolerance:
            raise TappingGenerationError(
                DiagnosticCode.TAP_DIAMETER_MISMATCH,
                "Tap nominal diameter does not match the strategy",
            )
        if abs(geometry.pitch.value - strategy.pitch.value) > tolerance:
            raise TappingGenerationError(
                DiagnosticCode.TAP_PITCH_MISMATCH,
                "Tap pitch does not match the strategy",
            )
        expected_hand = (
            ToolHand.RIGHT
            if strategy.hand is TappingHand.RIGHT_HAND_TAP
            else ToolHand.LEFT
        )
        if geometry.hand is not expected_hand:
            raise TappingGenerationError(
                DiagnosticCode.TAP_HAND_MISMATCH,
                "Tap hand does not match the strategy",
            )
        required_depth = strategy.depth.depth.value
        if (
            geometry.threaded_length.value + tolerance < required_depth
            or tool.usable_length.value + tolerance < required_depth
            or assembly.stickout.value + tolerance < required_depth
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_UNSUPPORTED_TOOL,
                "Tap threaded length, usable length, or stickout is insufficient",
            )
        for hole in holes:
            if (
                hole.diameter is not None
                and abs(hole.diameter.value - strategy.nominal_diameter.value) > tolerance
            ):
                raise TappingGenerationError(
                    DiagnosticCode.TAP_DIAMETER_MISMATCH,
                    "Known hole diameter does not match the tapping strategy",
                )
        return assembly, tool

    @staticmethod
    def _validate_machine(
        operation: Operation,
        strategy: TappingStrategy,
        machine: MachineDefinition | None,
    ) -> MachineDefinition:
        requirement = operation.machine_requirement
        if requirement is None or machine is None:
            raise TappingGenerationError(
                DiagnosticCode.TAP_MACHINE_INCOMPATIBLE,
                "Tapping requires a selected milling machine",
            )
        if (
            machine.machine_id != requirement.machine_id
            or machine.revision != requirement.expected_revision
            or machine.content_fingerprint != requirement.expected_fingerprint
            or machine.unit is not requirement.unit
            or machine.unit is not strategy.unit
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_MACHINE_INCOMPATIBLE,
                "Tapping machine snapshot or unit is incompatible",
            )
        if (
            machine.kind not in {MachineKind.MILL, MachineKind.MILL_TURN}
            or not machine.capabilities.tapping
            or OperationCapability.TAPPING not in requirement.required_capabilities
            or OperationCapability.TAPPING not in machine.capabilities.operations
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_MACHINE_INCOMPATIBLE,
                "Machine does not declare the tapping operation capability",
            )
        required_mode = (
            TappingMode.RIGID
            if strategy.synchronization_policy is TappingSynchronizationPolicy.RIGID
            else TappingMode.FLOATING
        )
        if required_mode not in machine.capabilities.tapping_modes:
            raise TappingGenerationError(
                DiagnosticCode.TAP_SYNC_UNSUPPORTED,
                "Machine does not support the selected tapping synchronization policy",
            )
        required_directions = {
            SpindleDirection.CLOCKWISE,
            SpindleDirection.COUNTERCLOCKWISE,
        }
        speed = strategy.spindle_speed.value
        speed_candidates = tuple(
            spindle for spindle in machine.spindles
            if spindle.minimum_speed.value <= speed <= spindle.maximum_speed.value
        )
        if not speed_candidates or not any(
            required_directions.issubset(spindle.directions)
            for spindle in speed_candidates
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_MACHINE_INCOMPATIBLE,
                "Machine spindle range or direction capability is incompatible",
            )
        if not any(
            spindle.synchronized_feed
            and required_directions.issubset(spindle.directions)
            for spindle in speed_candidates
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_SYNC_UNSUPPORTED,
                "Machine spindle does not support synchronized feed",
            )
        feed_unit = (
            FeedUnit.MM_PER_MINUTE
            if strategy.unit is LengthUnit.MM
            else FeedUnit.INCH_PER_MINUTE
        )
        derived_feed = FeedRate(strategy.pitch.value * speed, feed_unit)
        if (
            derived_feed.to(machine.capabilities.maximum_feed.unit).value
            > machine.capabilities.maximum_feed.value
        ):
            raise TappingGenerationError(
                DiagnosticCode.TAP_MACHINE_INCOMPATIBLE,
                "Derived tapping feed exceeds the machine maximum feed",
            )
        return machine


def _persistent_references(strategy: TappingStrategy) -> tuple[GeometryReference, ...]:
    source = strategy.geometry.source
    if isinstance(source, HoleReference):
        return (source.reference,)
    assert isinstance(source, HolePattern)
    return tuple(
        location.reference.reference
        for location in source.locations
        if location.reference is not None
    )


def _spindle_semantics(
    hand: TappingHand,
) -> tuple[SpindleState, SpindleState]:
    if hand is TappingHand.RIGHT_HAND_TAP:
        return SpindleState.CLOCKWISE, SpindleState.COUNTERCLOCKWISE
    return SpindleState.COUNTERCLOCKWISE, SpindleState.CLOCKWISE


def _rapid_if_needed(
    builder: ToolpathBuilder,
    end: Pose,
    rapid_rate: FeedRate,
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

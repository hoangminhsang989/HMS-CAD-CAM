"""Deterministic controller-neutral single-point axial boring core."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from uuid import UUID, uuid5

from hms_cadcam.cam.automatic_boring import (
    BORING_AUTOMATIC_POLICY_KEY,
    BoringAutomaticContext,
    resolve_boring_automatic_contract,
    validate_boring_automatic_contract,
)
from hms_cadcam.cam.automatic_hole_geometry import HoleGeometryContext
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
)

from hms_cadcam.cam.domain import (
    ArtifactStatus,
    BoringBarGeometry,
    BoringCoolantMode,
    BoringStrategy,
    BoringValidationError,
    ComputationToken,
    ContentFingerprint,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    DrillGeometryInput,
    DrillingRegion,
    GeometryInputRole,
    GeometryReference,
    GeometryResolutionStatus,
    HolderDefinition,
    HolePattern,
    HoleReference,
    HoleSourceKind,
    Length,
    MachineCoolantCapability,
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
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolAssemblyStatus,
    ToolCoolantCapability,
    ToolDefinition,
    ToolFamily,
    ToolHand,
    ToolReferenceStatus,
    ToolpathArtifactId,
    ValidationDiagnostic,
    Vector3,
    assess_tool_assembly,
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

_ARTIFACT_NAMESPACE = UUID("10730343-20f7-40c9-a70d-c143cdefb84b")
_MAX_EVENTS_ESTIMATE = 100_000


class BoringGenerationError(ValueError):
    """Boring validation or generation failed with a stable diagnostic."""

    def __init__(self, code: DiagnosticCode, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def diagnostic(self) -> ValidationDiagnostic:
        return ValidationDiagnostic(DiagnosticSeverity.ERROR, self.code, str(self))


@dataclass(frozen=True, slots=True)
class BoringHole:
    """One normalized boring location in Setup WCS."""

    position: Point3
    axis: Vector3
    depth: Length
    diameter: Length | None
    source_kind: HoleSourceKind


@dataclass(frozen=True, slots=True)
class BoringInputs:
    operation: Operation
    setup: Setup
    strategy: BoringStrategy
    region: DrillingRegion
    holes: tuple[BoringHole, ...]
    assembly: ToolAssembly
    tool: ToolDefinition
    holder: HolderDefinition
    machine: MachineDefinition
    input_fingerprint: DependencyFingerprint


@dataclass(frozen=True, slots=True)
class BoringComputeResult:
    operation: Operation
    artifact: ToolpathArtifact | None
    accepted: bool
    diagnostics: tuple[ValidationDiagnostic, ...] = ()


class BoringGenerator:
    """Validate inputs and generate controlled axial boring Toolpath IR."""

    def resolve_inputs(
        self,
        operation: Operation,
        setup: Setup,
        *,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
        holder: HolderDefinition | None,
        machine: MachineDefinition | None,
        resolved_geometry: ResolvedDrillingGeometry | None,
    ) -> BoringInputs:
        try:
            strategy = BoringStrategy.from_operation_parameters(operation.parameters)
        except BoringValidationError as error:
            raise BoringGenerationError(error.code, str(error)) from error
        if (
            operation.family is not OperationFamily.DRILLING
            or operation.setup_id != setup.setup_id
            or setup.kind not in {SetupKind.MILL, SetupKind.MILL_TURN}
            or not operation.enabled
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring requires the matching enabled milling Setup",
            )
        if strategy.unit is not setup.wcs.origin.unit:
            raise BoringGenerationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring strategy and Setup WCS units do not match",
            )
        self._validate_operation_geometry(operation, setup, strategy)
        region = self._validate_resolved_geometry(resolved_geometry, strategy)
        holes = self._holes_in_setup(region, setup, strategy)
        assembly_value, tool_value, holder_value = self._validate_tool(
            operation, strategy, assembly, tool, holder
        )
        self._validate_automatic_setup(
            operation,
            strategy,
            region,
            assembly_value,
            tool_value,
            holder_value,
        )
        machine_value = self._validate_machine(operation, strategy, machine)
        if len(holes) * 14 + 6 > _MAX_EVENTS_ESTIMATE:
            raise BoringGenerationError(
                DiagnosticCode.BORE_GENERATION_FAILED,
                "Boring operation exceeds the safe event limit",
            )
        tool_context_fingerprint = ContentFingerprint.from_payload({
            "assembly": assembly_value.to_dict(),
            "tool": tool_value.to_dict(),
            "holder": holder_value.to_dict(),
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
            (("boring", geometry_fingerprint),),
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
            tool_context_fingerprint,
            machine_value.content_fingerprint,
        )
        return BoringInputs(
            operation,
            setup,
            strategy,
            region,
            holes,
            assembly_value,
            tool_value,
            holder_value,
            machine_value,
            snapshot.fingerprint,
        )

    def begin(self, inputs: BoringInputs) -> tuple[BoringInputs, ComputationToken]:
        state, token = inputs.operation.artifact_state.begin(inputs.input_fingerprint)
        return replace(
            inputs,
            operation=replace(inputs.operation, artifact_state=state),
        ), token

    def generate(self, inputs: BoringInputs) -> ToolpathArtifact:
        operation, strategy = inputs.operation, inputs.strategy
        token = operation.artifact_state.token
        if operation.artifact_state.status is not ArtifactStatus.COMPUTING or token is None:
            raise BoringGenerationError(
                DiagnosticCode.BORE_GENERATION_FAILED,
                "Boring generation requires a current computation token",
            )
        artifact_uuid = uuid5(
            _ARTIFACT_NAMESPACE,
            f"{operation.operation_id}|{inputs.input_fingerprint.digest}|{token.generation}",
        )
        tool_context_fingerprint = ContentFingerprint.from_payload({
            "assembly": inputs.assembly.to_dict(),
            "tool": inputs.tool.to_dict(),
            "holder": inputs.holder.to_dict(),
        })
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
            tool_assembly_fingerprint=tool_context_fingerprint,
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
            boring_geometry = inputs.tool.cutting_geometry
            assert isinstance(boring_geometry, BoringBarGeometry)
            boring_geometry_payload = boring_geometry.to_dict()
            marker_metadata = (
                ("assembly_fingerprint", inputs.assembly.content_fingerprint.digest),
                ("clearance_height", format(
                    strategy.clearance_height.value, ".17g"
                )),
                ("coolant", strategy.coolant.value),
                ("dwell_seconds", format(strategy.dwell_seconds, ".17g")),
                ("expected_assembly_fingerprint",
                 operation.tool_assembly.expected_fingerprint.digest),
                ("expected_holder_fingerprint",
                 inputs.assembly.expected_holder_fingerprint.digest),
                ("expected_tool_fingerprint",
                 inputs.assembly.expected_tool_fingerprint.digest),
                ("feed_per_revolution", format(
                    strategy.feed_per_revolution.value, ".17g"
                )),
                ("feed_unit", strategy.feed_per_revolution.unit.value),
                ("final_depth", format(strategy.final_depth.value, ".17g")),
                ("finished_bore_diameter", format(
                    strategy.finished_bore_diameter.value, ".17g"
                )),
                ("format", "hms_boring_process_v1"),
                ("holder_fingerprint", inputs.holder.content_fingerprint.digest),
                ("holder_id", str(inputs.holder.holder_id)),
                ("hole_count", str(len(inputs.holes))),
                ("length_unit", strategy.unit.value),
                ("metadata_version", "1"),
                ("operation_family", operation.family.value),
                ("pre_bore_diameter", format(
                    strategy.pre_bore_diameter.value, ".17g"
                )),
                ("radial_stock", format(strategy.radial_stock.value, ".17g")),
                ("retract_policy", strategy.retract_policy.value),
                ("retract_height", format(
                    strategy.retract_height.value, ".17g"
                )),
                ("rpm", format(strategy.spindle_rpm.value, ".17g")),
                ("spindle_direction", strategy.spindle_direction.value),
                ("strategy_key", "boring_v1"),
                ("strategy_version", str(strategy.strategy_version)),
                ("tool_context_fingerprint", tool_context_fingerprint.digest),
                ("tool_family", inputs.tool.family.value),
                ("tool_fingerprint", inputs.tool.content_fingerprint.digest),
                ("tool_geometry_kind", boring_geometry_payload["kind"]),
                ("tool_geometry_version", str(
                    boring_geometry_payload["geometry_version"]
                )),
                ("tool_id", str(inputs.tool.tool_id)),
                ("tool_assembly_id", str(inputs.assembly.assembly_id)),
                ("tool_maximum_bore_diameter", format(
                    boring_geometry.maximum_bore_diameter.value, ".17g"
                )),
                ("tool_minimum_bore_diameter", format(
                    boring_geometry.minimum_bore_diameter.value, ".17g"
                )),
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
                    f"bore.hole.{hole_index}.rapid",
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
                    provenance=f"bore.hole.{hole_index}.approach",
                )
                builder.marker(
                    "bore.process_begin",
                    metadata=marker_metadata,
                    provenance=f"bore.hole.{hole_index}.process.begin",
                )
                builder.set_spindle(
                    spindle_state,
                    strategy.spindle_rpm,
                    provenance=f"bore.hole.{hole_index}.spindle.begin",
                )
                if coolant_state is not CoolantState.OFF:
                    builder.set_coolant(
                        coolant_state,
                        provenance=f"bore.hole.{hole_index}.coolant.begin",
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
                    provenance=f"bore.hole.{hole_index}.descent",
                )
                if strategy.dwell_seconds > 0.0:
                    builder.dwell(
                        strategy.dwell_seconds,
                        provenance=f"bore.hole.{hole_index}.dwell",
                    )
                builder.linear_to(
                    retract,
                    strategy.feed_per_revolution,
                    motion_class=MotionClass.RETRACT,
                    provenance=f"bore.hole.{hole_index}.controlled_retract",
                )
                builder.marker(
                    "bore.hole_complete",
                    provenance=f"bore.hole.{hole_index}.complete",
                )
                builder.rapid_to(
                    clearance,
                    rapid_rate=inputs.machine.capabilities.maximum_rapid,
                    provenance=f"bore.hole.{hole_index}.final_retract",
                )
                if coolant_state is not CoolantState.OFF:
                    builder.set_coolant(
                        CoolantState.OFF,
                        provenance=f"bore.hole.{hole_index}.coolant.end",
                    )
                builder.set_spindle(
                    SpindleState.OFF,
                    provenance=f"bore.hole.{hole_index}.spindle.end",
                )
                builder.marker(
                    "bore.process_end",
                    metadata=marker_metadata,
                    provenance=f"bore.hole.{hole_index}.process.end",
                )
            return builder.finalize()
        except BoringGenerationError:
            builder.abort()
            raise
        except Exception as error:
            builder.abort()
            raise BoringGenerationError(
                DiagnosticCode.BORE_GENERATION_FAILED,
                str(error) or "Boring generation failed",
            ) from error

    @staticmethod
    def _validate_operation_geometry(
        operation: Operation,
        setup: Setup,
        strategy: BoringStrategy,
    ) -> None:
        expected = _persistent_references(strategy)
        supplied = tuple(
            value for value in operation.geometry_inputs
            if value.role is GeometryInputRole.DRIVE_GEOMETRY
        )
        if len(supplied) != len(operation.geometry_inputs) or len(supplied) != len(expected):
            raise BoringGenerationError(
                DiagnosticCode.BORE_GEOMETRY_MISSING,
                "Operation persistent hole references do not match boring geometry",
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
                raise BoringGenerationError(
                    DiagnosticCode.BORE_SOURCE_MISMATCH,
                    "Boring reference is missing, mismatched, or outside Setup scope",
                )
            remaining.pop(match_index)

    @staticmethod
    def _validate_resolved_geometry(
        resolved: ResolvedDrillingGeometry | None,
        strategy: BoringStrategy,
    ) -> DrillingRegion:
        if resolved is None:
            raise BoringGenerationError(
                DiagnosticCode.BORE_GEOMETRY_MISSING,
                "Boring geometry has not been resolved",
            )
        if not isinstance(resolved, ResolvedDrillingGeometry):
            raise BoringGenerationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Boring resolver returned an invalid result",
            )
        if resolved.status is not GeometryResolutionStatus.RESOLVED:
            code = {
                GeometryResolutionStatus.MISSING: DiagnosticCode.BORE_GEOMETRY_MISSING,
                GeometryResolutionStatus.STALE: DiagnosticCode.BORE_GEOMETRY_STALE,
                GeometryResolutionStatus.TOPOLOGY_CHANGED: DiagnosticCode.BORE_GEOMETRY_STALE,
                GeometryResolutionStatus.AMBIGUOUS: DiagnosticCode.BORE_GEOMETRY_AMBIGUOUS,
                GeometryResolutionStatus.SOURCE_MISMATCH: DiagnosticCode.BORE_SOURCE_MISMATCH,
            }.get(resolved.status, DiagnosticCode.BORE_INVALID_PARAMETERS)
            message = (
                resolved.diagnostics[0].message
                if resolved.diagnostics
                else "Boring geometry could not be resolved"
            )
            raise BoringGenerationError(code, message)
        region = resolved.region
        assert region is not None
        if region.geometry_input != strategy.geometry:
            raise BoringGenerationError(
                DiagnosticCode.BORE_SOURCE_MISMATCH,
                "Resolved boring geometry does not match the operation input",
            )
        if region.depth != strategy.depth:
            raise BoringGenerationError(
                DiagnosticCode.BORE_DEPTH_INVALID,
                "Resolved boring depth does not match the strategy",
            )
        return region

    @staticmethod
    def _holes_in_setup(
        region: DrillingRegion,
        setup: Setup,
        strategy: BoringStrategy,
    ) -> tuple[BoringHole, ...]:
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

        holes: list[BoringHole] = []
        for location in region.pattern.locations:
            position = setup_point(location.position)
            axis = Vector3(
                location.axis.dot(setup.wcs.x_axis),
                location.axis.dot(setup.wcs.y_axis),
                location.axis.dot(setup.wcs.z_axis),
            )
            if axis.dot(Vector3(0.0, 0.0, 1.0)) < 1.0 - strategy.tolerance.value:
                raise BoringGenerationError(
                    DiagnosticCode.BORE_INVALID_PARAMETERS,
                    "Boring v1 requires hole axes aligned with Setup +Z",
                )
            if abs(position.z - strategy.top_z.value) > strategy.tolerance.value:
                raise BoringGenerationError(
                    DiagnosticCode.BORE_DEPTH_INVALID,
                    "Hole plane must match boring top Z in Setup WCS",
                )
            if (
                location.source_kind is HoleSourceKind.CIRCULAR_EDGE
                and location.diameter is not None
                and abs(
                    location.diameter.value
                    - strategy.finished_bore_diameter.value
                ) > strategy.tolerance.value
            ):
                raise BoringGenerationError(
                    DiagnosticCode.BORE_DIAMETER_MISMATCH,
                    "Circular EDGE diameter does not match finished bore diameter",
                )
            holes.append(BoringHole(
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
        strategy: BoringStrategy,
        assembly: ToolAssembly | None,
        tool: ToolDefinition | None,
        holder: HolderDefinition | None,
    ) -> tuple[ToolAssembly, ToolDefinition, HolderDefinition]:
        reference_status = operation.tool_assembly.assess(assembly)
        if reference_status is ToolReferenceStatus.MISSING:
            raise BoringGenerationError(
                DiagnosticCode.BORE_TOOL_MISSING,
                "Boring Tool Assembly is missing",
            )
        if reference_status is ToolReferenceStatus.STALE:
            raise BoringGenerationError(
                DiagnosticCode.BORE_TOOL_STALE,
                "Boring Tool Assembly is stale",
            )
        if reference_status is not ToolReferenceStatus.VALID:
            raise BoringGenerationError(
                DiagnosticCode.BORE_UNSUPPORTED_TOOL,
                "Boring Tool Assembly has an incompatible unit",
            )
        assert assembly is not None
        if tool is None or tool.tool_id != assembly.tool_id:
            raise BoringGenerationError(
                DiagnosticCode.BORE_TOOL_MISSING,
                "Boring Tool Definition is missing",
            )
        if (
            assembly.holder_id is None
            or holder is None
            or holder.holder_id != assembly.holder_id
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_TOOL_MISSING,
                "Boring requires a current Holder Definition",
            )
        evidence = ToolAssemblyEvidence(
            tool_exists=True,
            tool_revision=tool.revision,
            tool_fingerprint=tool.content_fingerprint,
            tool_unit=tool.unit,
            holder_exists=True,
            holder_revision=holder.revision,
            holder_fingerprint=holder.content_fingerprint,
            holder_unit=holder.unit,
        )
        assembly_status = assess_tool_assembly(assembly, evidence)
        if assembly_status in {
            ToolAssemblyStatus.MISSING_TOOL,
            ToolAssemblyStatus.MISSING_HOLDER,
        }:
            raise BoringGenerationError(
                DiagnosticCode.BORE_TOOL_MISSING,
                "Boring tool or holder snapshot is missing",
            )
        if assembly_status in {
            ToolAssemblyStatus.TOOL_REVISION_MISMATCH,
            ToolAssemblyStatus.HOLDER_REVISION_MISMATCH,
        }:
            raise BoringGenerationError(
                DiagnosticCode.BORE_TOOL_STALE,
                "Boring tool or holder does not match the assembly snapshot",
            )
        if assembly_status is not ToolAssemblyStatus.VALID:
            raise BoringGenerationError(
                DiagnosticCode.BORE_UNSUPPORTED_TOOL,
                "Boring tool, holder, and assembly units are incompatible",
            )
        if (
            tool.family is not ToolFamily.BORING_BAR
            or not isinstance(tool.cutting_geometry, BoringBarGeometry)
            or tool.unit is not strategy.unit
            or assembly.unit is not strategy.unit
            or holder.unit is not strategy.unit
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_UNSUPPORTED_TOOL,
                "Boring requires a compatible BORING_BAR tool and holder",
            )
        geometry = tool.cutting_geometry
        tolerance = strategy.tolerance.value
        assert strategy.pre_bore_diameter is not None
        if (
            strategy.pre_bore_diameter.value
            < geometry.minimum_bore_diameter.value + tolerance
            or strategy.finished_bore_diameter.value
            > geometry.maximum_bore_diameter.value + tolerance
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_TOOL_ACCESS_INVALID,
                "Boring bar cannot enter the pre-bore or reach finished diameter",
            )
        expected_hand = (
            ToolHand.RIGHT
            if strategy.spindle_direction is SpindleDirection.CLOCKWISE
            else ToolHand.LEFT
        )
        if geometry.hand is not expected_hand:
            raise BoringGenerationError(
                DiagnosticCode.BORE_UNSUPPORTED_TOOL,
                "Boring bar hand does not match spindle direction",
            )
        required_depth = strategy.cutting_depth.value
        if (
            geometry.cutting_length.value + tolerance < required_depth
            or tool.usable_length.value + tolerance < required_depth
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_UNSUPPORTED_TOOL,
                "Boring cutting length or usable length is insufficient",
            )
        if (
            strategy.pre_bore_diameter.value
            - tool.shank.diameter.value
            <= tolerance
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_CLEARANCE_INVALID,
                "Boring shank lacks positive diametral pre-bore clearance",
            )
        if assembly.stickout.value - required_depth <= tolerance:
            raise BoringGenerationError(
                DiagnosticCode.BORE_CLEARANCE_INVALID,
                "Boring stickout margin does not keep the holder above the entrance plane",
            )
        required_tool_coolant = {
            BoringCoolantMode.FLOOD: ToolCoolantCapability.FLOOD,
            BoringCoolantMode.MIST: ToolCoolantCapability.MIST,
            BoringCoolantMode.THROUGH_TOOL: ToolCoolantCapability.THROUGH_TOOL,
        }.get(strategy.coolant)
        if (
            required_tool_coolant is not None
            and required_tool_coolant not in tool.coolant_capabilities
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_UNSUPPORTED_TOOL,
                "Boring bar does not support the selected coolant mode",
            )
        return assembly, tool, holder

    @staticmethod
    def _validate_automatic_setup(
        operation: Operation,
        strategy: BoringStrategy,
        region: DrillingRegion,
        assembly: ToolAssembly,
        tool: ToolDefinition,
        holder: HolderDefinition,
    ) -> None:
        """Recompute persisted Boring AUTO dependencies before emission."""
        raw = dict(operation.parameters.values).get(AUTOMATIC_PARAMETER_CONTRACT_KEY)
        if raw is None:
            return
        try:
            if not isinstance(raw, str):
                raise ValueError("invalid payload")
            stored = AutomaticParameterContract.from_json(raw)
            if stored.policy_key != BORING_AUTOMATIC_POLICY_KEY:
                raise ValueError("wrong policy")
            geometry = tool.cutting_geometry
            if not isinstance(geometry, BoringBarGeometry):
                raise ValueError("invalid Boring geometry")
            current = resolve_boring_automatic_contract(
                BoringAutomaticContext(
                    HoleGeometryContext(
                        strategy.unit,
                        region.pattern.locations,
                        strategy.geometry.source.fingerprint.digest,
                        True,
                        strategy.tolerance.value,
                    ),
                    tool.family,
                    tool.content_fingerprint.digest,
                    holder.content_fingerprint.digest,
                    geometry.minimum_bore_diameter.to(strategy.unit).value,
                    geometry.maximum_bore_diameter.to(strategy.unit).value,
                    geometry.axial_cutting_length.to(strategy.unit).value,
                    assembly.stickout.to(strategy.unit).value,
                    strategy.top_z.value,
                    strategy.final_depth.value,
                    strategy.clearance_height.value,
                    strategy.retract_height.value,
                    strategy.finished_bore_diameter.value,
                ),
                quality_profile=stored.quality_profile,
            )
            validate_boring_automatic_contract(stored, current)
        except (TypeError, ValueError) as error:
            raise BoringGenerationError(
                DiagnosticCode.BORE_INVALID_PARAMETERS,
                "Persisted Boring Auto Setup is stale or malformed",
            ) from error

    @staticmethod
    def _validate_machine(
        operation: Operation,
        strategy: BoringStrategy,
        machine: MachineDefinition | None,
    ) -> MachineDefinition:
        requirement = operation.machine_requirement
        if requirement is None or machine is None:
            raise BoringGenerationError(
                DiagnosticCode.BORE_MACHINE_INCOMPATIBLE,
                "Boring requires a selected milling machine",
            )
        if (
            machine.machine_id != requirement.machine_id
            or machine.revision != requirement.expected_revision
            or machine.content_fingerprint != requirement.expected_fingerprint
            or machine.unit is not requirement.unit
            or machine.unit is not strategy.unit
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_MACHINE_INCOMPATIBLE,
                "Boring machine snapshot or unit is incompatible",
            )
        if (
            machine.kind not in {MachineKind.MILL, MachineKind.MILL_TURN}
            or OperationCapability.DRILLING not in requirement.required_capabilities
            or OperationCapability.DRILLING not in machine.capabilities.operations
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_MACHINE_INCOMPATIBLE,
                "Machine does not declare the drilling operation capability",
            )
        speed = strategy.spindle_rpm.value
        if not any(
            spindle.minimum_speed.value <= speed <= spindle.maximum_speed.value
            and strategy.spindle_direction in spindle.directions
            for spindle in machine.spindles
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_MACHINE_INCOMPATIBLE,
                "Machine spindle range or direction is incompatible",
            )
        derived_feed = strategy.feed_per_minute.to(
            machine.capabilities.maximum_feed.unit
        )
        if derived_feed.value > machine.capabilities.maximum_feed.value:
            raise BoringGenerationError(
                DiagnosticCode.BORE_MACHINE_INCOMPATIBLE,
                "Derived boring feed exceeds the machine maximum feed",
            )
        required_machine_coolant = {
            BoringCoolantMode.FLOOD: MachineCoolantCapability.FLOOD,
            BoringCoolantMode.MIST: MachineCoolantCapability.MIST,
            BoringCoolantMode.THROUGH_TOOL: (
                MachineCoolantCapability.THROUGH_SPINDLE
            ),
        }.get(strategy.coolant)
        if (
            required_machine_coolant is not None
            and required_machine_coolant not in machine.capabilities.coolant
        ):
            raise BoringGenerationError(
                DiagnosticCode.BORE_MACHINE_INCOMPATIBLE,
                "Machine does not support the selected coolant mode",
            )
        return machine


def _persistent_references(strategy: BoringStrategy) -> tuple[GeometryReference, ...]:
    source = strategy.geometry.source
    if isinstance(source, HoleReference):
        return (source.reference,)
    assert isinstance(source, HolePattern)
    return tuple(
        location.reference.reference
        for location in source.locations
        if location.reference is not None
    )


def _coolant_state(mode: BoringCoolantMode) -> CoolantState:
    return {
        BoringCoolantMode.OFF: CoolantState.OFF,
        BoringCoolantMode.FLOOD: CoolantState.FLOOD,
        BoringCoolantMode.MIST: CoolantState.MIST,
        BoringCoolantMode.THROUGH_TOOL: CoolantState.THROUGH_TOOL,
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

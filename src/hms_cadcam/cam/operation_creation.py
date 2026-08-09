"""Typed, persistence-neutral state for the Stage16A operation wizard."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Mapping
from uuid import UUID, uuid4

from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    CamJobId,
    CamNodeId,
    DrillApproachPolicy,
    DrillDepthDefinition,
    DrillGeometryInput,
    DrillRetractPolicy,
    DrillingCycle,
    DrillingStrategy,
    EffectiveValueValidation,
    FeedRate,
    FeedUnit,
    HolderDefinition,
    HolePattern,
    HoleReference,
    Length,
    Revision,
    Setup,
    SetupId,
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolAssemblyId,
    ToolAssemblyStatus,
    ToolDefinition,
    ToolDefinitionId,
    ToolProfileListState,
    ToolProfileValueSource,
    ToolProgramProfileId,
    SpindleSpeed,
    Vector3,
    assess_tool_assembly,
    assess_tool_program_profile,
)
from hms_cadcam.cam.domain.machine import OperationCapability
from hms_cadcam.cam.domain.tool_profiles import ToolStrategyProfileSchema
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.persistence.models import CamProjectSnapshot


HoleSource = HoleReference | HolePattern


class OperationCreationStep(StrEnum):
    """The three user-visible steps; terminal states are separate."""

    SELECT_OPERATION = "select_operation"
    SELECT_TOOL = "select_tool"
    CONFIGURE_OPERATION = "configure_operation"


class OperationCreationState(StrEnum):
    SELECT_OPERATION = "select_operation"
    SELECT_TOOL = "select_tool"
    CONFIGURE_OPERATION = "configure_operation"
    READY_TO_CREATE = "ready_to_create"
    CREATED = "created"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class OperationStrategyChoice:
    strategy_id: str
    display_name: str
    description: str
    supported_tool_families: tuple[str, ...]
    required_capability: OperationCapability


@dataclass(frozen=True, slots=True)
class OperationToolChoice:
    """One project-owned Tool Assembly with truthful compatibility evidence."""

    assembly_id: ToolAssemblyId
    tool_id: ToolDefinitionId
    tool_name: str
    assembly_name: str
    family: str
    diameter_text: str
    holder_text: str
    compatible: bool
    reason: str
    profile_state: ToolProfileListState
    profile_id: ToolProgramProfileId | None
    provenance: tuple[ToolProfileValueSource, ...]
    tool_revision: Revision
    configuration_revision: Revision
    assembly_revision: Revision


@dataclass(frozen=True, slots=True)
class OperationCreationSession:
    """Immutable working-copy identity; it never owns a persisted Operation."""

    session_id: UUID
    project_id: UUID
    project_generation: int
    job_id: CamJobId
    setup_id: SetupId
    parent_node_id: CamNodeId
    state: OperationCreationState = OperationCreationState.SELECT_OPERATION
    current_step: OperationCreationStep = OperationCreationStep.SELECT_OPERATION
    strategy_id: str | None = None
    tool_assembly_id: ToolAssemblyId | None = None
    tool_id: ToolDefinitionId | None = None
    profile_id: ToolProgramProfileId | None = None
    tool_configuration_revision: Revision | None = None
    resolved_provenance: tuple[ToolProfileValueSource, ...] = ()
    working_values: tuple[tuple[str, object], ...] = ()
    validation_errors: tuple[str, ...] = ()

    @classmethod
    def start(
        cls,
        *,
        project_id: UUID,
        project_generation: int,
        job_id: CamJobId,
        setup_id: SetupId,
        parent_node_id: CamNodeId,
    ) -> "OperationCreationSession":
        if project_id.int == 0 or project_generation <= 0:
            raise ValueError("Operation creation project identity is invalid")
        return cls(
            uuid4(), project_id, project_generation, job_id, setup_id, parent_node_id
        )

    @property
    def program_context_id(self) -> str:
        """Expose the repository's actual Job/Setup program context."""
        return f"{self.job_id}:{self.setup_id}"

    def _require_active(self) -> None:
        """Reject every attempt to reopen a terminal creation session."""
        if self.state in {
            OperationCreationState.CREATED,
            OperationCreationState.CANCELLED,
        }:
            raise RuntimeError("Operation creation session is terminal")

    def select_strategy(
        self,
        strategy_id: str,
        *,
        selected_tool_remains_compatible: bool = False,
    ) -> "OperationCreationSession":
        self._require_active()
        if not strategy_id:
            raise ValueError("Strategy identity is required")
        keep_tool = (
            self.strategy_id is not None
            and self.tool_assembly_id is not None
            and selected_tool_remains_compatible
        )
        return replace(
            self,
            strategy_id=strategy_id,
            tool_assembly_id=self.tool_assembly_id if keep_tool else None,
            tool_id=self.tool_id if keep_tool else None,
            profile_id=self.profile_id if keep_tool else None,
            tool_configuration_revision=(
                self.tool_configuration_revision if keep_tool else None
            ),
            resolved_provenance=self.resolved_provenance if keep_tool else (),
            working_values=(),
            validation_errors=(),
            current_step=OperationCreationStep.SELECT_TOOL,
            state=OperationCreationState.SELECT_TOOL,
        )

    def select_tool(self, choice: OperationToolChoice) -> "OperationCreationSession":
        self._require_active()
        if self.strategy_id is None:
            raise RuntimeError("Select a strategy before selecting a Tool")
        if not choice.compatible:
            raise ValueError(choice.reason or "Tool is incompatible")
        return replace(
            self,
            tool_assembly_id=choice.assembly_id,
            tool_id=choice.tool_id,
            profile_id=choice.profile_id,
            tool_configuration_revision=choice.configuration_revision,
            resolved_provenance=choice.provenance,
            working_values=(),
            validation_errors=(),
            current_step=OperationCreationStep.CONFIGURE_OPERATION,
            state=OperationCreationState.CONFIGURE_OPERATION,
        )

    def configure(
        self,
        values: Mapping[str, object],
        *,
        validation_errors: tuple[str, ...] = (),
    ) -> "OperationCreationSession":
        self._require_active()
        if self.tool_assembly_id is None:
            raise RuntimeError("Select a Tool before configuring the operation")
        normalized = tuple(sorted(values.items(), key=lambda item: item[0]))
        ready = not validation_errors
        return replace(
            self,
            working_values=normalized,
            validation_errors=validation_errors,
            current_step=OperationCreationStep.CONFIGURE_OPERATION,
            state=(
                OperationCreationState.READY_TO_CREATE
                if ready
                else OperationCreationState.CONFIGURE_OPERATION
            ),
        )

    def back(self) -> "OperationCreationSession":
        self._require_active()
        if self.current_step is OperationCreationStep.CONFIGURE_OPERATION:
            return replace(
                self,
                current_step=OperationCreationStep.SELECT_TOOL,
                state=OperationCreationState.SELECT_TOOL,
                working_values=(),
                validation_errors=(),
            )
        if self.current_step is OperationCreationStep.SELECT_TOOL:
            return replace(
                self,
                current_step=OperationCreationStep.SELECT_OPERATION,
                state=OperationCreationState.SELECT_OPERATION,
                validation_errors=(),
            )
        return self

    def mark_created(self) -> "OperationCreationSession":
        self._require_active()
        if self.state is not OperationCreationState.READY_TO_CREATE:
            raise RuntimeError("Operation creation session is not ready")
        return replace(self, state=OperationCreationState.CREATED)

    def cancel(self) -> "OperationCreationSession":
        if self.state is OperationCreationState.CREATED:
            raise RuntimeError("Created operation cannot be cancelled")
        if self.state is OperationCreationState.CANCELLED:
            return self
        return replace(
            self,
            state=OperationCreationState.CANCELLED,
            working_values=(),
            validation_errors=(),
        )


class Stage16AStrategyRegistry:
    """Thin product registry over the existing Tool profile schemas."""

    _DESCRIPTIONS = {
        "drilling_v1": "Khoan các lỗ đã chọn bằng chu trình khoan hiện có.",
        "parallel_finishing_3d": "Gia công tinh bề mặt theo các đường chạy song song.",
        "z_level_finishing_3d": "Gia công tinh theo từng cao độ Z.",
    }

    def choices(self) -> tuple[OperationStrategyChoice, ...]:
        return tuple(self._choice(schema) for schema in DEFAULT_TOOL_PROFILE_REGISTRY.schemas)

    def choice(self, strategy_id: str) -> OperationStrategyChoice:
        schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy_id)
        return self._choice(schema)

    @classmethod
    def _choice(cls, schema: ToolStrategyProfileSchema) -> OperationStrategyChoice:
        capability = (
            OperationCapability.DRILLING
            if schema.strategy_id == "drilling_v1"
            else OperationCapability.MILLING
        )
        return OperationStrategyChoice(
            schema.strategy_id,
            schema.display_name_vi,
            cls._DESCRIPTIONS[schema.strategy_id],
            schema.supported_tool_families,
            capability,
        )


class Stage16AToolSelectionService:
    """Enumerate and revalidate project-owned Tool Assemblies without persistence."""

    def __init__(
        self,
        snapshot: CamProjectSnapshot,
        *,
        setup_unit: LengthUnit,
    ) -> None:
        self._snapshot = snapshot
        self._setup_unit = setup_unit
        self._strategies = Stage16AStrategyRegistry()

    def choices(self, strategy_id: str, query: str = "") -> tuple[OperationToolChoice, ...]:
        strategy = self._strategies.choice(strategy_id)
        needle = query.strip().casefold()
        choices = tuple(
            self._choice(assembly, strategy)
            for assembly in self._snapshot.tool_assemblies
        )
        filtered = tuple(
            item
            for item in choices
            if not needle
            or needle
            in " ".join(
                (
                    item.tool_name,
                    item.assembly_name,
                    item.family,
                    str(item.tool_id),
                    str(item.assembly_id),
                )
            ).casefold()
        )
        return tuple(
            sorted(
                filtered,
                key=lambda item: (
                    not item.compatible,
                    item.tool_name.casefold(),
                    str(item.assembly_id),
                ),
            )
        )

    def require_current(
        self,
        strategy_id: str,
        assembly_id: ToolAssemblyId,
        *,
        tool_id: ToolDefinitionId,
        configuration_revision: Revision,
    ) -> OperationToolChoice:
        choice = next(
            (
                item
                for item in self.choices(strategy_id)
                if item.assembly_id == assembly_id
            ),
            None,
        )
        if choice is None:
            raise ValueError("Tool Assembly was deleted from the project.")
        if choice.tool_id != tool_id:
            raise ValueError("Tool Assembly now references another Tool.")
        if choice.configuration_revision != configuration_revision:
            raise ValueError(
                "Tool configuration or profile changed; select the Tool again."
            )
        if not choice.compatible:
            raise ValueError(choice.reason)
        return choice

    def _choice(
        self,
        assembly: ToolAssembly,
        strategy: OperationStrategyChoice,
    ) -> OperationToolChoice:
        tool = self._tool(assembly)
        holder = self._holder(assembly)
        status = assess_tool_assembly(
            assembly,
            ToolAssemblyEvidence(
                tool_exists=tool is not None,
                tool_revision=tool.revision if tool is not None else None,
                tool_fingerprint=(
                    tool.content_fingerprint if tool is not None else None
                ),
                tool_unit=tool.unit if tool is not None else None,
                holder_exists=holder is not None,
                holder_revision=holder.revision if holder is not None else None,
                holder_fingerprint=(
                    holder.content_fingerprint if holder is not None else None
                ),
                holder_unit=holder.unit if holder is not None else None,
            ),
        )
        family = tool.family.value if tool is not None else "missing"
        compatible = status is ToolAssemblyStatus.VALID
        reason = "Compatible Tool."
        if not compatible:
            reason = {
                ToolAssemblyStatus.MISSING_TOOL: "Tool Definition is missing.",
                ToolAssemblyStatus.TOOL_REVISION_MISMATCH: (
                    "Tool Assembly references an older Tool revision."
                ),
                ToolAssemblyStatus.MISSING_HOLDER: "Declared Holder is missing.",
                ToolAssemblyStatus.HOLDER_REVISION_MISMATCH: (
                    "Holder changed after Tool Assembly was created."
                ),
                ToolAssemblyStatus.INCOMPATIBLE_UNIT: (
                    "Tool and Holder units are incompatible."
                ),
            }.get(status, "Tool Assembly is invalid.")
        elif assembly.unit is not self._setup_unit:
            compatible = False
            reason = "Tool Assembly unit does not match the current Setup."
        elif family not in strategy.supported_tool_families:
            compatible = False
            reason = "Tool family is not supported by the selected strategy."

        profile_state = ToolProfileListState.NOT_CONFIGURED
        profile_id = None
        provenance: list[ToolProfileValueSource] = []
        if tool is not None:
            schema = DEFAULT_TOOL_PROFILE_REGISTRY.schema(strategy.strategy_id)
            if not tool.common_defaults.is_empty:
                provenance.append(ToolProfileValueSource.TOOL_COMMON_DEFAULT)
            usable = []
            for profile in tool.profiles_for_strategy(strategy.strategy_id):
                assessed = assess_tool_program_profile(
                    profile,
                    tool,
                    DEFAULT_TOOL_PROFILE_REGISTRY,
                    holder_fingerprint=(
                        holder.content_fingerprint if holder is not None else None
                    ),
                )
                profile_state = assessed.state
                if assessed.usable:
                    usable.append(profile)
            if len(usable) == 1:
                profile_id = usable[0].profile_id
                provenance.append(ToolProfileValueSource.TOOL_PROGRAM_PROFILE)
            elif len(usable) > 1:
                compatible = False
                reason = (
                    "Multiple enabled Tool profiles require an explicit selection."
                )
                profile_state = ToolProfileListState.INCOMPATIBLE
            if not provenance:
                provenance.append(ToolProfileValueSource.AUTOMATIC_POLICY)
            if any(field.report_dict()["has_safe_default"] for field in schema.fields):
                provenance.append(ToolProfileValueSource.SAFE_DEFAULT)

        diameter = getattr(getattr(tool, "cutting_geometry", None), "diameter", None)
        diameter_text = "—"
        if diameter is not None:
            diameter_text = f"D{diameter.value:g} {diameter.unit.value} / R{diameter.value / 2.0:g}"
        return OperationToolChoice(
            assembly.assembly_id,
            assembly.tool_id,
            tool.name if tool is not None else "Tool bị thiếu",
            assembly.name,
            family,
            diameter_text,
            holder.name if holder is not None else "Không khai báo Holder",
            compatible,
            reason,
            profile_state,
            profile_id,
            tuple(dict.fromkeys(provenance)),
            tool.revision if tool is not None else Revision(0),
            tool.configuration_revision if tool is not None else Revision(0),
            assembly.revision,
        )

    def _tool(self, assembly: ToolAssembly) -> ToolDefinition | None:
        return next(
            (
                item
                for item in self._snapshot.tool_definitions
                if item.tool_id == assembly.tool_id
            ),
            None,
        )

    def _holder(self, assembly: ToolAssembly) -> HolderDefinition | None:
        if assembly.holder_id is None:
            return None
        return next(
            (
                item
                for item in self._snapshot.holder_definitions
                if item.holder_id == assembly.holder_id
            ),
            None,
        )


def default_drilling_creation_strategy(
    setup: Setup,
    source: HoleSource,
) -> DrillingStrategy:
    """Build the established conservative Drilling working-copy defaults."""
    unit = setup.wcs.origin.unit
    if source.unit is not unit:
        raise ValueError("Đơn vị hình học Khoan không khớp Setup WCS.")
    plane_origin = (
        source.plane_origin
        if isinstance(source, HoleReference)
        else source.locations[0].plane_origin
    )
    delta = Vector3(
        plane_origin.x - setup.wcs.origin.x,
        plane_origin.y - setup.wcs.origin.y,
        plane_origin.z - setup.wcs.origin.z,
    )
    top_z = delta.dot(setup.wcs.z_axis)
    scale = 1.0 if unit is LengthUnit.MM else 1.0 / 25.4
    feed_unit = (
        FeedUnit.MM_PER_MINUTE
        if unit is LengthUnit.MM
        else FeedUnit.INCH_PER_MINUTE
    )
    return DrillingStrategy(
        unit=unit,
        geometry=DrillGeometryInput(source, unit),
        depth=DrillDepthDefinition(
            unit,
            Length(top_z, unit),
            Length(top_z - 5.0 * scale, unit),
        ),
        cycle=DrillingCycle.DRILL,
        clearance_height=Length(top_z + 8.0 * scale, unit),
        retract_height=Length(top_z + 3.0 * scale, unit),
        feed_rate=FeedRate(120.0 * scale, feed_unit),
        spindle_speed=SpindleSpeed(1500.0),
        dwell_seconds=0.0,
        retract_policy=DrillRetractPolicy.RETRACT_HEIGHT,
        approach_policy=DrillApproachPolicy.RAPID_CLEARANCE_FEED_RETRACT,
        tolerance=Length(1.0e-7 * scale, unit),
    )

def resolution_sources(values: tuple[object, ...]) -> tuple[ToolProfileValueSource, ...]:
    """Return stable unique provenance from existing resolver value objects."""
    result: list[ToolProfileValueSource] = []
    for value in values:
        source = getattr(value, "source", None)
        validation = getattr(value, "validation_status", None)
        if (
            isinstance(source, ToolProfileValueSource)
            and validation is not EffectiveValueValidation.BLOCKED
            and source not in result
        ):
            result.append(source)
    return tuple(result)


__all__ = [
    "OperationCreationSession",
    "OperationCreationState",
    "OperationCreationStep",
    "OperationStrategyChoice",
    "OperationToolChoice",
    "Stage16AStrategyRegistry",
    "Stage16AToolSelectionService",
    "default_drilling_creation_strategy",
    "resolution_sources",
]

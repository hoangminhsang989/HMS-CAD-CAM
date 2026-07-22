"""Shared production bindings for the Stage 9A.6 drilling family editors.

The module intentionally maps only fields owned by the existing v1 domain
strategies.  Controller cycle codes remain a Post concern and no QWidget,
database, file-system, or native CAD object crosses this boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import math

from hms_cadcam.cam.domain import (
    BoringCoolantMode,
    BoringRetractPolicy,
    BoringStrategy,
    DirtyReason,
    DrillApproachPolicy,
    DrillDepthDefinition,
    DrillGeometryInput,
    DrillRetractPolicy,
    DrillingCycle,
    DrillingStrategy,
    FeedRate,
    FeedUnit,
    GeometryInputId,
    GeometryInputRole,
    HolderDefinition,
    HolePattern,
    HoleReference,
    Length,
    MachineDefinition,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationGeometryInput,
    ReamingCoolantMode,
    ReamingRetractPolicy,
    ReamingStrategy,
    Setup,
    SpindleDirection,
    SpindleSpeed,
    TappingHand,
    TappingStrategy,
    TappingSynchronizationPolicy,
    ToolAssembly,
    ToolAssemblyReference,
    ToolDefinition,
    ToolFamily,
)
from hms_cadcam.ui.function_editor.model import (
    ApplicabilityOperator,
    FunctionEditorAction,
    FunctionEditorApplicability,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorFooter,
    FunctionEditorResetBehavior,
    FunctionEditorSection,
    FunctionEditorStrategyKey,
    FunctionEditorSummary,
    FunctionEditorValidationKind,
    FunctionEditorValidationRule,
    FunctionEditorValueConversion,
    FunctionEditorValueSource,
    ParameterDisclosureLevel,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema


HoleSource = HoleReference | HolePattern
HoleStrategy = DrillingStrategy | TappingStrategy | ReamingStrategy | BoringStrategy


class DrillingFamilyEditorKind(StrEnum):
    """Production editor variants over the existing controller-neutral models."""

    DRILLING = "drilling"
    TAPPING = "tapping"
    REAMING = "reaming"
    BORING = "boring"

    @property
    def strategy_key(self) -> str:
        return {
            DrillingFamilyEditorKind.DRILLING: "drilling_v1",
            DrillingFamilyEditorKind.TAPPING: "tapping_v1",
            DrillingFamilyEditorKind.REAMING: "reaming_v1",
            DrillingFamilyEditorKind.BORING: "boring_v1",
        }[self]

    @property
    def title(self) -> str:
        return self.value.title()

    @property
    def required_capability(self) -> OperationCapability:
        return (
            OperationCapability.TAPPING
            if self is DrillingFamilyEditorKind.TAPPING
            else OperationCapability.DRILLING
        )

    @property
    def tool_families(self) -> tuple[ToolFamily, ...]:
        return {
            DrillingFamilyEditorKind.DRILLING: (
                ToolFamily.DRILL,
                ToolFamily.CENTER_DRILL,
            ),
            DrillingFamilyEditorKind.TAPPING: (ToolFamily.TAP,),
            DrillingFamilyEditorKind.REAMING: (ToolFamily.REAMER,),
            DrillingFamilyEditorKind.BORING: (ToolFamily.BORING_BAR,),
        }[self]


@dataclass(frozen=True, slots=True)
class DrillingFamilyEditorContext:
    """Native-free project snapshot used by one drilling-family editor."""

    kind: DrillingFamilyEditorKind
    operation_name: str
    operation: Operation
    setup: Setup
    tool_assemblies: tuple[ToolAssembly, ...]
    tool_definitions: tuple[ToolDefinition, ...]
    holder_definitions: tuple[HolderDefinition, ...]
    machine_definitions: tuple[MachineDefinition, ...]
    hole_source: HoleSource
    geometry_resolved: bool
    geometry_diagnostic: str = ""

    def __post_init__(self) -> None:
        if self.operation.strategy_key != self.kind.strategy_key:
            raise ValueError("Drilling-family context strategy does not match its editor")


@dataclass(slots=True)
class DrillingFamilyEditorDraftContext:
    """Transient typed hole selection; never serialized or fingerprinted."""

    hole_source: HoleSource
    pending_input_ids: dict[str, GeometryInputId] | None = None


@dataclass(frozen=True, slots=True)
class DrillingFamilyOperationUpdate:
    """Fully validated candidate consumed by one atomic application command."""

    operation_name: str
    operation: Operation
    strategy: HoleStrategy
    assembly: ToolAssembly
    tool: ToolDefinition | None
    holder: HolderDefinition | None
    machine: MachineDefinition
    hole_source: HoleSource


_COMMON_FIELD_IDS = frozenset(
    {
        "operation_name",
        "operation_type",
        "enabled",
        "geometry_summary",
        "hole_count",
        "selection_mode",
        "machining_direction",
        "coordinate_system",
        "geometry_source_id",
        "tool_assembly_id",
        "tool_details",
        "holder_summary",
        "top_z",
        "final_depth",
        "clearance_height",
        "retract_height",
        "spindle_speed",
        "machine_id",
        "capability_summary",
        "tolerance",
    }
)
_VARIANT_FIELD_IDS = {
    DrillingFamilyEditorKind.DRILLING: frozenset(
        {
            "cycle",
            "feed_rate",
            "coolant_summary",
            "peck_depth",
            "dwell_seconds",
            "retract_policy",
            "approach_policy",
        }
    ),
    DrillingFamilyEditorKind.TAPPING: frozenset(
        {
            "thread_system",
            "nominal_diameter",
            "pitch",
            "hand",
            "synchronized_feed",
            "coolant_summary",
            "synchronization_policy",
            "dwell_seconds",
        }
    ),
    DrillingFamilyEditorKind.REAMING: frozenset(
        {
            "nominal_diameter",
            "pre_hole_diameter",
            "diametral_allowance",
            "feed_per_revolution",
            "feed_per_minute",
            "spindle_direction",
            "coolant",
            "dwell_seconds",
            "retract_policy",
        }
    ),
    DrillingFamilyEditorKind.BORING: frozenset(
        {
            "finished_bore_diameter",
            "pre_bore_diameter",
            "radial_stock",
            "boring_mode",
            "feed_per_revolution",
            "feed_per_minute",
            "spindle_direction",
            "coolant",
            "dwell_seconds",
            "retract_policy",
        }
    ),
}


def strategy_from_operation(context: DrillingFamilyEditorContext) -> HoleStrategy:
    """Decode one supported v1 strategy without changing its binding."""
    parameters = context.operation.parameters
    if context.kind is DrillingFamilyEditorKind.DRILLING:
        return DrillingStrategy.from_operation_parameters(parameters)
    if context.kind is DrillingFamilyEditorKind.TAPPING:
        return TappingStrategy.from_operation_parameters(parameters)
    if context.kind is DrillingFamilyEditorKind.REAMING:
        return ReamingStrategy.from_operation_parameters(parameters)
    return BoringStrategy.from_operation_parameters(parameters)


def _text(value: object, field_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_id} là bắt buộc.")
    return value.strip()


def _number(value: object, field_id: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_id} phải là số hữu hạn.")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_id} phải là số hữu hạn.") from error
    if not math.isfinite(result):
        raise ValueError(f"{field_id} phải là số hữu hạn.")
    return result


def _boolean(value: object, field_id: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_id} phải là boolean.")
    return value


def _source_identity(source: HoleSource) -> str:
    return source.fingerprint.digest


def _hole_count(source: HoleSource) -> int:
    return 1 if isinstance(source, HoleReference) else len(source.locations)


def _source_kinds(source: HoleSource) -> str:
    if isinstance(source, HoleReference):
        return source.reference.kind.value
    kinds = sorted({item.source_kind.value for item in source.locations})
    return ", ".join(kinds)


def _source_axis(source: HoleSource) -> object:
    return source.axis if isinstance(source, HoleReference) else source.locations[0].axis


def _source_references(source: HoleSource) -> tuple[HoleReference, ...]:
    if isinstance(source, HoleReference):
        return (source,)
    return tuple(
        item.reference for item in source.locations if item.reference is not None
    )


def _geometry_summary(context: DrillingFamilyEditorContext) -> str:
    return str(
        drilling_family_geometry_values(
            context.hole_source,
            context.geometry_resolved,
            context.geometry_diagnostic,
        )["geometry_summary"]
    )


def drilling_family_geometry_values(
    source: HoleSource,
    resolved: bool,
    diagnostic: str = "",
) -> dict[str, PresentationValue]:
    """Return the shared presentation fields for one transient hole source."""
    status = "RESOLVED" if resolved else "MISSING/STALE/UNSUPPORTED"
    summary = f"{_hole_count(source)} lỗ · {_source_kinds(source)} · {status}"
    if diagnostic:
        summary = f"{summary} · {diagnostic}"
    axis = _source_axis(source)
    return {
        "geometry_summary": summary,
        "hole_count": str(_hole_count(source)),
        "selection_mode": _source_kinds(source),
        "machining_direction": f"({axis.x:g}, {axis.y:g}, {axis.z:g})",
        "geometry_source_id": _source_identity(source),
    }


def _selected_resources(
    context: DrillingFamilyEditorContext,
) -> tuple[ToolAssembly | None, ToolDefinition | None, HolderDefinition | None]:
    assembly = next(
        (
            item
            for item in context.tool_assemblies
            if item.assembly_id == context.operation.tool_assembly.assembly_id
        ),
        None,
    )
    tool = (
        next(
            (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
            None,
        )
        if assembly is not None
        else None
    )
    holder = (
        next(
            (
                item
                for item in context.holder_definitions
                if assembly.holder_id is not None and item.holder_id == assembly.holder_id
            ),
            None,
        )
        if assembly is not None
        else None
    )
    return assembly, tool, holder


def _tool_summaries(context: DrillingFamilyEditorContext) -> tuple[str, str, str]:
    assembly, tool, holder = _selected_resources(context)
    if assembly is None:
        return "Tool Assembly bị thiếu", "Holder không khả dụng", "Chưa chọn dao"
    if tool is None:
        tool_text = f"{assembly.name} · Tool Definition bị thiếu"
    else:
        geometry = tool.cutting_geometry
        diameter = getattr(geometry, "diameter", None)
        diameter_text = (
            ""
            if diameter is None
            else f" · D{diameter.value:g} {diameter.unit.value}"
        )
        range_text = ""
        minimum = getattr(geometry, "minimum_bore_diameter", None)
        maximum = getattr(geometry, "maximum_bore_diameter", None)
        if minimum is not None and maximum is not None:
            range_text = f" · D{minimum.value:g}–{maximum.value:g}"
        tool_text = (
            f"{tool.name} · {tool.family.value}{diameter_text}{range_text} · "
            f"usable {tool.usable_length.value:g} · stickout {assembly.stickout.value:g}"
        )
    holder_text = (
        "Không có holder"
        if assembly.holder_id is None
        else holder.name if holder is not None else "Holder bị thiếu hoặc stale"
    )
    return tool_text, holder_text, assembly.name


def _machine_summary(context: DrillingFamilyEditorContext) -> str:
    requirement = context.operation.machine_requirement
    machine = next(
        (
            item
            for item in context.machine_definitions
            if requirement is not None and item.machine_id == requirement.machine_id
        ),
        None,
    )
    if machine is None:
        return "Máy chưa xác định · Post capability chưa xác định"
    operations = ", ".join(item.value for item in machine.capabilities.operations)
    details = [f"{machine.name} · {operations or 'không có capability'}"]
    if context.kind is DrillingFamilyEditorKind.TAPPING:
        modes = ", ".join(item.value for item in machine.capabilities.tapping_modes)
        details.append(f"sync {modes or 'UNSPECIFIED'}")
    details.append("Post cycle kiểm tra tại Post; UI không ánh xạ G-code")
    return " · ".join(details)


def drilling_family_applied_values(
    context: DrillingFamilyEditorContext,
) -> dict[str, PresentationValue]:
    """Convert one operation snapshot to deterministic presentation primitives."""
    strategy = strategy_from_operation(context)
    tool_text, holder_text, _tool_name = _tool_summaries(context)
    geometry_values = drilling_family_geometry_values(
        context.hole_source,
        context.geometry_resolved,
        context.geometry_diagnostic,
    )
    values: dict[str, PresentationValue] = {
        "operation_name": context.operation_name,
        "operation_type": context.kind.title,
        "enabled": context.operation.enabled,
        **geometry_values,
        "coordinate_system": context.setup.work_offset.name,
        "tool_assembly_id": str(context.operation.tool_assembly.assembly_id),
        "tool_details": tool_text,
        "holder_summary": holder_text,
        "top_z": str(strategy.top_z.value),
        "final_depth": str(strategy.final_depth.value),
        "clearance_height": str(strategy.clearance_height.value),
        "retract_height": str(strategy.retract_height.value),
        "machine_id": (
            ""
            if context.operation.machine_requirement is None
            else str(context.operation.machine_requirement.machine_id)
        ),
        "capability_summary": _machine_summary(context),
        "tolerance": str(strategy.tolerance.value),
    }
    if isinstance(strategy, DrillingStrategy):
        values.update(
            {
                "cycle": strategy.cycle.value,
                "feed_rate": str(strategy.feed_rate.value),
                "spindle_speed": str(strategy.spindle_speed.value),
                "coolant_summary": "Drilling v1 chưa lưu coolant; Post không được UI suy đoán",
                "peck_depth": "" if strategy.peck_depth is None else str(strategy.peck_depth.value),
                "dwell_seconds": str(strategy.dwell_seconds),
                "retract_policy": strategy.retract_policy.value,
                "approach_policy": strategy.approach_policy.value,
            }
        )
    elif isinstance(strategy, TappingStrategy):
        feed = strategy.pitch.value * strategy.spindle_speed.value
        values.update(
            {
                "thread_system": (
                    "Metric pitch" if strategy.unit.value == "mm" else "Inch pitch"
                ),
                "nominal_diameter": str(strategy.nominal_diameter.value),
                "pitch": str(strategy.pitch.value),
                "hand": strategy.hand.value,
                "spindle_speed": str(strategy.spindle_speed.value),
                "synchronized_feed": str(feed),
                "coolant_summary": "Tapping v1 chưa lưu coolant; Post không được UI suy đoán",
                "synchronization_policy": strategy.synchronization_policy.value,
                "dwell_seconds": str(strategy.dwell_seconds),
            }
        )
    elif isinstance(strategy, ReamingStrategy):
        assert strategy.pre_hole_diameter is not None
        values.update(
            {
                "nominal_diameter": str(strategy.nominal_diameter.value),
                "pre_hole_diameter": str(strategy.pre_hole_diameter.value),
                "diametral_allowance": str(
                    strategy.nominal_diameter.value - strategy.pre_hole_diameter.value
                ),
                "spindle_speed": str(strategy.spindle_speed.value),
                "feed_per_revolution": str(strategy.feed_per_revolution.value),
                "feed_per_minute": str(
                    strategy.spindle_speed.value * strategy.feed_per_revolution.value
                ),
                "spindle_direction": strategy.spindle_direction.value,
                "coolant": strategy.coolant.value,
                "dwell_seconds": str(strategy.dwell_seconds),
                "retract_policy": strategy.retract_policy.value,
            }
        )
    else:
        assert isinstance(strategy, BoringStrategy)
        assert strategy.pre_bore_diameter is not None
        values.update(
            {
                "finished_bore_diameter": str(strategy.finished_bore_diameter.value),
                "pre_bore_diameter": str(strategy.pre_bore_diameter.value),
                "radial_stock": str(strategy.radial_stock.value),
                "boring_mode": "Axial boring · controlled feed retract",
                "spindle_speed": str(strategy.spindle_rpm.value),
                "feed_per_revolution": str(strategy.feed_per_revolution.value),
                "feed_per_minute": str(strategy.feed_per_minute.value),
                "spindle_direction": strategy.spindle_direction.value,
                "coolant": strategy.coolant.value,
                "dwell_seconds": str(strategy.dwell_seconds),
                "retract_policy": strategy.retract_policy.value,
            }
        )
    return values


def _complete_values(
    context: DrillingFamilyEditorContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    complete = drilling_family_applied_values(context)
    complete.update(values)
    return complete


def _source_for_values(
    context: DrillingFamilyEditorContext,
    draft: DrillingFamilyEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> HoleSource:
    identity = _text(values["geometry_source_id"], "geometry_source_id")
    source = next(
        (
            item
            for item in (draft.hole_source, context.hole_source)
            if _source_identity(item) == identity
        ),
        None,
    )
    if source is None:
        raise ValueError("Hole geometry draft không khớp persistent source; hãy chọn lại.")
    return source


def _strategy_from_values(
    context: DrillingFamilyEditorContext,
    source: HoleSource,
    values: Mapping[str, PresentationValue],
) -> HoleStrategy:
    complete = _complete_values(context, values)
    current = strategy_from_operation(context)
    unit = context.setup.wcs.origin.unit
    depth = DrillDepthDefinition(
        unit,
        Length(_number(complete["top_z"], "top_z"), unit),
        Length(_number(complete["final_depth"], "final_depth"), unit),
    )
    geometry = DrillGeometryInput(source, unit)
    clearance = Length(_number(complete["clearance_height"], "clearance_height"), unit)
    retract = Length(_number(complete["retract_height"], "retract_height"), unit)
    tolerance = Length(_number(complete["tolerance"], "tolerance"), unit)
    if context.kind is DrillingFamilyEditorKind.DRILLING:
        assert isinstance(current, DrillingStrategy)
        cycle = DrillingCycle(_text(complete["cycle"], "cycle"))
        peck_text = str(complete["peck_depth"]).strip()
        peck = (
            Length(_number(peck_text, "peck_depth"), unit)
            if cycle is DrillingCycle.PECK_DRILL
            else None
        )
        feed_unit = FeedUnit.MM_PER_MINUTE if unit.value == "mm" else FeedUnit.INCH_PER_MINUTE
        return DrillingStrategy(
            unit,
            geometry,
            depth,
            cycle,
            clearance,
            retract,
            FeedRate(_number(complete["feed_rate"], "feed_rate"), feed_unit),
            SpindleSpeed(_number(complete["spindle_speed"], "spindle_speed")),
            _number(complete["dwell_seconds"], "dwell_seconds"),
            peck,
            DrillRetractPolicy(_text(complete["retract_policy"], "retract_policy")),
            DrillApproachPolicy(_text(complete["approach_policy"], "approach_policy")),
            tolerance,
        )
    if context.kind is DrillingFamilyEditorKind.TAPPING:
        return TappingStrategy(
            unit,
            geometry,
            depth,
            Length(_number(complete["nominal_diameter"], "nominal_diameter"), unit),
            Length(_number(complete["pitch"], "pitch"), unit),
            TappingHand(_text(complete["hand"], "hand")),
            SpindleSpeed(_number(complete["spindle_speed"], "spindle_speed")),
            clearance,
            retract,
            TappingSynchronizationPolicy(
                _text(complete["synchronization_policy"], "synchronization_policy")
            ),
            _number(complete["dwell_seconds"], "dwell_seconds"),
            tolerance,
        )
    feed_unit = (
        FeedUnit.MM_PER_REVOLUTION
        if unit.value == "mm"
        else FeedUnit.INCH_PER_REVOLUTION
    )
    if context.kind is DrillingFamilyEditorKind.REAMING:
        return ReamingStrategy(
            unit,
            geometry,
            depth,
            Length(_number(complete["nominal_diameter"], "nominal_diameter"), unit),
            Length(_number(complete["pre_hole_diameter"], "pre_hole_diameter"), unit),
            SpindleSpeed(_number(complete["spindle_speed"], "spindle_speed")),
            FeedRate(
                _number(complete["feed_per_revolution"], "feed_per_revolution"),
                feed_unit,
            ),
            clearance,
            retract,
            SpindleDirection(_text(complete["spindle_direction"], "spindle_direction")),
            ReamingRetractPolicy(_text(complete["retract_policy"], "retract_policy")),
            ReamingCoolantMode(_text(complete["coolant"], "coolant")),
            _number(complete["dwell_seconds"], "dwell_seconds"),
            tolerance,
        )
    return BoringStrategy(
        unit,
        geometry,
        depth,
        Length(
            _number(complete["finished_bore_diameter"], "finished_bore_diameter"),
            unit,
        ),
        Length(_number(complete["pre_bore_diameter"], "pre_bore_diameter"), unit),
        SpindleSpeed(_number(complete["spindle_speed"], "spindle_speed")),
        FeedRate(
            _number(complete["feed_per_revolution"], "feed_per_revolution"),
            feed_unit,
        ),
        clearance,
        retract,
        SpindleDirection(_text(complete["spindle_direction"], "spindle_direction")),
        BoringRetractPolicy(_text(complete["retract_policy"], "retract_policy")),
        BoringCoolantMode(_text(complete["coolant"], "coolant")),
        _number(complete["dwell_seconds"], "dwell_seconds"),
        tolerance,
    )


def _geometry_inputs(
    existing: tuple[OperationGeometryInput, ...],
    source: HoleSource,
    draft: DrillingFamilyEditorDraftContext,
) -> tuple[OperationGeometryInput, ...]:
    previous = {item.reference.reference_id: item for item in existing}
    pending = draft.pending_input_ids or {}
    result: list[OperationGeometryInput] = []
    for order, hole in enumerate(_source_references(source)):
        old = previous.get(hole.reference.reference_id)
        key = str(hole.reference.reference_id)
        input_id = old.input_id if old is not None else pending.get(key)
        if input_id is None:
            input_id = GeometryInputId.new()
            pending[key] = input_id
        result.append(
            OperationGeometryInput(
                input_id,
                GeometryInputRole.DRIVE_GEOMETRY,
                hole.reference,
                True,
                hole.reference.kind,
                order,
            )
        )
    draft.pending_input_ids = pending
    return tuple(result)


def prepare_drilling_family_update(
    context: DrillingFamilyEditorContext,
    draft: DrillingFamilyEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> DrillingFamilyOperationUpdate:
    """Build one legacy-equivalent candidate without mutating project state."""
    complete = _complete_values(context, values)
    source = _source_for_values(context, draft, complete)
    strategy = _strategy_from_values(context, source, complete)
    assembly_id = _text(complete["tool_assembly_id"], "tool_assembly_id")
    assembly = next(
        (item for item in context.tool_assemblies if str(item.assembly_id) == assembly_id),
        None,
    )
    if assembly is None:
        raise ValueError(f"{context.kind.value}.tool_missing: Tool Assembly không tồn tại.")
    tool = next(
        (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
        None,
    )
    holder = next(
        (
            item
            for item in context.holder_definitions
            if assembly.holder_id is not None and item.holder_id == assembly.holder_id
        ),
        None,
    )
    machine_id = _text(complete["machine_id"], "machine_id")
    machine = next(
        (item for item in context.machine_definitions if str(item.machine_id) == machine_id),
        None,
    )
    if machine is None:
        raise ValueError(f"{context.kind.value}.machine_missing: Máy không tồn tại.")
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (context.kind.required_capability,),
    )
    geometry_inputs = _geometry_inputs(context.operation.geometry_inputs, source, draft)
    parameter_set = strategy.to_operation_parameters()
    tool_reference = ToolAssemblyReference.from_assembly(assembly)
    enabled = _boolean(complete["enabled"], "enabled")
    parameter_changed = parameter_set != context.operation.parameters
    geometry_changed = geometry_inputs != context.operation.geometry_inputs
    tool_changed = tool_reference != context.operation.tool_assembly
    machine_changed = requirement != context.operation.machine_requirement
    enabled_changed = enabled != context.operation.enabled
    changed = context.operation
    if any((parameter_changed, geometry_changed, tool_changed, machine_changed, enabled_changed)):
        reason = (
            DirtyReason.GEOMETRY_CHANGED
            if geometry_changed
            else DirtyReason.TOOL_CHANGED
            if tool_changed
            else DirtyReason.MACHINE_CHANGED
            if machine_changed
            else DirtyReason.PARAMETERS_CHANGED
            if parameter_changed
            else DirtyReason.UPSTREAM_CHANGED
        )
        changed = replace(
            context.operation,
            parameters=parameter_set,
            geometry_inputs=geometry_inputs,
            tool_assembly=tool_reference,
            machine_requirement=requirement,
            enabled=enabled,
            revision=context.operation.revision.next(),
            artifact_state=context.operation.artifact_state.mark_dirty(reason),
        )
    return DrillingFamilyOperationUpdate(
        _text(complete["operation_name"], "operation_name"),
        changed,
        strategy,
        assembly,
        tool,
        holder,
        machine,
        source,
    )


def _minimum(code: str, message: str, value: float = 1.0e-12) -> FunctionEditorValidationRule:
    return FunctionEditorValidationRule(
        FunctionEditorValidationKind.MINIMUM, value, message, code
    )


def _cross(
    kind: FunctionEditorValidationKind,
    other: str,
    code: str,
    message: str,
) -> FunctionEditorValidationRule:
    return FunctionEditorValidationRule(kind, other, message, code)


def _number_field(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    unit: str,
    order: int,
    validators: tuple[FunctionEditorValidationRule, ...] = (),
    level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC,
    applicable_when: FunctionEditorApplicability | None = None,
    read_only: bool = False,
    source: FunctionEditorValueSource = FunctionEditorValueSource.USER,
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.READ_ONLY if read_only else FunctionEditorFieldKind.NUMBER,
        value,
        unit=unit,
        source=source,
        applicable_when=applicable_when,
        required=True,
        disclosure_level=level,
        validators=validators,
        help_key=f"drilling_family.{field_id}",
        order=order,
        binding_key=(f"derived.{field_id}" if read_only else f"strategy.{field_id}"),
        conversion=FunctionEditorValueConversion.FLOAT,
        reset_behavior=FunctionEditorResetBehavior.APPLIED,
    )


def _choice_field(
    field_id: str,
    label: str,
    value: PresentationValue,
    choices: tuple[str, ...],
    labels: tuple[tuple[PresentationValue, str], ...],
    *,
    order: int,
    level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC,
    binding_key: str | None = None,
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.CHOICE,
        value,
        choices=choices,
        choice_labels=labels,
        required=True,
        disclosure_level=level,
        order=order,
        binding_key=binding_key or f"strategy.{field_id}",
        help_key=f"drilling_family.{field_id}",
    )


def _read_only(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    order: int,
    source: FunctionEditorValueSource = FunctionEditorValueSource.DERIVED,
    level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC,
    action_id: str = "",
    action_label: str = "",
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.READ_ONLY,
        value,
        source=source,
        disclosure_level=level,
        order=order,
        binding_key=f"derived.{field_id}",
        action_id=action_id,
        action_label=action_label,
        help_key=f"drilling_family.{field_id}",
    )


def _choice_data(
    values: tuple[object, ...], id_getter, name_getter
) -> tuple[tuple[str, ...], tuple[tuple[PresentationValue, str], ...]]:
    ordered = sorted(values, key=lambda item: (name_getter(item).casefold(), str(id_getter(item))))
    if not ordered:
        return ("",), (("", "Chưa có resource phù hợp"),)
    return (
        tuple(str(id_getter(item)) for item in ordered),
        tuple(
            (str(id_getter(item)), f"{name_getter(item)} · {str(id_getter(item))[:8]}")
            for item in ordered
        ),
    )


def _compatible_tools(context: DrillingFamilyEditorContext) -> tuple[ToolAssembly, ...]:
    tools = {item.tool_id: item for item in context.tool_definitions}
    return tuple(
        assembly
        for assembly in context.tool_assemblies
        if assembly.assembly_id == context.operation.tool_assembly.assembly_id
        or (
            tools.get(assembly.tool_id) is not None
            and tools[assembly.tool_id].family in context.kind.tool_families
        )
    )


def _process_fields(
    context: DrillingFamilyEditorContext,
    values: Mapping[str, PresentationValue],
    unit: str,
) -> tuple[FunctionEditorField, ...]:
    positive = lambda field: (_minimum(f"{context.kind.value}.{field}_positive", f"{field} phải > 0."),)
    if context.kind is DrillingFamilyEditorKind.DRILLING:
        return (
            _choice_field(
                "cycle",
                "Chế độ khoan",
                values["cycle"],
                tuple(item.value for item in DrillingCycle),
                (
                    (DrillingCycle.SPOT_DRILL.value, "Spot drilling"),
                    (DrillingCycle.DRILL.value, "Standard drilling"),
                    (DrillingCycle.PECK_DRILL.value, "Peck drilling"),
                ),
                order=10,
            ),
        )
    if context.kind is DrillingFamilyEditorKind.TAPPING:
        return (
            _read_only("thread_system", "Hệ ren", values["thread_system"], order=10),
            _number_field(
                "nominal_diameter", "Đường kính ren", values["nominal_diameter"],
                unit=unit, order=20, validators=positive("nominal_diameter"),
            ),
            _number_field(
                "pitch", "Bước ren", values["pitch"], unit=unit, order=30,
                validators=positive("pitch"),
            ),
            _choice_field(
                "hand", "Chiều ren", values["hand"],
                tuple(item.value for item in TappingHand),
                (
                    (TappingHand.RIGHT_HAND_TAP.value, "Right-hand"),
                    (TappingHand.LEFT_HAND_TAP.value, "Left-hand"),
                ),
                order=40,
            ),
        )
    if context.kind is DrillingFamilyEditorKind.REAMING:
        return (
            _number_field(
                "nominal_diameter", "Đường kính doa", values["nominal_diameter"],
                unit=unit, order=10, validators=positive("nominal_diameter"),
            ),
            _number_field(
                "pre_hole_diameter", "Đường kính lỗ trước", values["pre_hole_diameter"],
                unit=unit, order=20,
                validators=(
                    _minimum("ream.prehole_positive", "Đường kính lỗ trước phải > 0."),
                    _cross(
                        FunctionEditorValidationKind.LESS_THAN_FIELD,
                        "nominal_diameter",
                        "ream.prehole_invalid",
                        "Lỗ trước phải nhỏ hơn đường kính doa.",
                    ),
                ),
            ),
            _number_field(
                "diametral_allowance", "Lượng dư đường kính", values["diametral_allowance"],
                unit=unit, order=30, read_only=True,
            ),
        )
    return (
        _read_only("boring_mode", "Chế độ boring", values["boring_mode"], order=10),
        _number_field(
            "finished_bore_diameter", "Đường kính đích", values["finished_bore_diameter"],
            unit=unit, order=20, validators=positive("finished_bore_diameter"),
        ),
        _number_field(
            "pre_bore_diameter", "Đường kính lỗ trước", values["pre_bore_diameter"],
            unit=unit, order=30,
            validators=(
                _minimum("bore.prebore_positive", "Đường kính lỗ trước phải > 0."),
                _cross(
                    FunctionEditorValidationKind.LESS_THAN_FIELD,
                    "finished_bore_diameter",
                    "bore.prebore_invalid",
                    "Lỗ trước phải nhỏ hơn đường kính đích.",
                ),
            ),
        ),
        _number_field(
            "radial_stock", "Lượng dư hướng kính", values["radial_stock"],
            unit=unit, order=40, read_only=True,
        ),
    )


def _cutting_fields(
    context: DrillingFamilyEditorContext,
    values: Mapping[str, PresentationValue],
    unit: str,
) -> tuple[FunctionEditorField, ...]:
    rpm = _number_field(
        "spindle_speed", "Tốc độ trục chính", values["spindle_speed"],
        unit="rpm", order=10,
        validators=(_minimum(f"{context.kind.value}.spindle_positive", "Spindle phải > 0."),),
    )
    if context.kind is DrillingFamilyEditorKind.DRILLING:
        feed_unit = "mm/min" if unit == "mm" else "in/min"
        return (
            rpm,
            _number_field(
                "feed_rate", "Lượng chạy dao", values["feed_rate"], unit=feed_unit,
                order=20, validators=(_minimum("drill.feed_positive", "Feed phải > 0."),),
            ),
            _read_only(
                "coolant_summary",
                "Coolant",
                values["coolant_summary"],
                order=30,
                level=ParameterDisclosureLevel.ADVANCED,
            ),
        )
    if context.kind is DrillingFamilyEditorKind.TAPPING:
        feed_unit = "mm/min" if unit == "mm" else "in/min"
        return (
            rpm,
            _number_field(
                "synchronized_feed", "Feed đồng bộ", values["synchronized_feed"],
                unit=feed_unit, order=20, read_only=True,
            ),
            _read_only(
                "coolant_summary",
                "Coolant",
                values["coolant_summary"],
                order=30,
                level=ParameterDisclosureLevel.ADVANCED,
            ),
        )
    per_rev = "mm/rev" if unit == "mm" else "in/rev"
    per_minute = "mm/min" if unit == "mm" else "in/min"
    coolant_enum = ReamingCoolantMode if context.kind is DrillingFamilyEditorKind.REAMING else BoringCoolantMode
    return (
        rpm,
        _number_field(
            "feed_per_revolution", "Feed mỗi vòng", values["feed_per_revolution"],
            unit=per_rev, order=20,
            validators=(_minimum(f"{context.kind.value}.feed_positive", "Feed mỗi vòng phải > 0."),),
        ),
        _number_field(
            "feed_per_minute", "Feed mỗi phút", values["feed_per_minute"],
            unit=per_minute, order=30, read_only=True,
        ),
        _choice_field(
            "coolant", "Coolant", values["coolant"],
            tuple(item.value for item in coolant_enum),
            tuple((item.value, item.value.replace("_", " ").title()) for item in coolant_enum),
            order=40,
        ),
    )


def _advanced_fields(
    context: DrillingFamilyEditorContext,
    values: Mapping[str, PresentationValue],
    unit: str,
) -> tuple[FunctionEditorField, ...]:
    dwell = _number_field(
        "dwell_seconds", "Dwell đáy", values["dwell_seconds"], unit="s", order=40,
        validators=(_minimum(f"{context.kind.value}.dwell_nonnegative", "Dwell không được âm.", 0.0),),
        level=ParameterDisclosureLevel.ADVANCED,
    )
    if context.kind is DrillingFamilyEditorKind.DRILLING:
        return (
            _number_field(
                "peck_depth", "Chiều sâu peck", values["peck_depth"], unit=unit,
                order=10,
                validators=(_minimum("drill.peck_positive", "Peck phải > 0."),),
                level=ParameterDisclosureLevel.ADVANCED,
                applicable_when=FunctionEditorApplicability(
                    "cycle", ApplicabilityOperator.EQUALS, DrillingCycle.PECK_DRILL.value
                ),
            ),
            dwell,
            _choice_field(
                "retract_policy", "Mức rút giữa peck", values["retract_policy"],
                tuple(item.value for item in DrillRetractPolicy),
                tuple((item.value, item.value.replace("_", " ").title()) for item in DrillRetractPolicy),
                order=50, level=ParameterDisclosureLevel.ADVANCED,
            ),
            _read_only(
                "approach_policy", "Chính sách tiếp cận", values["approach_policy"],
                order=60, level=ParameterDisclosureLevel.ADVANCED,
            ),
        )
    if context.kind is DrillingFamilyEditorKind.TAPPING:
        return (
            _choice_field(
                "synchronization_policy", "Đồng bộ tapping", values["synchronization_policy"],
                tuple(item.value for item in TappingSynchronizationPolicy),
                tuple(
                    (item.value, item.value.title())
                    for item in TappingSynchronizationPolicy
                ),
                order=10, level=ParameterDisclosureLevel.ADVANCED,
            ),
            dwell,
        )
    retract_value = values["retract_policy"]
    return (
        _choice_field(
            "spindle_direction", "Chiều trục chính", values["spindle_direction"],
            tuple(item.value for item in SpindleDirection),
            tuple((item.value, item.value.title()) for item in SpindleDirection),
            order=10, level=ParameterDisclosureLevel.ADVANCED,
        ),
        dwell,
        _read_only(
            "retract_policy", "Chế độ rút dao", retract_value, order=50,
            level=ParameterDisclosureLevel.ADVANCED,
        ),
    )


def build_drilling_family_schema(
    context: DrillingFamilyEditorContext,
) -> FunctionEditorSchema:
    """Build compact shared sections configured by one actual domain strategy."""
    values = drilling_family_applied_values(context)
    unit = context.setup.wcs.origin.unit.value
    tools = _compatible_tools(context)
    tool_choices, tool_labels = _choice_data(
        tools, lambda item: item.assembly_id, lambda item: item.name
    )
    machine_choices, machine_labels = _choice_data(
        context.machine_definitions, lambda item: item.machine_id, lambda item: item.name
    )
    tool_text, _holder_text, tool_name = _tool_summaries(context)
    sections = (
        FunctionEditorSection(
            "basic",
            "BASIC",
            (
                FunctionEditorField(
                    "operation_name", "Tên nguyên công", FunctionEditorFieldKind.TEXT,
                    values["operation_name"], required=True, order=10,
                    binding_key="node.name", conversion=FunctionEditorValueConversion.TEXT,
                ),
                _read_only(
                    "operation_type",
                    "Loại nguyên công",
                    values["operation_type"],
                    order=20,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                FunctionEditorField(
                    "enabled", "Kích hoạt nguyên công", FunctionEditorFieldKind.CHECKBOX,
                    values["enabled"], required=True, order=30,
                    binding_key="operation.enabled",
                    disclosure_level=ParameterDisclosureLevel.ADVANCED,
                ),
            ),
            "Nhận diện và trạng thái operation.",
            order=10,
        ),
        FunctionEditorSection(
            "geometry",
            "GEOMETRY",
            (
                _read_only(
                    "geometry_summary", "Hole geometry", values["geometry_summary"],
                    order=10, source=FunctionEditorValueSource.GEOMETRY,
                    action_id="select_holes", action_label="Chọn lại",
                ),
                _read_only("hole_count", "Số lỗ", values["hole_count"], order=20),
                _read_only(
                    "selection_mode",
                    "Kiểu chọn",
                    values["selection_mode"],
                    order=30,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "machining_direction", "Hướng gia công", values["machining_direction"],
                    order=40, source=FunctionEditorValueSource.GEOMETRY,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "coordinate_system", "Hệ tọa độ", values["coordinate_system"],
                    order=50, source=FunctionEditorValueSource.SETUP,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
                _read_only(
                    "geometry_source_id", "Persistent source", values["geometry_source_id"],
                    order=60, source=FunctionEditorValueSource.GEOMETRY,
                    level=ParameterDisclosureLevel.EXPERT,
                ),
            ),
            f"{values['hole_count']} lỗ · {values['selection_mode']}",
            order=20,
        ),
        FunctionEditorSection(
            "tool",
            "TOOL",
            (
                _choice_field(
                    "tool_assembly_id", "Dao", values["tool_assembly_id"],
                    tool_choices, tool_labels, order=10,
                    binding_key="operation.tool_assembly",
                ),
                _read_only(
                    "tool_details", "Thông số dao", tool_text, order=20,
                    source=FunctionEditorValueSource.TOOL,
                ),
                _read_only(
                    "holder_summary", "Holder", values["holder_summary"], order=30,
                    source=FunctionEditorValueSource.TOOL,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
            ),
            tool_name,
            order=30,
        ),
        FunctionEditorSection(
            "process",
            "CYCLE / PROCESS",
            _process_fields(context, values, unit),
            "Chỉ các mode được domain v1 mô hình hóa.",
            order=40,
        ),
        FunctionEditorSection(
            "levels",
            "LEVELS / DEPTH",
            (
                _number_field(
                    "top_z", "Top / reference Z", values["top_z"], unit=unit, order=10,
                ),
                _number_field(
                    "final_depth", "Final depth Z", values["final_depth"], unit=unit,
                    order=20,
                    validators=(
                        _cross(
                            FunctionEditorValidationKind.LESS_THAN_FIELD,
                            "top_z",
                            f"{context.kind.value}.depth_invalid",
                            "Final depth phải thấp hơn Top Z.",
                        ),
                    ),
                ),
            ),
            f"Top {values['top_z']} · Final {values['final_depth']} {unit}",
            order=50,
        ),
        FunctionEditorSection(
            "cutting",
            "CUTTING PARAMETERS",
            _cutting_fields(context, values, unit),
            "Spindle, feed và coolant theo contract hiện có.",
            order=60,
        ),
        FunctionEditorSection(
            "linking",
            "CLEARANCE / RETRACT",
            (
                _number_field(
                    "clearance_height", "Clearance", values["clearance_height"],
                    unit=unit, order=10,
                    validators=(
                        _cross(
                            FunctionEditorValidationKind.GREATER_THAN_FIELD,
                            "retract_height",
                            f"{context.kind.value}.clearance_invalid",
                            "Clearance phải cao hơn Retract.",
                        ),
                    ),
                ),
                _number_field(
                    "retract_height", "Retract", values["retract_height"], unit=unit,
                    order=20,
                    validators=(
                        _cross(
                            FunctionEditorValidationKind.GREATER_THAN_FIELD,
                            "top_z",
                            f"{context.kind.value}.retract_invalid",
                            "Retract phải cao hơn Top Z.",
                        ),
                    ),
                ),
            ),
            f"C {values['clearance_height']} · R {values['retract_height']} {unit}",
            order=70,
        ),
        FunctionEditorSection(
            "advanced",
            "ADVANCED",
            _advanced_fields(context, values, unit),
            "Tham số ít dùng nhưng đã có domain support.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=80,
        ),
        FunctionEditorSection(
            "capability",
            "MACHINE / POST CAPABILITY",
            (
                _choice_field(
                    "machine_id", "Máy", values["machine_id"], machine_choices,
                    machine_labels, order=10, level=ParameterDisclosureLevel.ADVANCED,
                    binding_key="operation.machine_requirement",
                ),
                _read_only(
                    "capability_summary", "Capability", values["capability_summary"],
                    order=20, source=FunctionEditorValueSource.MACHINE,
                    level=ParameterDisclosureLevel.ADVANCED,
                ),
            ),
            "Controller-neutral; cycle code chỉ được quyết định ở Post.",
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            default_expanded=False,
            order=90,
        ),
        FunctionEditorSection(
            "expert",
            "EXPERT",
            (
                _number_field(
                    "tolerance", "Tolerance", values["tolerance"], unit=unit,
                    order=10,
                    validators=(
                        _minimum(
                            f"{context.kind.value}.tolerance_positive",
                            "Tolerance phải > 0.",
                        ),
                    ),
                    level=ParameterDisclosureLevel.EXPERT,
                ),
            ),
            "Tolerance hình học v1; không thêm tham số chuyên sâu giả.",
            disclosure_level=ParameterDisclosureLevel.EXPERT,
            default_expanded=False,
            order=100,
        ),
    )
    schema = FunctionEditorSchema(
        f"{context.kind.value}_production_9a6",
        FunctionEditorStrategyKey(f"{context.kind.strategy_key}_9a6"),
        FunctionEditorSummary(
            context.operation_name,
            f"{context.kind.title} · domain v1",
            tool_name,
            f"{_hole_count(context.hole_source)} lỗ",
            context.operation.artifact_state.status.value.upper(),
        ),
        sections,
        FunctionEditorFooter(
            (
                FunctionEditorAction.RESET_DRAFT,
                FunctionEditorAction.PREVIEW,
                FunctionEditorAction.VALIDATE,
                FunctionEditorAction.APPLY,
                FunctionEditorAction.CALCULATE,
                FunctionEditorAction.CLOSE,
            ),
            preview_supported=True,
            calculate_supported=True,
        ),
    )
    validate_drilling_family_schema_contract(schema, context.kind)
    return schema


def validate_drilling_family_schema_contract(
    schema: FunctionEditorSchema,
    kind: DrillingFamilyEditorKind,
) -> None:
    """Fail closed when a production mapping is missing or invents a field."""
    expected = _COMMON_FIELD_IDS | _VARIANT_FIELD_IDS[kind]
    actual = {field.field_id for field in schema.fields}
    if actual != expected:
        raise ValueError(
            "Drilling-family schema mapping mismatch; "
            f"missing={sorted(expected - actual)}, unsupported={sorted(actual - expected)}"
        )
    for field in schema.fields:
        if not field.binding_key:
            raise ValueError(f"Drilling-family field lacks binding: {field.field_id}")

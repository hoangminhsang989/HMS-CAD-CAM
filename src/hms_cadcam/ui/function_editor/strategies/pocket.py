"""Production Function Editor binding for the unchanged Pocket 2.5D v1 domain."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math

from hms_cadcam.cam.application import pocket_depth_levels
from hms_cadcam.cam.domain import (
    BoxStock,
    DirtyReason,
    FeedRate,
    FeedUnit,
    GeometryInputId,
    GeometryInputRole,
    GeometryReference,
    HolderDefinition,
    Length,
    MachineDefinition,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationGeometryInput,
    PocketCuttingDirection,
    PocketDepthDefinition,
    PocketEntryPolicy,
    PocketGeometryInput,
    PocketStrategy,
    Setup,
    SpindleSpeed,
    ToolAssembly,
    ToolAssemblyReference,
    ToolDefinition,
)
from hms_cadcam.ui.function_editor.model import (
    FunctionEditorAction,
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
from hms_cadcam.ui.localization import ui_text


@dataclass(frozen=True, slots=True)
class PocketEditorContext:
    """Native-free snapshot used to construct one Pocket production editor."""

    operation_name: str
    operation: Operation
    setup: Setup
    tool_assemblies: tuple[ToolAssembly, ...]
    tool_definitions: tuple[ToolDefinition, ...]
    holder_definitions: tuple[HolderDefinition, ...]
    machine_definitions: tuple[MachineDefinition, ...]
    geometry_reference: GeometryReference | None = None
    geometry_resolved: bool = False
    geometry_segment_count: int | None = None
    geometry_orientation: str = ""
    geometry_island_count: int | None = None
    geometry_diagnostic: str = ""


@dataclass(slots=True)
class PocketEditorDraftContext:
    """Typed transient boundary binding; never serialized or fingerprinted."""

    geometry_reference: GeometryReference | None
    pending_input_id: GeometryInputId | None = None


@dataclass(frozen=True, slots=True)
class PocketOperationUpdate:
    """Fully validated legacy-equivalent candidate for one atomic command."""

    operation_name: str
    operation: Operation
    strategy: PocketStrategy
    assembly: ToolAssembly
    tool: ToolDefinition | None
    machine: MachineDefinition
    geometry_reference: GeometryReference


_FIELD_IDS = frozenset(
    {
        "operation_name",
        "geometry_summary",
        "geometry_reference_id",
        "island_summary",
        "tool_assembly_id",
        "tool_details",
        "holder_summary",
        "machining_pattern",
        "cutting_direction",
        "stepover",
        "radial_stock_allowance",
        "cutting_feed_rate",
        "spindle_speed",
        "top_z",
        "bottom_z",
        "final_depth_summary",
        "stepdown",
        "level_count",
        "axial_allowance",
        "entry_policy",
        "plunge_feed_rate",
        "clearance_height",
        "retract_height",
        "machine_id",
        "enabled",
        "tolerance",
    }
)
_PARAMETER_KEYS = frozenset(
    {
        "unit",
        "top_z",
        "bottom_z",
        "axial_allowance",
        "stepover",
        "stepdown",
        "radial_stock_allowance",
        "clearance_height",
        "retract_height",
        "cutting_feed_rate",
        "plunge_feed_rate",
        "spindle_speed",
        "entry_policy",
        "cutting_direction",
        "tolerance",
    }
)


def _text(value: object, field_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_id} là bắt buộc.")
    return value.strip()


def _number(value: object, field_id: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_id} phải là số hữu hạn.")
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_id} phải là số hữu hạn.") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{field_id} phải là số hữu hạn.")
    return parsed


def _boolean(value: object, field_id: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field_id} phải là giá trị boolean.")
    return value


def _choice_data(
    values: tuple[object, ...], id_getter, name_getter
) -> tuple[tuple[str, ...], tuple[tuple[PresentationValue, str], ...]]:
    ordered = sorted(
        values, key=lambda item: (name_getter(item).casefold(), str(id_getter(item)))
    )
    choices = tuple(str(id_getter(item)) for item in ordered)
    labels = tuple(
        (str(id_getter(item)), f"{name_getter(item)} · {str(id_getter(item))[:8]}")
        for item in ordered
    )
    return choices, labels


def _selected_tool(
    context: PocketEditorContext,
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


def _tool_summaries(context: PocketEditorContext) -> tuple[str, str, str]:
    assembly, tool, holder = _selected_tool(context)
    if assembly is None:
        return "Tool Assembly không còn tồn tại", "Holder không khả dụng", "Tool bị thiếu"
    if tool is None:
        tool_text = f"{assembly.name} · Tool Definition bị thiếu"
    else:
        diameter = getattr(tool.cutting_geometry, "diameter", None)
        diameter_text = "?" if diameter is None else f"{diameter.value:g} {diameter.unit.value}"
        corner = getattr(tool.cutting_geometry, "corner_radius", None)
        corner_text = "" if corner is None else f" · R{corner.value:g}"
        tool_text = (
            f"{tool.name} · {tool.family.value} · D{diameter_text}{corner_text} · "
            f"usable {tool.usable_length.value:g} · stickout {assembly.stickout.value:g}"
        )
    holder_text = "Không có holder" if assembly.holder_id is None else (
        holder.name if holder is not None else "Holder bị thiếu hoặc stale"
    )
    return tool_text, holder_text, assembly.name


def _geometry_summary(context: PocketEditorContext) -> str:
    reference = context.geometry_reference
    if reference is None:
        return "Chưa chọn region · yêu cầu 1 outer loop kín LINE/ARC"
    island_count = context.geometry_island_count
    islands = "? Islands" if island_count is None else f"{island_count} Islands"
    status = "RESOLVED" if context.geometry_resolved else "MISSING/STALE/UNSUPPORTED"
    details: list[str] = ["1 Closed Region", islands, status]
    if context.geometry_segment_count is not None:
        details.insert(1, f"{context.geometry_segment_count} segments")
    if context.geometry_orientation:
        details.append(context.geometry_orientation)
    if context.geometry_diagnostic:
        details.append(context.geometry_diagnostic)
    return " · ".join(details)


def _island_summary(context: PocketEditorContext) -> str:
    count = context.geometry_island_count
    if count is None:
        return "Chưa xác định · Pocket v1 không hỗ trợ island"
    if count:
        return f"{count} island · UNSUPPORTED trong Pocket v1"
    return "0 island · Pocket v1 chỉ gia công một outer region"


def _parameter_defaults(context: PocketEditorContext) -> dict[str, str]:
    stock = context.setup.stock
    if not isinstance(stock, BoxStock):
        return {}
    unit = context.setup.wcs.origin.unit
    scale = 1.0 if unit.value == "mm" else 1.0 / 25.4
    top = stock.size_z.value
    return {
        "top_z": str(top),
        "bottom_z": str(top - scale),
        "axial_allowance": "0.0",
        "stepover": str(4.0 * scale),
        "stepdown": str(scale),
        "radial_stock_allowance": "0.0",
        "clearance_height": str(top + 5.0 * scale),
        "retract_height": str(top + 2.0 * scale),
        "cutting_feed_rate": str(500.0 * scale),
        "plunge_feed_rate": str(100.0 * scale),
        "spindle_speed": "1000.0",
        "tolerance": str(1.0e-7 * scale),
    }


def _parameter_data(context: PocketEditorContext) -> dict[str, object]:
    parameters = context.operation.parameters
    if parameters.strategy_key != "pocket_2_5d" or parameters.strategy_version != 1:
        raise ValueError("Operation không dùng Pocket strategy v1.")
    data = dict(parameters.values)
    if set(data) != _PARAMETER_KEYS:
        raise ValueError("Pocket operation parameters không khớp codec v1.")
    if data["unit"] != context.setup.wcs.origin.unit.value:
        raise ValueError("Pocket unit không khớp Setup WCS.")
    return data


def pocket_applied_values(
    context: PocketEditorContext,
) -> dict[str, PresentationValue]:
    """Map every production field to deterministic presentation primitives."""
    data = _parameter_data(context)
    if context.geometry_reference is not None:
        PocketStrategy.from_operation_parameters(
            context.operation.parameters, context.geometry_reference
        )
    tool_text, holder_text, _assembly_name = _tool_summaries(context)
    try:
        count = len(
            pocket_depth_levels(
                _number(data["top_z"], "top_z"),
                _number(data["bottom_z"], "bottom_z")
                + _number(data["axial_allowance"], "axial_allowance"),
                _number(data["stepdown"], "stepdown"),
                _number(data["tolerance"], "tolerance"),
            )
        )
    except ValueError:
        count = 0
    reference = context.geometry_reference
    return {
        "operation_name": context.operation_name,
        "geometry_summary": _geometry_summary(context),
        "geometry_reference_id": "" if reference is None else str(reference.reference_id),
        "island_summary": _island_summary(context),
        "tool_assembly_id": str(context.operation.tool_assembly.assembly_id),
        "tool_details": tool_text,
        "holder_summary": holder_text,
        "machining_pattern": "offset_inward",
        "cutting_direction": str(data["cutting_direction"]),
        "stepover": str(data["stepover"]),
        "radial_stock_allowance": str(data["radial_stock_allowance"]),
        "cutting_feed_rate": str(data["cutting_feed_rate"]),
        "spindle_speed": str(data["spindle_speed"]),
        "top_z": str(data["top_z"]),
        "bottom_z": str(data["bottom_z"]),
        "final_depth_summary": str(
            _number(data["bottom_z"], "bottom_z")
            + _number(data["axial_allowance"], "axial_allowance")
        ),
        "stepdown": str(data["stepdown"]),
        "level_count": str(count),
        "axial_allowance": str(data["axial_allowance"]),
        "entry_policy": str(data["entry_policy"]),
        "plunge_feed_rate": str(data["plunge_feed_rate"]),
        "clearance_height": str(data["clearance_height"]),
        "retract_height": str(data["retract_height"]),
        "machine_id": (
            str(context.operation.machine_requirement.machine_id)
            if context.operation.machine_requirement is not None
            else ""
        ),
        "enabled": context.operation.enabled,
        "tolerance": str(data["tolerance"]),
    }


def _complete_values(
    context: PocketEditorContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    complete = pocket_applied_values(context)
    complete.update(values)
    return complete


def _strategy_from_values(
    context: PocketEditorContext,
    reference: GeometryReference,
    values: Mapping[str, PresentationValue],
) -> PocketStrategy:
    complete = _complete_values(context, values)
    if _text(complete["machining_pattern"], "machining_pattern") != "offset_inward":
        raise ValueError("Pocket v1 chỉ hỗ trợ deterministic inward offset.")
    unit = context.setup.wcs.origin.unit
    feed_unit = FeedUnit.MM_PER_MINUTE if unit.value == "mm" else FeedUnit.INCH_PER_MINUTE
    return PocketStrategy(
        unit,
        PocketGeometryInput(reference, unit),
        PocketDepthDefinition(
            unit,
            Length(_number(complete["top_z"], "top_z"), unit),
            Length(_number(complete["bottom_z"], "bottom_z"), unit),
            Length(_number(complete["axial_allowance"], "axial_allowance"), unit),
        ),
        Length(_number(complete["stepover"], "stepover"), unit),
        Length(_number(complete["stepdown"], "stepdown"), unit),
        Length(
            _number(complete["radial_stock_allowance"], "radial_stock_allowance"),
            unit,
        ),
        Length(_number(complete["clearance_height"], "clearance_height"), unit),
        Length(_number(complete["retract_height"], "retract_height"), unit),
        FeedRate(
            _number(complete["cutting_feed_rate"], "cutting_feed_rate"), feed_unit
        ),
        FeedRate(
            _number(complete["plunge_feed_rate"], "plunge_feed_rate"), feed_unit
        ),
        SpindleSpeed(_number(complete["spindle_speed"], "spindle_speed")),
        PocketEntryPolicy(_text(complete["entry_policy"], "entry_policy")),
        PocketCuttingDirection(
            _text(complete["cutting_direction"], "cutting_direction")
        ),
        Length(_number(complete["tolerance"], "tolerance"), unit),
    )


def _geometry_reference_for_values(
    context: PocketEditorContext,
    draft: PocketEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> GeometryReference:
    complete = _complete_values(context, values)
    identity = _text(complete["geometry_reference_id"], "geometry_reference_id")
    reference = next(
        (
            item
            for item in (draft.geometry_reference, context.geometry_reference)
            if item is not None and str(item.reference_id) == identity
        ),
        None,
    )
    if reference is None:
        raise ValueError("Persistent Pocket region không khớp; hãy Select Geometry lại.")
    return reference


def prepare_pocket_update(
    context: PocketEditorContext,
    draft: PocketEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> PocketOperationUpdate:
    """Build the exact candidate used by the legacy Pocket Apply path."""
    if len(context.operation.geometry_inputs) > 1:
        raise ValueError(
            "Pocket v1 có duplicate/additional geometry input; không thể tự sửa âm thầm."
        )
    if (
        len(context.operation.geometry_inputs) == 1
        and context.operation.geometry_inputs[0].role is not GeometryInputRole.BOUNDARY
    ):
        raise ValueError("Pocket geometry input hiện tại không có role BOUNDARY.")
    complete = _complete_values(context, values)
    reference = _geometry_reference_for_values(context, draft, complete)
    strategy = _strategy_from_values(context, reference, complete)
    assembly_id = _text(complete["tool_assembly_id"], "tool_assembly_id")
    assembly = next(
        (item for item in context.tool_assemblies if str(item.assembly_id) == assembly_id),
        None,
    )
    if assembly is None:
        raise ValueError("Pocket thiếu Tool Assembly hợp lệ trong project.")
    machine_id = _text(complete["machine_id"], "machine_id")
    machine = next(
        (item for item in context.machine_definitions if str(item.machine_id) == machine_id),
        None,
    )
    if machine is None:
        raise ValueError("Pocket thiếu máy phay hợp lệ trong project.")
    tool = next(
        (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
        None,
    )
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (OperationCapability.MILLING,),
    )
    existing = (
        context.operation.geometry_inputs[0]
        if len(context.operation.geometry_inputs) == 1
        else None
    )
    if existing is not None and existing.reference.reference_id == reference.reference_id:
        input_id = existing.input_id
    else:
        input_id = draft.pending_input_id or GeometryInputId.new()
        draft.pending_input_id = input_id
    geometry_inputs = (
        OperationGeometryInput(
            input_id,
            GeometryInputRole.BOUNDARY,
            reference,
            True,
            reference.kind,
            0,
        ),
    )
    parameter_set = strategy.to_operation_parameters()
    tool_reference = ToolAssemblyReference.from_assembly(assembly)
    enabled = _boolean(complete["enabled"], "enabled")
    parameter_changed = parameter_set != context.operation.parameters
    geometry_changed = geometry_inputs != context.operation.geometry_inputs
    tool_changed = tool_reference != context.operation.tool_assembly
    machine_changed = requirement != context.operation.machine_requirement
    enabled_changed = enabled != context.operation.enabled
    changed = context.operation
    if any(
        (parameter_changed, geometry_changed, tool_changed, machine_changed, enabled_changed)
    ):
        if geometry_changed:
            reason = DirtyReason.GEOMETRY_CHANGED
        elif tool_changed:
            reason = DirtyReason.TOOL_CHANGED
        elif machine_changed:
            reason = DirtyReason.MACHINE_CHANGED
        elif parameter_changed:
            reason = DirtyReason.PARAMETERS_CHANGED
        else:
            reason = DirtyReason.UPSTREAM_CHANGED
        changed = replace(
            context.operation,
            parameters=parameter_set,
            tool_assembly=tool_reference,
            machine_requirement=requirement,
            geometry_inputs=geometry_inputs,
            enabled=enabled,
            revision=context.operation.revision.next(),
            artifact_state=context.operation.artifact_state.mark_dirty(reason),
        )
    return PocketOperationUpdate(
        _text(complete["operation_name"], "operation_name"),
        changed,
        strategy,
        assembly,
        tool,
        machine,
        reference,
    )


def validate_pocket_schema_contract(schema: FunctionEditorSchema) -> None:
    """Fail closed when a production field is missing, duplicate or invented."""
    actual = {field.field_id for field in schema.fields}
    if actual != _FIELD_IDS:
        raise ValueError(
            "Pocket schema mapping mismatch; "
            f"missing={sorted(_FIELD_IDS - actual)}, unsupported={sorted(actual - _FIELD_IDS)}"
        )
    for field in schema.fields:
        if not field.binding_key:
            raise ValueError(f"Pocket field lacks binding: {field.field_id}")


def _minimum(
    code: str, message: str, value: float = 1.0e-12
) -> FunctionEditorValidationRule:
    return FunctionEditorValidationRule(
        FunctionEditorValidationKind.MINIMUM, value, message, code
    )


def _number_field(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    unit: str,
    binding_key: str,
    order: int,
    default: PresentationValue = None,
    validators: tuple[FunctionEditorValidationRule, ...] = (),
    disclosure_level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC,
    help_text: str = "",
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.NUMBER,
        value,
        unit=unit,
        source=FunctionEditorValueSource.USER,
        default=default,
        default_label=("HMS Pocket v1 · Setup/Stock" if default is not None else ""),
        required=True,
        disclosure_level=disclosure_level,
        validators=validators,
        tooltip=help_text,
        help_text=help_text,
        help_key=f"pocket.{field_id}",
        order=order,
        binding_key=binding_key,
        conversion=FunctionEditorValueConversion.FLOAT,
        reset_behavior=FunctionEditorResetBehavior.APPLIED,
    )


def build_pocket_sections(
    context: PocketEditorContext,
) -> tuple[FunctionEditorSection, ...]:
    """Build deterministic operator-oriented sections over Pocket v1 only."""
    values = pocket_applied_values(context)
    defaults = _parameter_defaults(context)
    unit = context.setup.wcs.origin.unit.value
    feed_unit = "mm/min" if unit == "mm" else "in/min"
    tool_choices, tool_labels = _choice_data(
        tuple(context.tool_assemblies),
        lambda item: item.assembly_id,
        lambda item: item.name,
    )
    machine_choices, machine_labels = _choice_data(
        tuple(context.machine_definitions),
        lambda item: item.machine_id,
        lambda item: item.name,
    )
    basic = FunctionEditorSection(
        "basic",
        "BASIC",
        (
            FunctionEditorField(
                "operation_name",
                "Tên operation",
                FunctionEditorFieldKind.TEXT,
                values["operation_name"],
                required=True,
                tooltip="Tên hiển thị; OperationId vẫn là identity.",
                help_key="pocket.operation_name",
                order=10,
                binding_key="node.name",
                conversion=FunctionEditorValueConversion.TEXT,
            ),
        ),
        "Các quyết định Pocket cốt lõi nằm trong Geometry, Tool, Cutting, Levels và Entry.",
        order=10,
    )
    geometry = FunctionEditorSection(
        "geometry",
        "GEOMETRY",
        (
            FunctionEditorField(
                "geometry_summary",
                "Pocket region",
                FunctionEditorFieldKind.READ_ONLY,
                values["geometry_summary"],
                source=FunctionEditorValueSource.GEOMETRY,
                required=True,
                tooltip="Pocket v1 nhận đúng một outer loop kín LINE/ARC.",
                help_text="Select/Rebind dùng GeometryReference typed; không giữ OCP object.",
                help_key="pocket.geometry_summary",
                order=10,
                binding_key="derived.geometry_summary",
                action_id="select_geometry",
                action_label="Select",
            ),
            FunctionEditorField(
                "island_summary",
                "Islands",
                FunctionEditorFieldKind.READ_ONLY,
                values["island_summary"],
                source=FunctionEditorValueSource.GEOMETRY,
                tooltip="Pocket v1 fail-closed khi profile có inner loop; UI không tự suy ra island.",
                help_key="pocket.islands",
                order=20,
                binding_key="derived.island_summary",
            ),
            FunctionEditorField(
                "geometry_reference_id",
                "Geometry identity",
                FunctionEditorFieldKind.READ_ONLY,
                values["geometry_reference_id"],
                source=FunctionEditorValueSource.GEOMETRY,
                required=True,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                tooltip="ID định kiểu ổn định; không phải tên hiển thị hay đối tượng OCP.",
                help_key="pocket.geometry_identity",
                order=30,
                binding_key="operation.geometry_inputs.boundary",
            ),
        ),
        "Một vùng kín; đảo được chẩn đoán theo nguyên tắc chặn an toàn của hợp đồng v1.",
        order=20,
    )
    tool = FunctionEditorSection(
        "tool",
        "TOOL",
        (
            FunctionEditorField(
                "tool_assembly_id",
                "Tool Assembly",
                FunctionEditorFieldKind.CHOICE,
                values["tool_assembly_id"],
                required=True,
                choices=tool_choices,
                choice_labels=tool_labels,
                tooltip="Chọn project-owned Tool Assembly; hình học dao là read-only.",
                help_key="pocket.tool_assembly",
                order=10,
                binding_key="operation.tool_assembly",
            ),
            FunctionEditorField(
                "tool_details",
                "Tool / Shank",
                FunctionEditorFieldKind.READ_ONLY,
                values["tool_details"],
                source=FunctionEditorValueSource.TOOL,
                help_key="pocket.tool_details",
                order=20,
                binding_key="derived.tool_details",
            ),
            FunctionEditorField(
                "holder_summary",
                "Holder",
                FunctionEditorFieldKind.READ_ONLY,
                values["holder_summary"],
                source=FunctionEditorValueSource.TOOL,
                help_key="pocket.holder",
                order=30,
                binding_key="derived.holder_summary",
            ),
        ),
        "Tool Library là nguồn chân lý; Pocket v1 chỉ hỗ trợ END_MILL hợp lệ.",
        order=30,
    )
    cutting = FunctionEditorSection(
        "cutting",
        "CUTTING",
        (
            FunctionEditorField(
                "machining_pattern",
                "Machining pattern",
                FunctionEditorFieldKind.READ_ONLY,
                values["machining_pattern"],
                source=FunctionEditorValueSource.DEFAULT,
                tooltip="Generator Pocket v1 chỉ có deterministic inward offset loops.",
                help_key="pocket.pattern",
                order=10,
                binding_key="derived.machining_pattern",
            ),
            FunctionEditorField(
                "cutting_direction",
                "Hướng cắt",
                FunctionEditorFieldKind.CHOICE,
                values["cutting_direction"],
                required=True,
                choices=tuple(item.value for item in PocketCuttingDirection),
                choice_labels=tuple(
                    (item.value, "Climb" if item is PocketCuttingDirection.CLIMB else "Conventional")
                    for item in PocketCuttingDirection
                ),
                tooltip="Đổi traversal của offset loops; không tự đảo geometry nguồn.",
                help_key="pocket.cutting_direction",
                order=20,
                binding_key="parameters.cutting_direction",
            ),
            _number_field(
                "stepover",
                "Stepover",
                values["stepover"],
                unit=unit,
                binding_key="parameters.stepover",
                order=30,
                default=defaults.get("stepover"),
                validators=(_minimum("pocket.stepover_positive", "Stepover phải lớn hơn 0."),),
                help_text="Khoảng cách tuyệt đối; miền yêu cầu nhỏ hơn đường kính dao.",
            ),
            _number_field(
                "radial_stock_allowance",
                "Wall Stock Allowance",
                values["radial_stock_allowance"],
                unit=unit,
                binding_key="parameters.radial_stock_allowance",
                order=40,
                default=defaults.get("radial_stock_allowance"),
                validators=(
                    _minimum(
                        "pocket.radial_allowance_nonnegative",
                        "Lượng dư thành không được âm.",
                        0.0,
                    ),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
            ),
            _number_field(
                "cutting_feed_rate",
                "Feed cắt",
                values["cutting_feed_rate"],
                unit=feed_unit,
                binding_key="parameters.cutting_feed_rate",
                order=50,
                default=defaults.get("cutting_feed_rate"),
                validators=(_minimum("pocket.feed_positive", "Feed cắt phải lớn hơn 0."),),
            ),
            _number_field(
                "spindle_speed",
                "Tốc độ trục chính",
                values["spindle_speed"],
                unit="RPM",
                binding_key="parameters.spindle_speed",
                order=60,
                default=defaults.get("spindle_speed"),
                validators=(_minimum("pocket.spindle_positive", "Spindle RPM phải lớn hơn 0."),),
            ),
        ),
        "Offset pattern, direction, stepover, wall allowance và tốc độ cắt.",
        order=40,
    )
    levels = FunctionEditorSection(
        "levels",
        "LEVELS",
        (
            _number_field(
                "top_z",
                "Top",
                values["top_z"],
                unit=unit,
                binding_key="parameters.top_z",
                order=10,
                default=defaults.get("top_z"),
                help_text="Tọa độ tuyệt đối trong WCS thiết lập; phải khớp mặt phẳng của biên.",
            ),
            _number_field(
                "bottom_z",
                "Depth",
                values["bottom_z"],
                unit=unit,
                binding_key="parameters.bottom_z",
                order=20,
                default=defaults.get("bottom_z"),
                validators=(
                    FunctionEditorValidationRule(
                        FunctionEditorValidationKind.LESS_THAN_FIELD,
                        "top_z",
                        "Bottom Z phải thấp hơn Top Z.",
                        "pocket.bottom_below_top",
                    ),
                ),
                help_text="Nominal bottom trong Setup WCS; floor allowance nâng final cutter Z.",
            ),
            _number_field(
                "stepdown",
                "Stepdown",
                values["stepdown"],
                unit=unit,
                binding_key="parameters.stepdown",
                order=30,
                default=defaults.get("stepdown"),
                validators=(_minimum("pocket.stepdown_positive", "Stepdown phải lớn hơn 0."),),
            ),
            _number_field(
                "axial_allowance",
                "Floor Stock Allowance",
                values["axial_allowance"],
                unit=unit,
                binding_key="parameters.axial_allowance",
                order=40,
                default=defaults.get("axial_allowance"),
                validators=(
                    _minimum(
                        "pocket.axial_allowance_nonnegative",
                        "Lượng dư đáy không được âm.",
                        0.0,
                    ),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
            ),
            FunctionEditorField(
                "final_depth_summary",
                "Final cutter Z",
                FunctionEditorFieldKind.READ_ONLY,
                values["final_depth_summary"],
                unit=unit,
                source=FunctionEditorValueSource.DERIVED,
                tooltip="Derived = Bottom Z + floor allowance; không phải input thứ hai.",
                help_key="pocket.final_depth",
                order=50,
                binding_key="derived.final_depth",
            ),
            FunctionEditorField(
                "level_count",
                "Số lớp đã Apply",
                FunctionEditorFieldKind.READ_ONLY,
                values["level_count"],
                source=FunctionEditorValueSource.DERIVED,
                tooltip="Derived theo thuật toán pocket_depth_levels hiện có.",
                help_key="pocket.level_count",
                order=60,
                binding_key="derived.level_count",
            ),
        ),
        "Absolute Setup-WCS Z, chia lớp và hai allowance độc lập của domain v1.",
        order=50,
    )
    entry = FunctionEditorSection(
        "entry",
        "ENTRY",
        (
            FunctionEditorField(
                "entry_policy",
                "Entry Method",
                FunctionEditorFieldKind.CHOICE,
                values["entry_policy"],
                required=True,
                choices=tuple(item.value for item in PocketEntryPolicy),
                choice_labels=((PocketEntryPolicy.VERTICAL_PLUNGE.value, "Vertical plunge"),),
                tooltip="Pocket v1 chỉ hỗ trợ vertical plunge; không có ramp/helix/pre-drill.",
                help_key="pocket.entry_policy",
                order=10,
                binding_key="parameters.entry_policy",
            ),
            _number_field(
                "plunge_feed_rate",
                "Plunge feed",
                values["plunge_feed_rate"],
                unit=feed_unit,
                binding_key="parameters.plunge_feed_rate",
                order=20,
                default=defaults.get("plunge_feed_rate"),
                validators=(_minimum("pocket.plunge_positive", "Plunge feed phải lớn hơn 0."),),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_text="Generator plunge thẳng tại start của từng offset loop.",
            ),
        ),
        "Entry policy thực sự tồn tại; khả năng plunge-safe vẫn do generator hiện có kiểm tra.",
        order=60,
    )
    linking = FunctionEditorSection(
        "linking",
        "LINKING",
        (
            _number_field(
                "clearance_height",
                "Clearance",
                values["clearance_height"],
                unit=unit,
                binding_key="parameters.clearance_height",
                order=10,
                default=defaults.get("clearance_height"),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_text="Explicit operation value trong Setup WCS; không phải machine Z.",
            ),
            _number_field(
                "retract_height",
                "Retract",
                values["retract_height"],
                unit=unit,
                binding_key="parameters.retract_height",
                order=20,
                default=defaults.get("retract_height"),
                validators=(
                    FunctionEditorValidationRule(
                        FunctionEditorValidationKind.GREATER_THAN_FIELD,
                        "top_z",
                        "Z rút dao phải cao hơn Z đỉnh.",
                        "pocket.retract_above_top",
                    ),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_text="Domain yêu cầu Clearance >= Retract > Top.",
            ),
        ),
        "Safe motion v1 chỉ có explicit Clearance và Retract trong Setup WCS.",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=70,
    )
    advanced = FunctionEditorSection(
        "advanced",
        "ADVANCED",
        (
            FunctionEditorField(
                "machine_id",
                "Máy",
                FunctionEditorFieldKind.CHOICE,
                values["machine_id"],
                required=True,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                choices=machine_choices,
                choice_labels=machine_labels,
                tooltip="Machine requirement hiện có; domain kiểm tra capability/feed/spindle.",
                help_key="pocket.machine",
                order=10,
                binding_key="operation.machine_requirement",
            ),
            FunctionEditorField(
                "enabled",
                "Operation được bật",
                FunctionEditorFieldKind.CHECKBOX,
                values["enabled"],
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_key="pocket.enabled",
                order=20,
                binding_key="operation.enabled",
                conversion=FunctionEditorValueConversion.BOOLEAN,
            ),
        ),
        "Machine binding và trạng thái operation.",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=80,
    )
    expert = FunctionEditorSection(
        "expert",
        "EXPERT",
        (
            _number_field(
                "tolerance",
                "Algorithm tolerance",
                values["tolerance"],
                unit=unit,
                binding_key="parameters.tolerance",
                order=10,
                default=defaults.get("tolerance"),
                validators=(_minimum("pocket.tolerance_positive", "Tolerance phải lớn hơn 0."),),
                disclosure_level=ParameterDisclosureLevel.EXPERT,
                help_text="Precision của offset/depth algorithm; giá trị nhỏ có thể tăng chi phí tính.",
            ),
        ),
        "Precision duy nhất thực sự tồn tại trong Pocket v1.",
        disclosure_level=ParameterDisclosureLevel.EXPERT,
        default_expanded=False,
        order=90,
    )
    return (basic, geometry, tool, cutting, levels, entry, linking, advanced, expert)


def pocket_footer() -> FunctionEditorFooter:
    return FunctionEditorFooter(
        actions=(
            FunctionEditorAction.RESET_DRAFT,
            FunctionEditorAction.PREVIEW,
            FunctionEditorAction.VALIDATE,
            FunctionEditorAction.APPLY,
            FunctionEditorAction.CALCULATE,
            FunctionEditorAction.CLOSE,
        ),
        preview_supported=True,
        calculate_supported=True,
        apply_supported=True,
    )


def build_pocket_schema(context: PocketEditorContext) -> FunctionEditorSchema:
    """Build the production Pocket schema without changing domain semantics."""
    data = _parameter_data(context)
    if context.geometry_reference is not None:
        PocketStrategy.from_operation_parameters(
            context.operation.parameters, context.geometry_reference
        )
    _tool_text, _holder_text, assembly_name = _tool_summaries(context)
    geometry = _geometry_summary(context)
    schema = FunctionEditorSchema(
        "pocket_production_9a5_3",
        FunctionEditorStrategyKey("pocket_2_5d_9a5_3"),
        FunctionEditorSummary(
            context.operation_name,
            (
                f"Hốc 2.5D · Bù · {ui_text(data['cutting_direction'])} · "
                f"Bước ngang {_number(data['stepover'], 'stepover'):g} · "
                "Z đỉnh/Chiều sâu "
                f"{_number(data['top_z'], 'top_z'):g}/"
                f"{_number(data['bottom_z'], 'bottom_z'):g} {data['unit']}"
            ),
            tool=assembly_name,
            geometry=geometry,
            operation_status=context.operation.artifact_state.status.value.upper(),
        ),
        build_pocket_sections(context),
        pocket_footer(),
    )
    validate_pocket_schema_contract(schema)
    return schema


__all__ = [
    "PocketEditorContext",
    "PocketEditorDraftContext",
    "PocketOperationUpdate",
    "build_pocket_schema",
    "pocket_applied_values",
    "prepare_pocket_update",
    "validate_pocket_schema_contract",
]

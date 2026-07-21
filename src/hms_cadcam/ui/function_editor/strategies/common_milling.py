"""Shared typed bindings for the two production Facing editor variants."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import math

from hms_cadcam.cam.domain import (
    BoxStock,
    DirtyReason,
    FacingBoundarySource,
    FacingCutDirection,
    FacingParameters,
    FeedRate,
    FeedUnit,
    GeometryInputId,
    GeometryInputRole,
    GeometryReference,
    GeometryReferenceKind,
    HolderDefinition,
    Length,
    MachineDefinition,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationGeometryInput,
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
    FunctionEditorValidationKind,
    FunctionEditorValidationRule,
    FunctionEditorValueConversion,
    FunctionEditorValueSource,
    ParameterDisclosureLevel,
    PresentationValue,
)
from hms_cadcam.ui.function_editor.schema import FunctionEditorSchema


class FacingEditorVariant(StrEnum):
    """Presentation variants over the unchanged ``facing_2_5d`` contract."""

    STOCK = "stock"
    PLANAR_FACE = "planar_face"

    @property
    def boundary_source(self) -> FacingBoundarySource:
        return (
            FacingBoundarySource.STOCK_BOX
            if self is FacingEditorVariant.STOCK
            else FacingBoundarySource.PLANAR_FACE
        )


@dataclass(frozen=True, slots=True)
class FacingEditorContext:
    """Native-free project snapshot needed to construct a production editor."""

    operation_name: str
    operation: Operation
    setup: Setup
    tool_assemblies: tuple[ToolAssembly, ...]
    tool_definitions: tuple[ToolDefinition, ...]
    holder_definitions: tuple[HolderDefinition, ...]
    machine_definitions: tuple[MachineDefinition, ...]
    geometry_reference: GeometryReference | None = None
    geometry_resolved: bool = False


@dataclass(slots=True)
class FacingEditorDraftContext:
    """Transient typed geometry selection; never serialized or fingerprinted."""

    geometry_reference: GeometryReference | None
    pending_input_id: GeometryInputId | None = None


@dataclass(frozen=True, slots=True)
class FacingOperationUpdate:
    """Fully validated candidate consumed by one atomic application command."""

    operation_name: str
    operation: Operation
    parameters: FacingParameters
    assembly: ToolAssembly
    tool: ToolDefinition | None
    machine: MachineDefinition
    geometry_reference: GeometryReference | None


_BASE_FIELD_IDS = frozenset(
    {
        "operation_name",
        "geometry_summary",
        "tool_assembly_id",
        "tool_details",
        "holder_summary",
        "top_height",
        "target_height",
        "stock_allowance",
        "stepdown",
        "stepover",
        "feed_rate",
        "plunge_feed_rate",
        "spindle_speed",
        "direction",
        "raster_angle_degrees",
        "clearance_height",
        "retract_height",
        "machine_id",
        "enabled",
    }
)
_STOCK_FIELD_IDS = _BASE_FIELD_IDS | {"geometry_bounds", "overtravel"}
_PLANAR_FIELD_IDS = _BASE_FIELD_IDS | {"geometry_reference_id"}


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


def _parameter_defaults(context: FacingEditorContext) -> dict[str, str]:
    stock = context.setup.stock
    if not isinstance(stock, BoxStock):
        return {}
    unit = context.setup.wcs.origin.unit
    scale = 1.0 if unit.value == "mm" else 1.0 / 25.4
    top = stock.size_z.value
    return {
        "top_height": str(top),
        "target_height": str(top - scale),
        "stock_allowance": "0.0",
        "stepdown": str(scale),
        "stepover": str(5.0 * scale),
        "feed_rate": str(500.0 * scale),
        "plunge_feed_rate": str(100.0 * scale),
        "spindle_speed": "1000.0",
        "raster_angle_degrees": "0.0",
        "clearance_height": str(top + 5.0 * scale),
        "retract_height": str(top + 2.0 * scale),
        "overtravel": str(scale),
    }


def _choice_data(
    values: tuple[object, ...], id_getter, name_getter
) -> tuple[tuple[str, ...], tuple[tuple[PresentationValue, str], ...]]:
    ordered = sorted(values, key=lambda item: (name_getter(item).casefold(), str(id_getter(item))))
    choices = tuple(str(id_getter(item)) for item in ordered)
    labels = tuple(
        (str(id_getter(item)), f"{name_getter(item)} · {str(id_getter(item))[:8]}")
        for item in ordered
    )
    return choices, labels


def _tool_details(context: FacingEditorContext) -> tuple[str, str]:
    assembly = next(
        (
            item
            for item in context.tool_assemblies
            if item.assembly_id == context.operation.tool_assembly.assembly_id
        ),
        None,
    )
    if assembly is None:
        return "Tool Assembly không còn tồn tại", "Holder không khả dụng"
    tool = next(
        (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
        None,
    )
    if tool is None:
        tool_text = f"{assembly.name} · Tool Definition bị thiếu"
    else:
        diameter = getattr(tool.cutting_geometry, "diameter", None)
        diameter_text = "?" if diameter is None else f"{diameter.value:g} {diameter.unit.value}"
        tool_text = (
            f"{tool.name} · {tool.family.value} · D{diameter_text} · "
            f"usable {tool.usable_length.value:g} · stickout {assembly.stickout.value:g}"
        )
    holder = next(
        (
            item
            for item in context.holder_definitions
            if assembly.holder_id is not None and item.holder_id == assembly.holder_id
        ),
        None,
    )
    holder_text = "Không có holder" if assembly.holder_id is None else (
        holder.name if holder is not None else "Holder bị thiếu hoặc stale"
    )
    return tool_text, holder_text


def _geometry_values(
    context: FacingEditorContext, variant: FacingEditorVariant
) -> tuple[str, str]:
    if variant is FacingEditorVariant.STOCK:
        stock = context.setup.stock
        if isinstance(stock, BoxStock):
            return (
                "Toàn bộ mặt trên Stock BOX",
                f"{stock.size_x.value:g} × {stock.size_y.value:g} × {stock.size_z.value:g} {stock.size_x.unit.value}",
            )
        return "Stock không hỗ trợ Facing v1", "—"
    reference = context.geometry_reference
    if reference is None:
        return "Chưa chọn planar FACE", ""
    status = "RESOLVED" if context.geometry_resolved else "STALE/INVALID"
    return (
        f"{reference.hint or 'Planar FACE'} · {status}",
        str(reference.reference_id),
    )


def facing_applied_values(
    context: FacingEditorContext, variant: FacingEditorVariant
) -> dict[str, PresentationValue]:
    """Convert an operation snapshot to deterministic presentation primitives."""
    parameters = FacingParameters.from_operation_parameters(context.operation.parameters)
    tool_text, holder_text = _tool_details(context)
    geometry_summary, geometry_detail = _geometry_values(context, variant)
    values: dict[str, PresentationValue] = {
        "operation_name": context.operation_name,
        "geometry_summary": geometry_summary,
        "tool_assembly_id": str(context.operation.tool_assembly.assembly_id),
        "tool_details": tool_text,
        "holder_summary": holder_text,
        "top_height": str(parameters.top_height.value),
        "target_height": str(parameters.target_height.value),
        "stock_allowance": str(parameters.stock_allowance.value),
        "stepdown": str(parameters.stepdown.value),
        "stepover": str(parameters.stepover.value),
        "feed_rate": str(parameters.feed_rate.value),
        "plunge_feed_rate": str(parameters.plunge_feed_rate.value),
        "spindle_speed": str(parameters.spindle_speed.value),
        "direction": parameters.direction.value,
        "raster_angle_degrees": str(parameters.raster_angle_degrees),
        "clearance_height": str(parameters.clearance_height.value),
        "retract_height": str(parameters.retract_height.value),
        "machine_id": (
            str(context.operation.machine_requirement.machine_id)
            if context.operation.machine_requirement is not None
            else ""
        ),
        "enabled": context.operation.enabled,
    }
    if variant is FacingEditorVariant.STOCK:
        values["geometry_bounds"] = geometry_detail
        values["overtravel"] = str(parameters.overtravel.value)
    else:
        values["geometry_reference_id"] = geometry_detail
    return values


def _parameters_from_values(
    context: FacingEditorContext,
    variant: FacingEditorVariant,
    values: Mapping[str, PresentationValue],
) -> FacingParameters:
    current = FacingParameters.from_operation_parameters(context.operation.parameters)
    unit = context.setup.wcs.origin.unit
    feed_unit = (
        FeedUnit.MM_PER_MINUTE if unit.value == "mm" else FeedUnit.INCH_PER_MINUTE
    )
    overtravel = (
        Length(_number(values["overtravel"], "overtravel"), unit)
        if "overtravel" in values
        else current.overtravel
    )
    return FacingParameters(
        unit,
        variant.boundary_source,
        Length(_number(values["top_height"], "top_height"), unit),
        Length(_number(values["target_height"], "target_height"), unit),
        Length(_number(values["stepdown"], "stepdown"), unit),
        Length(_number(values["stepover"], "stepover"), unit),
        Length(_number(values["stock_allowance"], "stock_allowance"), unit),
        Length(_number(values["clearance_height"], "clearance_height"), unit),
        Length(_number(values["retract_height"], "retract_height"), unit),
        FeedRate(_number(values["feed_rate"], "feed_rate"), feed_unit),
        FeedRate(_number(values["plunge_feed_rate"], "plunge_feed_rate"), feed_unit),
        SpindleSpeed(_number(values["spindle_speed"], "spindle_speed")),
        FacingCutDirection(_text(values["direction"], "direction")),
        _number(values["raster_angle_degrees"], "raster_angle_degrees"),
        overtravel,
    )


def _geometry_reference_for_values(
    context: FacingEditorContext,
    draft: FacingEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> GeometryReference | None:
    if "geometry_reference_id" not in values:
        return None
    identity = _text(values["geometry_reference_id"], "geometry_reference_id")
    candidates = (draft.geometry_reference, context.geometry_reference)
    reference = next(
        (
            item
            for item in candidates
            if item is not None and str(item.reference_id) == identity
        ),
        None,
    )
    if reference is None or reference.kind is not GeometryReferenceKind.FACE:
        raise ValueError("Planar Face Facing thiếu persistent FACE hợp lệ.")
    return reference


def prepare_facing_update(
    context: FacingEditorContext,
    draft: FacingEditorDraftContext,
    variant: FacingEditorVariant,
    values: Mapping[str, PresentationValue],
) -> FacingOperationUpdate:
    """Build the exact legacy-equivalent operation candidate without mutation."""
    parameters = _parameters_from_values(context, variant, values)
    assembly_id = _text(values["tool_assembly_id"], "tool_assembly_id")
    assembly = next(
        (item for item in context.tool_assemblies if str(item.assembly_id) == assembly_id),
        None,
    )
    if assembly is None:
        raise ValueError("Tool Assembly không còn tồn tại trong project.")
    machine_id = _text(values["machine_id"], "machine_id")
    machine = next(
        (item for item in context.machine_definitions if str(item.machine_id) == machine_id),
        None,
    )
    if machine is None:
        raise ValueError("Máy đã chọn không còn tồn tại trong project.")
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
    reference = _geometry_reference_for_values(context, draft, values)
    geometry_inputs: tuple[OperationGeometryInput, ...] = ()
    if variant is FacingEditorVariant.PLANAR_FACE:
        assert reference is not None
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
                GeometryReferenceKind.FACE,
                0,
            ),
        )
    parameter_set = parameters.to_operation_parameters()
    tool_reference = ToolAssemblyReference.from_assembly(assembly)
    enabled = _boolean(values["enabled"], "enabled")
    inputs_changed = (
        parameter_set != context.operation.parameters
        or tool_reference != context.operation.tool_assembly
        or requirement != context.operation.machine_requirement
        or geometry_inputs != context.operation.geometry_inputs
    )
    enabled_changed = enabled != context.operation.enabled
    changed = context.operation
    if inputs_changed or enabled_changed:
        reason = (
            DirtyReason.PARAMETERS_CHANGED
            if inputs_changed
            else DirtyReason.UPSTREAM_CHANGED
        )
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
    return FacingOperationUpdate(
        _text(values["operation_name"], "operation_name"),
        changed,
        parameters,
        assembly,
        tool,
        machine,
        reference,
    )


def validate_facing_schema_contract(
    schema: FunctionEditorSchema, variant: FacingEditorVariant
) -> None:
    """Reject missing, duplicate or unsupported production field mappings."""
    expected = _STOCK_FIELD_IDS if variant is FacingEditorVariant.STOCK else _PLANAR_FIELD_IDS
    actual = {field.field_id for field in schema.fields}
    if actual != expected:
        missing = sorted(expected - actual)
        unsupported = sorted(actual - expected)
        raise ValueError(
            f"Facing schema mapping mismatch; missing={missing}, unsupported={unsupported}"
        )
    for field in schema.fields:
        if not field.binding_key:
            raise ValueError(f"Facing field lacks binding: {field.field_id}")


def _positive(code: str, message: str) -> FunctionEditorValidationRule:
    return FunctionEditorValidationRule(
        FunctionEditorValidationKind.MINIMUM, 1.0e-12, message, code
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
    source: FunctionEditorValueSource = FunctionEditorValueSource.USER,
    read_only: bool = False,
    help_text: str = "",
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.READ_ONLY if read_only else FunctionEditorFieldKind.NUMBER,
        value,
        unit=unit,
        source=source,
        default=default,
        default_label="HMS Facing v1" if default is not None else "",
        required=True,
        validators=validators,
        tooltip=help_text,
        help_text=help_text,
        help_key=f"facing.{field_id}",
        order=order,
        binding_key=binding_key,
        conversion=FunctionEditorValueConversion.FLOAT,
        reset_behavior=FunctionEditorResetBehavior.APPLIED,
    )


def build_facing_sections(
    context: FacingEditorContext, variant: FacingEditorVariant
) -> tuple[FunctionEditorSection, ...]:
    """Build shared WorkNC-inspired groups without inventing domain fields."""
    values = facing_applied_values(context, variant)
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
                "operation_name", "Tên operation", FunctionEditorFieldKind.TEXT,
                values["operation_name"], required=True,
                tooltip="Tên hiển thị trong Operation Manager; identity vẫn là OperationId.",
                help_key="facing.operation_name", order=10,
                binding_key="node.name", conversion=FunctionEditorValueConversion.TEXT,
            ),
        ),
        "Thông tin nhận diện operation.",
        order=10,
    )
    geometry_fields = [
        FunctionEditorField(
            "geometry_summary", "Vùng gia công", FunctionEditorFieldKind.READ_ONLY,
            values["geometry_summary"],
            source=(FunctionEditorValueSource.STOCK if variant is FacingEditorVariant.STOCK
                    else FunctionEditorValueSource.GEOMETRY),
            tooltip=("Mặt trên Stock BOX hiện hành." if variant is FacingEditorVariant.STOCK
                     else "Chọn đúng một planar FACE trong viewport rồi dùng Select."),
            help_key="facing.geometry_summary", order=10,
            binding_key="derived.geometry_summary",
            action_id=("select_geometry" if variant is FacingEditorVariant.PLANAR_FACE else ""),
            action_label=("Select" if variant is FacingEditorVariant.PLANAR_FACE else ""),
        )
    ]
    if variant is FacingEditorVariant.STOCK:
        geometry_fields.append(
            FunctionEditorField(
                "geometry_bounds", "Kích thước Stock", FunctionEditorFieldKind.READ_ONLY,
                values["geometry_bounds"], source=FunctionEditorValueSource.STOCK,
                help_key="facing.geometry_bounds", order=20,
                binding_key="derived.stock_bounds",
            )
        )
    else:
        geometry_fields.append(
            FunctionEditorField(
                "geometry_reference_id", "Geometry identity", FunctionEditorFieldKind.READ_ONLY,
                values["geometry_reference_id"], source=FunctionEditorValueSource.GEOMETRY,
                required=True, disclosure_level=ParameterDisclosureLevel.ADVANCED,
                tooltip="Persistent GeometryReferenceId; không chứa OCP object.",
                help_key="facing.geometry_identity", order=20,
                binding_key="operation.geometry_inputs.boundary",
            )
        )
    geometry = FunctionEditorSection(
        "geometry", "GEOMETRY", tuple(geometry_fields),
        "Nguồn và trạng thái vùng gia công.", order=20,
    )
    tool = FunctionEditorSection(
        "tool", "TOOL",
        (
            FunctionEditorField(
                "tool_assembly_id", "Tool Assembly", FunctionEditorFieldKind.CHOICE,
                values["tool_assembly_id"], source=FunctionEditorValueSource.USER,
                required=True, choices=tool_choices, choice_labels=tool_labels,
                tooltip="Chọn Tool Assembly project-owned; thay đổi chỉ nằm trong draft.",
                help_key="facing.tool_assembly", order=10,
                binding_key="operation.tool_assembly",
            ),
            FunctionEditorField(
                "tool_details", "Dao và shank", FunctionEditorFieldKind.READ_ONLY,
                values["tool_details"], source=FunctionEditorValueSource.TOOL,
                help_key="facing.tool_details", order=20,
                binding_key="derived.tool_details",
            ),
            FunctionEditorField(
                "holder_summary", "Holder", FunctionEditorFieldKind.READ_ONLY,
                values["holder_summary"], source=FunctionEditorValueSource.TOOL,
                help_key="facing.holder", order=30,
                binding_key="derived.holder_summary",
            ),
        ),
        "Tool Assembly và chi tiết read-only từ Tool Library.", order=30,
    )
    cutting = FunctionEditorSection(
        "cutting", "CUTTING",
        (
            _number_field(
                "stepover", "Khoảng cách đường cắt", values["stepover"], unit=unit,
                binding_key="parameters.stepover", order=10,
                default=defaults.get("stepover"),
                validators=(_positive("facing.stepover_positive", "Stepover phải lớn hơn 0."),),
                help_text="Stepover không được lớn hơn đường kính dao.",
            ),
            FunctionEditorField(
                "direction", "Hướng cắt", FunctionEditorFieldKind.CHOICE,
                values["direction"], required=True,
                choices=tuple(item.value for item in FacingCutDirection),
                tooltip="Climb, conventional hoặc bidirectional theo contract Facing v1.",
                help_key="facing.direction", order=20,
                binding_key="parameters.direction",
            ),
            _number_field(
                "feed_rate", "Feed cắt", values["feed_rate"], unit=feed_unit,
                binding_key="parameters.feed_rate", order=30,
                default=defaults.get("feed_rate"),
                validators=(_positive("facing.feed_positive", "Feed phải lớn hơn 0."),),
            ),
            _number_field(
                "spindle_speed", "Tốc độ trục chính", values["spindle_speed"], unit="RPM",
                binding_key="parameters.spindle_speed", order=40,
                default=defaults.get("spindle_speed"),
                validators=(_positive("facing.spindle_positive", "Spindle RPM phải lớn hơn 0."),),
            ),
        ),
        "Ý đồ cắt và tốc độ công nghệ.", order=40,
    )
    levels = FunctionEditorSection(
        "levels", "LEVELS",
        (
            _number_field(
                "top_height", "Top Z", values["top_height"], unit=unit,
                binding_key="parameters.top_height", order=10,
                default=defaults.get("top_height"),
                help_text="Facing v1 yêu cầu Top Z bằng mặt trên Stock BOX.",
            ),
            _number_field(
                "target_height", "Target Z", values["target_height"], unit=unit,
                binding_key="parameters.target_height", order=20,
                default=(None if variant is FacingEditorVariant.PLANAR_FACE else defaults.get("target_height")),
                source=(FunctionEditorValueSource.GEOMETRY if variant is FacingEditorVariant.PLANAR_FACE
                        else FunctionEditorValueSource.USER),
                read_only=variant is FacingEditorVariant.PLANAR_FACE,
                help_text=("Được cập nhật rõ ràng từ planar FACE đã Select."
                           if variant is FacingEditorVariant.PLANAR_FACE
                           else "Mức Z mục tiêu trước stock allowance."),
            ),
            _number_field(
                "stock_allowance", "Lượng dư", values["stock_allowance"], unit=unit,
                binding_key="parameters.stock_allowance", order=30,
                default=defaults.get("stock_allowance"),
                validators=(FunctionEditorValidationRule(
                    FunctionEditorValidationKind.MINIMUM, 0.0,
                    "Stock allowance không được âm.", "facing.allowance_nonnegative",
                ),),
            ),
            _number_field(
                "stepdown", "Chiều sâu mỗi lớp", values["stepdown"], unit=unit,
                binding_key="parameters.stepdown", order=40,
                default=defaults.get("stepdown"),
                validators=(_positive("facing.stepdown_positive", "Stepdown phải lớn hơn 0."),),
            ),
        ),
        "Top, target, allowance và phân lớp theo Setup WCS.", order=50,
    )
    linking = FunctionEditorSection(
        "linking", "LINKING",
        (
            _number_field(
                "clearance_height", "Clearance Z", values["clearance_height"], unit=unit,
                binding_key="parameters.clearance_height", order=10,
                default=defaults.get("clearance_height"),
            ),
            _number_field(
                "retract_height", "Retract Z", values["retract_height"], unit=unit,
                binding_key="parameters.retract_height", order=20,
                default=defaults.get("retract_height"),
                validators=(FunctionEditorValidationRule(
                    FunctionEditorValidationKind.GREATER_THAN_FIELD, "top_height",
                    "Retract Z phải cao hơn Top Z.", "facing.retract_above_top",
                ),),
            ),
        ),
        "Mặt phẳng an toàn và rút dao.",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=60,
    )
    advanced_fields = [
        _number_field(
            "plunge_feed_rate", "Feed tiếp cận/rút", values["plunge_feed_rate"], unit=feed_unit,
            binding_key="parameters.plunge_feed_rate", order=10,
            default=defaults.get("plunge_feed_rate"),
            validators=(_positive("facing.plunge_positive", "Plunge feed phải lớn hơn 0."),),
        ),
        _number_field(
            "raster_angle_degrees", "Góc raster", values["raster_angle_degrees"], unit="°",
            binding_key="parameters.raster_angle_degrees", order=20,
            default=defaults.get("raster_angle_degrees"),
            help_text="Giá trị hữu hạn được domain chuẩn hóa modulo 180°.",
        ),
    ]
    if variant is FacingEditorVariant.STOCK:
        advanced_fields.append(
            _number_field(
                "overtravel", "Overtravel", values["overtravel"], unit=unit,
                binding_key="parameters.overtravel", order=30,
                default=defaults.get("overtravel"),
                validators=(FunctionEditorValidationRule(
                    FunctionEditorValidationKind.MINIMUM, 0.0,
                    "Overtravel không được âm.", "facing.overtravel_nonnegative",
                ),),
            )
        )
    advanced_fields.extend(
        (
            FunctionEditorField(
                "machine_id", "Máy", FunctionEditorFieldKind.CHOICE,
                values["machine_id"], required=True,
                choices=machine_choices, choice_labels=machine_labels,
                tooltip="Machine requirement hiện có của operation; compatibility được domain kiểm tra.",
                help_key="facing.machine", order=40,
                binding_key="operation.machine_requirement",
            ),
            FunctionEditorField(
                "enabled", "Operation được bật", FunctionEditorFieldKind.CHECKBOX,
                values["enabled"], help_key="facing.enabled", order=50,
                binding_key="operation.enabled",
                conversion=FunctionEditorValueConversion.BOOLEAN,
            ),
        )
    )
    advanced = FunctionEditorSection(
        "advanced", "ADVANCED", tuple(advanced_fields),
        "Override ít dùng thuộc contract Facing v1.",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=70,
    )
    return (basic, geometry, tool, cutting, levels, linking, advanced)


def facing_footer() -> FunctionEditorFooter:
    """Return the Stage 9A.5.1 production footer order."""
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

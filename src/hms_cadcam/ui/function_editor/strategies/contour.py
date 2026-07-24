"""Production Function Editor binding for the unchanged 2D Contour v1 domain."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math

from hms_cadcam.cam.domain import (
    BoxStock,
    ContourCutDirection,
    ContourLeadPolicy,
    ContourParameters,
    ContourProfileSource,
    ContourSide,
    ContourStartPolicy,
    DirtyReason,
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
from hms_cadcam.ui.localization import ui_text


@dataclass(frozen=True, slots=True)
class ContourEditorContext:
    """Native-free snapshot used to construct one Contour production editor."""

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


@dataclass(slots=True)
class ContourEditorDraftContext:
    """Typed transient geometry binding; never serialized or fingerprinted."""

    geometry_reference: GeometryReference | None
    pending_input_id: GeometryInputId | None = None


@dataclass(frozen=True, slots=True)
class ContourOperationUpdate:
    """Fully validated legacy-equivalent candidate for one atomic command."""

    operation_name: str
    operation: Operation
    parameters: ContourParameters
    assembly: ToolAssembly
    tool: ToolDefinition | None
    machine: MachineDefinition
    geometry_reference: GeometryReference


_FIELD_IDS = frozenset(
    {
        "operation_name",
        "geometry_summary",
        "geometry_reference_id",
        "profile_source",
        "tool_assembly_id",
        "tool_details",
        "holder_summary",
        "side",
        "direction",
        "compensation_summary",
        "radial_stock_allowance",
        "cutting_feed_rate",
        "spindle_speed",
        "top_height",
        "final_depth",
        "multiple_depth_passes",
        "stepdown",
        "axial_stock_allowance",
        "clearance_height",
        "retract_height",
        "lead_policy",
        "lead_length",
        "plunge_feed_rate",
        "finishing_pass",
        "machine_id",
        "enabled",
        "start_policy",
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
    context: ContourEditorContext,
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


def _tool_summaries(context: ContourEditorContext) -> tuple[str, str, str]:
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


def _geometry_summary(context: ContourEditorContext) -> str:
    reference = context.geometry_reference
    if reference is None:
        return "Chưa chọn profile · yêu cầu 1 loop kín LINE/ARC"
    status = "RESOLVED" if context.geometry_resolved else "MISSING/STALE"
    kind = "Planar FACE" if reference.kind is GeometryReferenceKind.FACE else "Closed WIRE"
    details: list[str] = ["1 chain", "closed", kind, status]
    if context.geometry_segment_count is not None:
        details.insert(1, f"{context.geometry_segment_count} segments")
    if context.geometry_orientation:
        details.append(context.geometry_orientation)
    return " · ".join(details)


def _parameter_defaults(context: ContourEditorContext) -> dict[str, str]:
    stock = context.setup.stock
    if not isinstance(stock, BoxStock):
        return {}
    unit = context.setup.wcs.origin.unit
    scale = 1.0 if unit.value == "mm" else 1.0 / 25.4
    top = stock.size_z.value
    return {
        "top_height": str(top),
        "final_depth": str(top - scale),
        "stepdown": str(scale),
        "radial_stock_allowance": "0.0",
        "axial_stock_allowance": "0.0",
        "clearance_height": str(top + 5.0 * scale),
        "retract_height": str(top + 2.0 * scale),
        "cutting_feed_rate": str(500.0 * scale),
        "plunge_feed_rate": str(100.0 * scale),
        "spindle_speed": "1000.0",
        "lead_length": str(scale),
    }


def contour_applied_values(
    context: ContourEditorContext,
) -> dict[str, PresentationValue]:
    """Map every production field to deterministic presentation primitives."""
    parameters = ContourParameters.from_operation_parameters(context.operation.parameters)
    tool_text, holder_text, _assembly_name = _tool_summaries(context)
    reference = context.geometry_reference
    return {
        "operation_name": context.operation_name,
        "geometry_summary": _geometry_summary(context),
        "geometry_reference_id": "" if reference is None else str(reference.reference_id),
        "profile_source": parameters.profile_source.value,
        "tool_assembly_id": str(context.operation.tool_assembly.assembly_id),
        "tool_details": tool_text,
        "holder_summary": holder_text,
        "side": parameters.side.value,
        "direction": parameters.direction.value,
        "compensation_summary": (
            "ON profile · không offset bán kính"
            if parameters.side is ContourSide.ON
            else "HMS computer offset · không phát G41/G42/D"
        ),
        "radial_stock_allowance": str(parameters.radial_stock_allowance.value),
        "cutting_feed_rate": str(parameters.cutting_feed_rate.value),
        "spindle_speed": str(parameters.spindle_speed.value),
        "top_height": str(parameters.top_height.value),
        "final_depth": str(parameters.final_depth.value),
        "multiple_depth_passes": parameters.multiple_depth_passes,
        "stepdown": str(parameters.stepdown.value),
        "axial_stock_allowance": str(parameters.axial_stock_allowance.value),
        "clearance_height": str(parameters.clearance_height.value),
        "retract_height": str(parameters.retract_height.value),
        "lead_policy": parameters.lead_policy.value,
        "lead_length": str(parameters.lead_length.value),
        "plunge_feed_rate": str(parameters.plunge_feed_rate.value),
        "finishing_pass": parameters.finishing_pass,
        "machine_id": (
            str(context.operation.machine_requirement.machine_id)
            if context.operation.machine_requirement is not None
            else ""
        ),
        "enabled": context.operation.enabled,
        "start_policy": parameters.start_policy.value,
    }


def _complete_values(
    context: ContourEditorContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    """Restore hidden-but-valid v1 values before rebuilding the full domain object."""
    complete = contour_applied_values(context)
    complete.update(values)
    return complete


def _parameters_from_values(
    context: ContourEditorContext,
    values: Mapping[str, PresentationValue],
) -> ContourParameters:
    complete = _complete_values(context, values)
    unit = context.setup.wcs.origin.unit
    feed_unit = FeedUnit.MM_PER_MINUTE if unit.value == "mm" else FeedUnit.INCH_PER_MINUTE
    return ContourParameters(
        unit,
        ContourProfileSource(_text(complete["profile_source"], "profile_source")),
        ContourSide(_text(complete["side"], "side")),
        Length(_number(complete["top_height"], "top_height"), unit),
        Length(_number(complete["final_depth"], "final_depth"), unit),
        Length(_number(complete["stepdown"], "stepdown"), unit),
        Length(
            _number(complete["radial_stock_allowance"], "radial_stock_allowance"),
            unit,
        ),
        Length(
            _number(complete["axial_stock_allowance"], "axial_stock_allowance"),
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
        ContourCutDirection(_text(complete["direction"], "direction")),
        ContourStartPolicy(_text(complete["start_policy"], "start_policy")),
        ContourLeadPolicy(_text(complete["lead_policy"], "lead_policy")),
        Length(_number(complete["lead_length"], "lead_length"), unit),
        _boolean(complete["finishing_pass"], "finishing_pass"),
        _boolean(complete["multiple_depth_passes"], "multiple_depth_passes"),
    )


def _geometry_reference_for_values(
    context: ContourEditorContext,
    draft: ContourEditorDraftContext,
    values: Mapping[str, PresentationValue],
    parameters: ContourParameters,
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
    expected_kind = (
        GeometryReferenceKind.FACE
        if parameters.profile_source is ContourProfileSource.PLANAR_FACE_OUTER
        else GeometryReferenceKind.SKETCH_OR_PROFILE
    )
    if reference is None or reference.kind is not expected_kind:
        raise ValueError(
            "Profile source và persistent geometry không khớp; hãy Select Geometry lại."
        )
    return reference


def prepare_contour_update(
    context: ContourEditorContext,
    draft: ContourEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> ContourOperationUpdate:
    """Build the exact candidate used by the legacy Contour Apply path."""
    complete = _complete_values(context, values)
    parameters = _parameters_from_values(context, complete)
    reference = _geometry_reference_for_values(context, draft, complete, parameters)
    assembly_id = _text(complete["tool_assembly_id"], "tool_assembly_id")
    assembly = next(
        (item for item in context.tool_assemblies if str(item.assembly_id) == assembly_id),
        None,
    )
    if assembly is None:
        raise ValueError("Tool Assembly không còn tồn tại trong project.")
    machine_id = _text(complete["machine_id"], "machine_id")
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
    expected_kind = (
        GeometryReferenceKind.FACE
        if parameters.profile_source is ContourProfileSource.PLANAR_FACE_OUTER
        else GeometryReferenceKind.SKETCH_OR_PROFILE
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
            GeometryInputRole.PROFILE,
            reference,
            True,
            expected_kind,
            0,
        ),
    )
    parameter_set = parameters.to_operation_parameters()
    tool_reference = ToolAssemblyReference.from_assembly(assembly)
    enabled = _boolean(complete["enabled"], "enabled")
    inputs_changed = (
        parameter_set != context.operation.parameters
        or tool_reference != context.operation.tool_assembly
        or requirement != context.operation.machine_requirement
        or geometry_inputs != context.operation.geometry_inputs
    )
    enabled_changed = enabled != context.operation.enabled
    changed = context.operation
    if inputs_changed or enabled_changed:
        reason = DirtyReason.PARAMETERS_CHANGED if inputs_changed else DirtyReason.UPSTREAM_CHANGED
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
    return ContourOperationUpdate(
        _text(complete["operation_name"], "operation_name"),
        changed,
        parameters,
        assembly,
        tool,
        machine,
        reference,
    )


def validate_contour_schema_contract(schema: FunctionEditorSchema) -> None:
    """Fail closed when a production field is missing, duplicate or invented."""
    actual = {field.field_id for field in schema.fields}
    if actual != _FIELD_IDS:
        raise ValueError(
            "Contour schema mapping mismatch; "
            f"missing={sorted(_FIELD_IDS - actual)}, unsupported={sorted(actual - _FIELD_IDS)}"
        )
    for field in schema.fields:
        if not field.binding_key:
            raise ValueError(f"Contour field lacks binding: {field.field_id}")


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
    applicable_when: FunctionEditorApplicability | None = None,
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
        default_label=("HMS Contour v1 · Setup/Stock" if default is not None else ""),
        applicable_when=applicable_when,
        required=True,
        disclosure_level=disclosure_level,
        validators=validators,
        tooltip=help_text,
        help_text=help_text,
        help_key=f"contour.{field_id}",
        order=order,
        binding_key=binding_key,
        conversion=FunctionEditorValueConversion.FLOAT,
        reset_behavior=FunctionEditorResetBehavior.APPLIED,
    )


def _choice_labels(enum_type, labels: dict[object, str]) -> tuple[tuple[PresentationValue, str], ...]:
    return tuple((item.value, labels.get(item, item.value)) for item in enum_type)


def build_contour_sections(
    context: ContourEditorContext,
) -> tuple[FunctionEditorSection, ...]:
    """Build deterministic operator-oriented sections over Contour v1 only."""
    values = contour_applied_values(context)
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
                help_key="contour.operation_name",
                order=10,
                binding_key="node.name",
                conversion=FunctionEditorValueConversion.TEXT,
            ),
        ),
        "Nhận diện operation; các quyết định cốt lõi nằm ngay trong section theo workflow.",
        order=10,
    )
    geometry = FunctionEditorSection(
        "geometry",
        "GEOMETRY",
        (
            FunctionEditorField(
                "geometry_summary",
                "Profile / chain",
                FunctionEditorFieldKind.READ_ONLY,
                values["geometry_summary"],
                source=FunctionEditorValueSource.GEOMETRY,
                required=True,
                tooltip="Contour v1 nhận đúng một loop kín LINE/ARC; không tự đảo geometry.",
                help_text="Select/Rebind tạo GeometryReference typed; preview không giữ OCP object.",
                help_key="contour.geometry_summary",
                order=10,
                binding_key="derived.geometry_summary",
                action_id="select_geometry",
                action_label="Select",
            ),
            FunctionEditorField(
                "profile_source",
                "Nguồn profile",
                FunctionEditorFieldKind.CHOICE,
                values["profile_source"],
                required=True,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                choices=tuple(item.value for item in ContourProfileSource),
                choice_labels=_choice_labels(
                    ContourProfileSource,
                    {
                        ContourProfileSource.PLANAR_FACE_OUTER: "Outer loop của planar FACE",
                        ContourProfileSource.CLOSED_WIRE: "Closed WIRE",
                    },
                ),
                tooltip="Nguồn phải khớp loại GeometryReference đã Select.",
                help_key="contour.profile_source",
                order=20,
                binding_key="parameters.profile_source",
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
                help_key="contour.geometry_identity",
                order=30,
                binding_key="operation.geometry_inputs.profile",
            ),
        ),
        "Một chain kín, trạng thái resolve và orientation hiện hành.",
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
                help_key="contour.tool_assembly",
                order=10,
                binding_key="operation.tool_assembly",
            ),
            FunctionEditorField(
                "tool_details",
                "Tool / Shank",
                FunctionEditorFieldKind.READ_ONLY,
                values["tool_details"],
                source=FunctionEditorValueSource.TOOL,
                help_key="contour.tool_details",
                order=20,
                binding_key="derived.tool_details",
            ),
            FunctionEditorField(
                "holder_summary",
                "Holder",
                FunctionEditorFieldKind.READ_ONLY,
                values["holder_summary"],
                source=FunctionEditorValueSource.TOOL,
                help_key="contour.holder",
                order=30,
                binding_key="derived.holder_summary",
            ),
        ),
        "Tool Library là nguồn chân lý cho diameter, corner radius, holder và stickout.",
        order=30,
    )
    cutting = FunctionEditorSection(
        "cutting",
        "CUTTING",
        (
            FunctionEditorField(
                "side",
                "Phía contour",
                FunctionEditorFieldKind.CHOICE,
                values["side"],
                required=True,
                choices=tuple(item.value for item in ContourSide),
                choice_labels=_choice_labels(
                    ContourSide,
                    {
                        ContourSide.ON: "ON · tâm dao theo profile",
                        ContourSide.INSIDE: "INSIDE · offset vào trong",
                        ContourSide.OUTSIDE: "OUTSIDE · offset ra ngoài",
                    },
                ),
                tooltip="Không đồng nhất field này với orientation hoặc cutting direction.",
                help_key="contour.side",
                order=10,
                binding_key="parameters.side",
            ),
            FunctionEditorField(
                "direction",
                "Hướng cắt",
                FunctionEditorFieldKind.CHOICE,
                values["direction"],
                required=True,
                choices=tuple(item.value for item in ContourCutDirection),
                choice_labels=_choice_labels(
                    ContourCutDirection,
                    {
                        ContourCutDirection.CLIMB: "Climb",
                        ContourCutDirection.CONVENTIONAL: "Conventional",
                    },
                ),
                tooltip="Generator quyết định traversal từ Side + Direction; không sửa geometry.",
                help_key="contour.direction",
                order=20,
                binding_key="parameters.direction",
            ),
            FunctionEditorField(
                "compensation_summary",
                "Bù bán kính dao",
                FunctionEditorFieldKind.READ_ONLY,
                values["compensation_summary"],
                source=FunctionEditorValueSource.DEFAULT,
                tooltip="Contour v1 offset trong HMS; không có CONTROL/WEAR, D offset hoặc G41/G42.",
                help_key="contour.compensation",
                order=30,
                binding_key="derived.compensation_policy",
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
                        "contour.radial_allowance_nonnegative",
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
                validators=(_minimum("contour.feed_positive", "Feed cắt phải lớn hơn 0."),),
            ),
            _number_field(
                "spindle_speed",
                "Tốc độ trục chính",
                values["spindle_speed"],
                unit="RPM",
                binding_key="parameters.spindle_speed",
                order=60,
                default=defaults.get("spindle_speed"),
                validators=(
                    _minimum("contour.spindle_positive", "Spindle RPM phải lớn hơn 0."),
                ),
            ),
        ),
        "Side, cutting direction, allowance và tốc độ công nghệ.",
        order=40,
    )
    levels = FunctionEditorSection(
        "levels",
        "LEVELS",
        (
            _number_field(
                "top_height",
                "Top",
                values["top_height"],
                unit=unit,
                binding_key="parameters.top_height",
                order=10,
                default=defaults.get("top_height"),
                help_text="Tọa độ tuyệt đối trong hệ tọa độ Thiết lập; không phải tọa độ máy.",
            ),
            _number_field(
                "final_depth",
                "Depth",
                values["final_depth"],
                unit=unit,
                binding_key="parameters.final_depth",
                order=20,
                default=defaults.get("final_depth"),
                help_text="Chiều cắt đi theo -Z; axial allowance được cộng vào final cutter depth.",
            ),
            FunctionEditorField(
                "multiple_depth_passes",
                "Nhiều lớp chiều sâu",
                FunctionEditorFieldKind.CHECKBOX,
                values["multiple_depth_passes"],
                help_key="contour.multiple_depth_passes",
                order=30,
                binding_key="parameters.multiple_depth_passes",
                conversion=FunctionEditorValueConversion.BOOLEAN,
            ),
            _number_field(
                "stepdown",
                "Stepdown",
                values["stepdown"],
                unit=unit,
                binding_key="parameters.stepdown",
                order=40,
                default=defaults.get("stepdown"),
                validators=(
                    _minimum("contour.stepdown_positive", "Stepdown phải lớn hơn 0."),
                ),
                applicable_when=FunctionEditorApplicability(
                    "multiple_depth_passes", ApplicabilityOperator.TRUTHY
                ),
            ),
            _number_field(
                "axial_stock_allowance",
                "Floor Stock Allowance",
                values["axial_stock_allowance"],
                unit=unit,
                binding_key="parameters.axial_stock_allowance",
                order=50,
                default=defaults.get("axial_stock_allowance"),
                validators=(
                    _minimum(
                        "contour.axial_allowance_nonnegative",
                        "Lượng dư đáy không được âm.",
                        0.0,
                    ),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
            ),
        ),
            "Đỉnh, chiều sâu cuối và chính sách chia lớp trong hệ tọa độ Thiết lập.",
        order=50,
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
                        "top_height",
                        "Z rút dao phải cao hơn Z đỉnh.",
                        "contour.retract_above_top",
                    ),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
            ),
            FunctionEditorField(
                "lead_policy",
                "Kiểu lead-in/out",
                FunctionEditorFieldKind.READ_ONLY,
                values["lead_policy"],
                source=FunctionEditorValueSource.DEFAULT,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                tooltip="Contour v1 luôn dùng linear lead-in và linear lead-out cùng chiều dài.",
                help_key="contour.lead_policy",
                order=30,
                binding_key="parameters.lead_policy",
            ),
            _number_field(
                "lead_length",
                "Lead Length",
                values["lead_length"],
                unit=unit,
                binding_key="parameters.lead_length",
                order=40,
                default=defaults.get("lead_length"),
                validators=(
                    _minimum("contour.lead_positive", "Chiều dài lead phải lớn hơn 0."),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
            ),
            _number_field(
                "plunge_feed_rate",
                "Feed tiếp cận / rút",
                values["plunge_feed_rate"],
                unit=feed_unit,
                binding_key="parameters.plunge_feed_rate",
                order=50,
                default=defaults.get("plunge_feed_rate"),
                validators=(
                    _minimum("contour.plunge_positive", "Plunge feed phải lớn hơn 0."),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
            ),
        ),
        "Khoảng an toàn, rút dao và dẫn dao tuyến tính v1; mọi Z đều thuộc hệ tọa độ Thiết lập.",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=60,
    )
    advanced = FunctionEditorSection(
        "advanced",
        "ADVANCED",
        (
            FunctionEditorField(
                "finishing_pass",
                "Spring finishing pass",
                FunctionEditorFieldKind.CHECKBOX,
                values["finishing_pass"],
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                tooltip="Lặp lại loop tại lớp cuối; không phải rest machining.",
                help_key="contour.finishing_pass",
                order=10,
                binding_key="parameters.finishing_pass",
                conversion=FunctionEditorValueConversion.BOOLEAN,
            ),
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
                help_key="contour.machine",
                order=20,
                binding_key="operation.machine_requirement",
            ),
            FunctionEditorField(
                "enabled",
                "Operation được bật",
                FunctionEditorFieldKind.CHECKBOX,
                values["enabled"],
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_key="contour.enabled",
                order=30,
                binding_key="operation.enabled",
                conversion=FunctionEditorValueConversion.BOOLEAN,
            ),
        ),
        "Tùy chọn ít dùng nhưng có trong Contour v1.",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=70,
    )
    expert = FunctionEditorSection(
        "expert",
        "EXPERT",
        (
            FunctionEditorField(
                "start_policy",
                "Canonical start policy",
                FunctionEditorFieldKind.READ_ONLY,
                values["start_policy"],
                source=FunctionEditorValueSource.DEFAULT,
                disclosure_level=ParameterDisclosureLevel.EXPERT,
                tooltip="Hợp đồng thuật toán v1: midpoint có (X, Y) nhỏ nhất; không có tùy chỉnh.",
                help_text=(
                    "Phần trình bày chỉ đọc; chính sách này vẫn được bảo toàn "
                    "khi mã hóa và kiểm tra dấu nhận dạng."
                ),
                help_key="contour.start_policy",
                order=10,
                binding_key="parameters.start_policy",
            ),
        ),
        "Algorithm policy read-only; Contour v1 không có tolerance/filter/post override.",
        disclosure_level=ParameterDisclosureLevel.EXPERT,
        default_expanded=False,
        order=80,
    )
    return (basic, geometry, tool, cutting, levels, linking, advanced, expert)


def contour_footer() -> FunctionEditorFooter:
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


def build_contour_schema(context: ContourEditorContext) -> FunctionEditorSchema:
    """Build the production 2D Contour schema without changing domain semantics."""
    parameters = ContourParameters.from_operation_parameters(context.operation.parameters)
    _tool_text, _holder_text, assembly_name = _tool_summaries(context)
    geometry = _geometry_summary(context)
    schema = FunctionEditorSchema(
        "contour_production_9a5_2",
        FunctionEditorStrategyKey("contour_2d_9a5_2"),
        FunctionEditorSummary(
            context.operation_name,
            (
                f"Biên dạng 2D · {ui_text(parameters.side.value)} · "
                f"{ui_text(parameters.direction.value)} · Z đỉnh "
                f"{parameters.top_height.value:g} · Chiều sâu "
                f"{parameters.final_depth.value:g} {parameters.unit.value}"
            ),
            tool=assembly_name,
            geometry=geometry,
            operation_status=context.operation.artifact_state.status.value.upper(),
        ),
        build_contour_sections(context),
        contour_footer(),
    )
    validate_contour_schema_contract(schema)
    return schema


__all__ = [
    "ContourEditorContext",
    "ContourEditorDraftContext",
    "ContourOperationUpdate",
    "build_contour_schema",
    "contour_applied_values",
    "prepare_contour_update",
    "validate_contour_schema_contract",
]

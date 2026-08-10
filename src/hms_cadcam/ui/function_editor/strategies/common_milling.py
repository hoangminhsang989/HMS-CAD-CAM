"""Shared typed bindings for the two production Facing editor variants."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
import math

from hms_cadcam.cam.automatic_facing import (
    FACING_AUTOMATIC_POLICY_KEY,
    FacingAutomaticContext,
    FacingAutomaticVariant,
    resolve_facing_automatic_contract,
)
from hms_cadcam.cam.automatic_parameters import (
    AUTOMATIC_PARAMETER_CONTRACT_KEY,
    AutomaticParameterContract,
    AutomaticParameterMode,
    AutomaticParameterStatus,
    AutomaticParameterValue,
    AutomaticValidationResult,
    CamQualityProfile,
)
from hms_cadcam.cam.domain import (
    BoxStock,
    DependencyFingerprint,
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
    OperationParameterSet,
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
        "quality_profile",
        "automatic_summary",
        "automatic_stepover",
        "automatic_stepdown",
        "stepover_mode",
        "stepdown_mode",
    }
)
_STOCK_FIELD_IDS = _BASE_FIELD_IDS | {
    "geometry_bounds",
    "overtravel",
    "automatic_overtravel",
    "overtravel_mode",
}
_PLANAR_FIELD_IDS = _BASE_FIELD_IDS | {"geometry_reference_id"}

_FACING_POLICY_TOLERANCE = 1.0e-8


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


def _stored_automatic_contract(
    context: FacingEditorContext,
) -> AutomaticParameterContract | None:
    raw = dict(context.operation.parameters.values).get(
        AUTOMATIC_PARAMETER_CONTRACT_KEY
    )
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("Facing automatic metadata is invalid.")
    try:
        contract = AutomaticParameterContract.from_json(raw)
    except ValueError as error:
        raise ValueError("Facing automatic metadata is malformed.") from error
    if contract.policy_key != FACING_AUTOMATIC_POLICY_KEY:
        raise ValueError("Facing automatic policy identity is invalid.")
    return contract


def _selected_tool(
    context: FacingEditorContext,
    assembly_id: object | None = None,
) -> ToolDefinition | None:
    identity = (
        str(context.operation.tool_assembly.assembly_id)
        if assembly_id is None
        else str(assembly_id)
    )
    assembly = next(
        (
            item
            for item in context.tool_assemblies
            if str(item.assembly_id) == identity
        ),
        None,
    )
    if assembly is None:
        return None
    return next(
        (
            item
            for item in context.tool_definitions
            if item.tool_id == assembly.tool_id
        ),
        None,
    )


def _automatic_context(
    context: FacingEditorContext,
    variant: FacingEditorVariant,
    parameters: FacingParameters,
    assembly_id: object | None = None,
    values: Mapping[str, PresentationValue] | None = None,
) -> FacingAutomaticContext:
    unit = parameters.unit
    tool = _selected_tool(context, assembly_id)
    geometry = None if tool is None else tool.cutting_geometry
    diameter = getattr(geometry, "diameter", None)
    corner_radius = getattr(geometry, "corner_radius", None)
    axial = None if geometry is None else geometry.axial_cutting_length
    boundary_fingerprint: str | None = None
    if variant is FacingEditorVariant.STOCK and isinstance(context.setup.stock, BoxStock):
        boundary_fingerprint = DependencyFingerprint.from_payload(
            context.setup.stock.to_dict()
        ).digest
    elif context.geometry_reference is not None:
        boundary_fingerprint = (
            context.geometry_reference.expected_geometry_fingerprint.digest
        )
    depth_span = parameters.top_height.value - parameters.final_cut_height
    if values is not None:
        try:
            top_height = _number(values["top_height"], "top_height")
            target_height = _number(values["target_height"], "target_height")
            stock_allowance = _number(
                values["stock_allowance"],
                "stock_allowance",
            )
        except (KeyError, ValueError):
            # Keep the last validated dependency while a numeric editor contains
            # an incomplete token. Final draft validation still fails closed.
            pass
        else:
            depth_span = top_height - (target_height + stock_allowance)
    return FacingAutomaticContext(
        FacingAutomaticVariant.STOCK_BOX
        if variant is FacingEditorVariant.STOCK
        else FacingAutomaticVariant.PLANAR_FACE,
        unit,
        None if tool is None else tool.family,
        None if diameter is None else diameter.to(unit).value,
        None if corner_radius is None else corner_radius.to(unit).value,
        None if axial is None else axial.to(unit).value,
        depth_span if depth_span > 0.0 else None,
        _FACING_POLICY_TOLERANCE,
        boundary_fingerprint,
        None if tool is None else tool.content_fingerprint.digest,
    )


def _quality_profile(value: object) -> CamQualityProfile:
    try:
        return CamQualityProfile(str(value))
    except ValueError as error:
        raise ValueError("Hồ sơ chất lượng tự động không hợp lệ.") from error


def _legacy_manual_contract(
    base: AutomaticParameterContract,
    parameters: FacingParameters,
    variant: FacingEditorVariant,
) -> AutomaticParameterContract:
    legacy = {
        "stepover": parameters.stepover.value,
        "stepdown": parameters.stepdown.value,
        "overtravel": parameters.overtravel.value,
    }
    values: list[AutomaticParameterValue] = []
    for item in base.values:
        if item.key == "overtravel" and variant is FacingEditorVariant.PLANAR_FACE:
            values.append(item)
            continue
        values.append(
            replace(
                item,
                mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                override_value=legacy[item.key],
                validation=AutomaticValidationResult(True),
                reason=(
                    "Legacy explicit numeric value preserved as intentional manual override."
                ),
            )
        )
    return replace(base, values=tuple(values))


def _recompute_automatic_contract(
    context: FacingEditorContext,
    variant: FacingEditorVariant,
    *,
    values: Mapping[str, PresentationValue] | None = None,
) -> AutomaticParameterContract:
    parameters = FacingParameters.from_operation_parameters(context.operation.parameters)
    stored = _stored_automatic_contract(context)
    profile = (
        _quality_profile(values["quality_profile"])
        if values is not None and "quality_profile" in values
        else stored.quality_profile
        if stored is not None
        else CamQualityProfile.BALANCED
    )
    assembly_id = None if values is None else values.get("tool_assembly_id")
    base = resolve_facing_automatic_contract(
        _automatic_context(context, variant, parameters, assembly_id, values),
        quality_profile=profile,
    )
    if values is None:
        if stored is None:
            return _legacy_manual_contract(base, parameters, variant)
        merged: list[AutomaticParameterValue] = []
        for item in base.values:
            try:
                previous = stored.value(item.key)
            except KeyError:
                merged.append(item)
                continue
            if previous.has_manual_override:
                merged.append(
                    replace(
                        item,
                        mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                        override_value=previous.override_value,
                        validation=previous.validation,
                    )
                )
            else:
                merged.append(item)
        return replace(base, values=tuple(merged))
    updated: list[AutomaticParameterValue] = []
    for item in base.values:
        mode_key = f"{item.key}_mode"
        if mode_key not in values:
            updated.append(item)
            continue
        try:
            mode = AutomaticParameterMode(str(values[mode_key]))
        except ValueError as error:
            raise ValueError(f"Chế độ {item.key} không hợp lệ.") from error
        if mode is AutomaticParameterMode.AUTO:
            if item.status is not AutomaticParameterStatus.RESOLVED:
                raise ValueError(f"{item.key} chưa đủ evidence để dùng AUTO: {item.reason}")
            updated.append(item)
            continue
        if mode not in {
            AutomaticParameterMode.MANUAL,
            AutomaticParameterMode.MANUAL_OVERRIDE,
        }:
            raise ValueError(f"{item.key} không hỗ trợ chế độ đã chọn.")
        override = _number(values[item.key], item.key)
        updated.append(
            replace(
                item,
                mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                override_value=override,
                validation=AutomaticValidationResult(True),
                reason="Explicit Advanced manual override.",
            )
        )
    return replace(base, values=tuple(updated))


def _automatic_text(value: AutomaticParameterValue, unit: str) -> str:
    if value.mode is AutomaticParameterMode.NOT_APPLICABLE:
        return f"Không áp dụng · {value.reason}"
    prefix = "Tự động" if value.mode is AutomaticParameterMode.AUTO else "Tùy chỉnh"
    suffix = " · đã giới hạn an toàn" if value.clamped else ""
    return f"{prefix} · {value.effective_value:g} {unit}{suffix}"


def _automatic_presentation(
    contract: AutomaticParameterContract,
    variant: FacingEditorVariant,
    unit: str,
) -> dict[str, PresentationValue]:
    visible_keys = ["stepover", "stepdown"]
    if variant is FacingEditorVariant.STOCK:
        visible_keys.append("overtravel")
    entries = [contract.value(key) for key in visible_keys]
    auto = sum(item.mode is AutomaticParameterMode.AUTO for item in entries)
    manual = sum(item.has_manual_override for item in entries)
    unavailable = len(entries) - auto - manual
    result: dict[str, PresentationValue] = {
        "quality_profile": contract.quality_profile.value,
        "automatic_summary": (
            f"{auto} tự động · {manual} tùy chỉnh · {unavailable} không áp dụng"
        ),
    }
    for item in entries:
        result[f"automatic_{item.key}"] = _automatic_text(item, unit)
        result[f"{item.key}_mode"] = item.mode.value
        if item.effective_value is not None:
            result[item.key] = str(item.effective_value)
    return result


def facing_draft_transform(
    context: FacingEditorContext,
    variant: FacingEditorVariant,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    """Recompute AUTO fields after Tool/profile/mode edits; preserve overrides."""
    contract = _recompute_automatic_contract(context, variant, values=values)
    return _automatic_presentation(
        contract,
        variant,
        context.setup.wcs.origin.unit.value,
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
    values.update(
        _automatic_presentation(
            _recompute_automatic_contract(context, variant),
            variant,
            context.setup.wcs.origin.unit.value,
        )
    )
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
    complete = dict(values)
    complete.update(facing_draft_transform(context, variant, complete))
    parameters = _parameters_from_values(context, variant, complete)
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
    reference = _geometry_reference_for_values(context, draft, complete)
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
    automatic = _recompute_automatic_contract(
        context,
        variant,
        values=complete,
    )
    base_parameters = parameters.to_operation_parameters()
    visible_automatic_keys = ["stepover", "stepdown"]
    if variant is FacingEditorVariant.STOCK:
        visible_automatic_keys.append("overtravel")
    persist_automatic = _stored_automatic_contract(context) is not None or any(
        automatic.value(key).mode is AutomaticParameterMode.AUTO
        for key in visible_automatic_keys
    )
    parameter_set = (
        OperationParameterSet(
            base_parameters.strategy_key,
            base_parameters.strategy_version,
            base_parameters.values
            + ((AUTOMATIC_PARAMETER_CONTRACT_KEY, automatic.to_json()),),
            base_parameters.schema_version,
        )
        if persist_automatic
        else base_parameters
    )
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
        _text(complete["operation_name"], "operation_name"),
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
    disclosure_level: ParameterDisclosureLevel = ParameterDisclosureLevel.BASIC,
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
        disclosure_level=disclosure_level,
        validators=validators,
        tooltip=help_text,
        help_text=help_text,
        help_key=f"facing.{field_id}",
        order=order,
        binding_key=binding_key,
        conversion=FunctionEditorValueConversion.FLOAT,
        reset_behavior=FunctionEditorResetBehavior.APPLIED,
    )


def _automatic_mode_field(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    order: int,
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.CHOICE,
        value,
        source=FunctionEditorValueSource.USER,
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        choices=(
            AutomaticParameterMode.AUTO.value,
            AutomaticParameterMode.MANUAL_OVERRIDE.value,
        ),
        choice_labels=(
            (AutomaticParameterMode.AUTO.value, "Tự động"),
            (AutomaticParameterMode.MANUAL_OVERRIDE.value, "Tùy chỉnh"),
        ),
        tooltip="Chế độ tự động tính lại từ bằng chứng; tùy chỉnh giữ ý định người dùng.",
        help_key=f"facing.{field_id}",
        order=order,
        binding_key=f"automatic.{field_id}",
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
                tooltip="Tên hiển thị trong Trình quản lý nguyên công; identity vẫn là OperationId.",
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
                "geometry_bounds", "Kích thước phôi", FunctionEditorFieldKind.READ_ONLY,
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
                tooltip="ID tham chiếu hình học ổn định; không chứa đối tượng OCP.",
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
                "tool_details", "Tool / Shank", FunctionEditorFieldKind.READ_ONLY,
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
    automatic_fields = [
        FunctionEditorField(
            "quality_profile",
            "Hồ sơ chất lượng",
            FunctionEditorFieldKind.CHOICE,
            values["quality_profile"],
            source=FunctionEditorValueSource.USER,
            choices=tuple(item.value for item in CamQualityProfile),
            choice_labels=(
                (CamQualityProfile.FAST.value, "Nhanh"),
                (CamQualityProfile.BALANCED.value, "Cân bằng"),
                (CamQualityProfile.HIGH.value, "Chất lượng cao"),
            ),
            tooltip="Điều chỉnh tỷ lệ bước ngang/bước xuống trong giới hạn hình học thực.",
            help_key="facing.quality_profile",
            order=10,
            binding_key="automatic.quality_profile",
        ),
        FunctionEditorField(
            "automatic_summary",
            "Trạng thái tham số tự động",
            FunctionEditorFieldKind.READ_ONLY,
            values["automatic_summary"],
            source=FunctionEditorValueSource.DERIVED,
            tooltip="Tóm tắt chế độ tự động, giá trị tùy chỉnh và tham số thiếu bằng chứng.",
            help_key="facing.automatic_summary",
            order=20,
            binding_key="automatic.summary",
            action_id="use_automatic_parameters",
            action_label="AUTO",
        ),
        FunctionEditorField(
            "automatic_stepover",
            "Bước ngang tự động",
            FunctionEditorFieldKind.READ_ONLY,
            values["automatic_stepover"],
            source=FunctionEditorValueSource.DERIVED,
            help_key="facing.automatic_stepover",
            order=30,
            binding_key="automatic.stepover_summary",
        ),
        FunctionEditorField(
            "automatic_stepdown",
            "Bước xuống tự động",
            FunctionEditorFieldKind.READ_ONLY,
            values["automatic_stepdown"],
            source=FunctionEditorValueSource.DERIVED,
            help_key="facing.automatic_stepdown",
            order=40,
            binding_key="automatic.stepdown_summary",
        ),
    ]
    if variant is FacingEditorVariant.STOCK:
        automatic_fields.append(
            FunctionEditorField(
                "automatic_overtravel",
                "Vượt biên tự động",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_overtravel"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="facing.automatic_overtravel",
                order=50,
                binding_key="automatic.overtravel_summary",
            )
        )
    automatic = FunctionEditorSection(
        "automatic_parameters",
        "THAM SỐ TỰ ĐỘNG",
        tuple(automatic_fields),
        "Giá trị suy ra từ Tool, hình học, đơn vị và hồ sơ chất lượng.",
        order=35,
    )
    cutting = FunctionEditorSection(
        "cutting", "CUTTING",
        (
            _automatic_mode_field(
                "stepover_mode",
                "Chế độ Bước ngang",
                values["stepover_mode"],
                order=5,
            ),
            _number_field(
                "stepover", "Stepover", values["stepover"], unit=unit,
                binding_key="parameters.stepover", order=10,
                default=defaults.get("stepover"),
                validators=(_positive("facing.stepover_positive", "Stepover phải lớn hơn 0."),),
                help_text="Stepover không được lớn hơn đường kính dao.",
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                source=(
                    FunctionEditorValueSource.DERIVED
                    if values["stepover_mode"] == AutomaticParameterMode.AUTO.value
                    else FunctionEditorValueSource.USER
                ),
            ),
            FunctionEditorField(
                "direction", "Hướng cắt", FunctionEditorFieldKind.CHOICE,
                values["direction"], required=True,
                choices=tuple(item.value for item in FacingCutDirection),
                tooltip="Phay thuận, phay nghịch hoặc hai chiều theo hợp đồng Phay mặt v1.",
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
                "top_height", "Top", values["top_height"], unit=unit,
                binding_key="parameters.top_height", order=10,
                default=defaults.get("top_height"),
                help_text="Facing v1 yêu cầu Top Z bằng mặt trên Stock BOX.",
            ),
            _number_field(
                "target_height", "Depth", values["target_height"], unit=unit,
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
                "stock_allowance", "Stock Allowance", values["stock_allowance"], unit=unit,
                binding_key="parameters.stock_allowance", order=30,
                default=defaults.get("stock_allowance"),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                validators=(FunctionEditorValidationRule(
                    FunctionEditorValidationKind.MINIMUM, 0.0,
                    "Lượng dư phôi không được âm.", "facing.allowance_nonnegative",
                ),),
            ),
            _automatic_mode_field(
                "stepdown_mode",
                "Chế độ Bước xuống",
                values["stepdown_mode"],
                order=35,
            ),
            _number_field(
                "stepdown", "Stepdown", values["stepdown"], unit=unit,
                binding_key="parameters.stepdown", order=40,
                default=defaults.get("stepdown"),
                validators=(_positive("facing.stepdown_positive", "Stepdown phải lớn hơn 0."),),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                source=(
                    FunctionEditorValueSource.DERIVED
                    if values["stepdown_mode"] == AutomaticParameterMode.AUTO.value
                    else FunctionEditorValueSource.USER
                ),
            ),
        ),
        "Top, target, allowance và phân lớp theo Setup WCS.", order=50,
    )
    linking = FunctionEditorSection(
        "linking", "LINKING",
        (
            _number_field(
                "clearance_height", "Clearance", values["clearance_height"], unit=unit,
                binding_key="parameters.clearance_height", order=10,
                default=defaults.get("clearance_height"),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
            ),
            _number_field(
                "retract_height", "Retract", values["retract_height"], unit=unit,
                binding_key="parameters.retract_height", order=20,
                default=defaults.get("retract_height"),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                validators=(FunctionEditorValidationRule(
                    FunctionEditorValidationKind.GREATER_THAN_FIELD, "top_height",
                    "Z rút dao phải cao hơn Z đỉnh.", "facing.retract_above_top",
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
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            validators=(_positive("facing.plunge_positive", "Plunge feed phải lớn hơn 0."),),
        ),
        _number_field(
            "raster_angle_degrees", "Góc raster", values["raster_angle_degrees"], unit="°",
            binding_key="parameters.raster_angle_degrees", order=20,
            default=defaults.get("raster_angle_degrees"),
            disclosure_level=ParameterDisclosureLevel.ADVANCED,
            help_text="Giá trị hữu hạn được miền chuẩn hóa theo môđun 180°.",
        ),
    ]
    if variant is FacingEditorVariant.STOCK:
        advanced_fields.extend(
            (
                _automatic_mode_field(
                    "overtravel_mode",
                    "Chế độ Vượt biên",
                    values["overtravel_mode"],
                    order=25,
                ),
                _number_field(
                    "overtravel", "Overtravel", values["overtravel"], unit=unit,
                    binding_key="parameters.overtravel", order=30,
                    default=defaults.get("overtravel"),
                    disclosure_level=ParameterDisclosureLevel.ADVANCED,
                    source=(
                        FunctionEditorValueSource.DERIVED
                        if values["overtravel_mode"]
                        == AutomaticParameterMode.AUTO.value
                        else FunctionEditorValueSource.USER
                    ),
                    validators=(FunctionEditorValidationRule(
                        FunctionEditorValidationKind.MINIMUM, 0.0,
                        "Overtravel không được âm.", "facing.overtravel_nonnegative",
                    ),),
                ),
            )
        )
    advanced_fields.extend(
        (
            FunctionEditorField(
                "machine_id", "Máy", FunctionEditorFieldKind.CHOICE,
                values["machine_id"], required=True,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                choices=machine_choices, choice_labels=machine_labels,
                tooltip=(
                    "Yêu cầu máy hiện có của nguyên công; tính tương thích "
                    "được dữ liệu nghiệp vụ kiểm tra."
                ),
                help_key="facing.machine", order=40,
                binding_key="operation.machine_requirement",
            ),
            FunctionEditorField(
                "enabled", "Operation được bật", FunctionEditorFieldKind.CHECKBOX,
                values["enabled"], disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_key="facing.enabled", order=50,
                binding_key="operation.enabled",
                conversion=FunctionEditorValueConversion.BOOLEAN,
            ),
        )
    )
    advanced = FunctionEditorSection(
        "advanced", "ADVANCED", tuple(advanced_fields),
        "Tùy chỉnh ít dùng thuộc hợp đồng Phay mặt v1.",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=70,
    )
    return (basic, geometry, tool, automatic, cutting, levels, linking, advanced)


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

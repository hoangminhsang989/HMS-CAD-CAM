"""Production Function Editor binding for the unchanged 2D Contour v1 domain."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math

from hms_cadcam.cam.application.contour import (
    ContourGenerationError,
    prepare_contour_machining_geometry,
)
from hms_cadcam.cam.automatic_contour import (
    CONTOUR_AUTOMATIC_POLICY_KEY,
    CONTOUR_AUTOMATIC_USER_KEYS,
    ContourAutomaticContext,
    resolve_contour_automatic_contract,
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
    ContourCutDirection,
    ContourLeadPolicy,
    ContourParameters,
    ContourProfileDescriptor,
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
    OperationParameterSet,
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
    geometry_profile: ContourProfileDescriptor | None = None


@dataclass(slots=True)
class ContourEditorDraftContext:
    """Typed transient geometry binding; never serialized or fingerprinted."""

    geometry_reference: GeometryReference | None
    pending_input_id: GeometryInputId | None = None
    geometry_profile: ContourProfileDescriptor | None = None


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
        "stepdown_mode",
        "axial_stock_allowance",
        "clearance_height",
        "retract_height",
        "lead_policy",
        "lead_in_mode",
        "lead_in_length",
        "lead_out_mode",
        "lead_out_length",
        "quality_profile",
        "automatic_summary",
        "automatic_stepdown",
        "automatic_lead_in",
        "automatic_lead_out",
        "automatic_lead_provenance",
        "automatic_entry_placement",
        "plunge_feed_rate",
        "finishing_pass",
        "machine_id",
        "enabled",
        "start_policy",
    }
)

_AUTOMATIC_REASON_TEXT = {
    "A validated closed Contour profile is required before AUTO setup.": (
        "Cần profile Contour kín đã xác minh trước khi dùng thiết lập AUTO."
    ),
    "Single-depth Contour does not use an automatic stepdown.": (
        "Contour một lớp không sử dụng stepdown tự động."
    ),
    "A supported cutter with explicit axial geometry and stickout is required.": (
        "Cần cutter được hỗ trợ với hình học cắt dọc trục và stickout rõ ràng."
    ),
    "Positive depth-span and usable axial capacity evidence is required.": (
        "Cần depth span dương và bằng chứng khả năng cắt dọc trục sử dụng được."
    ),
    "Requested depth exceeds validated cutter or assembly axial capacity.": (
        "Chiều sâu yêu cầu vượt khả năng dọc trục đã xác minh của cutter hoặc assembly."
    ),
    "Validated positive stepdown bounds are unavailable.": (
        "Không có giới hạn stepdown dương đã xác minh."
    ),
    "Derived from depth span, explicit axial cutting length, assembly stickout and quality profile.": (
        "Suy ra từ depth span, chiều dài cắt dọc trục, stickout và hồ sơ chất lượng."
    ),
    "A supported cutter and validated closed-profile lead clearance are required.": (
        "Cần cutter được hỗ trợ và khoảng trống lead của profile kín đã xác minh."
    ),
    "A supported cutter with explicit diameter geometry is required.": (
        "Cần cutter được hỗ trợ với hình học đường kính rõ ràng."
    ),
    "Ranked deterministic non-corner entry with validated tangent continuity.": (
        "Điểm vào không ở góc được xếp hạng ổn định với tiếp tuyến liên tục đã xác minh."
    ),
    "Ranked deterministic non-corner entry with validated normal linear fallback.": (
        "Điểm vào không ở góc được xếp hạng ổn định với lead tuyến tính pháp tuyến an toàn."
    ),
    "Cutter-scaled lead-in bounded by local segment, curvature and profile clearance.": (
        "Lead-in theo kích thước cutter, bị chặn bởi segment, độ cong và khoảng trống profile."
    ),
    "Cutter-scaled lead-out independently bounded by local exit geometry and profile clearance.": (
        "Lead-out theo kích thước cutter, được chặn độc lập bởi hình học thoát và khoảng trống profile."
    ),
}


def _automatic_reason(reason: str) -> str:
    return ui_text(_AUTOMATIC_REASON_TEXT.get(reason, reason))


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
    assembly_id: object | None = None,
) -> tuple[ToolAssembly | None, ToolDefinition | None, HolderDefinition | None]:
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
        "lead_in_length": str(scale),
        "lead_out_length": str(scale),
    }


def _stored_automatic_contract(
    context: ContourEditorContext,
) -> AutomaticParameterContract | None:
    raw = dict(context.operation.parameters.values).get(
        AUTOMATIC_PARAMETER_CONTRACT_KEY
    )
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("Contour automatic metadata không hợp lệ.")
    try:
        contract = AutomaticParameterContract.from_json(raw)
    except ValueError as error:
        raise ValueError("Contour automatic metadata bị hỏng.") from error
    if contract.policy_key != CONTOUR_AUTOMATIC_POLICY_KEY:
        raise ValueError("Contour automatic policy identity không hợp lệ.")
    return contract


def _quality_profile(value: object) -> CamQualityProfile:
    try:
        return CamQualityProfile(str(value))
    except ValueError as error:
        raise ValueError("Hồ sơ chất lượng tự động không hợp lệ.") from error


def _automatic_context(
    context: ContourEditorContext,
    draft: ContourEditorDraftContext,
    parameters: ContourParameters,
    values: Mapping[str, PresentationValue] | None = None,
) -> ContourAutomaticContext:
    assembly_id = None if values is None else values.get("tool_assembly_id")
    assembly, tool, _holder = _selected_tool(context, assembly_id)
    geometry = None if tool is None else tool.cutting_geometry
    diameter = getattr(geometry, "diameter", None)
    corner_radius = getattr(geometry, "corner_radius", None)
    axial = None if geometry is None else geometry.axial_cutting_length
    profile = draft.geometry_profile or context.geometry_profile
    dependency_parameters = parameters
    depth_span = parameters.top_height.value - parameters.final_cut_depth
    multiple_depth_passes = parameters.multiple_depth_passes
    if values is not None:
        try:
            unit = parameters.unit
            dependency_parameters = replace(
                parameters,
                side=ContourSide(_text(values["side"], "side")),
                direction=ContourCutDirection(
                    _text(values["direction"], "direction")
                ),
                radial_stock_allowance=Length(
                    _number(values["radial_stock_allowance"], "radial_stock_allowance"),
                    unit,
                ),
            )
            top_height = _number(values["top_height"], "top_height")
            final_depth = _number(values["final_depth"], "final_depth")
            axial_allowance = _number(
                values["axial_stock_allowance"], "axial_stock_allowance"
            )
            depth_span = top_height - (final_depth + axial_allowance)
            multiple_depth_passes = _boolean(
                values["multiple_depth_passes"], "multiple_depth_passes"
            )
        except (KeyError, TypeError, ValueError):
            # Preserve the last validated dependencies while an editor token is
            # incomplete. Final draft validation still fails closed.
            pass
    machining_loop = None
    source_loop = None
    if profile is not None and diameter is not None and diameter.value > 0.0:
        try:
            source_path, machining_loop, _polygon = prepare_contour_machining_geometry(
                profile,
                context.setup,
                dependency_parameters,
                diameter.to(parameters.unit).value,
            )
            source_loop = source_path.loop
        except (ContourGenerationError, TypeError, ValueError):
            machining_loop = None
            source_loop = None
    return ContourAutomaticContext(
        parameters.unit,
        None if tool is None else tool.family,
        None if diameter is None else diameter.to(parameters.unit).value,
        None if corner_radius is None else corner_radius.to(parameters.unit).value,
        None if axial is None else axial.to(parameters.unit).value,
        None if assembly is None else assembly.stickout.to(parameters.unit).value,
        depth_span if depth_span > 0.0 else None,
        None,
        dependency_parameters.side,
        multiple_depth_passes,
        machining_loop,
        source_loop,
        (
            None
            if profile is None
            else profile.geometry_fingerprint.digest
        ),
        None if tool is None else tool.content_fingerprint.digest,
    )


def _legacy_manual_contract(
    base: AutomaticParameterContract,
    parameters: ContourParameters,
) -> AutomaticParameterContract:
    legacy = {
        "stepdown": parameters.stepdown.value,
        "lead_in_length": parameters.lead_length.value,
        "lead_out_length": parameters.lead_length.value,
    }
    values: list[AutomaticParameterValue] = []
    for item in base.values:
        if item.key not in CONTOUR_AUTOMATIC_USER_KEYS:
            values.append(item)
            continue
        values.append(
            replace(
                item,
                mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                override_value=legacy[item.key],
                validation=AutomaticValidationResult(True),
                reason="Legacy explicit numeric value preserved as intentional manual override.",
            )
        )
    return replace(base, values=tuple(values))


def _recompute_automatic_contract(
    context: ContourEditorContext,
    draft: ContourEditorDraftContext,
    *,
    values: Mapping[str, PresentationValue] | None = None,
) -> AutomaticParameterContract:
    parameters = ContourParameters.from_operation_parameters(context.operation.parameters)
    stored = _stored_automatic_contract(context)
    profile = (
        _quality_profile(values["quality_profile"])
        if values is not None and "quality_profile" in values
        else stored.quality_profile
        if stored is not None
        else CamQualityProfile.BALANCED
    )
    base = resolve_contour_automatic_contract(
        _automatic_context(context, draft, parameters, values),
        quality_profile=profile,
    )
    if values is None:
        if stored is None:
            return _legacy_manual_contract(base, parameters)
        merged: list[AutomaticParameterValue] = []
        for item in base.values:
            if item.key not in CONTOUR_AUTOMATIC_USER_KEYS:
                merged.append(item)
                continue
            try:
                previous = stored.value(item.key)
            except KeyError:
                legacy_value = {
                    "stepdown": parameters.stepdown.value,
                    "lead_in_length": parameters.lead_length.value,
                    "lead_out_length": parameters.lead_length.value,
                }[item.key]
                merged.append(
                    replace(
                        item,
                        mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                        override_value=legacy_value,
                        validation=AutomaticValidationResult(True),
                        reason=(
                            "Missing additive field loaded as preserved legacy manual intent."
                        ),
                    )
                )
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
            elif previous.mode is AutomaticParameterMode.AUTO:
                merged.append(
                    item
                    if item.status is AutomaticParameterStatus.RESOLVED
                    else replace(item, mode=AutomaticParameterMode.AUTO)
                )
            else:
                merged.append(item)
        return replace(base, values=tuple(merged))
    updated: list[AutomaticParameterValue] = []
    mode_fields = {
        "stepdown": "stepdown_mode",
        "lead_in_length": "lead_in_mode",
        "lead_out_length": "lead_out_mode",
    }
    for item in base.values:
        mode_key = mode_fields.get(item.key)
        if mode_key is None or mode_key not in values:
            updated.append(item)
            continue
        try:
            mode = AutomaticParameterMode(str(values[mode_key]))
        except ValueError as error:
            raise ValueError(f"Chế độ {item.key} không hợp lệ.") from error
        if mode is AutomaticParameterMode.AUTO:
            if item.status is not AutomaticParameterStatus.RESOLVED:
                previous = None
                if stored is not None:
                    try:
                        previous = stored.value(item.key)
                    except KeyError:
                        pass
                if previous is not None and previous.mode is AutomaticParameterMode.AUTO:
                    updated.append(replace(item, mode=AutomaticParameterMode.AUTO))
                    continue
                raise ValueError(f"{item.key} chưa đủ evidence để dùng AUTO: {item.reason}")
            updated.append(item)
            continue
        if mode not in {
            AutomaticParameterMode.MANUAL,
            AutomaticParameterMode.MANUAL_OVERRIDE,
        }:
            raise ValueError(f"{item.key} không hỗ trợ chế độ đã chọn.")
        value_key = item.key
        override = _number(values[value_key], value_key)
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
        return f"Không áp dụng · {_automatic_reason(value.reason)}"
    if (
        value.mode is AutomaticParameterMode.AUTO
        and (
            value.status is not AutomaticParameterStatus.RESOLVED
            or value.effective_value is None
        )
    ):
        return f"Tự động không khả dụng · {_automatic_reason(value.reason)}"
    prefix = "Tự động" if value.mode is AutomaticParameterMode.AUTO else "Tùy chỉnh"
    suffix = " · đã giới hạn an toàn" if value.clamped else ""
    return f"{prefix} · {float(value.effective_value):g} {unit}{suffix}"


def _automatic_presentation(
    contract: AutomaticParameterContract,
    unit: str,
) -> dict[str, PresentationValue]:
    entries = [contract.value(key) for key in CONTOUR_AUTOMATIC_USER_KEYS]
    auto = sum(
        item.mode is AutomaticParameterMode.AUTO
        and item.status is AutomaticParameterStatus.RESOLVED
        for item in entries
    )
    manual = sum(item.has_manual_override for item in entries)
    unavailable = len(entries) - auto - manual
    lead_form = contract.value("lead_form")
    entry = contract.value("entry_segment_index")
    result: dict[str, PresentationValue] = {
        "quality_profile": contract.quality_profile.value,
        "automatic_summary": (
            f"{auto} tự động · {manual} tùy chỉnh · {unavailable} không áp dụng"
        ),
        "automatic_stepdown": _automatic_text(contract.value("stepdown"), unit),
        "automatic_lead_in": _automatic_text(
            contract.value("lead_in_length"), unit
        ),
        "automatic_lead_out": _automatic_text(
            contract.value("lead_out_length"), unit
        ),
        "automatic_lead_provenance": (
            f"Không khả dụng · {_automatic_reason(lead_form.reason)}"
            if lead_form.status is not AutomaticParameterStatus.RESOLVED
            else f"{lead_form.effective_value} · {_automatic_reason(lead_form.reason)}"
        ),
        "automatic_entry_placement": (
            f"Không khả dụng · {_automatic_reason(entry.reason)}"
            if entry.status is not AutomaticParameterStatus.RESOLVED
            else f"segment {entry.effective_value} · {_automatic_reason(entry.reason)}"
        ),
    }
    field_names = {
        "stepdown": ("stepdown_mode", "stepdown"),
        "lead_in_length": ("lead_in_mode", "lead_in_length"),
        "lead_out_length": ("lead_out_mode", "lead_out_length"),
    }
    for key, (mode_key, value_key) in field_names.items():
        item = contract.value(key)
        result[mode_key] = item.mode.value
        if item.effective_value is not None:
            result[value_key] = str(item.effective_value)
    return result


def contour_draft_transform(
    context: ContourEditorContext,
    draft: ContourEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    """Recompute AUTO fields after Tool/geometry/depth/quality edits."""
    contract = _recompute_automatic_contract(context, draft, values=values)
    return _automatic_presentation(contract, context.setup.wcs.origin.unit.value)


def contour_applied_values(
    context: ContourEditorContext,
) -> dict[str, PresentationValue]:
    """Map every production field to deterministic presentation primitives."""
    parameters = ContourParameters.from_operation_parameters(context.operation.parameters)
    draft = ContourEditorDraftContext(
        context.geometry_reference,
        geometry_profile=context.geometry_profile,
    )
    automatic = _recompute_automatic_contract(context, draft)
    tool_text, holder_text, _assembly_name = _tool_summaries(context)
    reference = context.geometry_reference
    values: dict[str, PresentationValue] = {
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
        "lead_in_length": str(parameters.lead_length.value),
        "lead_out_length": str(parameters.lead_length.value),
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
    values.update(
        _automatic_presentation(automatic, context.setup.wcs.origin.unit.value)
    )
    return values


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
    draft: ContourEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> tuple[ContourParameters, AutomaticParameterContract]:
    complete = _complete_values(context, values)
    automatic = _recompute_automatic_contract(context, draft, values=complete)
    automatic_values = _automatic_presentation(
        automatic, context.setup.wcs.origin.unit.value
    )
    complete.update(automatic_values)
    unit = context.setup.wcs.origin.unit
    feed_unit = FeedUnit.MM_PER_MINUTE if unit.value == "mm" else FeedUnit.INCH_PER_MINUTE
    parameters = ContourParameters(
        unit,
        ContourProfileSource(_text(complete["profile_source"], "profile_source")),
        ContourSide(_text(complete["side"], "side")),
        Length(_number(complete["top_height"], "top_height"), unit),
        Length(_number(complete["final_depth"], "final_depth"), unit),
        Length(
            (
                _number(complete["stepdown"], "stepdown")
                if automatic.value("stepdown").effective_value is None
                else float(automatic.value("stepdown").effective_value)
            ),
            unit,
        ),
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
        Length(
            (
                _number(complete["lead_in_length"], "lead_in_length")
                if automatic.value("lead_in_length").effective_value is None
                else float(automatic.value("lead_in_length").effective_value)
            ),
            unit,
        ),
        _boolean(complete["finishing_pass"], "finishing_pass"),
        _boolean(complete["multiple_depth_passes"], "multiple_depth_passes"),
    )
    return parameters, automatic


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
    parameters, automatic = _parameters_from_values(context, draft, complete)
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
    base_parameters = parameters.to_operation_parameters()
    previous_values = contour_applied_values(context)
    automatic_changed = any(
        complete.get(key) != previous_values.get(key)
        for key in (
            "quality_profile",
            "stepdown_mode",
            "stepdown",
            "lead_in_mode",
            "lead_in_length",
            "lead_out_mode",
            "lead_out_length",
        )
    )
    persist_automatic = (
        _stored_automatic_contract(context) is not None
        or automatic_changed
        or any(
            automatic.value(key).mode is AutomaticParameterMode.AUTO
            for key in CONTOUR_AUTOMATIC_USER_KEYS
        )
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
    source: FunctionEditorValueSource = FunctionEditorValueSource.USER,
) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.NUMBER,
        value,
        unit=unit,
        source=source,
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


def _automatic_mode_field(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    order: int,
    auto_available: bool,
    applicable_when: FunctionEditorApplicability | None = None,
) -> FunctionEditorField:
    choices = (
        (
            AutomaticParameterMode.AUTO.value,
            AutomaticParameterMode.MANUAL_OVERRIDE.value,
        )
        if auto_available
        else (
            (
                AutomaticParameterMode.AUTO.value,
                AutomaticParameterMode.MANUAL_OVERRIDE.value,
            )
            if value == AutomaticParameterMode.AUTO.value
            else (AutomaticParameterMode.MANUAL_OVERRIDE.value,)
        )
    )
    labels = (
        (
            (AutomaticParameterMode.AUTO.value, "Tự động"),
            (AutomaticParameterMode.MANUAL_OVERRIDE.value, "Tùy chỉnh"),
        )
        if auto_available
        else (
            (
                (AutomaticParameterMode.AUTO.value, "Tự động · hiện không khả dụng"),
                (AutomaticParameterMode.MANUAL_OVERRIDE.value, "Tùy chỉnh"),
            )
            if value == AutomaticParameterMode.AUTO.value
            else ((AutomaticParameterMode.MANUAL_OVERRIDE.value, "Tùy chỉnh · AUTO không khả dụng"),)
        )
    )
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.CHOICE,
        value,
        required=True,
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        applicable_when=applicable_when,
        choices=choices,
        choice_labels=labels,
        tooltip=(
            "AUTO recompute theo Tool, profile, depth và quality; override được giữ nguyên."
            if auto_available
            else "AUTO không khả dụng vì thiếu evidence hình học hoặc cutter hợp lệ."
        ),
        help_key=f"contour.{field_id}",
        order=order,
        binding_key=f"automatic.{field_id}",
    )


def _choice_labels(enum_type, labels: dict[object, str]) -> tuple[tuple[PresentationValue, str], ...]:
    return tuple((item.value, labels.get(item, item.value)) for item in enum_type)


def build_contour_sections(
    context: ContourEditorContext,
) -> tuple[FunctionEditorSection, ...]:
    """Build deterministic operator-oriented sections over Contour v1 only."""
    values = contour_applied_values(context)
    automatic_contract = _recompute_automatic_contract(
        context,
        ContourEditorDraftContext(
            context.geometry_reference,
            geometry_profile=context.geometry_profile,
        ),
    )
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
    automatic = FunctionEditorSection(
        "automatic_parameters",
        "THAM SỐ TỰ ĐỘNG",
        (
            FunctionEditorField(
                "automatic_summary",
                "Contour 2D Auto Setup",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_summary"],
                source=FunctionEditorValueSource.DERIVED,
                tooltip="Tóm tắt chế độ tự động, giá trị tùy chỉnh và tham số thiếu bằng chứng.",
                help_key="contour.automatic_summary",
                order=10,
                binding_key="automatic.summary",
                action_id="use_contour_automatic_parameters",
                action_label="Dùng tự động khả dụng",
            ),
            FunctionEditorField(
                "automatic_stepdown",
                "Stepdown tự động",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_stepdown"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="contour.automatic_stepdown",
                order=20,
                binding_key="automatic.stepdown_summary",
            ),
            FunctionEditorField(
                "automatic_lead_in",
                "Lead-in tự động",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_lead_in"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="contour.automatic_lead_in",
                order=30,
                binding_key="automatic.lead_in_summary",
            ),
            FunctionEditorField(
                "automatic_lead_out",
                "Lead-out tự động",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_lead_out"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="contour.automatic_lead_out",
                order=40,
                binding_key="automatic.lead_out_summary",
            ),
            FunctionEditorField(
                "quality_profile",
                "Hồ sơ chất lượng",
                FunctionEditorFieldKind.CHOICE,
                values["quality_profile"],
                required=True,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                choices=tuple(item.value for item in CamQualityProfile),
                choice_labels=(
                    (CamQualityProfile.FAST.value, "Nhanh"),
                    (CamQualityProfile.BALANCED.value, "Cân bằng"),
                    (CamQualityProfile.HIGH.value, "Chất lượng cao"),
                ),
                tooltip="Điều chỉnh tỷ lệ stepdown/lead trong giới hạn hình học đã xác minh.",
                help_key="contour.quality_profile",
                order=50,
                binding_key="automatic.quality_profile",
            ),
            FunctionEditorField(
                "automatic_lead_provenance",
                "Nguồn gốc lead",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_lead_provenance"],
                source=FunctionEditorValueSource.DERIVED,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_key="contour.automatic_lead_provenance",
                order=60,
                binding_key="automatic.lead_provenance",
            ),
            FunctionEditorField(
                "automatic_entry_placement",
                "Vị trí vào/ra",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_entry_placement"],
                source=FunctionEditorValueSource.GEOMETRY,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_key="contour.automatic_entry_placement",
                order=70,
                binding_key="automatic.entry_placement",
            ),
        ),
        "Giá trị dẫn xuất, nguồn gốc và trạng thái dự phòng an toàn của biên dạng kín.",
        order=35,
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
            _automatic_mode_field(
                "stepdown_mode",
                "Chế độ stepdown",
                values["stepdown_mode"],
                order=35,
                auto_available=(
                    automatic_contract.value("stepdown").status
                    is AutomaticParameterStatus.RESOLVED
                ),
                applicable_when=FunctionEditorApplicability(
                    "multiple_depth_passes", ApplicabilityOperator.TRUTHY
                ),
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
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                source=(
                    FunctionEditorValueSource.DERIVED
                    if values["stepdown_mode"] == AutomaticParameterMode.AUTO.value
                    else FunctionEditorValueSource.USER
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
            _automatic_mode_field(
                "lead_in_mode",
                "Chế độ lead-in",
                values["lead_in_mode"],
                order=40,
                auto_available=(
                    automatic_contract.value("lead_in_length").status
                    is AutomaticParameterStatus.RESOLVED
                ),
            ),
            _number_field(
                "lead_in_length",
                "Chiều dài lead-in",
                values["lead_in_length"],
                unit=unit,
                binding_key="automatic.lead_in_length",
                order=50,
                default=defaults.get("lead_in_length"),
                validators=(
                    _minimum("contour.lead_in_positive", "Chiều dài lead-in phải lớn hơn 0."),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                source=(
                    FunctionEditorValueSource.DERIVED
                    if values["lead_in_mode"] == AutomaticParameterMode.AUTO.value
                    else FunctionEditorValueSource.USER
                ),
            ),
            _automatic_mode_field(
                "lead_out_mode",
                "Chế độ lead-out",
                values["lead_out_mode"],
                order=60,
                auto_available=(
                    automatic_contract.value("lead_out_length").status
                    is AutomaticParameterStatus.RESOLVED
                ),
            ),
            _number_field(
                "lead_out_length",
                "Chiều dài lead-out",
                values["lead_out_length"],
                unit=unit,
                binding_key="automatic.lead_out_length",
                order=70,
                default=defaults.get("lead_out_length"),
                validators=(
                    _minimum("contour.lead_out_positive", "Chiều dài lead-out phải lớn hơn 0."),
                ),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                source=(
                    FunctionEditorValueSource.DERIVED
                    if values["lead_out_mode"] == AutomaticParameterMode.AUTO.value
                    else FunctionEditorValueSource.USER
                ),
            ),
            _number_field(
                "plunge_feed_rate",
                "Feed tiếp cận / rút",
                values["plunge_feed_rate"],
                unit=feed_unit,
                binding_key="parameters.plunge_feed_rate",
                order=80,
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
    return (
        basic,
        geometry,
        tool,
        automatic,
        cutting,
        levels,
        linking,
        advanced,
        expert,
    )


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
    "contour_draft_transform",
    "prepare_contour_update",
    "validate_contour_schema_contract",
]

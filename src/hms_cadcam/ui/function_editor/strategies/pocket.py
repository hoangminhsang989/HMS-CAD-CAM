"""Production Function Editor binding for the unchanged Pocket 2.5D v1 domain."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math

from hms_cadcam.cam.application import (
    PocketGenerationError,
    pocket_depth_levels,
    prepare_pocket_machining_geometry,
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
from hms_cadcam.cam.automatic_pocket import (
    POCKET_AUTOMATIC_POLICY_KEY,
    POCKET_AUTOMATIC_USER_KEYS,
    PocketAutomaticContext,
    pocket_geometric_stepover_target,
    resolve_pocket_automatic_contract,
)
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
    OperationParameterSet,
    PocketCuttingDirection,
    PocketDepthDefinition,
    PocketEntryPolicy,
    PocketGeometryInput,
    PocketRegion,
    PocketStrategy,
    REST_POCKET_STRATEGY_KEY,
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
    geometry_region: PocketRegion | None = None
    material_state_source: str = "Không áp dụng"
    material_state_status: str = "Không áp dụng"


@dataclass(slots=True)
class PocketEditorDraftContext:
    """Typed transient boundary binding; never serialized or fingerprinted."""

    geometry_reference: GeometryReference | None
    pending_input_id: GeometryInputId | None = None
    geometry_region: PocketRegion | None = None


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
        "stepover_mode",
        "radial_stock_allowance",
        "cutting_feed_rate",
        "spindle_speed",
        "top_z",
        "bottom_z",
        "final_depth_summary",
        "stepdown",
        "stepdown_mode",
        "level_count",
        "axial_allowance",
        "entry_policy",
        "plunge_feed_rate",
        "clearance_height",
        "retract_height",
        "machine_id",
        "enabled",
        "tolerance",
        "quality_profile",
        "automatic_summary",
        "automatic_stepdown",
        "automatic_stepover",
        "automatic_entry_location",
        "automatic_entry_form",
        "automatic_linking",
        "automatic_provenance",
        "material_state_source",
        "material_state_status",
    }
)
_REST_FIELD_IDS = _FIELD_IDS | {"lead_in_length"}
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
        "lead_in_length",
    }
)

_AUTOMATIC_REASON_TEXT = {
    "A validated cutter-accessible closed Pocket region is required.": "Cần vùng Pocket kín mà tâm dao tiếp cận được và đã xác minh.",
    "Pocket AUTO requires a generator-supported End Mill with axial geometry and stickout.": "Pocket AUTO cần dao phay ngón được generator hỗ trợ, có hình học cắt dọc trục và stickout rõ ràng.",
    "Positive Pocket depth span and usable axial capacity are required.": "Cần khoảng sâu Pocket dương và khả năng cắt dọc trục sử dụng được.",
    "Pocket depth exceeds validated axial cutting length or stickout.": "Chiều sâu Pocket vượt quá chiều dài cắt dọc trục hoặc stickout đã xác minh.",
    "Validated positive Pocket stepdown bounds are unavailable.": "Không có giới hạn stepdown Pocket dương đã xác minh.",
    "Derived from Pocket depth span, axial cutting length, stickout and quality profile.": "Suy ra từ khoảng sâu Pocket, chiều dài cắt dọc trục, stickout và hồ sơ chất lượng.",
    "Production Pocket offset geometry did not prove a reachable cutter-centre region.": "Hình học offset Pocket production chưa chứng minh được vùng tâm dao tiếp cận được.",
    "Pocket AUTO stepover requires a generator-supported End Mill diameter.": "Stepover AUTO cần đường kính dao phay ngón được generator hỗ trợ.",
    "Validated positive Pocket stepover bounds are unavailable.": "Không có giới hạn stepover Pocket dương đã xác minh.",
    "Geometric Pocket coverage derived from cutter diameter and quality profile; no material-load claim.": "Độ phủ hình học Pocket suy ra từ đường kính dao và hồ sơ chất lượng; không tuyên bố tải vật liệu.",
    "No deterministic cutter-centre-accessible Pocket entry location was proven.": "Chưa chứng minh được vị trí vào Pocket xác định mà tâm dao tiếp cận được.",
    "Ranked deterministic Pocket entry by local boundary clearance and stable geometry tie-break.": "Xếp hạng vị trí vào Pocket xác định theo khoảng hở biên cục bộ và quy tắc hòa hình học ổn định.",
    "Validated cutter-centre clearance to the closed Pocket outer boundary.": "Khoảng hở tâm dao tới biên ngoài Pocket kín đã được xác minh.",
    "Vertical plunge is the only generator form and Tool metadata does not prove center-cutting capability.": "Cắm thẳng là kiểu duy nhất của generator và metadata Tool chưa chứng minh khả năng cắt tâm.",
    "Existing retract linking is preserved because no complete stay-down path validator exists.": "Giữ liên kết rút dao hiện có vì chưa có bộ xác minh đầy đủ cho đường stay-down.",
    "Legacy explicit Pocket numeric value preserved as manual intent.": "Giữ giá trị số Pocket cũ như ý định thủ công.",
    "Missing additive field loaded as preserved legacy manual intent.": "Trường bổ sung bị thiếu được nạp như ý định thủ công cũ.",
    "Explicit Advanced manual override.": "Tùy chỉnh thủ công rõ ràng trong Advanced.",
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
    context: PocketEditorContext,
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


def _stored_automatic_contract(
    context: PocketEditorContext,
) -> AutomaticParameterContract | None:
    raw = dict(context.operation.parameters.values).get(
        AUTOMATIC_PARAMETER_CONTRACT_KEY
    )
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("Pocket automatic metadata is invalid.")
    try:
        contract = AutomaticParameterContract.from_json(raw)
    except ValueError as error:
        raise ValueError("Pocket automatic metadata is malformed.") from error
    if contract.policy_key != POCKET_AUTOMATIC_POLICY_KEY:
        raise ValueError("Pocket automatic policy identity is invalid.")
    return contract


def _quality_profile(value: object) -> CamQualityProfile:
    try:
        return CamQualityProfile(str(value))
    except ValueError as error:
        raise ValueError("Pocket quality profile is invalid.") from error


def _automatic_context(
    context: PocketEditorContext,
    draft: PocketEditorDraftContext,
    parameters: PocketStrategy,
    quality_profile: CamQualityProfile,
    *,
    values: Mapping[str, PresentationValue] | None = None,
    stepover_probe: float | None = None,
) -> PocketAutomaticContext:
    assembly_id = None if values is None else values.get("tool_assembly_id")
    assembly, tool, _holder = _selected_tool(context, assembly_id)
    geometry = None if tool is None else tool.cutting_geometry
    diameter = getattr(geometry, "diameter", None)
    axial = None if geometry is None else geometry.axial_cutting_length
    region = draft.geometry_region or context.geometry_region
    dependency_parameters = parameters
    depth_span = parameters.top_z.value - parameters.final_depth.value
    tolerance = parameters.tolerance.value
    if values is not None:
        try:
            unit = parameters.unit
            top_z = _number(values["top_z"], "top_z")
            bottom_z = _number(values["bottom_z"], "bottom_z")
            axial_allowance = _number(
                values["axial_allowance"], "axial_allowance"
            )
            tolerance = _number(values["tolerance"], "tolerance")
            dependency_parameters = replace(
                parameters,
                depth=PocketDepthDefinition(
                    unit,
                    Length(top_z, unit),
                    Length(bottom_z, unit),
                    Length(axial_allowance, unit),
                ),
                radial_stock_allowance=Length(
                    _number(
                        values["radial_stock_allowance"],
                        "radial_stock_allowance",
                    ),
                    unit,
                ),
                cutting_direction=PocketCuttingDirection(
                    _text(values["cutting_direction"], "cutting_direction")
                ),
                tolerance=Length(tolerance, unit),
            )
            depth_span = top_z - (bottom_z + axial_allowance)
        except (KeyError, TypeError, ValueError):
            pass
    if stepover_probe is None:
        stepover_probe = parameters.stepover.value
    source_loop = None
    offset_loops: tuple = ()
    accessibility_result = "unresolved"
    if (
        region is not None
        and diameter is not None
        and diameter.unit is parameters.unit
        and diameter.value > 0.0
    ):
        try:
            try:
                target, _lower, _upper, _clamped = pocket_geometric_stepover_target(
                    diameter.value,
                    max(tolerance, 1.0e-9),
                    quality_profile,
                )
            except ValueError:
                target = stepover_probe
            source_path, offset_loops = prepare_pocket_machining_geometry(
                region,
                context.setup,
                tool_diameter=diameter.value,
                radial_stock_allowance=dependency_parameters.radial_stock_allowance.value,
                stepover=(
                    stepover_probe
                    if stepover_probe is not None
                    and stepover_probe > 0.0
                    else target
                ),
                tolerance=tolerance,
                cutting_direction=dependency_parameters.cutting_direction,
            )
            source_loop = source_path.loop
            accessibility_result = "reachable" if offset_loops else "empty"
        except (PocketGenerationError, TypeError, ValueError):
            accessibility_result = "unresolved"
    return PocketAutomaticContext(
        parameters.unit,
        None if tool is None else tool.family,
        None if diameter is None else diameter.to(parameters.unit).value,
        None if axial is None else axial.to(parameters.unit).value,
        None if assembly is None else assembly.stickout.to(parameters.unit).value,
        depth_span if depth_span > 0.0 else None,
        tolerance if tolerance > 0.0 else None,
        source_loop,
        tuple(offset_loops),
        None if region is None else region.fingerprint.digest,
        None if region is None else region.boundary.fingerprint.digest,
        None,
        None if tool is None else tool.content_fingerprint.digest,
        accessibility_result,
    )


def _legacy_manual_contract(
    base: AutomaticParameterContract,
    strategy: PocketStrategy,
) -> AutomaticParameterContract:
    legacy = {
        "stepdown": strategy.stepdown.value,
        "stepover": strategy.stepover.value,
    }
    return replace(
        base,
        values=tuple(
            replace(
                item,
                mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                override_value=legacy[item.key],
                validation=AutomaticValidationResult(True),
                reason="Legacy explicit Pocket numeric value preserved as manual intent.",
            )
            if item.key in POCKET_AUTOMATIC_USER_KEYS
            else item
            for item in base.values
        ),
    )


def _recompute_automatic_contract(
    context: PocketEditorContext,
    draft: PocketEditorDraftContext,
    *,
    values: Mapping[str, PresentationValue] | None = None,
) -> AutomaticParameterContract:
    strategy = PocketStrategy.from_operation_parameters(
        context.operation.parameters,
        context.geometry_reference
        or (draft.geometry_reference if draft.geometry_reference is not None else None),
    )
    stored = _stored_automatic_contract(context)
    profile = (
        _quality_profile(values["quality_profile"])
        if values is not None and "quality_profile" in values
        else stored.quality_profile
        if stored is not None
        else CamQualityProfile.BALANCED
    )
    assembly_id = None if values is None else values.get("tool_assembly_id")
    assembly, tool, _holder = _selected_tool(context, assembly_id)
    diameter = (
        getattr(tool.cutting_geometry, "diameter", None) if tool is not None else None
    )
    draft_tolerance = strategy.tolerance.value
    if values is not None and "tolerance" in values:
        try:
            candidate_tolerance = _number(values["tolerance"], "tolerance")
        except (TypeError, ValueError):
            pass
        else:
            if candidate_tolerance > 0.0:
                draft_tolerance = candidate_tolerance
    stepover_probe = strategy.stepover.value
    mode_value = None if values is None else values.get("stepover_mode")
    if mode_value is not None and str(mode_value) == AutomaticParameterMode.AUTO.value:
        if diameter is not None:
            try:
                stepover_probe, _lower, _upper, _clamped = pocket_geometric_stepover_target(
                    diameter.to(strategy.unit).value,
                    max(draft_tolerance, 1.0e-9),
                    profile,
                )
            except ValueError:
                pass
    elif mode_value is not None:
        stepover_probe = _number(values["stepover"], "stepover")
    elif stored is not None:
        try:
            previous = stored.value("stepover")
        except KeyError:
            previous = None
        if previous is not None and previous.has_manual_override:
            stepover_probe = float(previous.override_value)
        elif previous is not None and previous.mode is AutomaticParameterMode.AUTO and diameter is not None:
            try:
                stepover_probe, _lower, _upper, _clamped = pocket_geometric_stepover_target(
                    diameter.to(strategy.unit).value,
                    max(draft_tolerance, 1.0e-9),
                    profile,
                )
            except ValueError:
                pass
    base = resolve_pocket_automatic_contract(
        _automatic_context(
            context,
            draft,
            strategy,
            profile,
            values=values,
            stepover_probe=stepover_probe,
        ),
        quality_profile=profile,
    )
    if values is None:
        if stored is None:
            return _legacy_manual_contract(base, strategy)
        merged: list[AutomaticParameterValue] = []
        for item in base.values:
            if item.key not in POCKET_AUTOMATIC_USER_KEYS:
                merged.append(item)
                continue
            try:
                previous = stored.value(item.key)
            except KeyError:
                merged.append(
                    replace(
                        item,
                        mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                        override_value={
                            "stepdown": strategy.stepdown.value,
                            "stepover": strategy.stepover.value,
                        }[item.key],
                        validation=AutomaticValidationResult(True),
                        reason="Missing additive field loaded as preserved legacy manual intent.",
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
    mode_fields = {"stepdown": "stepdown_mode", "stepover": "stepover_mode"}
    for item in base.values:
        mode_key = mode_fields.get(item.key)
        if mode_key is None or mode_key not in values:
            updated.append(item)
            continue
        try:
            mode = AutomaticParameterMode(str(values[mode_key]))
        except ValueError as error:
            raise ValueError(f"Pocket {item.key} mode is invalid.") from error
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
                raise ValueError(f"Pocket AUTO evidence is unavailable: {item.reason}")
            updated.append(item)
            continue
        override = _number(values[item.key], item.key)
        if override <= 0.0:
            raise ValueError(f"Pocket {item.key} override must be positive.")
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


def _unbound_automatic_contract(
    context: PocketEditorContext,
) -> AutomaticParameterContract:
    data = _parameter_data(context)
    unit = context.setup.wcs.origin.unit
    assembly, tool, _holder = _selected_tool(context)
    geometry = None if tool is None else tool.cutting_geometry
    diameter = getattr(geometry, "diameter", None)
    axial = None if geometry is None else geometry.axial_cutting_length
    depth_span = (
        _number(data["top_z"], "top_z")
        - _number(data["bottom_z"], "bottom_z")
        - _number(data["axial_allowance"], "axial_allowance")
    )
    stored = _stored_automatic_contract(context)
    profile = (
        stored.quality_profile
        if stored is not None
        else CamQualityProfile.BALANCED
    )
    base = resolve_pocket_automatic_contract(
        PocketAutomaticContext(
            unit,
            None if tool is None else tool.family,
            None if diameter is None else diameter.to(unit).value,
            None if axial is None else axial.to(unit).value,
            None if assembly is None else assembly.stickout.to(unit).value,
            depth_span if depth_span > 0.0 else None,
            _number(data["tolerance"], "tolerance"),
            None,
            (),
            None,
            None,
            None,
            None if tool is None else tool.content_fingerprint.digest,
            "unresolved",
        ),
        quality_profile=profile,
    )
    current = {
        "stepdown": _number(data["stepdown"], "stepdown"),
        "stepover": _number(data["stepover"], "stepover"),
    }
    if stored is None:
        return replace(
            base,
            values=tuple(
                replace(
                    item,
                    mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                    override_value=current[item.key],
                    validation=AutomaticValidationResult(True),
                    reason="Legacy explicit Pocket numeric value preserved as manual intent.",
                )
                if item.key in POCKET_AUTOMATIC_USER_KEYS
                else item
                for item in base.values
            ),
        )
    merged: list[AutomaticParameterValue] = []
    for item in base.values:
        if item.key not in POCKET_AUTOMATIC_USER_KEYS:
            merged.append(item)
            continue
        try:
            previous = stored.value(item.key)
        except KeyError:
            previous = None
        if previous is not None and previous.has_manual_override:
            merged.append(
                replace(
                    item,
                    mode=AutomaticParameterMode.MANUAL_OVERRIDE,
                    override_value=previous.override_value,
                    validation=previous.validation,
                )
            )
        elif previous is not None and previous.mode is AutomaticParameterMode.AUTO:
            merged.append(replace(item, mode=AutomaticParameterMode.AUTO))
        else:
            merged.append(item)
    return replace(base, values=tuple(merged))


def _current_automatic_contract(
    context: PocketEditorContext,
) -> AutomaticParameterContract:
    if context.geometry_reference is None:
        return _unbound_automatic_contract(context)
    return _recompute_automatic_contract(
        context,
        PocketEditorDraftContext(
            context.geometry_reference,
            geometry_region=context.geometry_region,
        ),
    )


def _automatic_text(value: AutomaticParameterValue, unit: str) -> str:
    if value.mode is AutomaticParameterMode.NOT_APPLICABLE:
        return f"{ui_text('Không khả dụng')} · {_automatic_reason(value.reason)}"
    if (
        value.mode is AutomaticParameterMode.AUTO
        and (
            value.status is not AutomaticParameterStatus.RESOLVED
            or value.effective_value is None
        )
    ):
        return f"{ui_text('Tự động không khả dụng')} · {_automatic_reason(value.reason)}"
    prefix = (
        ui_text("Tự động")
        if value.mode is AutomaticParameterMode.AUTO
        else ui_text("Tùy chỉnh")
    )
    suffix = f" · {ui_text('đã giới hạn an toàn')}" if value.clamped else ""
    value_text = (
        f"{float(value.effective_value):g} {unit}"
        if isinstance(value.effective_value, (int, float))
        else str(value.effective_value)
    )
    return f"{prefix} · {value_text}{suffix}"


def _automatic_presentation(
    contract: AutomaticParameterContract,
    unit: str,
) -> dict[str, PresentationValue]:
    entries = [contract.value(key) for key in POCKET_AUTOMATIC_USER_KEYS]
    auto = sum(
        item.mode is AutomaticParameterMode.AUTO
        and item.status is AutomaticParameterStatus.RESOLVED
        for item in entries
    )
    manual = sum(item.has_manual_override for item in entries)
    unavailable = len(entries) - auto - manual
    entry_index = contract.value("entry_segment_index")
    entry_x = contract.value("entry_point_x")
    entry_y = contract.value("entry_point_y")
    entry_form = contract.value("entry_form")
    linking = contract.value("linking_mode")
    if entry_index.status is AutomaticParameterStatus.RESOLVED:
        entry_text = (
            f"{ui_text('Đoạn')} {entry_index.effective_value} · "
            f"({float(entry_x.effective_value):g}, {float(entry_y.effective_value):g})"
        )
    else:
        entry_text = (
            f"{ui_text('Không khả dụng')} · {_automatic_reason(entry_index.reason)}"
        )
    result: dict[str, PresentationValue] = {
        "quality_profile": contract.quality_profile.value,
        "automatic_summary": (
            f"{auto} AUTO · {manual} {ui_text('tùy chỉnh')} · "
            f"{unavailable} {ui_text('không khả dụng')}"
        ),
        "automatic_stepdown": _automatic_text(contract.value("stepdown"), unit),
        "automatic_stepover": _automatic_text(contract.value("stepover"), unit),
        "automatic_entry_location": entry_text,
        "automatic_entry_form": (
            ui_text("Không khả dụng") + " · " + _automatic_reason(entry_form.reason)
            if entry_form.status is not AutomaticParameterStatus.RESOLVED
            else str(entry_form.effective_value)
        ),
        "automatic_linking": (
            ui_text("Giữ hành vi rút dao hiện có")
            + " · "
            + _automatic_reason(linking.reason)
            if linking.status is not AutomaticParameterStatus.RESOLVED
            else str(linking.effective_value)
        ),
        "automatic_provenance": (
            f"Stepdown: {_automatic_reason(contract.value('stepdown').reason)} "
            f"Stepover: {_automatic_reason(contract.value('stepover').reason)}"
        ),
    }
    for key, mode_key in (
        ("stepdown", "stepdown_mode"),
        ("stepover", "stepover_mode"),
    ):
        item = contract.value(key)
        result[mode_key] = item.mode.value
        if item.effective_value is not None:
            result[key] = str(item.effective_value)
    return result


def pocket_draft_transform(
    context: PocketEditorContext,
    draft: PocketEditorDraftContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    """Recompute Pocket AUTO values after Tool, geometry, depth or quality edits."""
    contract = _recompute_automatic_contract(context, draft, values=values)
    return _automatic_presentation(contract, context.setup.wcs.origin.unit.value)


def _parameter_data(context: PocketEditorContext) -> dict[str, object]:
    parameters = context.operation.parameters
    if parameters.strategy_key not in {
        "pocket_2_5d", REST_POCKET_STRATEGY_KEY,
    } or parameters.strategy_version != 1:
        raise ValueError("Operation không dùng Pocket strategy v1.")
    data = dict(parameters.values)
    data.pop(AUTOMATIC_PARAMETER_CONTRACT_KEY, None)
    # R266 Lead-In is additive.  Historical Pocket/Rest payloads did not
    # contain it and retain their exact zero-lead motion semantics.
    data.setdefault("lead_in_length", 0.0)
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
    automatic = _current_automatic_contract(context)
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
    values: dict[str, PresentationValue] = {
        "operation_name": context.operation_name,
        "material_state_source": context.material_state_source,
        "material_state_status": context.material_state_status,
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
    if context.operation.strategy_key == REST_POCKET_STRATEGY_KEY:
        values["lead_in_length"] = str(data["lead_in_length"])
    values.update(
        _automatic_presentation(automatic, context.setup.wcs.origin.unit.value)
    )
    return values


def _complete_values(
    context: PocketEditorContext,
    values: Mapping[str, PresentationValue],
) -> dict[str, PresentationValue]:
    complete = pocket_applied_values(context)
    complete.update(values)
    return complete


def _strategy_from_values(
    context: PocketEditorContext,
    draft: PocketEditorDraftContext,
    reference: GeometryReference,
    values: Mapping[str, PresentationValue],
) -> tuple[PocketStrategy, AutomaticParameterContract]:
    complete = _complete_values(context, values)
    automatic = _recompute_automatic_contract(context, draft, values=complete)
    complete.update(
        _automatic_presentation(automatic, context.setup.wcs.origin.unit.value)
    )
    if _text(complete["machining_pattern"], "machining_pattern") != "offset_inward":
        raise ValueError("Pocket v1 chỉ hỗ trợ deterministic inward offset.")
    unit = context.setup.wcs.origin.unit
    feed_unit = FeedUnit.MM_PER_MINUTE if unit.value == "mm" else FeedUnit.INCH_PER_MINUTE
    strategy = PocketStrategy(
        unit,
        PocketGeometryInput(reference, unit),
        PocketDepthDefinition(
            unit,
            Length(_number(complete["top_z"], "top_z"), unit),
            Length(_number(complete["bottom_z"], "bottom_z"), unit),
            Length(_number(complete["axial_allowance"], "axial_allowance"), unit),
        ),
        Length(
            float(automatic.value("stepover").effective_value)
            if automatic.value("stepover").effective_value is not None
            else _number(complete["stepover"], "stepover"),
            unit,
        ),
        Length(
            float(automatic.value("stepdown").effective_value)
            if automatic.value("stepdown").effective_value is not None
            else _number(complete["stepdown"], "stepdown"),
            unit,
        ),
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
        lead_in_length=Length(
            _number(complete.get("lead_in_length", "0"), "lead_in_length"), unit
        ),
    )
    return strategy, automatic


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
    strategy, automatic = _strategy_from_values(
        context, draft, reference, complete
    )
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
    base_parameters = strategy.to_operation_parameters()
    previous_values = pocket_applied_values(context)
    automatic_changed = any(
        complete.get(key) != previous_values.get(key)
        for key in (
            "quality_profile",
            "stepdown_mode",
            "stepdown",
            "stepover_mode",
            "stepover",
        )
    )
    persist_automatic = (
        _stored_automatic_contract(context) is not None
        or automatic_changed
        or any(
            automatic.value(key).mode is AutomaticParameterMode.AUTO
            for key in POCKET_AUTOMATIC_USER_KEYS
        )
        or automatic.value("entry_segment_index").status
        is AutomaticParameterStatus.RESOLVED
    )
    parameter_set = (
        OperationParameterSet(
            context.operation.strategy_key,
            base_parameters.strategy_version,
            base_parameters.values
            + ((AUTOMATIC_PARAMETER_CONTRACT_KEY, automatic.to_json()),),
            base_parameters.schema_version,
        )
        if persist_automatic
        else OperationParameterSet(
            context.operation.strategy_key,
            base_parameters.strategy_version,
            base_parameters.values,
            base_parameters.schema_version,
        )
    )
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
    expected = (
        _REST_FIELD_IDS
        if str(schema.strategy) == "rest_pocket_3axis_r266"
        else _FIELD_IDS
    )
    if actual != expected:
        raise ValueError(
            "Pocket schema mapping mismatch; "
            f"missing={sorted(expected - actual)}, unsupported={sorted(actual - expected)}"
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


def _automatic_mode_field(
    field_id: str,
    label: str,
    value: PresentationValue,
    *,
    order: int,
    auto_available: bool,
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
    labels = tuple(
        (choice, "AUTO" if choice == AutomaticParameterMode.AUTO.value else "Manual override")
        for choice in choices
    )
    return FunctionEditorField(
        field_id,
        label,
        FunctionEditorFieldKind.CHOICE,
        value,
        required=True,
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        choices=choices,
        choice_labels=labels,
        tooltip=(
            "AUTO recomputes from Tool, Pocket geometry, depth and quality; manual override is preserved."
            if auto_available
            else "AUTO is unavailable because validated Pocket or cutter evidence is missing."
        ),
        help_key=f"pocket.{field_id}",
        order=order,
        binding_key=f"automatic.{field_id}",
    )


def build_pocket_sections(
    context: PocketEditorContext,
) -> tuple[FunctionEditorSection, ...]:
    """Build deterministic operator-oriented sections over Pocket v1 only."""
    values = pocket_applied_values(context)
    automatic_contract = _current_automatic_contract(context)
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
            FunctionEditorField(
                "material_state_source",
                "Nguồn phần dư",
                FunctionEditorFieldKind.READ_ONLY,
                values["material_state_source"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="pocket.material_state_source",
                order=15,
                binding_key="derived.material_state_source",
            ),
            FunctionEditorField(
                "material_state_status",
                "Trạng thái phần dư",
                FunctionEditorFieldKind.READ_ONLY,
                values["material_state_status"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="pocket.material_state_status",
                order=16,
                binding_key="derived.material_state_status",
            ),
            FunctionEditorField(
                "quality_profile",
                "Hồ sơ chất lượng",
                FunctionEditorFieldKind.CHOICE,
                values["quality_profile"],
                required=True,
                choices=tuple(item.value for item in CamQualityProfile),
                choice_labels=(
                    (CamQualityProfile.FAST.value, "Nhanh"),
                    (CamQualityProfile.BALANCED.value, "Cân bằng"),
                    (CamQualityProfile.HIGH.value, "Chất lượng cao"),
                ),
                tooltip="Chính sách hình học dùng chung; không phải mô hình tải vật liệu.",
                help_key="pocket.quality_profile",
                order=20,
                binding_key="automatic.quality_profile",
            ),
            FunctionEditorField(
                "automatic_summary",
                "Pocket 2D Auto Setup",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_summary"],
                source=FunctionEditorValueSource.DERIVED,
                tooltip="Tóm tắt trạng thái tự động, tùy chỉnh và không khả dụng.",
                help_key="pocket.automatic_summary",
                order=30,
                binding_key="automatic.summary",
            ),
            FunctionEditorField(
                "automatic_stepdown",
                "Stepdown tự động",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_stepdown"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="pocket.automatic_stepdown",
                order=40,
                binding_key="automatic.stepdown.summary",
            ),
            FunctionEditorField(
                "automatic_stepover",
                "Bước ngang tự động",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_stepover"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="pocket.automatic_stepover",
                order=50,
                binding_key="automatic.stepover.summary",
            ),
            FunctionEditorField(
                "automatic_entry_location",
                "Vị trí vào dao",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_entry_location"],
                source=FunctionEditorValueSource.DERIVED,
                help_key="pocket.automatic_entry_location",
                order=60,
                binding_key="automatic.entry_location",
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
            _automatic_mode_field(
                "stepover_mode",
                "Chế độ stepover",
                values["stepover_mode"],
                order=30,
                auto_available=(
                    automatic_contract.value("stepover").status
                    is AutomaticParameterStatus.RESOLVED
                ),
            ),
            _number_field(
                "stepover",
                "Stepover",
                values["stepover"],
                unit=unit,
                binding_key="parameters.stepover",
                order=40,
                default=defaults.get("stepover"),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                validators=(_minimum("pocket.stepover_positive", "Stepover phải lớn hơn 0."),),
                help_text="Khoảng cách tuyệt đối; miền yêu cầu nhỏ hơn đường kính dao.",
            ),
            _number_field(
                "radial_stock_allowance",
                "Wall Stock Allowance",
                values["radial_stock_allowance"],
                unit=unit,
                binding_key="parameters.radial_stock_allowance",
                order=50,
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
                order=60,
                default=defaults.get("cutting_feed_rate"),
                validators=(_minimum("pocket.feed_positive", "Feed cắt phải lớn hơn 0."),),
            ),
            _number_field(
                "spindle_speed",
                "Tốc độ trục chính",
                values["spindle_speed"],
                unit="RPM",
                binding_key="parameters.spindle_speed",
                order=70,
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
                help_text="Tọa độ tuyệt đối trong hệ tọa độ Thiết lập; phải khớp mặt phẳng của biên.",
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
                help_text="Đáy danh nghĩa trong hệ tọa độ Thiết lập; lượng dư đáy nâng Z dao cuối.",
            ),
            _automatic_mode_field(
                "stepdown_mode",
                "Chế độ stepdown",
                values["stepdown_mode"],
                order=30,
                auto_available=(
                    automatic_contract.value("stepdown").status
                    is AutomaticParameterStatus.RESOLVED
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
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                validators=(_minimum("pocket.stepdown_positive", "Stepdown phải lớn hơn 0."),),
            ),
            _number_field(
                "axial_allowance",
                "Floor Stock Allowance",
                values["axial_allowance"],
                unit=unit,
                binding_key="parameters.axial_allowance",
                order=50,
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
                order=60,
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
                order=70,
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
            FunctionEditorField(
                "automatic_entry_form",
                "Trạng thái kiểu vào dao",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_entry_form"],
                source=FunctionEditorValueSource.DERIVED,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                tooltip="Chế độ vào dao tự động không chọn cắm thẳng khi dữ liệu Dao chưa chứng minh khả năng cắt tâm.",
                help_key="pocket.automatic_entry_form",
                order=20,
                binding_key="automatic.entry_form",
            ),
            _number_field(
                "plunge_feed_rate",
                "Plunge feed",
                values["plunge_feed_rate"],
                unit=feed_unit,
                binding_key="parameters.plunge_feed_rate",
                order=30,
                default=defaults.get("plunge_feed_rate"),
                validators=(_minimum("pocket.plunge_positive", "Plunge feed phải lớn hơn 0."),),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_text="Generator plunge thẳng tại start của từng offset loop.",
            ),
            *(
                (
                    _number_field(
                        "lead_in_length",
                        ui_text("Lead-In Length"),
                        values["lead_in_length"],
                        unit=unit,
                        binding_key="parameters.lead_in_length",
                        order=40,
                        default=defaults.get("lead_in_length", 0.0),
                        validators=(
                            _minimum(
                                "rest_pocket.lead_in_nonnegative",
                                "Chiều dài Lead-In không được âm.",
                                0.0,
                            ),
                        ),
                        disclosure_level=ParameterDisclosureLevel.ADVANCED,
                        help_text=(
                            "Chỉ nguyên công phần dư: đoạn cắt tuyến tính thực phải nằm "
                            "hoàn toàn trong vật liệu dư; 0 giữ nguyên chuyển động lịch sử."
                        ),
                    ),
                )
                if context.operation.strategy_key == REST_POCKET_STRATEGY_KEY
                else ()
            ),
        ),
        "Entry policy thực sự tồn tại; khả năng plunge-safe vẫn do generator hiện có kiểm tra.",
        order=60,
    )
    linking = FunctionEditorSection(
        "linking",
        "LINKING",
        (
            FunctionEditorField(
                "automatic_linking",
                "Liên kết tự động",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_linking"],
                source=FunctionEditorValueSource.DERIVED,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                tooltip="Giữ nguyên rút dao và liên kết an toàn hiện có khi chưa có bộ xác minh đầy đủ cho đường chạy dao không nâng dao.",
                help_key="pocket.automatic_linking",
                order=10,
                binding_key="automatic.linking",
            ),
            _number_field(
                "clearance_height",
                "Clearance",
                values["clearance_height"],
                unit=unit,
                binding_key="parameters.clearance_height",
                order=20,
                default=defaults.get("clearance_height"),
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_text="Giá trị nguyên công tường minh trong hệ tọa độ Thiết lập; không phải Z máy.",
            ),
            _number_field(
                "retract_height",
                "Retract",
                values["retract_height"],
                unit=unit,
                binding_key="parameters.retract_height",
                order=30,
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
                help_text="Miền yêu cầu Khoảng an toàn >= Rút dao > Cao độ trên.",
            ),
        ),
        "Chuyển động an toàn v1 chỉ có Khoảng an toàn và Rút dao tường minh trong hệ tọa độ Thiết lập.",
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        default_expanded=False,
        order=70,
    )
    advanced = FunctionEditorSection(
        "advanced",
        "ADVANCED",
        (
            FunctionEditorField(
                "automatic_provenance",
                "Nguồn gốc thiết lập tự động",
                FunctionEditorFieldKind.READ_ONLY,
                values["automatic_provenance"],
                source=FunctionEditorValueSource.DERIVED,
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                tooltip="Giải thích chính sách, giới hạn và trạng thái dự phòng của tham số Hốc tự động.",
                help_key="pocket.automatic_provenance",
                order=10,
                binding_key="automatic.provenance",
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
                help_key="pocket.machine",
                order=20,
                binding_key="operation.machine_requirement",
            ),
            FunctionEditorField(
                "enabled",
                "Operation được bật",
                FunctionEditorFieldKind.CHECKBOX,
                values["enabled"],
                disclosure_level=ParameterDisclosureLevel.ADVANCED,
                help_key="pocket.enabled",
                order=30,
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
    is_rest = context.operation.strategy_key == REST_POCKET_STRATEGY_KEY
    schema = FunctionEditorSchema(
        "rest_pocket_production_r266" if is_rest else "pocket_production_9a5_3",
        FunctionEditorStrategyKey(
            "rest_pocket_3axis_r266" if is_rest else "pocket_2_5d_9a5_3"
        ),
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
    "pocket_draft_transform",
    "prepare_pocket_update",
    "validate_pocket_schema_contract",
]

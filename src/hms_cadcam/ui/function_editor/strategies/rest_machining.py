"""Production Function Editor projection for R272/R274 Rest machining.

The module is intentionally presentation-only.  It parses the already
registered Rest parameter codecs and builds ordinary ``Operation`` updates;
MaterialState resolution, preparation, toolpath generation and publication
remain owned by the application/project services.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
import math

from hms_cadcam.cam.domain import (
    DirtyReason,
    FeedRate,
    FeedUnit,
    Length,
    Operation,
    SpindleSpeed,
    ToolAssembly,
    ToolAssemblyReference,
    ToolDefinition,
)
from hms_cadcam.cam.domain.contour import (
    ContourCutDirection,
    ContourProfileSource,
    ContourSide,
)
from hms_cadcam.cam.domain.rest_contour import (
    REST_CONTOUR_STRATEGY_KEY,
    RestContourLinkingPolicy,
    RestContourParameters,
)
from hms_cadcam.cam.domain.rest_finishing import (
    REST_FINISHING_STRATEGY_KEY,
    RestFinishingParameters,
)
from hms_cadcam.ui.function_editor.model import (
    FunctionEditorAction,
    FunctionEditorDiagnostic,
    FunctionEditorDiagnosticSeverity,
    FunctionEditorField,
    FunctionEditorFieldKind,
    FunctionEditorFooter,
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
from hms_cadcam.ui.i18n import translation_service
from hms_cadcam.ui.localization import ui_text


_REST_CONTOUR_FAMILIES = frozenset(
    {"end_mill", "ball_end_mill", "bull_nose_end_mill"}
)
_REST_FINISHING_FAMILIES = frozenset({"end_mill"})


def _editor_text(value: str) -> str:
    if value.startswith("r275."):
        return translation_service().translate_key(value)
    return ui_text(value)


@dataclass(frozen=True, slots=True)
class RestMachiningDependencyPresentation:
    """Read-only projection of one official operation-tree dependency."""

    producer_operation_id: object
    producer_name: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class RestMachiningEditorContext:
    """Native-free immutable source used by the Rest production editor."""

    operation_name: str
    operation: Operation
    tool_assemblies: tuple[ToolAssembly, ...]
    tool_definitions: tuple[ToolDefinition, ...]
    dependency: RestMachiningDependencyPresentation
    dependency_choices: tuple[tuple[str, str], ...] = ()

    @property
    def is_finishing(self) -> bool:
        return self.operation.strategy_key == REST_FINISHING_STRATEGY_KEY


@dataclass(frozen=True, slots=True)
class RestMachiningOperationUpdate:
    operation_name: str
    operation: Operation
    assembly: ToolAssembly


def rest_creation_candidate_presentation(
    producer_operation_id: object, producer_name: str
) -> RestMachiningDependencyPresentation:
    """Describe a wizard candidate without asserting backend currentness."""
    return RestMachiningDependencyPresentation(
        producer_operation_id,
        producer_name,
        "CANDIDATE",
        _editor_text("r275.rest.dependency_candidate_detail"),
    )


def _finite_number(value: object, field_id: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_id} phải là số hữu hạn.")
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_id} phải là số hữu hạn.") from error
    if not math.isfinite(result):
        raise ValueError(f"{field_id} phải là số hữu hạn.")
    return result


def _required_text(value: object, field_id: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_id} là bắt buộc.")
    return value.strip()


def _tool(context: RestMachiningEditorContext) -> tuple[ToolAssembly, ToolDefinition]:
    assembly = next(
        (
            value
            for value in context.tool_assemblies
            if value.assembly_id == context.operation.tool_assembly.assembly_id
        ),
        None,
    )
    if assembly is None:
        raise ValueError("Tool Assembly không còn tồn tại trong project.")
    tool = next(
        (value for value in context.tool_definitions if value.tool_id == assembly.tool_id),
        None,
    )
    if tool is None:
        raise ValueError("Tool Definition không còn tồn tại trong project.")
    return assembly, tool


def _eligible_tools(
    context: RestMachiningEditorContext,
) -> tuple[tuple[str, ...], tuple[tuple[PresentationValue, str], ...]]:
    allowed = _REST_FINISHING_FAMILIES if context.is_finishing else _REST_CONTOUR_FAMILIES
    values: list[tuple[str, str]] = []
    for assembly in context.tool_assemblies:
        tool = next(
            (item for item in context.tool_definitions if item.tool_id == assembly.tool_id),
            None,
        )
        if tool is None or tool.family.value not in allowed:
            continue
        diameter = getattr(tool.cutting_geometry, "diameter", None)
        diameter_text = "?" if diameter is None else f"{diameter.value:g} {diameter.unit.value}"
        values.append(
            (
                str(assembly.assembly_id),
                f"{tool.name} · {tool.family.value} · D{diameter_text} · {assembly.name}",
            )
        )
    values.sort(key=lambda item: (item[1].casefold(), item[0]))
    return tuple(item[0] for item in values), tuple(values)


def _minimum(code: str, message: str, value: float = 1.0e-12) -> FunctionEditorValidationRule:
    return FunctionEditorValidationRule(
        FunctionEditorValidationKind.MINIMUM,
        value,
        ui_text(message),
        code,
    )


def _number_field(
    field_id: str,
    label: str,
    value: float,
    unit: str,
    order: int,
    *,
    minimum: float | None = None,
    advanced: bool = False,
) -> FunctionEditorField:
    display_label = _editor_text(label)
    validators = () if minimum is None else (
        _minimum(
            f"rest.ui.{field_id}.minimum",
            f"{display_label} không hợp lệ.",
            minimum,
        ),
    )
    return FunctionEditorField(
        field_id,
        display_label,
        FunctionEditorFieldKind.NUMBER,
        value,
        unit=unit,
        binding_key=field_id,
        conversion=FunctionEditorValueConversion.FLOAT,
        disclosure_level=(
            ParameterDisclosureLevel.ADVANCED
            if advanced
            else ParameterDisclosureLevel.BASIC
        ),
        validators=validators,
        order=order,
    )


def _read_only(field_id: str, label: str, value: str, order: int, *, advanced: bool = False) -> FunctionEditorField:
    return FunctionEditorField(
        field_id,
        _editor_text(label),
        FunctionEditorFieldKind.READ_ONLY,
        _editor_text(value),
        source=FunctionEditorValueSource.PROJECT,
        binding_key=field_id,
        disclosure_level=(ParameterDisclosureLevel.ADVANCED if advanced else ParameterDisclosureLevel.BASIC),
        order=order,
    )


def _operation_section(context: RestMachiningEditorContext) -> FunctionEditorSection:
    return FunctionEditorSection(
        "operation",
        _editor_text("r275.rest.operation"),
        (
            FunctionEditorField(
                "operation_name",
                _editor_text("r275.rest.operation_name"),
                FunctionEditorFieldKind.TEXT,
                context.operation_name,
                required=True,
                binding_key="operation_name",
                conversion=FunctionEditorValueConversion.TEXT,
                order=0,
            ),
            _read_only(
                "strategy_law",
                "r275.rest.strategy",
                (
                    "r275.rest.finishing_law"
                    if context.is_finishing
                    else "Rest Contour 3-Axis · biên dạng phần dư"
                ),
                1,
            ),
            _read_only(
                "parameter_mode",
                "r275.rest.parameter_mode",
                (
                    "r275.rest.manual_only_auto_unsupported"
                    if context.is_finishing
                    else (
                        "AUTO (backend-resolved)"
                        if dict(context.operation.parameters.values).get("automatic_parameter_contract")
                        else "MANUAL"
                    )
                ),
                2,
            ),
        ),
        order=0,
    )


def _dependency_section(context: RestMachiningEditorContext) -> FunctionEditorSection:
    dependency = context.dependency
    source_field = (
        FunctionEditorField(
            "material_state_source",
            _editor_text("r275.rest.material_state_source"),
            FunctionEditorFieldKind.CHOICE,
            str(dependency.producer_operation_id),
            choices=tuple(value for value, _label in context.dependency_choices),
            choice_labels=tuple(context.dependency_choices),
            required=True,
            binding_key="material_state_source",
            order=0,
        )
        if context.dependency_choices
        else _read_only(
            "material_state_source",
            "r275.rest.material_state_source",
            f"{dependency.producer_name} · {dependency.producer_operation_id}",
            0,
        )
    )
    return FunctionEditorSection(
        "dependency",
        _editor_text("r275.rest.material_state_dependency"),
        (
            source_field,
            _read_only(
                "material_state_status",
                "r275.rest.dependency_status",
                dependency.status,
                1,
            ),
            _read_only(
                "material_state_detail",
                "r275.rest.dependency_details",
                dependency.detail,
                2,
            ),
        ),
        summary=ui_text("Nguồn chính thức từ operation tree; UI không tạo Material State."),
        order=1,
    )


def _tool_section(context: RestMachiningEditorContext) -> FunctionEditorSection:
    choices, labels = _eligible_tools(context)
    assembly, tool = _tool(context)
    diameter = getattr(tool.cutting_geometry, "diameter", None)
    diameter_text = "?" if diameter is None else f"{diameter.value:g} {diameter.unit.value}"
    unsupported = (
        "Ball End Mill / Bull Nose / AUTO / 3D sidewall / undercut / 5-axis không được hỗ trợ; "
        "hãy chọn Flat End Mill hoặc dùng chiến lược thủ công phù hợp."
        if context.is_finishing
        else "Chỉ hiển thị các họ dao được backend Rest Contour hỗ trợ."
    )
    return FunctionEditorSection(
        "tool",
        _editor_text("r275.rest.tool_and_assembly"),
        (
            FunctionEditorField(
                "tool_assembly_id",
                _editor_text("r275.rest.eligible_tool_assembly"),
                FunctionEditorFieldKind.CHOICE,
                str(assembly.assembly_id),
                choices=choices,
                choice_labels=labels,
                required=True,
                binding_key="tool_assembly_id",
                order=0,
            ),
            _read_only(
                "tool_details",
                "r275.rest.tool_details",
                f"{tool.name} · {tool.family.value} · D{diameter_text} · {assembly.name}",
                1,
            ),
            _read_only(
                "unsupported_features",
                "r275.rest.support_limits",
                unsupported,
                2,
            ),
        ),
        order=2,
    )


def _rest_contour_parameter_sections(parameters: RestContourParameters) -> tuple[FunctionEditorSection, ...]:
    unit = parameters.unit.value
    basic = FunctionEditorSection(
        "basic_parameters",
        _editor_text("r275.rest.section.basic_primary"),
        (
            _number_field("top_height", "Cao độ đỉnh", parameters.top_height.value, unit, 0),
            _number_field("final_depth", "Chiều sâu cuối", parameters.final_depth.value, unit, 1),
            _number_field("stepdown", "Bước xuống tối đa", parameters.stepdown.value, unit, 2, minimum=1.0e-12),
            _number_field("radial_stock_allowance", "Lượng dư thành", parameters.radial_stock_allowance.value, unit, 3, minimum=0.0),
            _number_field("axial_stock_allowance", "Lượng dư đáy", parameters.axial_stock_allowance.value, unit, 4, minimum=0.0),
        ),
        order=3,
    )
    advanced = FunctionEditorSection(
        "advanced_parameters",
        _editor_text("r275.rest.section.advanced_safety_process"),
        (
            _number_field("tolerance", "r275.rest.tolerance", parameters.tolerance.value, unit, 0, minimum=1.0e-12, advanced=True),
            _number_field("clearance_height", "Cao độ an toàn", parameters.clearance_height.value, unit, 1, advanced=True),
            _number_field("retract_height", "Cao độ rút dao", parameters.retract_height.value, unit, 2, advanced=True),
            _number_field("cutting_feed_rate", "Lượng chạy dao cắt", parameters.cutting_feed_rate.value, parameters.cutting_feed_rate.unit.value, 3, minimum=1.0e-12, advanced=True),
            _number_field("plunge_feed_rate", "Lượng chạy dao cắm", parameters.plunge_feed_rate.value, parameters.plunge_feed_rate.unit.value, 4, minimum=1.0e-12, advanced=True),
            _number_field("spindle_speed", "Tốc độ trục chính", parameters.spindle_speed.value, "rpm", 5, minimum=1.0e-12, advanced=True),
            _number_field("lead_in_length", "Chiều dài Lead-in", parameters.lead_in_length.value, unit, 6, minimum=0.0, advanced=True),
            _number_field("lead_out_length", "Chiều dài Lead-out", parameters.lead_out_length.value, unit, 7, minimum=0.0, advanced=True),
        ),
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        order=4,
    )
    return basic, advanced


def _rest_finishing_parameter_sections(parameters: RestFinishingParameters) -> tuple[FunctionEditorSection, ...]:
    unit = parameters.unit.value
    basic = FunctionEditorSection(
        "basic_parameters",
        _editor_text("r275.rest.section.basic_constant_z_planar"),
        (
            _number_field("nominal_target_z", "r275.rest.nominal_target_z", parameters.nominal_target_z.value, unit, 0),
            _number_field("final_stock_allowance", "r275.rest.final_stock_allowance", parameters.final_stock_allowance.value, unit, 1, minimum=0.0),
            _number_field("tolerance", "r275.rest.tolerance", parameters.tolerance.value, unit, 2, minimum=1.0e-12),
            _number_field("stepover", "r275.rest.manual_stepover", parameters.stepover.value, unit, 3, minimum=1.0e-12),
            _number_field("max_stepdown", "r275.rest.manual_max_stepdown", parameters.max_stepdown.value, unit, 4, minimum=1.0e-12),
            _read_only("raster_direction", "r275.rest.raster_direction", "X axis", 5),
        ),
        order=3,
    )
    advanced = FunctionEditorSection(
        "advanced_parameters",
        _editor_text("r275.rest.section.advanced_safety_feeds"),
        (
            _number_field("clearance_height", "Cao độ an toàn", parameters.clearance_height.value, unit, 0, advanced=True),
            _number_field("retract_height", "Cao độ rút dao", parameters.retract_height.value, unit, 1, advanced=True),
            _number_field("cutting_feed_rate", "Lượng chạy dao cắt", parameters.cutting_feed_rate.value, parameters.cutting_feed_rate.unit.value, 2, minimum=1.0e-12, advanced=True),
            _number_field("plunge_feed_rate", "Lượng chạy dao cắm", parameters.plunge_feed_rate.value, parameters.plunge_feed_rate.unit.value, 3, minimum=1.0e-12, advanced=True),
            _number_field("spindle_speed", "Tốc độ trục chính", parameters.spindle_speed.value, "rpm", 4, minimum=1.0e-12, advanced=True),
        ),
        disclosure_level=ParameterDisclosureLevel.ADVANCED,
        order=4,
    )
    return basic, advanced


def build_rest_machining_schema(context: RestMachiningEditorContext) -> FunctionEditorSchema:
    """Build the LIGHT compact Rest editor using the shared Basic/Advanced law."""
    if context.operation.strategy_key == REST_CONTOUR_STRATEGY_KEY:
        parameters: RestContourParameters | RestFinishingParameters = RestContourParameters.from_operation_parameters(context.operation.parameters)
        parameter_sections = _rest_contour_parameter_sections(parameters)
        title = "Rest Contour"
        strategy = "Rest Contour 3-Axis"
    elif context.operation.strategy_key == REST_FINISHING_STRATEGY_KEY:
        parameters = RestFinishingParameters.from_operation_parameters(context.operation.parameters)
        parameter_sections = _rest_finishing_parameter_sections(parameters)
        title = "Rest Finishing"
        strategy = "MANUAL ONLY · Flat End Mill · Constant-Z planar · X raster"
    else:
        raise ValueError("Operation không phải Rest Contour/Rest Finishing.")
    assembly, _tool_definition = _tool(context)
    return FunctionEditorSchema(
        f"{context.operation.strategy_key}_production_r275",
        FunctionEditorStrategyKey(f"{context.operation.strategy_key}_r275"),
        FunctionEditorSummary(
            ui_text(context.operation_name or title),
            ui_text(strategy),
            tool=assembly.name,
            geometry=str(context.operation.geometry_inputs[0].reference.reference_id),
            operation_status=context.operation.artifact_state.status.value.upper(),
        ),
        (
            _operation_section(context),
            _dependency_section(context),
            _tool_section(context),
            *parameter_sections,
        ),
        FunctionEditorFooter(
            actions=(
                FunctionEditorAction.RESET_DRAFT,
                FunctionEditorAction.VALIDATE,
                FunctionEditorAction.APPLY,
                FunctionEditorAction.CALCULATE,
                FunctionEditorAction.CLOSE,
            ),
            calculate_supported=True,
            apply_supported=True,
        ),
    )


def rest_machining_applied_values(context: RestMachiningEditorContext) -> dict[str, PresentationValue]:
    schema = build_rest_machining_schema(context)
    return {field.field_id: field.value for field in schema.fields}


def _selected_assembly(context: RestMachiningEditorContext, value: object) -> tuple[ToolAssembly, ToolDefinition]:
    identity = _required_text(value, "tool_assembly_id")
    assembly = next((item for item in context.tool_assemblies if str(item.assembly_id) == identity), None)
    if assembly is None:
        raise ValueError("Tool Assembly không còn tồn tại trong project.")
    tool = next((item for item in context.tool_definitions if item.tool_id == assembly.tool_id), None)
    if tool is None:
        raise ValueError("Tool Definition không còn tồn tại trong project.")
    allowed = _REST_FINISHING_FAMILIES if context.is_finishing else _REST_CONTOUR_FAMILIES
    if tool.family.value not in allowed:
        alternative = "Flat End Mill" if context.is_finishing else "một họ dao Rest Contour được hỗ trợ"
        raise ValueError(f"{tool.family.value} không đủ điều kiện; hãy chọn {alternative}.")
    return assembly, tool


def prepare_rest_machining_update(
    context: RestMachiningEditorContext,
    values: Mapping[str, PresentationValue],
) -> RestMachiningOperationUpdate:
    """Build one validated ordinary operation update; never resolve material."""
    applied = rest_machining_applied_values(context)
    complete = {**applied, **dict(values)}
    operation_name = _required_text(complete.get("operation_name"), "operation_name")
    assembly, tool = _selected_assembly(context, complete.get("tool_assembly_id"))
    if context.is_finishing:
        current = RestFinishingParameters.from_operation_parameters(context.operation.parameters)
        stepover = _finite_number(complete.get("stepover"), "stepover")
        diameter = getattr(tool.cutting_geometry, "diameter", None)
        if diameter is None or diameter.unit is not current.unit or stepover > diameter.value:
            raise ValueError("stepover phải nhỏ hơn hoặc bằng đường kính Flat End Mill hiệu dụng.")
        parameters: RestContourParameters | RestFinishingParameters = RestFinishingParameters(
            current.unit,
            current.profile_source,
            Length(_finite_number(complete.get("nominal_target_z"), "nominal_target_z"), current.unit),
            Length(_finite_number(complete.get("final_stock_allowance"), "final_stock_allowance"), current.unit),
            Length(_finite_number(complete.get("tolerance"), "tolerance"), current.unit),
            Length(stepover, current.unit),
            Length(_finite_number(complete.get("max_stepdown"), "max_stepdown"), current.unit),
            Length(_finite_number(complete.get("clearance_height"), "clearance_height"), current.unit),
            Length(_finite_number(complete.get("retract_height"), "retract_height"), current.unit),
            FeedRate(_finite_number(complete.get("cutting_feed_rate"), "cutting_feed_rate"), current.cutting_feed_rate.unit),
            FeedRate(_finite_number(complete.get("plunge_feed_rate"), "plunge_feed_rate"), current.plunge_feed_rate.unit),
            SpindleSpeed(_finite_number(complete.get("spindle_speed"), "spindle_speed")),
        )
    else:
        current = RestContourParameters.from_operation_parameters(context.operation.parameters)
        parameters = RestContourParameters(
            current.unit,
            current.profile_source,
            current.side,
            Length(_finite_number(complete.get("top_height"), "top_height"), current.unit),
            Length(_finite_number(complete.get("final_depth"), "final_depth"), current.unit),
            Length(_finite_number(complete.get("stepdown"), "stepdown"), current.unit),
            Length(_finite_number(complete.get("radial_stock_allowance"), "radial_stock_allowance"), current.unit),
            Length(_finite_number(complete.get("axial_stock_allowance"), "axial_stock_allowance"), current.unit),
            Length(_finite_number(complete.get("clearance_height"), "clearance_height"), current.unit),
            Length(_finite_number(complete.get("retract_height"), "retract_height"), current.unit),
            FeedRate(_finite_number(complete.get("cutting_feed_rate"), "cutting_feed_rate"), current.cutting_feed_rate.unit),
            FeedRate(_finite_number(complete.get("plunge_feed_rate"), "plunge_feed_rate"), current.plunge_feed_rate.unit),
            SpindleSpeed(_finite_number(complete.get("spindle_speed"), "spindle_speed")),
            current.direction,
            Length(_finite_number(complete.get("tolerance"), "tolerance"), current.unit),
            Length(_finite_number(complete.get("lead_in_length"), "lead_in_length"), current.unit),
            Length(_finite_number(complete.get("lead_out_length"), "lead_out_length"), current.unit),
            current.linking_policy,
            current.automatic_parameter_contract,
        )
    parameter_set = parameters.to_operation_parameters()
    tool_reference = ToolAssemblyReference.from_assembly(assembly)
    changed = context.operation
    if parameter_set != context.operation.parameters or tool_reference != context.operation.tool_assembly:
        changed = replace(
            context.operation,
            parameters=parameter_set,
            tool_assembly=tool_reference,
            revision=context.operation.revision.next(),
            artifact_state=context.operation.artifact_state.mark_dirty(DirtyReason.PARAMETERS_CHANGED),
        )
    return RestMachiningOperationUpdate(operation_name, changed, assembly)


def rest_machining_validation_diagnostics(
    schema: FunctionEditorSchema,
    context: RestMachiningEditorContext,
    values: Mapping[str, PresentationValue],
) -> tuple[FunctionEditorDiagnostic, ...]:
    """Map domain/UI validation into focusable typed diagnostics."""
    try:
        prepare_rest_machining_update(context, values)
    except (KeyError, RuntimeError, TypeError, ValueError) as error:
        text = str(error) or "Tham số Rest không hợp lệ."
        folded = text.casefold()
        field_id = next(
            (
                field
                for token, field in (
                    ("stepover", "stepover"),
                    ("stepdown", "max_stepdown" if context.is_finishing else "stepdown"),
                    ("tolerance", "tolerance"),
                    ("allowance", "final_stock_allowance" if context.is_finishing else "radial_stock_allowance"),
                    ("tool", "tool_assembly_id"),
                    ("dao", "tool_assembly_id"),
                    ("dependency", "material_state_status"),
                    ("material", "material_state_status"),
                )
                if token in folded
            ),
            None,
        )
        return (
            FunctionEditorDiagnostic(
                "rest.ui.invalid_parameters",
                ui_text(text),
                FunctionEditorDiagnosticSeverity.ERROR,
                field_id,
                schema.section_for_field(field_id).section_id if field_id else None,
            ),
        )
    return ()


def rest_result_presentation(status: object, diagnostic_code: object = None, message: str = "") -> tuple[str, str, bool]:
    """Normalize typed lifecycle results without turning neutral states into errors."""
    normalized = str(getattr(status, "value", status)).upper()
    code = str(getattr(diagnostic_code, "value", diagnostic_code or ""))
    if normalized in {"NO_WORK", "NO_REST_MATERIAL", "NO_REST_FINISHING_MATERIAL"}:
        return "NO_WORK", _editor_text("r275.rest.no_work"), False
    if normalized == "CANCELLED" or code.endswith(".cancelled"):
        return "CANCELLED", ui_text("Đã hủy ổn định; không giả định có output một phần."), False
    if normalized == "SUCCESS":
        return "SUCCESS", ui_text("Toolpath Rest đã được backend xác minh và công bố."), False
    return "FAILED", ui_text(message or code or "Tạo đường chạy dao Rest thất bại an toàn."), True


__all__ = [
    "RestMachiningDependencyPresentation",
    "RestMachiningEditorContext",
    "RestMachiningOperationUpdate",
    "build_rest_machining_schema",
    "prepare_rest_machining_update",
    "rest_machining_applied_values",
    "rest_machining_validation_diagnostics",
    "rest_creation_candidate_presentation",
    "rest_result_presentation",
]

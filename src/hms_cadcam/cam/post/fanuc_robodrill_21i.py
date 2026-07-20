"""Production formatter for the confirmed FANUC ROBODRILL 21i WorkNC layout."""

from __future__ import annotations

from uuid import UUID

from hms_cadcam.cam.domain.ids import PostProcessorDefinitionId, ProductionControllerProfileId
from hms_cadcam.cam.domain.machine import MachineKind, OperationCapability, SpindleDirection, TappingMode
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.post.fanuc_validation import fanuc_number, validate_fanuc_output, validate_fanuc_program
from hms_cadcam.cam.post.assembly_model import ProgramAssemblyPlan, ProgramOperationSection
from hms_cadcam.cam.post.model import *
from hms_cadcam.cam.post.profile import (
    ArcOutputMode, ArcPolicy, BlockNumberPolicy, CoolantCodeMapping,
    CutterCompensationPolicy, DwellPolicy, NumericFormatPolicy,
    ProductionControllerProfile, ProductionProgramContext, ProgramNumberPolicy, SafeSequenceToken,
    SpindleCodeMapping, ToolActivationPolicy, WorkOffsetMapping,
)
from hms_cadcam.cam.post.validation import validate_program_ir, validate_request
from hms_cadcam.cam.toolpath.events import CoolantState, FeedMode


PROFILE_KEY = "robodrill_fanuc_21i_worknc_expanded_v1"
ADAPTER_KEY = "fanuc_robodrill_21i_worknc_v1"
_PROFILE_ID = ProductionControllerProfileId(UUID("b23d9d1b-70ef-54b8-8ef4-207d21000001"))
_DEFINITION_ID = PostProcessorDefinitionId(UUID("e32c2440-0494-5e85-879d-207d21000001"))


def robodrill_21i_profile() -> ProductionControllerProfile:
    return ProductionControllerProfile(
        profile_id=_PROFILE_ID,
        profile_key=PROFILE_KEY,
        profile_version=1,
        adapter_key=ADAPTER_KEY,
        adapter_version=1,
        controller_family="FANUC",
        controller_model="21i",
        machine_family="ROBODRILL",
        machine_type=MachineKind.MILL,
        axes=("X", "Y", "Z"),
        supported_units=(LengthUnit.MM,),
        supported_planes=(Plane.XY,),
        coordinate_mode=CoordinateMode.ABSOLUTE,
        supported_feed_modes=(FeedMode.UNITS_PER_MINUTE, FeedMode.UNITS_PER_REVOLUTION),
        supported_spindle_directions=(SpindleDirection.CLOCKWISE, SpindleDirection.COUNTERCLOCKWISE),
        minimum_rpm=None,
        maximum_rpm=None,
        feed_limits=(),
        arc_policy=ArcPolicy(ArcOutputMode.IJK_INCREMENTAL_FROM_START, Plane.XY, True, False, False, False, 0.001),
        work_offset_mapping=(WorkOffsetMapping("PRIMARY", 1, "G54"),),
        tool_activation_policy=ToolActivationPolicy.WORKNC_M06_TH,
        cutter_compensation_policy=CutterCompensationPolicy.LEGACY_WORKNC_LEFT,
        spindle_mapping=SpindleCodeMapping("M03", "M04", "M05"),
        coolant_mapping=CoolantCodeMapping("M08", "M09"),
        dwell_policy=DwellPolicy.UNSUPPORTED,
        program_number_policy=ProgramNumberPolicy.DISABLED,
        block_number_policy=BlockNumberPolicy.DISABLED,
        numeric_format=NumericFormatPolicy(4, 4, 4, 0, 3),
        comment_prefix="(",
        comment_suffix=")",
        maximum_comment_length=160,
        newline="\r\n",
        encoding="utf-8",
        maximum_line_length=256,
        maximum_program_size=8 * 1024 * 1024,
        allowed_extensions=(".fn",),
        supported_operation_strategies=("facing_2_5d", "contour_2d", "pocket_2_5d", "drilling_v1", "reaming_v1", "boring_v1"),
        safe_start_records=(SafeSequenceToken.PROGRAM_DELIMITER, SafeSequenceToken.COMMENTS, SafeSequenceToken.MODAL_CANCEL, SafeSequenceToken.MACHINE_Z_REFERENCE, SafeSequenceToken.TOOL_CHANGE, SafeSequenceToken.WORK_OFFSET_ORIGIN, SafeSequenceToken.LENGTH_COMPENSATION, SafeSequenceToken.CUTTER_COMPENSATION, SafeSequenceToken.PROCESS_STATE, SafeSequenceToken.PROGRAM_MOTIONS),
        safe_end_records=(SafeSequenceToken.CUTTER_CANCEL, SafeSequenceToken.COOLANT_OFF, SafeSequenceToken.SPINDLE_STOP, SafeSequenceToken.MACHINE_Z_REFERENCE, SafeSequenceToken.MACHINE_Y_REFERENCE, SafeSequenceToken.PROGRAM_END, SafeSequenceToken.PROGRAM_DELIMITER),
        display_name="FANUC ROBODRILL 21i / WorkNC expanded v1",
    )


def robodrill_21i_definition() -> PostProcessorDefinition:
    profile = robodrill_21i_profile()
    capabilities = PostProcessorCapabilities(
        supported_machine_kinds=(MachineKind.MILL,),
        supported_axes=("X", "Y", "Z"),
        supported_units=(LengthUnit.MM,),
        supported_feed_modes=(FeedMode.UNITS_PER_MINUTE, FeedMode.UNITS_PER_REVOLUTION),
        supported_spindle_directions=(SpindleDirection.CLOCKWISE, SpindleDirection.COUNTERCLOCKWISE),
        supported_coolant_modes=(CoolantState.OFF, CoolantState.FLOOD),
        supported_arc_planes=(Plane.XY,),
        arc_center_formats=(ArcCenterFormat.IJK,),
        supported_operation_strategies=("facing_2_5d", "contour_2d", "pocket_2_5d", "drilling_v1", "tapping_v1", "reaming_v1", "boring_v1"),
        supported_operation_capabilities=(OperationCapability.MILLING, OperationCapability.DRILLING, OperationCapability.TAPPING),
        tapping_synchronization=True,
        tapping_modes=(TappingMode.RIGID, TappingMode.FLOATING),
    )
    return PostProcessorDefinition(
        _DEFINITION_ID, 1, ADAPTER_KEY, 1, capabilities,
        numeric_precision=15, newline=profile.newline, encoding=profile.encoding,
        maximum_line_length=profile.maximum_line_length,
        maximum_program_size=profile.maximum_program_size,
        allow_comments=True, comment_prefix="(", display_name=profile.display_name,
        production_profile=profile,
    )


def _has_canonical_contract(definition: PostProcessorDefinition) -> bool:
    expected = robodrill_21i_definition()
    profile = definition.production_profile
    expected_profile = expected.production_profile
    return (
        definition.adapter_key == ADAPTER_KEY
        and definition.adapter_version == 1
        and definition.fingerprint == expected.fingerprint
        and profile is not None
        and expected_profile is not None
        and profile.profile_id == expected_profile.profile_id
        and profile.fingerprint == expected_profile.fingerprint
    )


def _single_program_header_lines(
    context: ProductionProgramContext, profile: ProductionControllerProfile
) -> list[str]:
    numeric = profile.numeric_format
    binding = context.tool_binding
    return [
        "%",
        "(SHL-TECH)",
        f"(FileName={context.file_name})",
        "(DAO=" + binding.tool_comment
        + ",R=" + fanuc_number(
            context.tool_radius.value,
            numeric.xyz_precision,
            force_decimal_point=True,
        )
        + ",LUONGDU=" + fanuc_number(
            context.stock_allowance.value,
            numeric.xyz_precision,
            force_decimal_point=True,
        )
        + ",CHIEUSAU=" + fanuc_number(
            context.cut_depth.value,
            numeric.xyz_precision,
            force_decimal_point=True,
        )
        + ")",
        "G90G80G49G40G17",
    ]


def _assembly_program_header_lines(plan: ProgramAssemblyPlan) -> list[str]:
    context = plan.shared_context
    lines = ["%", "(SHL-TECH)", f"(FileName={context.file_name})"]
    if context.program_identity is not None:
        lines.append(f"(PROGRAM={context.program_identity})")
    lines.extend(f"({key.upper()}={value})" for key, value in context.global_metadata)
    lines.append("G90G80G49G40G17")
    return lines


def _operation_section_lines(
    program: NCProgramIR, profile: ProductionControllerProfile
) -> list[str]:
    context = program.production_context
    assert context is not None
    numeric = profile.numeric_format
    force_xyz = lambda value: fanuc_number(
        value, numeric.xyz_precision, force_decimal_point=True
    )
    force_ijk = lambda value: fanuc_number(
        value, numeric.ijk_precision, force_decimal_point=True
    )
    binding = context.tool_binding
    lines = [
        "G91G28G0Z0",
        f"M06T{binding.tool_station}",
        "G90G40G54X0.Y0.",
        f"G43Z{force_xyz(context.safe_z.value)}H{binding.length_offset}",
    ]
    if context.use_legacy_cutter_compensation:
        assert binding.diameter_offset is not None
        lines.append(f"G41D{binding.diameter_offset}")

    current = (0.0, 0.0, context.safe_z.value)
    positioned = False
    pending_spindle: str | None = None
    last_spindle_start = max(
        (
            index
            for index, record in enumerate(program.records)
            if isinstance(record, SpindleStartRecord)
        ),
        default=-1,
    )
    last_coolant_on = max(
        (
            index
            for index, record in enumerate(program.records)
            if isinstance(record, CoolantRecord) and record.state is not CoolantState.OFF
        ),
        default=-1,
    )

    def flush_spindle() -> None:
        nonlocal pending_spindle
        if pending_spindle is not None:
            lines.append(pending_spindle)
            pending_spindle = None

    for index, record in enumerate(program.records):
        if pending_spindle is not None and not (
            isinstance(record, CoolantRecord) and record.state is not CoolantState.OFF
        ):
            flush_spindle()
        if isinstance(record, SpindleStartRecord):
            code = (
                profile.spindle_mapping.clockwise
                if record.direction is SpindleDirection.CLOCKWISE
                else profile.spindle_mapping.counterclockwise
            )
            pending_spindle = code + "S" + fanuc_number(
                record.speed.value, numeric.spindle_precision
            )
        elif isinstance(record, SpindleStopRecord):
            if index < last_spindle_start:
                lines.append(profile.spindle_mapping.stop)
        elif isinstance(record, CoolantRecord):
            if record.state is CoolantState.FLOOD:
                lines.append(profile.coolant_mapping.flood)
                flush_spindle()
            elif index < last_coolant_on:
                lines.append(profile.coolant_mapping.off)
        elif isinstance(record, (RapidMotionRecord, LinearMotionRecord, ArcMotionRecord)):
            flush_spindle()
            if not positioned:
                start = record.start.position
                if (current[0], current[1]) != (start.x, start.y):
                    lines.append(
                        f"G00X{force_xyz(start.x)}Y{force_xyz(start.y)}"
                        f"Z{force_xyz(context.safe_z.value)}"
                    )
                    current = (start.x, start.y, context.safe_z.value)
                if current[2] != start.z:
                    lines.append(
                        f"G00X{force_xyz(start.x)}Y{force_xyz(start.y)}"
                        f"Z{force_xyz(start.z)}"
                    )
                    current = (start.x, start.y, start.z)
                positioned = True
            end = record.end.position
            if isinstance(record, RapidMotionRecord):
                lines.append(
                    f"G00X{force_xyz(end.x)}Y{force_xyz(end.y)}Z{force_xyz(end.z)}"
                )
            elif isinstance(record, LinearMotionRecord):
                feed = fanuc_number(record.feed_rate.value, numeric.feed_precision)
                lines.append(
                    f"G01X{force_xyz(end.x)}Y{force_xyz(end.y)}Z{force_xyz(end.z)}F{feed}"
                )
            else:
                code = (
                    "G03"
                    if record.sweep_radians * record.plane_normal.z > 0.0
                    else "G02"
                )
                i_value = record.center.x - record.start.position.x
                j_value = record.center.y - record.start.position.y
                feed = fanuc_number(record.feed_rate.value, numeric.feed_precision)
                lines.append(
                    f"{code}X{force_xyz(end.x)}Y{force_xyz(end.y)}Z{force_xyz(end.z)}"
                    f"I{force_ijk(i_value)}J{force_ijk(j_value)}F{feed}"
                )
            current = (end.x, end.y, end.z)
        elif isinstance(record, DwellRecord):
            raise ValueError("post.fanuc.dwell_unsupported")
    flush_spindle()
    if context.use_legacy_cutter_compensation:
        lines.append("G40")
    lines.extend(
        (
            profile.coolant_mapping.off,
            profile.spindle_mapping.stop,
            "G91G28G0Z0",
        )
    )
    return lines


def _program_footer_lines() -> list[str]:
    return ["G28Y0.", "M30", "%"]


class FanucRobodrill21iAdapter:
    """Machine-specific single-operation production adapter; it does not export files."""

    def __init__(self, definition: PostProcessorDefinition | None = None) -> None:
        self._definition = definition or robodrill_21i_definition()

    def capabilities(self) -> PostProcessorCapabilities:
        return self._definition.capabilities

    def validate_request(self, request: PostRequest) -> tuple[PostDiagnostic, ...]:
        diagnostics = list(validate_request(request, request.post_definition))
        if not _has_canonical_contract(request.post_definition):
            diagnostics.append(PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.definition_mismatch"))
        if request.program_context is None:
            diagnostics.append(PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.context_missing"))
        return tuple(diagnostics)

    def validate_program_ir(self, program: NCProgramIR) -> tuple[PostDiagnostic, ...]:
        contract_diagnostics = () if _has_canonical_contract(self._definition) else (
            PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.definition_mismatch"),
        )
        diagnostics = (*contract_diagnostics, *validate_program_ir(program), *validate_fanuc_program(program, self._definition))
        return tuple(sorted(set(diagnostics), key=lambda item: (item.code.value, item.record_index if item.record_index is not None else -1, item.message_key)))

    def lower_program_ir(self, program: NCProgramIR) -> NCProgramIR:
        return program

    def format_program(self, program: NCProgramIR, definition: PostProcessorDefinition) -> str:
        if not _has_canonical_contract(definition):
            raise ValueError("post.fanuc.definition_mismatch")
        diagnostics = validate_fanuc_program(program, definition)
        if diagnostics:
            raise ValueError(diagnostics[0].message_key)
        profile = definition.production_profile
        context = program.production_context
        assert profile is not None and context is not None
        lines = _single_program_header_lines(context, profile)
        lines.extend(_operation_section_lines(program, profile))
        lines.extend(_program_footer_lines())
        return profile.newline.join(lines) + profile.newline

    def format_program_header(self, plan: ProgramAssemblyPlan) -> tuple[str, ...]:
        """Format only the global assembly header."""
        return tuple(_assembly_program_header_lines(plan))

    def format_operation_section(
        self, section: ProgramOperationSection
    ) -> tuple[str, ...]:
        """Format one independent tool section without global delimiters/footer."""
        profile = self._definition.production_profile
        if profile is None:
            raise ValueError("post.fanuc.profile_missing")
        diagnostics = validate_fanuc_program(section.program_ir, self._definition)
        if diagnostics:
            raise ValueError(diagnostics[0].message_key)
        operation_lines = _operation_section_lines(section.program_ir, profile)
        lines = [
            f"(OPERATION={section.operation_id},SECTION={section.order_index})",
            *operation_lines[:4],
        ]
        lines.extend(f"({key.upper()}={value})" for key, value in section.display_metadata)
        lines.extend(operation_lines[4:])
        return tuple(lines)

    def format_program_footer(self) -> tuple[str, ...]:
        """Format only the one global program footer."""
        return tuple(_program_footer_lines())

    def format_assembly(
        self, plan: ProgramAssemblyPlan, definition: PostProcessorDefinition
    ) -> str:
        """Format one complete explicit-order multi-operation production program."""
        if not _has_canonical_contract(definition):
            raise ValueError("post.fanuc.definition_mismatch")
        profile = definition.production_profile
        assert profile is not None
        if (
            plan.post_definition_id != definition.definition_id
            or plan.post_definition_fingerprint != definition.fingerprint
            or plan.production_profile_id != profile.profile_id
            or plan.production_profile_fingerprint != profile.fingerprint
            or plan.adapter_key != ADAPTER_KEY
            or plan.adapter_version != definition.adapter_version
        ):
            raise ValueError("assembly.profile_mismatch")
        lines = list(self.format_program_header(plan))
        for section in plan.sections:
            lines.extend(self.format_operation_section(section))
        lines.extend(self.format_program_footer())
        return profile.newline.join(lines) + profile.newline

    def validate_output(self, text: str, program: NCProgramIR, definition: PostProcessorDefinition) -> tuple[PostDiagnostic, ...]:
        if not _has_canonical_contract(definition):
            return (PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.definition_mismatch"),)
        diagnostics = list(validate_fanuc_output(text, program, definition))
        try:
            expected = self.format_program(program, definition)
        except ValueError:
            diagnostics.append(PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.VALIDATION_FAILED, "post.fanuc.expected_output_unavailable"))
            return tuple(sorted(set(diagnostics), key=lambda item: (item.code.value, item.message_key)))
        if text != expected:
            diagnostics.append(PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.output_semantic_mismatch"))
        return tuple(sorted(set(diagnostics), key=lambda item: (item.code.value, item.message_key)))

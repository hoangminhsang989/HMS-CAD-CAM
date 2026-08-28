"""Fail-closed validation for the FANUC ROBODRILL 21i WorkNC profile."""

from __future__ import annotations

import math
import re

from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.post.model import (
    ArcMotionRecord, CoordinateMode, CoolantRecord, DwellRecord, FeedModeRecord,
    LinearMotionRecord, NCProgramIR, Plane, PostDiagnostic, PostDiagnosticCode,
    PostProcessorDefinition, RapidMotionRecord, SpindleStartRecord,
    SpindleStopRecord,
)
from hms_cadcam.cam.post.profile import (
    CutterCompensationPolicy, DwellPolicy, ProductionControllerProfile,
)
from hms_cadcam.cam.toolpath.events import CoolantState, FeedMode, MotionClass


_NUMBER = r"-?(?:\d+(?:\.\d*)?|\.\d+)"
_MOTION = re.compile(rf"G0[01]X({_NUMBER})Y({_NUMBER})Z({_NUMBER})(?:F({_NUMBER}))?")
_ARC = re.compile(rf"G0([23])X({_NUMBER})Y({_NUMBER})Z({_NUMBER})I({_NUMBER})J({_NUMBER})F({_NUMBER})")
_COMMENT = re.compile(r"\([^()\r\n]*\)")
_SPINDLE = re.compile(rf"M0([34])S({_NUMBER})")
_TOOL = re.compile(r"M06T([1-9]\d{0,3})")
_LENGTH = re.compile(rf"G43Z({_NUMBER})H([1-9]\d{{0,3}})")
_CUTTER = re.compile(r"G41D([1-9]\d{0,3})")


def fanuc_number(value: float, precision: int, *, force_decimal_point: bool = False) -> str:
    """Format one finite number without locale or scientific notation."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("FANUC numeric value must be finite")
    threshold = 0.5 * (10.0 ** -precision)
    normalized = 0.0 if abs(float(value)) < threshold else float(value)
    text = format(normalized, f".{precision}f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"-0", ""}:
        text = "0"
    if force_decimal_point and "." not in text:
        text += "."
    return text


def work_offset_code(program: NCProgramIR, profile: ProductionControllerProfile) -> str | None:
    for mapping in profile.work_offset_mapping:
        if (program.work_offset.name.upper(), program.work_offset.numeric_slot) == (mapping.logical_name, mapping.numeric_slot):
            return mapping.controller_code
    return None


def validate_fanuc_program(program: NCProgramIR, definition: PostProcessorDefinition) -> tuple[PostDiagnostic, ...]:
    # Import locally because the adapter module also imports this validator.
    from hms_cadcam.cam.post.fanuc_robodrill_21i import (
        has_canonical_robodrill_contract,
    )

    diagnostics: list[PostDiagnostic] = []
    profile = definition.production_profile
    if profile is None or not has_canonical_robodrill_contract(definition):
        return (_diag(PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.profile_missing"),)
    context = program.production_context
    if context is None:
        return (_diag(PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.program_context_missing"),)
    if program.strategy_key == "tapping_v1":
        diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.fanuc.tapping_unsupported"))
    elif program.strategy_key not in profile.supported_operation_strategies:
        diagnostics.append(_diag(PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.strategy_unsupported"))
    if program.unit not in profile.supported_units:
        diagnostics.append(_diag(PostDiagnosticCode.UNIT_MISMATCH, "post.fanuc.unit_unsupported"))
    if program.coordinate_mode is not CoordinateMode.ABSOLUTE or program.plane is not Plane.XY:
        diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_MOTION, "post.fanuc.mode_or_plane_unsupported"))
    if work_offset_code(program, profile) != "G54":
        diagnostics.append(_diag(PostDiagnosticCode.SETUP_INVALID, "post.fanuc.g54_mapping_required"))
    if context.tool_binding.tool_assembly_fingerprint != program.tool_assembly_fingerprint:
        diagnostics.append(_diag(PostDiagnosticCode.TOOL_STALE, "post.fanuc.tool_binding_stale"))
    if context.safe_z.unit is not program.unit:
        diagnostics.append(_diag(PostDiagnosticCode.UNIT_MISMATCH, "post.fanuc.safe_z_unit_mismatch"))
    motion_records = tuple(record for record in program.records if isinstance(record, (RapidMotionRecord, LinearMotionRecord, ArcMotionRecord)))
    if not motion_records:
        diagnostics.append(_diag(PostDiagnosticCode.SOURCE_INVALID, "post.fanuc.motion_missing"))
    else:
        highest_z = max(max(record.start.position.z, record.end.position.z) for record in motion_records)
        if context.safe_z.value + 1.0e-12 < highest_z:
            diagnostics.append(_diag(PostDiagnosticCode.RAPID_UNSAFE, "post.fanuc.safe_z_below_motion"))
    if context.use_legacy_cutter_compensation:
        if profile.cutter_compensation_policy is not CutterCompensationPolicy.LEGACY_WORKNC_LEFT:
            diagnostics.append(_diag(PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.cutter_policy_mismatch"))
        if program.strategy_key != "contour_2d":
            diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_MOTION, "post.fanuc.legacy_compensation_strategy_unsupported"))
        if context.tool_binding.diameter_offset is None:
            diagnostics.append(_diag(PostDiagnosticCode.TOOL_MISSING, "post.fanuc.diameter_offset_missing"))
    if program.strategy_key in {"rest_contour_3axis", "rest_finishing_3axis"}:
        if profile.cutter_compensation_policy is not CutterCompensationPolicy.DISABLED:
            diagnostics.append(_diag(PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.rest_cutter_compensation_must_be_disabled"))
        if context.use_legacy_cutter_compensation:
            diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_MOTION, "post.fanuc.rest_cutter_compensation_unsupported"))
    if profile.dwell_policy is DwellPolicy.UNSUPPORTED and any(isinstance(record, DwellRecord) for record in program.records):
        diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_CYCLE, "post.fanuc.dwell_unsupported"))
    active_feed: FeedMode | None = None
    observed_feed_modes: set[FeedMode] = set()
    spindle_on = False
    for index, record in enumerate(program.records):
        if isinstance(record, (RapidMotionRecord, LinearMotionRecord, ArcMotionRecord)):
            axes = (record.start.tool_axis, record.end.tool_axis)
            if any(
                not math.isclose(axis.x, 0.0, rel_tol=0.0, abs_tol=1.0e-9)
                or not math.isclose(axis.y, 0.0, rel_tol=0.0, abs_tol=1.0e-9)
                or not math.isclose(axis.z, 1.0, rel_tol=0.0, abs_tol=1.0e-9)
                for axis in axes
            ):
                diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_MOTION, "post.fanuc.tool_axis_unsupported", index))
        if isinstance(record, FeedModeRecord):
            active_feed = record.mode
            observed_feed_modes.add(record.mode)
            if record.mode not in profile.supported_feed_modes:
                diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_FEED_MODE, "post.fanuc.feed_mode_unsupported", index))
        elif isinstance(record, SpindleStartRecord):
            spindle_on = True
            if record.direction not in profile.supported_spindle_directions:
                diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_SPINDLE, "post.fanuc.spindle_direction_unsupported", index))
            if profile.minimum_rpm is not None and record.speed.value < profile.minimum_rpm:
                diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_SPINDLE, "post.fanuc.rpm_below_limit", index))
            if profile.maximum_rpm is not None and record.speed.value > profile.maximum_rpm:
                diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_SPINDLE, "post.fanuc.rpm_above_limit", index))
        elif isinstance(record, SpindleStopRecord):
            spindle_on = False
        elif isinstance(record, CoolantRecord) and record.state not in {CoolantState.OFF, CoolantState.FLOOD}:
            diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_COOLANT, "post.fanuc.coolant_unsupported", index))
        elif isinstance(record, (LinearMotionRecord, ArcMotionRecord)):
            if record.motion_class is MotionClass.CUTTING and not spindle_on:
                diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_SPINDLE, "post.fanuc.spindle_required_before_cut", index))
            if active_feed is None:
                diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_FEED_MODE, "post.fanuc.feed_required_before_motion", index))
            _validate_rounded_motion(record, profile, diagnostics, index)
            if isinstance(record, ArcMotionRecord):
                normal = record.plane_normal
                if (
                    abs(normal.x) > 1.0e-9
                    or abs(normal.y) > 1.0e-9
                    or not math.isclose(abs(normal.z), normal.magnitude, rel_tol=0.0, abs_tol=1.0e-9)
                ):
                    diagnostics.append(_diag(PostDiagnosticCode.ARC_INVALID, "post.fanuc.non_xy_arc_unsupported", index))
                sweep = abs(record.sweep_radians)
                if not profile.arc_policy.allow_large_sweep and sweep > math.pi:
                    diagnostics.append(_diag(PostDiagnosticCode.ARC_INVALID, "post.fanuc.large_arc_unsupported", index))
                if not profile.arc_policy.allow_full_circle and math.isclose(sweep, math.tau, rel_tol=0.0, abs_tol=1.0e-9):
                    diagnostics.append(_diag(PostDiagnosticCode.ARC_INVALID, "post.fanuc.full_circle_unsupported", index))
                if not profile.arc_policy.allow_multi_turn and sweep > math.tau:
                    diagnostics.append(_diag(PostDiagnosticCode.ARC_INVALID, "post.fanuc.multi_turn_arc_unsupported", index))
                if not profile.arc_policy.allow_helical and not math.isclose(record.start.position.z, record.end.position.z, rel_tol=0.0, abs_tol=1.0e-9):
                    diagnostics.append(_diag(PostDiagnosticCode.ARC_INVALID, "post.fanuc.helical_arc_unsupported", index))
    if not observed_feed_modes:
        diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_FEED_MODE, "post.fanuc.feed_mode_missing"))
    return tuple(sorted(set(diagnostics), key=lambda item: (item.code.value, item.record_index if item.record_index is not None else -1, item.message_key)))


def validate_fanuc_output(text: str, program: NCProgramIR, definition: PostProcessorDefinition) -> tuple[PostDiagnostic, ...]:
    diagnostics: list[PostDiagnostic] = []
    profile = definition.production_profile
    context = program.production_context
    if profile is None or context is None:
        return (_diag(PostDiagnosticCode.INVALID_REQUEST, "post.fanuc.output_context_missing"),)
    if not isinstance(text, str):
        return (_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.output_not_text"),)
    try:
        encoded = text.encode(profile.encoding)
    except UnicodeEncodeError:
        return (_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.output_encoding_invalid"),)
    if len(encoded) > profile.maximum_program_size:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.program_too_large"))
    if not text.endswith("\r\n") or "\n" in text.replace("\r\n", "") or "\r" in text.replace("\r\n", ""):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.crlf_required"))
    if any(ord(char) < 32 and char not in "\r\n" for char in text):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.control_character"))
    lines = text.splitlines()
    if not lines or lines[0] != "%" or lines[-1] != "%" or lines.count("%") != 2:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.delimiters_invalid"))
        return tuple(diagnostics)
    required_header = ("(SHL-TECH)", f"(FileName={context.file_name})", "G90G80G49G40G17", "G91G28G0Z0", f"M06T{context.tool_binding.tool_station}", "G90G40G54X0.Y0.")
    if len(lines) < 15 or lines[1] != required_header[0] or lines[2] != required_header[1] or not lines[3].startswith("(DAO="):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.comments_or_header_invalid"))
    if len(lines) <= 8 or lines[4:8] != list(required_header[2:]):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.required_header_missing"))
    expected_length_line = (
        "G43Z"
        + fanuc_number(
            context.safe_z.value,
            profile.numeric_format.xyz_precision,
            force_decimal_point=True,
        )
        + f"H{context.tool_binding.length_offset}"
    )
    length_lines = [line for line in lines if _LENGTH.fullmatch(line)]
    if length_lines != [expected_length_line] or len(lines) <= 8 or lines[8] != expected_length_line:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.length_offset_invalid"))
    tool_lines = [line for line in lines if _TOOL.fullmatch(line)]
    if len(tool_lines) != 1 or int(_TOOL.fullmatch(tool_lines[0]).group(1)) != context.tool_binding.tool_station:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.tool_activation_invalid"))
    if any(re.match(r"[ON]\d", line) for line in lines if not line.startswith("(")):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.program_or_block_number_forbidden"))
    footer = ["M09", "M05", "G91G28G0Z0", "G28Y0.", "M30", "%"]
    if lines[-6:] != footer or lines.count("M30") != 1:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.footer_invalid"))
    cutter_lines = [line for line in lines if _CUTTER.fullmatch(line)]
    cutter_cancel_indices = [index for index, line in enumerate(lines) if line == "G40"]
    if context.use_legacy_cutter_compensation:
        expected = context.tool_binding.diameter_offset
        if (
            len(cutter_lines) != 1
            or expected is None
            or int(_CUTTER.fullmatch(cutter_lines[0]).group(1)) != expected
            or len(lines) <= 9
            or lines[9] != cutter_lines[0]
            or cutter_cancel_indices != [len(lines) - 7]
        ):
            diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.cutter_compensation_unbalanced"))
    elif cutter_lines or cutter_cancel_indices:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.unexpected_cutter_compensation"))
    allowed_static = {"%", "G90G80G49G40G17", "G91G28G0Z0", "G90G40G54X0.Y0.", "G40", "M08", "M09", "M05", "G28Y0.", "M30"}
    spindle_on = False
    coolant_on = False
    arc_lines: list[str] = []
    for line in lines:
        if len(line) > profile.maximum_line_length:
            diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.line_too_long"))
        if line.startswith("("):
            if _COMMENT.fullmatch(line) is None or len(line) > profile.maximum_comment_length + 2:
                diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.comment_invalid"))
            continue
        spindle_match = _SPINDLE.fullmatch(line)
        if spindle_match:
            spindle_on = True
            continue
        if line == "M05":
            spindle_on = False
            continue
        if line == "M08":
            coolant_on = True
            continue
        if line == "M09":
            coolant_on = False
            continue
        if _MOTION.fullmatch(line):
            if line.startswith("G01") and (not spindle_on or "F" not in line):
                diagnostics.append(_diag(PostDiagnosticCode.VALIDATION_FAILED, "post.fanuc.linear_process_state_invalid"))
            continue
        if _ARC.fullmatch(line):
            arc_lines.append(line)
            if not spindle_on:
                diagnostics.append(_diag(PostDiagnosticCode.VALIDATION_FAILED, "post.fanuc.arc_spindle_missing"))
            continue
        if line in allowed_static or _TOOL.fullmatch(line) or _LENGTH.fullmatch(line) or _CUTTER.fullmatch(line):
            continue
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.fanuc.unknown_command"))
    if spindle_on or coolant_on:
        diagnostics.append(_diag(PostDiagnosticCode.VALIDATION_FAILED, "post.fanuc.output_state_unbalanced"))
    expected_arcs = tuple(record for record in program.records if isinstance(record, ArcMotionRecord))
    if len(arc_lines) != len(expected_arcs):
        diagnostics.append(_diag(PostDiagnosticCode.ARC_INVALID, "post.fanuc.arc_count_mismatch"))
    else:
        for line, record in zip(arc_lines, expected_arcs):
            match = _ARC.fullmatch(line)
            assert match is not None
            expected_code = "3" if record.sweep_radians * record.plane_normal.z > 0.0 else "2"
            end_x, end_y = float(match.group(2)), float(match.group(3))
            i_value, j_value = float(match.group(5)), float(match.group(6))
            numeric = profile.numeric_format
            expected_line = (
                f"G0{expected_code}"
                f"X{fanuc_number(record.end.position.x, numeric.xyz_precision, force_decimal_point=True)}"
                f"Y{fanuc_number(record.end.position.y, numeric.xyz_precision, force_decimal_point=True)}"
                f"Z{fanuc_number(record.end.position.z, numeric.xyz_precision, force_decimal_point=True)}"
                f"I{fanuc_number(record.center.x - record.start.position.x, numeric.ijk_precision, force_decimal_point=True)}"
                f"J{fanuc_number(record.center.y - record.start.position.y, numeric.ijk_precision, force_decimal_point=True)}"
                f"F{fanuc_number(record.feed_rate.value, numeric.feed_precision)}"
            )
            radius_start = math.hypot(i_value, j_value)
            radius_end = math.hypot(end_x - (record.start.position.x + i_value), end_y - (record.start.position.y + j_value))
            if line != expected_line or abs(radius_start - radius_end) > profile.arc_policy.radius_tolerance:
                diagnostics.append(_diag(PostDiagnosticCode.ARC_INVALID, "post.fanuc.arc_output_geometry_invalid"))
    return tuple(sorted(set(diagnostics), key=lambda item: (item.code.value, item.message_key)))


def _validate_rounded_motion(record: LinearMotionRecord | ArcMotionRecord, profile: ProductionControllerProfile, diagnostics: list[PostDiagnostic], index: int) -> None:
    p = profile.numeric_format
    start = tuple(float(fanuc_number(value, p.xyz_precision)) for value in (record.start.position.x, record.start.position.y, record.start.position.z))
    end = tuple(float(fanuc_number(value, p.xyz_precision)) for value in (record.end.position.x, record.end.position.y, record.end.position.z))
    if start == end:
        diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_MOTION, "post.fanuc.rounding_collapses_motion", index))
    if isinstance(record, ArcMotionRecord):
        center = tuple(float(fanuc_number(value, p.ijk_precision)) for value in (record.center.x, record.center.y))
        start_radius = math.hypot(start[0] - center[0], start[1] - center[1])
        end_radius = math.hypot(end[0] - center[0], end[1] - center[1])
        if abs(start_radius - end_radius) > profile.arc_policy.radius_tolerance:
            diagnostics.append(_diag(PostDiagnosticCode.ARC_INVALID, "post.fanuc.arc_rounding_radius_mismatch", index))


def _diag(code: PostDiagnosticCode, key: str, record_index: int | None = None) -> PostDiagnostic:
    return PostDiagnostic(DiagnosticSeverity.ERROR, code, key, record_index=record_index)

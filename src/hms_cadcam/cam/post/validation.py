"""Layered validation for source, program IR and canonical neutral text."""

from __future__ import annotations

import math
import re

from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.post.model import (
    ArcMotionRecord, CoordinateMode, CoordinateModeRecord, CoolantRecord,
    FeedModeRecord, FeedValueRecord, LinearMotionRecord, NCProgramIR,
    PlaneRecord, PostDiagnostic, PostDiagnosticCode, PostProcessorDefinition, PostRequest,
    ProgramBeginRecord, ProgramEndRecord, RapidMotionRecord, SpindleStartRecord,
    SpindleStopRecord, ToolActivationRecord, UnitsRecord, WorkOffsetRecord,
)


def _diag(code: PostDiagnosticCode, key: str, *, record_index: int | None = None) -> PostDiagnostic:
    return PostDiagnostic(DiagnosticSeverity.ERROR, code, key, record_index=record_index)


def validate_program_ir(program: NCProgramIR) -> tuple[PostDiagnostic, ...]:
    diagnostics: list[PostDiagnostic] = []
    records = program.records
    required = ((ProgramBeginRecord, "post.program_begin_missing"), (UnitsRecord, "post.units_missing"),
                (CoordinateModeRecord, "post.coordinate_mode_missing"), (PlaneRecord, "post.plane_missing"),
                (WorkOffsetRecord, "post.work_offset_missing"), (ToolActivationRecord, "post.tool_activation_missing"),
                (ProgramEndRecord, "post.program_end_missing"))
    for typ, key in required:
        if sum(isinstance(record, typ) for record in records) != 1:
            diagnostics.append(_diag(PostDiagnosticCode.VALIDATION_FAILED, key))
    if not isinstance(records[0], ProgramBeginRecord) or not isinstance(records[-1], ProgramEndRecord):
        diagnostics.append(_diag(PostDiagnosticCode.VALIDATION_FAILED, "post.program_boundaries_invalid"))
    if any(record.sequence_index != index for index, record in enumerate(records)):
        diagnostics.append(_diag(PostDiagnosticCode.VALIDATION_FAILED, "post.record_sequence_invalid"))
    if not any(isinstance(record, CoordinateModeRecord) and record.mode is CoordinateMode.ABSOLUTE for record in records):
        diagnostics.append(_diag(PostDiagnosticCode.VALIDATION_FAILED, "post.absolute_mode_required"))
    active_feed = None
    spindle_on = False
    coolant_on = False
    for index, record in enumerate(records):
        if isinstance(record, FeedModeRecord):
            active_feed = record.mode
        elif isinstance(record, FeedValueRecord):
            if record.feed_rate.value <= 0 or not math.isfinite(record.feed_rate.value):
                diagnostics.append(_diag(PostDiagnosticCode.INVALID_REQUEST, "post.feed_invalid", record_index=index))
        elif isinstance(record, (LinearMotionRecord, ArcMotionRecord)):
            if active_feed is None:
                diagnostics.append(_diag(PostDiagnosticCode.VALIDATION_FAILED, "post.feed_mode_missing", record_index=index))
            if not all(math.isfinite(float(value)) for value in (record.start.position.x, record.start.position.y, record.start.position.z)):
                diagnostics.append(_diag(PostDiagnosticCode.INVALID_REQUEST, "post.motion_nonfinite", record_index=index))
        elif isinstance(record, SpindleStartRecord):
            spindle_on = True
        elif isinstance(record, SpindleStopRecord):
            spindle_on = False
        elif isinstance(record, CoolantRecord):
            coolant_on = record.state.value != "off"
        elif isinstance(record, RapidMotionRecord):
            if record.motion_class.value == "cutting":
                diagnostics.append(_diag(PostDiagnosticCode.RAPID_UNSAFE, "post.rapid_cutting", record_index=index))
    if spindle_on:
        diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_SPINDLE, "post.spindle_not_stopped"))
    if coolant_on:
        diagnostics.append(_diag(PostDiagnosticCode.UNSUPPORTED_COOLANT, "post.coolant_not_stopped"))
    return tuple(sorted(diagnostics, key=lambda item: (item.code.value, item.record_index if item.record_index is not None else -1, item.message_key)))


def validate_output(text: str, program: NCProgramIR, definition: PostProcessorDefinition) -> tuple[PostDiagnostic, ...]:
    diagnostics: list[PostDiagnostic] = []
    if not isinstance(text, str):
        return (_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_not_text"),)
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_not_utf8"))
        return tuple(diagnostics)
    if len(encoded) > definition.maximum_program_size:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_too_large"))
    if "\r\r\n" in text or "\r" in text.replace("\r\n", ""):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_newline_invalid"))
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_control_character"))
    if re.search(r"\b[GM]\d*\b", text, re.IGNORECASE):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_controller_syntax"))
    lines = text.splitlines()
    if sum(line.startswith("PROGRAM_BEGIN") for line in lines) != 1 or sum(line == "PROGRAM_END" for line in lines) != 1 or not lines or not lines[0].startswith("PROGRAM_BEGIN") or lines[-1] != "PROGRAM_END":
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_boundaries_invalid"))
    if any(len(line) > definition.maximum_line_length for line in text.splitlines()):
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_line_too_long"))
    if text.lower().find("nan") >= 0 or text.lower().find("inf") >= 0:
        diagnostics.append(_diag(PostDiagnosticCode.FORMAT_FAILED, "post.output_nonfinite"))
    return tuple(sorted(diagnostics, key=lambda item: (item.code.value, item.message_key)))


def validate_request(request: PostRequest, definition: PostProcessorDefinition) -> tuple[PostDiagnostic, ...]:
    if request.post_definition.fingerprint != definition.fingerprint:
        return (_diag(PostDiagnosticCode.INVALID_REQUEST, "post.definition_mismatch"),)
    if not definition.capabilities.supported_operation_strategies or request.post_definition.adapter_key == "":
        return (_diag(PostDiagnosticCode.INVALID_REQUEST, "post.definition_invalid"),)
    return ()

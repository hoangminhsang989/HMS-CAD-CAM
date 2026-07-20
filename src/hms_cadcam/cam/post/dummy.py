"""Deterministic canonical dummy adapter for headless contract tests."""

from __future__ import annotations

import math

from hms_cadcam.cam.domain.machine import MachineKind
from hms_cadcam.cam.post.model import *
from hms_cadcam.cam.post.validation import validate_output, validate_program_ir, validate_request


def _number(value: float, precision: int) -> str:
    if not math.isfinite(value):
        raise ValueError("non-finite output")
    if value == 0.0:
        value = 0.0
    return format(value, f".{precision}g")


def _point(point, precision: int) -> str:
    return f"X={_number(point.x, precision)} Y={_number(point.y, precision)} Z={_number(point.z, precision)}"


def _pose(pose, precision: int) -> str:
    axis = pose.tool_axis
    return f"{_point(pose.position, precision)} AX={_number(axis.x, precision)} AY={_number(axis.y, precision)} AZ={_number(axis.z, precision)}"


def canonical_definition() -> PostProcessorDefinition:
    return PostProcessorDefinition(
        PostProcessorDefinitionId.new(), 1, "canonical_dummy", 1,
        PostProcessorCapabilities(supported_machine_kinds=(MachineKind.MILL, MachineKind.MILL_TURN), supported_axes=("X", "Y", "Z", "axis_x", "axis_y", "axis_z"), tapping_synchronization=True, tapping_modes=(TappingMode.RIGID, TappingMode.FLOATING)),
        numeric_precision=15, maximum_line_length=1024, maximum_program_size=8 * 1024 * 1024,
        display_name="Canonical neutral dummy",
    )


class CanonicalDummyAdapter:
    """Neutral formatter; it is deliberately not a CNC post or exporter."""

    def validate_request(self, request: PostRequest) -> tuple[PostDiagnostic, ...]:
        if request.post_definition.adapter_key != "canonical_dummy":
            return (PostDiagnostic(DiagnosticSeverity.ERROR, PostDiagnosticCode.INVALID_REQUEST, "post.dummy_definition_mismatch"),)
        return validate_request(request, request.post_definition)

    def validate_program_ir(self, program: NCProgramIR) -> tuple[PostDiagnostic, ...]:
        return validate_program_ir(program)

    def lower_program_ir(self, program: NCProgramIR) -> NCProgramIR:
        return program

    def format_program(self, program: NCProgramIR, definition: PostProcessorDefinition) -> str:
        p = definition.numeric_precision
        lines: list[str] = []
        for record in program.records:
            if isinstance(record, ProgramBeginRecord):
                meta = " ".join(f"{key}={value}" for key, value in record.metadata)
                lines.append("PROGRAM_BEGIN" + ((" " + meta) if meta else ""))
            elif isinstance(record, UnitsRecord): lines.append(f"UNITS {record.unit.value.upper()}")
            elif isinstance(record, CoordinateModeRecord): lines.append(f"MODE {record.mode.value.upper()}")
            elif isinstance(record, PlaneRecord): lines.append(f"PLANE {record.plane.value.upper()}")
            elif isinstance(record, WorkOffsetRecord):
                offset = record.work_offset
                lines.append("WORK_OFFSET " + (f"NAME_UTF8_HEX={offset.name.encode('utf-8').hex()} SLOT={offset.numeric_slot}" if offset else "NONE"))
            elif isinstance(record, ToolActivationRecord):
                lines.append(f"TOOL_ACTIVATE ASSEMBLY={record.tool_assembly_id} FP={record.tool_assembly_fingerprint.digest}" + (f" TOOL={record.tool_id}" if record.tool_id else "") + (f" HOLDER={record.holder_id}" if record.holder_id else ""))
            elif isinstance(record, FeedModeRecord): lines.append(f"FEED_MODE {record.mode.value.upper()}")
            elif isinstance(record, FeedValueRecord): lines.append(f"FEED_VALUE {_number(record.feed_rate.value, p)} {record.feed_rate.unit.value.upper()}")
            elif isinstance(record, SpindleDirectionRecord): lines.append(f"SPINDLE_DIRECTION {record.direction.value.upper()}")
            elif isinstance(record, SpindleStartRecord): lines.append(f"SPINDLE_START DIRECTION={record.direction.value.upper()} RPM={_number(record.speed.value, p)}")
            elif isinstance(record, SpindleStopRecord): lines.append("SPINDLE_STOP")
            elif isinstance(record, CoolantRecord): lines.append(f"COOLANT {record.state.value.upper()}")
            elif isinstance(record, RapidMotionRecord): lines.append(f"MOVE_RAPID START[{_pose(record.start,p)}] END[{_pose(record.end,p)}]" + (f" RATE={_number(record.rapid_rate.value,p)} UNIT={record.rapid_rate.unit.value.upper()}" if record.rapid_rate else ""))
            elif isinstance(record, LinearMotionRecord): lines.append(f"MOVE_LINEAR START[{_pose(record.start,p)}] END[{_pose(record.end,p)}] FEED={_number(record.feed_rate.value,p)} UNIT={record.feed_rate.unit.value.upper()} CLASS={record.motion_class.value.upper()}")
            elif isinstance(record, ArcMotionRecord): lines.append(f"MOVE_ARC START[{_pose(record.start,p)}] END[{_pose(record.end,p)}] CENTER[{_point(record.center,p)}] NORMAL[NX={_number(record.plane_normal.x,p)} NY={_number(record.plane_normal.y,p)} NZ={_number(record.plane_normal.z,p)}] SWEEP={_number(record.sweep_radians,p)} FEED={_number(record.feed_rate.value,p)} UNIT={record.feed_rate.unit.value.upper()} CLASS={record.motion_class.value.upper()} PLANE={program.plane.value.upper()}")
            elif isinstance(record, DwellRecord): lines.append(f"DWELL SECONDS={_number(record.duration_seconds,p)}")
            elif isinstance(record, SemanticMarkerRecord):
                lines.append("MARKER KEY_UTF8_HEX=" + record.semantic_key.encode("utf-8").hex() + (" MESSAGE_UTF8_HEX=" + record.message.encode("utf-8").hex() if record.message else ""))
                lines.extend(f"MARKER_META KEY_UTF8_HEX={key.encode('utf-8').hex()} VALUE_UTF8_HEX={value.encode('utf-8').hex()}" for key, value in record.metadata)
            elif isinstance(record, ProgramEndRecord): lines.append("PROGRAM_END")
            else: raise ValueError("unsupported record")
        return definition.newline.join(lines) + definition.newline

    def validate_output(self, text: str, program: NCProgramIR, definition: PostProcessorDefinition) -> tuple[PostDiagnostic, ...]:
        return validate_output(text, program, definition)
    def capabilities(self) -> PostProcessorCapabilities:
        return canonical_definition().capabilities

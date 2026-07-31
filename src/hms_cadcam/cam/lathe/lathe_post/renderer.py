"""Qt-free deterministic renderer for the sample-derived basic Lathe Post."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import math
from collections.abc import Mapping, Sequence

from hms_cadcam.cam.lathe.lathe_post.basic_profile import BasicLathePostProfile, basic_lathe_post_profile
from hms_cadcam.cam.lathe.lathe_post.basic_types import (
    BasicPostDiagnostic,
    BasicPostDiagnosticCode,
    BasicPostMetadata,
    BasicPostReadiness,
    BasicToolMapping,
)
from hms_cadcam.cam.lathe.lathe_post.formatting import format_number, round_rpm, sanitize_comment, sanitize_filename_stem
from hms_cadcam.cam.lathe.lathe_post.ir import (
    DwellPayload,
    LatheProgramBlock,
    LatheProgramBlockKind,
    LatheProgramIRV1,
    LatheSpindleAction,
    LatheSpindleDirection,
    LatheUnits,
    MotionPayload,
    ThreadCutIntentPayload,
)
from hms_cadcam.cam.lathe.lathe_post.nc_validation import validate_basic_nc_text

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BasicNcOutputSnapshot:
    lines: tuple[str, ...]
    text: str
    sha256: str
    readiness: BasicPostReadiness
    suggested_filename: str
    diagnostics: tuple[BasicPostDiagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class BasicNcRenderResult:
    snapshot: BasicNcOutputSnapshot | None
    diagnostics: tuple[BasicPostDiagnostic, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.snapshot is not None and not self.diagnostics


def _diag(code: BasicPostDiagnosticCode, subject: str | None = None) -> BasicPostDiagnostic:
    return BasicPostDiagnostic(code.value, f"lathe.basic_post.diagnostic.{code.name.casefold()}", subject)


def _coordinate(value: object, profile: BasicLathePostProfile) -> str:
    return format_number(value, profile.coordinate_decimals, suppress_leading_zero=profile.suppress_leading_zero, trim_trailing_zero=profile.trim_trailing_zero)


def _motion_words(payload: MotionPayload, profile: BasicLathePostProfile, code: str, feed: object | None) -> str:
    words = [code, f"X{_coordinate(payload.end.x_diameter_mm, profile)}", f"Z{_coordinate(payload.end.z_mm, profile)}"]
    if feed is not None:
        words.append(f"F{format_number(feed, profile.pitch_decimals if isinstance(payload, ThreadCutIntentPayload) else profile.feed_decimals, suppress_leading_zero=profile.suppress_leading_zero, trim_trailing_zero=profile.trim_trailing_zero)}")
    return " ".join(words)


class LatheBasicFanucPostRendererV1:
    """Render one validated neutral Program IR without filesystem or Qt access."""

    def __init__(self, profile: BasicLathePostProfile | None = None) -> None:
        self.profile = profile or basic_lathe_post_profile()

    def render(
        self,
        program: LatheProgramIRV1,
        tool_mappings: Mapping[str, BasicToolMapping] | Sequence[BasicToolMapping],
        metadata: BasicPostMetadata | None = None,
    ) -> BasicNcRenderResult:
        diagnostics: list[BasicPostDiagnostic] = []
        if not isinstance(program, LatheProgramIRV1):
            return BasicNcRenderResult(None, (_diag(BasicPostDiagnosticCode.INVALID_PROGRAM),))
        if self.profile.profile_id != "hms.lathe.fanuc_basic_sample_v1" or not self.profile.machine_output_supported or self.profile.preview_only:
            return BasicNcRenderResult(None, (_diag(BasicPostDiagnosticCode.INVALID_PROFILE, self.profile.profile_id),))
        mapping = self._normalize_mappings(tool_mappings, diagnostics)
        if diagnostics:
            return BasicNcRenderResult(None, tuple(diagnostics))
        metadata = metadata or BasicPostMetadata(program.identity.program_id)
        lines = self._header(metadata)
        groups = self._groups(program, diagnostics)
        if diagnostics:
            return BasicNcRenderResult(None, tuple(diagnostics))
        thread_warning_emitted = False
        for index, group in enumerate(groups):
            op_begin = next(block for block in group if block.kind is LatheProgramBlockKind.OPERATION_BEGIN)
            op_end = next(block for block in group if block.kind is LatheProgramBlockKind.OPERATION_END)
            operation_payload = op_begin.payload
            operation_id = op_begin.operation_id or getattr(operation_payload, "operation_id", "")
            tool_blocks = [block for block in group if block.kind is LatheProgramBlockKind.TOOL_INTENT]
            spindle_blocks = [block for block in group if block.kind is LatheProgramBlockKind.SPINDLE_INTENT]
            motion_blocks = [block for block in group if block.kind in {LatheProgramBlockKind.RAPID_MOTION, LatheProgramBlockKind.LINEAR_CUT_MOTION, LatheProgramBlockKind.LEAD_IN_MOTION, LatheProgramBlockKind.LEAD_OUT_MOTION, LatheProgramBlockKind.THREAD_CUT_INTENT}]
            if len(tool_blocks) != 1 or not spindle_blocks or not motion_blocks:
                diagnostics.append(_diag(BasicPostDiagnosticCode.MISSING_OPERATION_BOUNDARY, operation_id))
                continue
            coolant_blocks = [block for block in group if block.kind is LatheProgramBlockKind.COOLANT_INTENT]
            coolant_enabled = self.profile.default_coolant_enabled
            for coolant_block in coolant_blocks:
                action = str(getattr(coolant_block.payload, "action", getattr(coolant_block.payload, "state", ""))).upper()
                if action in {"ON", "M8", "COOLANT_ON", "TRUE"}:
                    coolant_enabled = True
                elif action in {"OFF", "M9", "COOLANT_OFF", "FALSE"}:
                    coolant_enabled = False
                else:
                    diagnostics.append(_diag(BasicPostDiagnosticCode.UNSUPPORTED_BLOCK, coolant_block.kind.value))
            tool_payload = tool_blocks[0].payload
            tool = mapping.get(tool_payload.tool_id)
            if tool is None or not tool.enabled:
                diagnostics.append(_diag(BasicPostDiagnosticCode.MISSING_TOOL_MAPPING, tool_payload.tool_id))
                continue
            starts = [block.payload for block in spindle_blocks if getattr(block.payload, "action", None) is LatheSpindleAction.START]
            if len(starts) != 1:
                diagnostics.append(_diag(BasicPostDiagnosticCode.INVALID_SPINDLE, operation_id))
                continue
            if any(block.kind is LatheProgramBlockKind.DWELL_INTENT for block in group):
                diagnostics.append(_diag(BasicPostDiagnosticCode.BASIC_POST_DWELL_SYNTAX_UNDEFINED, operation_id))
                continue
            if any(isinstance(block.payload, ThreadCutIntentPayload) for block in motion_blocks) and not thread_warning_emitted:
                lines.append("(THREAD OUTPUT USES BASIC G32 - SPINDLE PHASE NOT VERIFIED)")
                thread_warning_emitted = True
            lines.append(f"(O DAO = {tool.tool_number} OFFSET O = {tool.geometry_offset_number})")
            description = tool.description or dict(metadata.tool_descriptions).get(tool.tool_id, "")
            if description:
                lines.append(f"({sanitize_comment(description, uppercase=self.profile.uppercase_comments)})")
            lines.append(f"G0 {self.profile.tool_word(tool.tool_number, tool.geometry_offset_number)}")
            if coolant_enabled:
                lines.append(self.profile.coolant_on_code)
            spindle = starts[0]
            try:
                rpm = round_rpm(spindle.speed_rpm)
            except ValueError:
                diagnostics.append(_diag(BasicPostDiagnosticCode.INVALID_SPINDLE, operation_id))
                continue
            direction = self.profile.spindle_cw_code if spindle.direction is LatheSpindleDirection.CW else self.profile.spindle_ccw_code
            lines.append(f"{self.profile.spindle_mode_code} S{rpm} {direction}")
            first_motion = True
            feed: float | None = None
            for block in motion_blocks:
                payload = block.payload
                if not isinstance(payload, MotionPayload):
                    diagnostics.append(_diag(BasicPostDiagnosticCode.UNSUPPORTED_BLOCK, block.kind.value))
                    continue
                is_thread = isinstance(payload, ThreadCutIntentPayload)
                if is_thread:
                    if not math.isclose(float(payload.feed_mm_per_rev or 0.0), float(payload.pitch_mm), rel_tol=0.0, abs_tol=1.0e-9):
                        diagnostics.append(_diag(BasicPostDiagnosticCode.THREAD_FEED_MISMATCH, operation_id))
                        continue
                    motion_code = "G32"
                    motion_feed = payload.pitch_mm
                elif block.kind is LatheProgramBlockKind.RAPID_MOTION:
                    motion_code = "G0"
                    motion_feed = None
                else:
                    motion_code = "G1"
                    motion_feed = payload.feed_mm_per_rev
                if first_motion and motion_code != "G0":
                    lines.append(f"G0 {self.profile.work_offset_code} X{_coordinate(payload.start.x_diameter_mm, self.profile)} Z{_coordinate(payload.start.z_mm, self.profile)}")
                elif first_motion:
                    lines.append(f"G0 {self.profile.work_offset_code} X{_coordinate(payload.end.x_diameter_mm, self.profile)} Z{_coordinate(payload.end.z_mm, self.profile)}")
                    first_motion = False
                    continue
                first_motion = False
                emit_feed = motion_feed is not None and (feed is None or not math.isclose(float(feed), float(motion_feed), rel_tol=0.0, abs_tol=1.0e-12))
                lines.append(_motion_words(payload, self.profile, motion_code, motion_feed if emit_feed else None))
                if emit_feed:
                    feed = float(motion_feed)
            if self.profile.emit_spindle_stop_each_operation:
                lines.append(self.profile.spindle_stop_code)
            if coolant_enabled:
                lines.append(self.profile.coolant_off_code)
            lines.append(self.profile.reference_return_code)
            if self.profile.emit_optional_stop_between_operations and (index < len(groups) - 1 or self.profile.optional_stop_after_last):
                lines.append(self.profile.optional_stop_code)
        if diagnostics:
            return BasicNcRenderResult(None, tuple(diagnostics))
        self._append_end(lines)
        lines = self._line_number(lines)
        text = "\r\n".join(lines) + "\r\n"
        output_diagnostics = validate_basic_nc_text(text, self.profile)
        if output_diagnostics:
            return BasicNcRenderResult(None, output_diagnostics)
        digest = hashlib.sha256(text.encode("ascii")).hexdigest()
        suggested = sanitize_filename_stem(metadata.file_stem) + self.profile.output_extension
        snapshot = BasicNcOutputSnapshot(tuple(lines), text, digest, BasicPostReadiness.BASIC_NC_PREVIEW_READY_UNVERIFIED, suggested)
        return BasicNcRenderResult(snapshot, ())

    def _normalize_mappings(self, mappings: Mapping[str, BasicToolMapping] | Sequence[BasicToolMapping], diagnostics: list[BasicPostDiagnostic]) -> dict[str, BasicToolMapping]:
        values = tuple(mappings.values()) if isinstance(mappings, Mapping) else tuple(mappings)
        result: dict[str, BasicToolMapping] = {}
        combinations: set[tuple[int, int]] = set()
        for item in values:
            if not isinstance(item, BasicToolMapping):
                diagnostics.append(_diag(BasicPostDiagnosticCode.INVALID_TOOL_MAPPING))
                continue
            if item.tool_id in result or (item.tool_number, item.geometry_offset_number) in combinations:
                diagnostics.append(_diag(BasicPostDiagnosticCode.DUPLICATE_TOOL_MAPPING, item.tool_id))
            result[item.tool_id] = item
            combinations.add((item.tool_number, item.geometry_offset_number))
        return result

    def _groups(self, program: LatheProgramIRV1, diagnostics: list[BasicPostDiagnostic]) -> list[list[LatheProgramBlock]]:
        if not program.blocks or program.blocks[0].kind is not LatheProgramBlockKind.PROGRAM_BEGIN or program.blocks[-1].kind is not LatheProgramBlockKind.PROGRAM_END:
            diagnostics.append(_diag(BasicPostDiagnosticCode.INVALID_PROGRAM, "lifecycle"))
            return []
        unit_blocks = [block for block in program.blocks if block.kind is LatheProgramBlockKind.SET_UNITS]
        plane_blocks = [block for block in program.blocks if block.kind is LatheProgramBlockKind.SET_PLANE]
        if len(unit_blocks) != 1 or getattr(unit_blocks[0].payload, "units", None) is not LatheUnits.MILLIMETRES:
            diagnostics.append(_diag(BasicPostDiagnosticCode.INVALID_PROGRAM, "units"))
        if len(plane_blocks) != 1:
            diagnostics.append(_diag(BasicPostDiagnosticCode.INVALID_PROGRAM, "plane"))
        groups: list[list[LatheProgramBlock]] = []
        current: list[LatheProgramBlock] | None = None
        for block in program.blocks:
            if block.kind is LatheProgramBlockKind.OPERATION_BEGIN:
                if current is not None:
                    diagnostics.append(_diag(BasicPostDiagnosticCode.MISSING_OPERATION_BOUNDARY, block.operation_id))
                current = [block]
            elif block.kind is LatheProgramBlockKind.OPERATION_END:
                if current is None:
                    diagnostics.append(_diag(BasicPostDiagnosticCode.MISSING_OPERATION_BOUNDARY, block.operation_id))
                else:
                    current.append(block)
                    groups.append(current)
                    current = None
            elif current is not None:
                current.append(block)
            elif block.kind not in {LatheProgramBlockKind.PROGRAM_BEGIN, LatheProgramBlockKind.SET_UNITS, LatheProgramBlockKind.SET_PLANE, LatheProgramBlockKind.PROGRAM_END}:
                diagnostics.append(_diag(BasicPostDiagnosticCode.UNSUPPORTED_BLOCK, block.kind.value))
        if current is not None:
            diagnostics.append(_diag(BasicPostDiagnosticCode.MISSING_OPERATION_BOUNDARY, "unterminated"))
        return groups

    def _header(self, metadata: BasicPostMetadata) -> list[str]:
        lines = ["%", self.profile.program_word(), "(HMS BASIC FANUC STYLE LATHE POST)"]
        if self.profile.warning_header_enabled and (not self.profile.machine_verified or not self.profile.production_approved):
            lines.append("(UNVERIFIED OUTPUT - CHECK BEFORE MACHINE USE)")
        lines.extend([f"(TEN FILE = {sanitize_comment(metadata.file_stem, uppercase=self.profile.uppercase_comments)})", "(SHL_TECH)"])
        if self.profile.emit_g18:
            lines.append("G18")
        if self.profile.emit_g40:
            lines.append("G40")
        if self.profile.emit_g80:
            lines.append("G80")
        lines.extend(["G21", self.profile.feed_mode_code])
        if self.profile.optional_setup_m73:
            lines.append("M73")
        if self.profile.optional_setup_m74:
            lines.append("M74")
        if self.profile.optional_secondary_work_offset_g55:
            lines.append("G55")
        if self.profile.optional_initial_tool_call is not None:
            tool = self.profile.optional_initial_tool_call
            lines.append(f"G0 {self.profile.tool_word(tool.tool_number, tool.offset_number)}")
            if self.profile.optional_manual_stop_after_initial_tool:
                lines.append("M0")
        return lines

    def _append_end(self, lines: list[str]) -> None:
        shutdown = [self.profile.spindle_stop_code, self.profile.coolant_off_code, self.profile.reference_return_code]
        if lines[-3:] != shutdown:
            for code in shutdown:
                if not lines or lines[-1] != code:
                    lines.append(code)
        if self.profile.emit_final_safe_tool:
            lines.append(f"T{self.profile.tool_word(self.profile.final_safe_tool.tool_number, self.profile.final_safe_tool.offset_number)[1:]}")
        lines.append(self.profile.program_end_code)
        lines.append("%")

    def _line_number(self, lines: list[str]) -> list[str]:
        if not self.profile.emit_line_numbers:
            return lines
        value = self.profile.line_number_start
        result: list[str] = []
        for line in lines:
            if line in {"%"} or line.startswith("(") or line.startswith("O"):
                result.append(line)
            else:
                result.append(f"N{value} {line}")
                value += self.profile.line_number_step
        return result


LatheBasicFanucPostV1 = LatheBasicFanucPostRendererV1

__all__ = ["BasicNcOutputSnapshot", "BasicNcRenderResult", "LatheBasicFanucPostRendererV1", "LatheBasicFanucPostV1"]

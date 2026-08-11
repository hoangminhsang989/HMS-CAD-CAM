"""Deterministic operator-readable Tranche3 reports."""

from __future__ import annotations

import csv
import io
from typing import Any

from hms_cadcam.cam.qualification.model import MachineQualificationContract, QualificationReport
from hms_cadcam.cam.qualification.offline_model import (
    MotionClass,
    NCReleaseCandidate,
    OfflineNCVerificationSession,
)
from hms_cadcam.cam.qualification.physical_model import MachineSetupQualification


UNKNOWN_VI = "Chưa xác minh"


def _value(value: object, suffix: str = "") -> str:
    if value is None:
        return UNKNOWN_VI
    return f"{value}{suffix}"


def risk_summary(session: OfflineNCVerificationSession) -> dict[str, int]:
    return {
        "total_blocks": len(session.blocks),
        "rapid_blocks": sum(item.motion_class is MotionClass.RAPID for item in session.blocks),
        "cutting_blocks": sum(
            item.motion_class in {MotionClass.CUTTING_LINEAR, MotionClass.CUTTING_ARC}
            for item in session.blocks
        ),
        "tool_changes": sum(item.motion_class is MotionClass.TOOL_CHANGE for item in session.blocks),
        "warnings": sum(item.severity.value == "WARNING" for item in session.findings),
        "blockers": sum(item.severity.value == "BLOCKER" for item in session.findings),
        "unresolved_blocks": sum(item.motion_class is MotionClass.UNRESOLVED for item in session.blocks),
    }


def execution_trace(session: OfflineNCVerificationSession) -> list[dict[str, Any]]:
    return [
        {
            "sequence": block.sequence,
            "line": block.original_line_number,
            "motion_class": block.motion_class.value,
            "tool": block.modal_after.tool,
            "modal_motion": block.modal_after.motion,
            "spindle_on": block.modal_after.spindle_on,
            "spindle_rpm": block.modal_after.spindle_rpm,
            "coolant_on": block.modal_after.coolant_on,
            "work_offset": block.modal_after.work_offset,
            "h": block.modal_after.h_offset,
            "d": block.modal_after.d_offset,
            "operation_id": block.operation_id,
            "transition": (
                "RETRACT_APPROACH"
                if block.motion_class is MotionClass.RAPID and block.modal_before.z != block.modal_after.z
                else None
            ),
        }
        for block in session.blocks
    ]


def motion_reviews(session: OfflineNCVerificationSession) -> dict[str, list[dict[str, Any]]]:
    rapids: list[dict[str, Any]] = []
    cutting: list[dict[str, Any]] = []
    tool_changes: list[dict[str, Any]] = []
    for block in session.blocks:
        if block.motion_class is MotionClass.RAPID:
            rapids.append(
                {
                    "line": block.original_line_number,
                    "start_program_point": [block.modal_before.x, block.modal_before.y, block.modal_before.z],
                    "end_program_point": [block.modal_after.x, block.modal_after.y, block.modal_after.z],
                    "setup_transform_status": (
                        "BOUND_TO_SETUP" if block.modal_after.work_offset == "G54" else "UNRESOLVED"
                    ),
                    "physical_coordinate_status": "PHYSICAL_ENDPOINTS_UNVERIFIED",
                    "fixture_clearance": "RAPID_PHYSICAL_CLEARANCE_UNVERIFIED",
                    "holder_clearance": "RAPID_PHYSICAL_CLEARANCE_UNVERIFIED",
                }
            )
        if block.motion_class in {MotionClass.CUTTING_LINEAR, MotionClass.CUTTING_ARC}:
            cutting.append(
                {
                    "line": block.original_line_number, "tool": block.modal_after.tool,
                    "spindle_on": block.modal_after.spindle_on,
                    "spindle_rpm": block.modal_after.spindle_rpm,
                    "feed": block.modal_after.feed,
                    "compensation": block.modal_after.compensation,
                    "h": block.modal_after.h_offset, "d": block.modal_after.d_offset,
                    "operation_id": block.operation_id,
                }
            )
        if block.motion_class is MotionClass.TOOL_CHANGE:
            tool_changes.append(
                {
                    "line": block.original_line_number,
                    "source_tool": block.modal_before.tool,
                    "destination_tool": block.modal_after.tool,
                    "spindle_before": block.modal_before.spindle_on,
                    "spindle_after": block.modal_after.spindle_on,
                    "coolant_after": block.modal_after.coolant_on,
                    "h": block.modal_after.h_offset, "d": block.modal_after.d_offset,
                    "safe_sequence": (
                        "STATIC_SEQUENCE_VALID"
                        if not block.modal_before.spindle_on and not block.modal_before.coolant_on
                        else "UNRESOLVED"
                    ),
                    "physical_position": "PHYSICAL_TOOL_CHANGE_POSITION_UNVERIFIED",
                }
            )
    return {"rapid": rapids, "cutting": cutting, "tool_changes": tool_changes}


def boundary_review(session: OfflineNCVerificationSession) -> dict[str, Any]:
    first = session.blocks[0].modal_after if session.blocks else None
    last = session.blocks[-1].modal_after if session.blocks else None
    valid_static = not any(
        item.code == "POST_SEQUENCE_INVALID" and item.severity.value == "BLOCKER"
        for item in session.findings
    )
    return {
        "start": None if first is None else {
            "units": first.units, "positioning": first.positioning, "plane": first.plane,
            "compensation": first.compensation, "work_offset": first.work_offset,
            "tool": first.tool, "spindle_on": first.spindle_on, "coolant_on": first.coolant_on,
        },
        "end": None if last is None else {
            "retract_status": "LOGICAL_TRACE_ONLY", "compensation": last.compensation,
            "spindle_stopped": not last.spindle_on, "coolant_off": not last.coolant_on,
            "program_end": any("M30" in block.normalized_tokens for block in session.blocks),
        },
        "static_sequence": "STATIC_SEQUENCE_VALID" if valid_static else "STATIC_SEQUENCE_INVALID",
        "physical_sequence": "PHYSICAL_SAFE_SEQUENCE_CONFIRMED: false",
    }


def verification_report_payload(session: OfflineNCVerificationSession) -> dict[str, Any]:
    return {
        "format": "HMS_STAGE18A_TRANCHE3_STATIC_VERIFICATION_REPORT",
        "format_version": 1,
        "session_fingerprint": session.session_fingerprint.to_dict(),
        "offline_logical_trace_only": True,
        "controller_emulator": False,
        "risk_summary": risk_summary(session),
        "boundary_review": boundary_review(session),
        "motion_reviews": motion_reviews(session),
        "execution_trace": execution_trace(session),
        "findings": [item.to_dict() for item in session.findings],
    }


def render_setup_sheet_vi(
    *,
    project_name: str,
    program_name: str,
    candidate: NCReleaseCandidate,
    session: OfflineNCVerificationSession,
    setup: MachineSetupQualification,
    contract: MachineQualificationContract,
) -> str:
    stock = setup.stock.dimensions
    fixture = setup.fixture
    lines = [
        "# PHIẾU THIẾT LẬP — BÀN GIAO CHẠY THỬ NGOÀI",
        "",
        f"- Dự án: {project_name}", f"- Chương trình: {program_name}",
        f"- Máy: {contract.display_name}", "- Bộ điều khiển: FANUC 31i-B",
        f"- NC SHA-256: {candidate.nc_sha256}",
        f"- Revision phát hành: {candidate.release_revision}",
        f"- Work offset: {setup.work_offset_transform.work_offset}",
        f"- Gốc chi tiết X/Y/Z: {_value(setup.part_zero.x)} / {_value(setup.part_zero.y)} / {_value(setup.part_zero.z)}",
        f"- Phôi X/Y/Z: {stock.x_mm} / {stock.y_mm} / {stock.z_mm} mm",
        f"- Đồ gá: {_value(None if fixture is None else fixture.fixture_id)}",
        f"- Trạng thái hành trình vật lý: Chưa đủ dữ liệu để xác minh hành trình tuyệt đối trên máy",
        f"- Trạng thái khoảng hở: {_value(None if setup.clearance_evidence is None else setup.clearance_evidence.result.value)}",
        f"- Qualification: {candidate.qualification_level}",
        "- Level2: CHƯA ĐẠT", "- Level3: CHƯA ĐẠT", "- MACHINE_READY: KHÔNG",
        "", "## Cảnh báo / lỗi chặn",
    ]
    lines.extend(
        f"- [{item.severity.value}] {item.code}: {item.message}" for item in session.findings
    )
    lines.extend(("", "Tham chiếu checklist: dry-run-checklist.md", ""))
    return "\n".join(lines)


def render_tool_list_csv(setup: MachineSetupQualification, session: OfflineNCVerificationSession) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow((
        "Tool logical ID", "Tool number", "Type", "Diameter mm", "Length mm", "Holder",
        "H", "D", "Operation usage", "Fingerprint",
    ))
    for tool in setup.tools:
        h_values = sorted({
            block.modal_after.h_offset for block in session.blocks
            if block.modal_after.tool == tool.tool_number and block.modal_after.h_offset is not None
        })
        d_values = sorted({
            block.modal_after.d_offset for block in session.blocks
            if block.modal_after.tool == tool.tool_number and block.modal_after.d_offset is not None
        })
        operation_usage = sorted({
            block.operation_id for block in session.blocks
            if block.modal_after.tool == tool.tool_number and block.operation_id is not None
        })
        writer.writerow((
            f"T{tool.tool_number}", tool.tool_number, UNKNOWN_VI,
            _value(tool.cutter_diameter_mm), _value(tool.total_assembly_length_mm),
            _value(None if tool.holder_fingerprint is None else tool.holder_fingerprint.digest),
            ",".join(map(str, h_values)) or UNKNOWN_VI,
            ",".join(map(str, d_values)) or UNKNOWN_VI,
            ",".join(operation_usage) or UNKNOWN_VI, tool.fingerprint.digest,
        ))
    return buffer.getvalue()


def operation_summary(session: OfflineNCVerificationSession) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    current_tool: int | None = None
    start_line: int | None = None
    relevant: list[Any] = []
    for block in session.blocks:
        if block.motion_class is MotionClass.TOOL_CHANGE:
            if relevant:
                groups.append(_operation_group(len(groups) + 1, current_tool, start_line, relevant))
            current_tool = block.modal_after.tool
            start_line = block.original_line_number
            relevant = []
        elif block.motion_class in {MotionClass.RAPID, MotionClass.CUTTING_LINEAR, MotionClass.CUTTING_ARC}:
            if start_line is None:
                start_line = block.original_line_number
            relevant.append(block)
    if relevant:
        groups.append(_operation_group(len(groups) + 1, current_tool, start_line, relevant))
    return groups


def _operation_group(order: int, tool: int | None, start: int | None, blocks: list[Any]) -> dict[str, Any]:
    end = blocks[-1].original_line_number
    spindle = next((item.modal_after.spindle_rpm for item in blocks if item.modal_after.spindle_rpm), None)
    feed = next((item.modal_after.feed for item in blocks if item.modal_after.feed), None)
    return {
        "operation_order": order, "operation_type": UNKNOWN_VI,
        "tool": tool, "spindle_rpm": spindle, "feed_mm_min": feed,
        "path_metadata": {"block_count": len(blocks), "machining_time": UNKNOWN_VI},
        "nc_block_range": [start, end], "qualification_status": "Đạt kiểm tra phần mềm",
    }


__all__ = [
    "UNKNOWN_VI", "boundary_review", "execution_trace", "motion_reviews",
    "operation_summary", "render_setup_sheet_vi", "render_tool_list_csv",
    "risk_summary", "verification_report_payload",
]

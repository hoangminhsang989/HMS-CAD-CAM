"""Deterministic offline FANUC NC block analysis for Stage18A Tranche3."""

from __future__ import annotations

from dataclasses import dataclass, replace
import re

from hms_cadcam.cam.qualification.model import FindingSeverity, sha256_bytes
from hms_cadcam.cam.qualification.offline_model import (
    ModalSnapshot,
    MotionClass,
    NCBlockRecord,
    OfflineFindingSeverity,
    StaticSafetyFinding,
)
from hms_cadcam.cam.qualification.validation import validate_fanuc_modal_sequence


_PAREN_COMMENT = re.compile(r"\([^)]*\)")
_TOKEN = re.compile(r"(?:[A-Z][+-]?(?:\d+(?:\.\d*)?|\.\d+)|%|/)", re.IGNORECASE)
_NUMBER = re.compile(r"([A-Z])([+-]?(?:\d+(?:\.\d*)?|\.\d+))", re.IGNORECASE)
_CANNED = re.compile(r"G8[1-9](?!\d)", re.IGNORECASE)
_TAPPING = re.compile(r"G84(?!\d)", re.IGNORECASE)
_UNSUPPORTED_OFFSET = re.compile(r"G5[5-9](?!\d)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class AnalysisPolicy:
    maximum_spindle_rpm: float | None = None
    maximum_feed_mm_min: float | None = None
    expected_tool_numbers: tuple[int, ...] = ()
    require_h_for_cutting: bool = True
    require_d_for_cutter_compensation: bool = True
    physical_travel_verified: bool = False
    fixture_placement_verified: bool = False
    holder_clearance_verified: bool = False
    collision_evidence_current: bool = False
    level2_evidence_current: bool = False
    owner_sample_available: bool = False


@dataclass(frozen=True, slots=True)
class NCAnalysisResult:
    nc_sha256: str
    blocks: tuple[NCBlockRecord, ...]
    findings: tuple[StaticSafetyFinding, ...]

    @property
    def risk_summary(self) -> dict[str, int]:
        return {
            "total_blocks": len(self.blocks),
            "rapid_blocks": sum(item.motion_class is MotionClass.RAPID for item in self.blocks),
            "cutting_blocks": sum(
                item.motion_class in {MotionClass.CUTTING_LINEAR, MotionClass.CUTTING_ARC}
                for item in self.blocks
            ),
            "tool_changes": sum(item.motion_class is MotionClass.TOOL_CHANGE for item in self.blocks),
            "warnings": sum(item.severity is OfflineFindingSeverity.WARNING for item in self.findings),
            "blockers": sum(item.severity is OfflineFindingSeverity.BLOCKER for item in self.findings),
            "unresolved_blocks": sum(item.motion_class is MotionClass.UNRESOLVED for item in self.blocks),
        }


def _finding(
    code: str,
    severity: OfflineFindingSeverity,
    message: str,
    *,
    line: int | None = None,
    source: str = "Stage18AOfflineNCAnalyzer",
    authority: str = "repository static analysis",
    remediation: str = "Review and regenerate the NC from authoritative sources.",
    impact: str = "VISIBLE_WARNING",
    ordinal: int = 1,
) -> StaticSafetyFinding:
    return StaticSafetyFinding(
        f"{code}:{line or 0}:{ordinal}", code, severity, message, line, source,
        authority, remediation, impact,
    )


def _words(tokens: tuple[str, ...]) -> dict[str, tuple[float, ...]]:
    values: dict[str, list[float]] = {}
    for token in tokens:
        match = _NUMBER.fullmatch(token)
        if match:
            values.setdefault(match.group(1).upper(), []).append(float(match.group(2)))
    return {key: tuple(items) for key, items in values.items()}


def _g_codes(tokens: tuple[str, ...]) -> set[int]:
    result: set[int] = set()
    for token in tokens:
        if token.startswith("G"):
            try:
                value = float(token[1:])
            except ValueError:
                continue
            if value.is_integer():
                result.add(int(value))
    return result


def _m_codes(tokens: tuple[str, ...]) -> set[int]:
    result: set[int] = set()
    for token in tokens:
        if token.startswith("M"):
            try:
                value = float(token[1:])
            except ValueError:
                continue
            if value.is_integer():
                result.add(int(value))
    return result


def _normalize_line(line: str) -> tuple[tuple[str, ...], str]:
    without_semicolon = line.split(";", 1)[0]
    code = _PAREN_COMMENT.sub("", without_semicolon).strip().upper().replace(" ", "").replace("\t", "")
    tokens = tuple(match.group(0).upper() for match in _TOKEN.finditer(code))
    residue = _TOKEN.sub("", code)
    return tokens, residue


def _motion_class(tokens: tuple[str, ...], residue: str, before: ModalSnapshot) -> MotionClass:
    if residue:
        return MotionClass.UNRESOLVED
    if not tokens:
        return MotionClass.NON_MOTION
    g_codes = _g_codes(tokens)
    m_codes = _m_codes(tokens)
    if 6 in m_codes:
        return MotionClass.TOOL_CHANGE
    if g_codes & {0}:
        return MotionClass.RAPID
    if g_codes & {1}:
        return MotionClass.CUTTING_LINEAR
    if g_codes & {2, 3}:
        return MotionClass.CUTTING_ARC
    has_axis = any(token.startswith(("X", "Y", "Z", "I", "J", "K", "R")) for token in tokens)
    if has_axis and before.motion == "G00":
        return MotionClass.RAPID
    if has_axis and before.motion == "G01":
        return MotionClass.CUTTING_LINEAR
    if has_axis and before.motion in {"G02", "G03"}:
        return MotionClass.CUTTING_ARC
    if m_codes & {3, 4, 5} or any(token.startswith("S") for token in tokens):
        return MotionClass.SPINDLE_CONTROL
    if m_codes & {7, 8, 9}:
        return MotionClass.COOLANT_CONTROL
    if g_codes & {40, 41, 42, 43, 49, 54, 55, 56, 57, 58, 59} or any(
        token.startswith(("H", "D")) for token in tokens
    ):
        return MotionClass.OFFSET_CONTROL
    if m_codes & {0, 1, 2, 30} or any(token.startswith("O") or token == "%" for token in tokens):
        return MotionClass.PROGRAM_CONTROL
    return MotionClass.NON_MOTION


def _advance(before: ModalSnapshot, tokens: tuple[str, ...]) -> ModalSnapshot:
    data = before.to_dict()
    g_codes = _g_codes(tokens)
    m_codes = _m_codes(tokens)
    for code in (0, 1, 2, 3):
        if code in g_codes:
            data["motion"] = f"G{code:02d}"
    if 20 in g_codes:
        data["units"] = "G20"
    if 21 in g_codes:
        data["units"] = "G21"
    if 90 in g_codes:
        data["positioning"] = "G90"
    if 91 in g_codes:
        data["positioning"] = "G91"
    for code in (17, 18, 19):
        if code in g_codes:
            data["plane"] = f"G{code}"
    for code in range(54, 60):
        if code in g_codes:
            data["work_offset"] = f"G{code}"
    if 40 in g_codes:
        data["compensation"] = "G40"
        data["d_offset"] = None
    if 41 in g_codes:
        data["compensation"] = "G41"
    if 42 in g_codes:
        data["compensation"] = "G42"
    if 49 in g_codes:
        data["h_offset"] = None
    words = _words(tokens)
    if 6 in m_codes and "T" in words:
        data["tool"] = int(words["T"][-1])
        data["h_offset"] = None
        data["d_offset"] = None
    if "H" in words:
        data["h_offset"] = int(words["H"][-1])
    if "D" in words:
        data["d_offset"] = int(words["D"][-1])
    if "S" in words:
        data["spindle_rpm"] = words["S"][-1]
    if "F" in words:
        data["feed"] = words["F"][-1]
    if m_codes & {3, 4}:
        data["spindle_on"] = True
    if 5 in m_codes:
        data["spindle_on"] = False
    if m_codes & {7, 8}:
        data["coolant_on"] = True
    if 9 in m_codes:
        data["coolant_on"] = False
    for axis in ("X", "Y", "Z"):
        if axis in words:
            data[axis.lower()] = words[axis][-1]
    return ModalSnapshot(**data)


def analyze_nc_bytes(payload: bytes, policy: AnalysisPolicy | None = None) -> NCAnalysisResult:
    """Analyze exact NC bytes without rewriting them or claiming controller emulation."""

    if not isinstance(payload, bytes) or not payload:
        raise TypeError("payload must be non-empty bytes")
    selected = policy or AnalysisPolicy()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = payload.decode("ascii")
        except UnicodeDecodeError as error:
            raise ValueError("NC bytes must be UTF-8 or ASCII for deterministic analysis") from error

    provisional: list[NCBlockRecord] = []
    findings: list[StaticSafetyFinding] = []
    state = ModalSnapshot()
    g54_seen = False
    ordinal_by_code: dict[str, int] = {}

    def add(code: str, severity: OfflineFindingSeverity, message: str, *, line: int | None = None,
            remediation: str = "Review and regenerate the NC from authoritative sources.",
            impact: str = "VISIBLE_WARNING", source: str = "Stage18AOfflineNCAnalyzer",
            authority: str = "repository static analysis") -> StaticSafetyFinding:
        ordinal_by_code[code] = ordinal_by_code.get(code, 0) + 1
        value = _finding(code, severity, message, line=line, remediation=remediation,
                         impact=impact, source=source, authority=authority,
                         ordinal=ordinal_by_code[code])
        findings.append(value)
        return value

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for line_number, original in enumerate(lines, start=1):
        tokens, residue = _normalize_line(original)
        if not tokens and not residue and not original.strip():
            continue
        before = state
        category = _motion_class(tokens, residue, before)
        after = _advance(before, tokens)
        block_findings: list[str] = []
        code_text = "".join(tokens)
        if after.work_offset == "G54" or "G54" in tokens:
            g54_seen = True
        if residue or category is MotionClass.UNRESOLVED:
            item = add(
                "UNRESOLVED_BLOCK_TOKEN", OfflineFindingSeverity.BLOCKER,
                f"Unresolved NC syntax remains in line {line_number}.", line=line_number,
                remediation="Remove or qualify the unresolved controller syntax.",
                impact="HANDOFF_BLOCKED",
            )
            block_findings.append(item.finding_id)
        if _UNSUPPORTED_OFFSET.search(code_text):
            item = add(
                "UNSUPPORTED_WORK_OFFSET", OfflineFindingSeverity.BLOCKER,
                "G55-G59 is outside the frozen G54 qualification contract.", line=line_number,
                impact="HANDOFF_BLOCKED",
            )
            block_findings.append(item.finding_id)
        if _CANNED.search(code_text):
            item = add(
                "UNSUPPORTED_CANNED_CYCLE_TOKEN", OfflineFindingSeverity.BLOCKER,
                "G81-G89 canned-cycle semantics are not qualified.", line=line_number,
                remediation="Expand the cycle into qualified explicit motion.",
                impact="HANDOFF_BLOCKED",
            )
            block_findings.append(item.finding_id)
        if _TAPPING.search(code_text):
            item = add(
                "TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED", OfflineFindingSeverity.BLOCKER,
                "Tapping remains outside the machine-qualified workflow.", line=line_number,
                remediation="Do not release Tapping NC under the current authority.",
                impact="HANDOFF_BLOCKED",
            )
            block_findings.append(item.finding_id)
        words = _words(tokens)
        if selected.maximum_spindle_rpm is not None and "S" in words and words["S"][-1] > selected.maximum_spindle_rpm:
            item = add(
                "SPINDLE_OUTSIDE_CONTRACT", OfflineFindingSeverity.BLOCKER,
                "Spindle command exceeds the frozen machine contract.", line=line_number,
                impact="HANDOFF_BLOCKED",
            )
            block_findings.append(item.finding_id)
        if selected.maximum_feed_mm_min is not None and "F" in words and words["F"][-1] > selected.maximum_feed_mm_min:
            item = add(
                "FEED_OUTSIDE_CONTRACT", OfflineFindingSeverity.BLOCKER,
                "Feed command exceeds the frozen machine contract.", line=line_number,
                impact="HANDOFF_BLOCKED",
            )
            block_findings.append(item.finding_id)
        if category in {MotionClass.CUTTING_LINEAR, MotionClass.CUTTING_ARC}:
            if after.tool is None or (
                selected.expected_tool_numbers and after.tool not in selected.expected_tool_numbers
            ):
                item = add(
                    "TOOL_BINDING_CONFLICT", OfflineFindingSeverity.BLOCKER,
                    "Cutting motion has no current Tool binding.", line=line_number,
                    impact="HANDOFF_BLOCKED",
                )
                block_findings.append(item.finding_id)
            if not after.spindle_on:
                item = add(
                    "CUTTING_WITHOUT_SPINDLE", OfflineFindingSeverity.BLOCKER,
                    "Cutting motion is present while spindle state is off or unresolved.",
                    line=line_number, impact="HANDOFF_BLOCKED",
                )
                block_findings.append(item.finding_id)
            if after.feed is None:
                item = add(
                    "FEED_STATE_UNRESOLVED", OfflineFindingSeverity.BLOCKER,
                    "Cutting motion has no resolved feed state.", line=line_number,
                    impact="HANDOFF_BLOCKED",
                )
                block_findings.append(item.finding_id)
            if selected.require_h_for_cutting and after.h_offset is None:
                item = add(
                    "MISSING_H_MAPPING", OfflineFindingSeverity.BLOCKER,
                    "Cutting motion has no resolved H length offset.", line=line_number,
                    impact="HANDOFF_BLOCKED",
                )
                block_findings.append(item.finding_id)
            if after.compensation in {"G41", "G42"} and selected.require_d_for_cutter_compensation and after.d_offset is None:
                item = add(
                    "MISSING_D_MAPPING", OfflineFindingSeverity.BLOCKER,
                    "Cutter compensation is active without a resolved D mapping.", line=line_number,
                    impact="HANDOFF_BLOCKED",
                )
                block_findings.append(item.finding_id)
        provisional.append(
            NCBlockRecord(
                len(provisional), line_number, original, tokens, category, before, after,
                tuple(sorted(block_findings)),
            )
        )
        state = after

    if not g54_seen:
        add(
            "G54_MISSING", OfflineFindingSeverity.BLOCKER,
            "The exact G54 work offset was not found.", impact="HANDOFF_BLOCKED",
        )

    for inherited in validate_fanuc_modal_sequence(text):
        severity = {
            FindingSeverity.INFO: OfflineFindingSeverity.INFO,
            FindingSeverity.WARNING: OfflineFindingSeverity.WARNING,
            FindingSeverity.ERROR: OfflineFindingSeverity.BLOCKER,
        }[inherited.severity]
        add(
            inherited.code.name, severity, inherited.code.value,
            source="validate_fanuc_modal_sequence",
            impact="HANDOFF_BLOCKED" if severity is OfflineFindingSeverity.BLOCKER else "VISIBLE_WARNING",
        )

    physical_unknowns = (
        (selected.physical_travel_verified, "UNKNOWN_PHYSICAL_TRAVEL_VALIDATION", "Chưa đủ dữ liệu để xác minh hành trình tuyệt đối trên máy"),
        (selected.fixture_placement_verified, "UNKNOWN_FIXTURE_PLACEMENT", "Fixture placement is not physically verified."),
        (selected.holder_clearance_verified, "UNKNOWN_HOLDER_CLEARANCE", "Holder clearance is not physically verified."),
        (selected.collision_evidence_current, "STALE_COLLISION_EVIDENCE", "Current collision evidence is unavailable or stale."),
        (selected.level2_evidence_current, "LEVEL2_EVIDENCE_STALE", "Level2 evidence is absent or stale; Level2 remains not achieved."),
        (selected.owner_sample_available, "OWNER_APPROVED_SAMPLE_UNAVAILABLE", "Owner-approved physical sample is unavailable."),
    )
    for satisfied, code, message in physical_unknowns:
        if not satisfied:
            add(
                code, OfflineFindingSeverity.WARNING, message,
                authority="physical evidence not supplied",
                remediation="Preserve this unknown in the external dry-run checklist.",
                impact="PHYSICAL_QUALIFICATION_NOT_ACHIEVED",
            )
    if any(item.motion_class is MotionClass.RAPID for item in provisional) and not (
        selected.physical_travel_verified and selected.fixture_placement_verified and selected.holder_clearance_verified
    ):
        add(
            "RAPID_PHYSICAL_CLEARANCE_UNVERIFIED", OfflineFindingSeverity.WARNING,
            "Rapid endpoints or physical clearance cannot be fully verified.",
            authority="offline logical trace only",
            remediation="Perform the controlled external dry-run checklist.",
            impact="PHYSICAL_QUALIFICATION_NOT_ACHIEVED",
        )
    if any(item.motion_class is MotionClass.TOOL_CHANGE for item in provisional):
        add(
            "PHYSICAL_TOOL_CHANGE_POSITION_UNVERIFIED", OfflineFindingSeverity.WARNING,
            "Physical Tool-change position is not controller-confirmed.",
            authority="offline logical trace only",
            remediation="Verify Tool-change position during controlled external preparation.",
            impact="PHYSICAL_QUALIFICATION_NOT_ACHIEVED",
        )

    findings_by_line: dict[int, list[str]] = {}
    for item in findings:
        if item.block_line is not None:
            findings_by_line.setdefault(item.block_line, []).append(item.finding_id)
    blocks = tuple(
        replace(
            item,
            finding_ids=tuple(sorted(set((*item.finding_ids, *findings_by_line.get(item.original_line_number, ())))))
        )
        for item in provisional
    )
    ordered_findings = tuple(sorted(findings, key=lambda item: (item.block_line or 0, item.code, item.finding_id)))
    return NCAnalysisResult(sha256_bytes(payload), blocks, ordered_findings)


__all__ = ["AnalysisPolicy", "NCAnalysisResult", "analyze_nc_bytes"]

"""Fail-closed Stage18A validation over existing Program Assembly output."""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.post.assembly_model import ProgramAssemblyResult
from hms_cadcam.cam.post.model import (
    ArcMotionRecord,
    FeedValueRecord,
    LinearMotionRecord,
    RapidMotionRecord,
    SpindleStartRecord,
)
from hms_cadcam.cam.qualification.model import (
    EvidenceResult,
    FindingCode,
    FindingSeverity,
    MachineQualificationContract,
    PhysicalEvidence,
    QualificationFinding,
    QualificationLevel,
    QualificationReport,
    StockEnvelope,
    ToolQualificationInput,
)
from hms_cadcam.cam.qualification.profile import ROBODRILL_ALPHA_D21MIB_PROFILE_ID


_TOOL = re.compile(r"M06T([1-9]\d{0,3})")
_LENGTH = re.compile(r"G43Z-?(?:\d+(?:\.\d*)?|\.\d+)H([1-9]\d{0,3})")
_CUTTER = re.compile(r"G4[12]D([1-9]\d{0,3})")
_CANNED = re.compile(r"G8[1-9](?!\d)", re.IGNORECASE)
_TAPPING_CYCLE = re.compile(r"G84(?!\d)", re.IGNORECASE)
_UNSUPPORTED_OFFSET = re.compile(r"G5[5-9](?!\d)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class StaticQualificationInput:
    assembly_result: ProgramAssemblyResult
    machine_contract: MachineQualificationContract
    tools: tuple[ToolQualificationInput, ...]
    stock_envelope: StockEnvelope | None
    physical_evidence: PhysicalEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.assembly_result, ProgramAssemblyResult):
            raise CamValidationError("Qualification assembly result is invalid")
        if not isinstance(self.machine_contract, MachineQualificationContract):
            raise CamValidationError("Qualification machine contract is invalid")
        if not isinstance(self.tools, tuple) or any(
            not isinstance(item, ToolQualificationInput) for item in self.tools
        ):
            raise CamValidationError("Qualification Tool inputs are invalid")
        if self.stock_envelope is not None and not isinstance(self.stock_envelope, StockEnvelope):
            raise CamValidationError("Qualification stock envelope is invalid")
        if self.physical_evidence is not None and not isinstance(
            self.physical_evidence, PhysicalEvidence
        ):
            raise CamValidationError("Qualification physical evidence is invalid")


def _finding(
    severity: FindingSeverity,
    code: FindingCode,
    *evidence: tuple[str, str],
) -> QualificationFinding:
    return QualificationFinding(severity, code, code.value, tuple(evidence))


def _contract_number(contract: MachineQualificationContract, key: str) -> float:
    value = contract.leaf(key).value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"Machine contract {key} is not numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise CamValidationError(f"Machine contract {key} is not positive")
    return result


def _arc_extrema(record: ArcMotionRecord) -> tuple[tuple[float, float, float], ...]:
    start = record.start.position
    end = record.end.position
    center = record.center
    points = [(start.x, start.y, start.z), (end.x, end.y, end.z)]
    radius = math.hypot(start.x - center.x, start.y - center.y)
    if radius <= 0.0 or record.plane_normal.z == 0.0:
        return tuple(points)
    start_angle = math.atan2(start.y - center.y, start.x - center.x)
    sweep = record.sweep_radians * (1.0 if record.plane_normal.z > 0.0 else -1.0)

    def within(angle: float) -> bool:
        tau = 2.0 * math.pi
        if sweep >= 0.0:
            return ((angle - start_angle) % tau) <= sweep + 1e-12
        return ((start_angle - angle) % tau) <= -sweep + 1e-12

    for angle in (0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0):
        if within(angle):
            fraction = 0.0 if sweep == 0.0 else ((angle - start_angle) / sweep)
            z = start.z + (end.z - start.z) * max(0.0, min(1.0, fraction))
            points.append((center.x + radius * math.cos(angle), center.y + radius * math.sin(angle), z))
    return tuple(points)


def _program_extents(result: ProgramAssemblyResult) -> tuple[float, float, float]:
    points: list[tuple[float, float, float]] = []
    for section in result.plan.sections:
        for record in section.program_ir.records:
            if isinstance(record, (RapidMotionRecord, LinearMotionRecord)):
                points.extend(
                    (
                        (record.start.position.x, record.start.position.y, record.start.position.z),
                        (record.end.position.x, record.end.position.y, record.end.position.z),
                    )
                )
            elif isinstance(record, ArcMotionRecord):
                points.extend(_arc_extrema(record))
    if not points:
        return (0.0, 0.0, 0.0)
    axes = tuple(zip(*points, strict=True))
    return tuple(max(values) - min(values) for values in axes)  # type: ignore[return-value]


def validate_fanuc_modal_sequence(text: str) -> tuple[QualificationFinding, ...]:
    """Validate known deterministic modal/order semantics without physical claims."""

    if not isinstance(text, str) or not text:
        return (_finding(FindingSeverity.ERROR, FindingCode.POST_SEQUENCE_INVALID),)
    lines = tuple(line.strip() for line in text.replace("\r\n", "\n").split("\n") if line.strip())
    findings: list[QualificationFinding] = []
    if len(lines) < 8 or lines[0] != "%" or lines[-1] != "%" or lines.count("M30") != 1:
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.POST_SEQUENCE_INVALID))
        return tuple(findings)
    try:
        initialization = lines.index("G90G80G49G40G17")
        first_tool = next(index for index, line in enumerate(lines) if _TOOL.fullmatch(line))
    except (ValueError, StopIteration):
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.POST_SEQUENCE_INVALID))
        return tuple(findings)
    if initialization >= first_tool:
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.POST_SEQUENCE_INVALID))
    if any(_UNSUPPORTED_OFFSET.search(line) for line in lines):
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.WORK_OFFSET_UNSUPPORTED))
    if any(_CANNED.search(line) for line in lines):
        findings.append(
            _finding(FindingSeverity.ERROR, FindingCode.CANNED_CYCLE_SUBSTITUTION_UNQUALIFIED)
        )
    if any(_TAPPING_CYCLE.search(line) for line in lines):
        findings.append(
            _finding(
                FindingSeverity.ERROR,
                FindingCode.TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED,
            )
        )
    spindle_on = False
    coolant_on = False
    cutter_comp = False
    active_tool: int | None = None
    active_h: int | None = None
    for line in lines:
        tool_match = _TOOL.fullmatch(line)
        if tool_match:
            if spindle_on or coolant_on or cutter_comp:
                findings.append(_finding(FindingSeverity.ERROR, FindingCode.POST_SEQUENCE_INVALID))
            active_tool = int(tool_match.group(1))
            active_h = None
            continue
        length_match = _LENGTH.fullmatch(line)
        if length_match:
            active_h = int(length_match.group(1))
            continue
        if _CUTTER.fullmatch(line):
            cutter_comp = True
            continue
        if line == "G40":
            cutter_comp = False
            continue
        if line.startswith(("M03S", "M04S")):
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
        if line.startswith(("G00", "G01", "G02", "G03")) and not line.startswith("G00G"):
            if active_tool is None or active_h is None:
                findings.append(_finding(FindingSeverity.ERROR, FindingCode.POST_SEQUENCE_INVALID))
    if spindle_on or coolant_on or cutter_comp:
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.POST_SEQUENCE_INVALID))
    required_footer = ("M09", "M05", "G91G28G0Z0", "G28Y0.", "M30", "%")
    if lines[-6:] != required_footer:
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.POST_SEQUENCE_INVALID))
    if not any(item.severity is FindingSeverity.ERROR for item in findings):
        findings.append(_finding(FindingSeverity.INFO, FindingCode.POST_SEQUENCE_VALID))
    findings.extend(
        (
            _finding(FindingSeverity.WARNING, FindingCode.UNVERIFIED_CONTROLLER_SEMANTICS),
            _finding(FindingSeverity.WARNING, FindingCode.PHYSICAL_SAFE_POSITION_UNVERIFIED),
        )
    )
    return tuple(findings)


def _validate_spans(
    result: ProgramAssemblyResult,
    contract: MachineQualificationContract,
) -> tuple[QualificationFinding, ...]:
    findings: list[QualificationFinding] = []
    spans = _program_extents(result)
    limits = (
        _contract_number(contract, "axes.x_travel_span"),
        _contract_number(contract, "axes.y_travel_span"),
        _contract_number(contract, "axes.z_travel_span"),
    )
    codes = (
        FindingCode.X_SPAN_EXCEEDED,
        FindingCode.Y_SPAN_EXCEEDED,
        FindingCode.Z_SPAN_EXCEEDED,
    )
    for axis, required, limit, code in zip("XYZ", spans, limits, codes, strict=True):
        if not math.isfinite(required) or required > limit + 1e-9:
            findings.append(
                _finding(
                    FindingSeverity.ERROR,
                    code,
                    ("axis", axis),
                    ("limit_mm", f"{limit:.6f}"),
                    ("required_span_mm", f"{required:.6f}"),
                )
            )
    findings.append(
        _finding(
            FindingSeverity.WARNING,
            FindingCode.PHYSICAL_TRAVEL_NOT_FULLY_VERIFIED,
            ("required_spans_mm", ",".join(f"{value:.6f}" for value in spans)),
        )
    )
    return tuple(findings)


def _validate_stock(
    stock: StockEnvelope | None,
    contract: MachineQualificationContract,
) -> tuple[QualificationFinding, ...]:
    if stock is None:
        return (_finding(FindingSeverity.ERROR, FindingCode.STOCK_ENVELOPE_MISSING),)
    table_x = _contract_number(contract, "table.width")
    table_y = _contract_number(contract, "table.depth")
    if stock.x_span_mm > table_x or stock.y_span_mm > table_y:
        return (
            _finding(
                FindingSeverity.ERROR,
                FindingCode.STOCK_EXCEEDS_TABLE,
                ("stock_mm", f"{stock.x_span_mm:.6f}x{stock.y_span_mm:.6f}"),
                ("table_mm", f"{table_x:.6f}x{table_y:.6f}"),
            ),
        )
    return (
        _finding(
            FindingSeverity.WARNING,
            FindingCode.TABLE_PLACEMENT_NOT_PHYSICALLY_VERIFIED,
            ("stock_mm", f"{stock.x_span_mm:.6f}x{stock.y_span_mm:.6f}"),
        ),
    )


def _validate_process_limits(
    result: ProgramAssemblyResult,
    contract: MachineQualificationContract,
) -> tuple[QualificationFinding, ...]:
    findings: list[QualificationFinding] = []
    maximum_rpm = _contract_number(contract, "spindle.maximum_rpm")
    maximum_feed = _contract_number(contract, "spindle.feed_envelope")
    maximum_rapid = _contract_number(contract, "spindle.rapid_envelope")
    for section in result.plan.sections:
        for record in section.program_ir.records:
            if isinstance(record, SpindleStartRecord):
                rpm = float(record.speed.value)
                if not math.isfinite(rpm) or rpm < 0.0:
                    findings.append(_finding(FindingSeverity.ERROR, FindingCode.SPINDLE_INVALID))
                elif rpm > maximum_rpm:
                    findings.append(
                        _finding(
                            FindingSeverity.ERROR,
                            FindingCode.SPINDLE_LIMIT_EXCEEDED,
                            ("maximum_rpm", f"{maximum_rpm:.6f}"),
                            ("rpm", f"{rpm:.6f}"),
                        )
                    )
            if isinstance(record, (LinearMotionRecord, ArcMotionRecord, FeedValueRecord)):
                feed = float(record.feed_rate.value)
                if not math.isfinite(feed) or feed < 0.0:
                    findings.append(_finding(FindingSeverity.ERROR, FindingCode.FEED_INVALID))
                elif feed > maximum_feed:
                    findings.append(
                        _finding(
                            FindingSeverity.ERROR,
                            FindingCode.FEED_LIMIT_EXCEEDED,
                            ("feed_mm_min", f"{feed:.6f}"),
                            ("maximum_mm_min", f"{maximum_feed:.6f}"),
                        )
                    )
            elif isinstance(record, RapidMotionRecord) and record.rapid_rate is not None:
                rapid = float(record.rapid_rate.value)
                if not math.isfinite(rapid) or rapid < 0.0:
                    findings.append(_finding(FindingSeverity.ERROR, FindingCode.FEED_INVALID))
                elif rapid > maximum_rapid:
                    findings.append(
                        _finding(
                            FindingSeverity.ERROR,
                            FindingCode.FEED_LIMIT_EXCEEDED,
                            ("maximum_rapid_mm_min", f"{maximum_rapid:.6f}"),
                            ("rapid_mm_min", f"{rapid:.6f}"),
                        )
                    )
    return tuple(findings)


def _validate_tools(
    result: ProgramAssemblyResult,
    tools: tuple[ToolQualificationInput, ...],
    contract: MachineQualificationContract,
) -> tuple[QualificationFinding, ...]:
    findings: list[QualificationFinding] = []
    by_fingerprint = {item.tool_assembly_fingerprint: item for item in tools}
    if len(by_fingerprint) != len(tools):
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.TOOL_NUMBER_CONFLICT))
    stations: dict[int, object] = {}
    h_offsets: dict[int, object] = {}
    d_offsets: dict[int, object] = {}
    maximum_diameter = _contract_number(contract, "tool_system.maximum_tool_diameter")
    maximum_length = _contract_number(contract, "tool_system.maximum_tool_length")
    required_taper = str(contract.leaf("tool_system.taper").value).upper()
    for section in result.plan.sections:
        fingerprint = section.tool_assembly_fingerprint
        tool = by_fingerprint.get(fingerprint)
        binding = section.tool_binding
        if tool is None:
            findings.append(
                _finding(
                    FindingSeverity.ERROR,
                    FindingCode.TOOL_INPUT_MISSING,
                    ("tool_fingerprint", fingerprint.digest),
                )
            )
            continue
        if tool.tool_number != binding.tool_station:
            findings.append(_finding(FindingSeverity.ERROR, FindingCode.TOOL_FINGERPRINT_STALE))
        if tool.h_offset is None or tool.h_offset != binding.length_offset:
            findings.append(_finding(FindingSeverity.ERROR, FindingCode.H_MAPPING_MISSING))
        context = section.program_ir.production_context
        needs_d = bool(context and context.use_legacy_cutter_compensation)
        if needs_d and (tool.d_offset is None or tool.d_offset != binding.diameter_offset):
            findings.append(_finding(FindingSeverity.ERROR, FindingCode.D_MAPPING_MISSING))
        for mapping, number, code in (
            (stations, tool.tool_number, FindingCode.TOOL_NUMBER_CONFLICT),
            (h_offsets, tool.h_offset, FindingCode.H_MAPPING_CONFLICT),
            (d_offsets, tool.d_offset, FindingCode.D_MAPPING_CONFLICT),
        ):
            if number is None:
                continue
            previous = mapping.get(number)
            if previous is not None and previous != fingerprint:
                findings.append(_finding(FindingSeverity.ERROR, code, ("number", str(number))))
            mapping[number] = fingerprint
        if tool.diameter_mm is not None and tool.diameter_mm > maximum_diameter:
            findings.append(_finding(FindingSeverity.ERROR, FindingCode.TOOL_DIAMETER_EXCEEDED))
        if tool.overall_length_mm is not None and tool.overall_length_mm > maximum_length:
            findings.append(_finding(FindingSeverity.ERROR, FindingCode.TOOL_LENGTH_EXCEEDED))
        if tool.taper is not None and tool.taper != required_taper:
            findings.append(_finding(FindingSeverity.ERROR, FindingCode.TOOL_TAPER_MISMATCH))
    capacity = int(_contract_number(contract, "tool_system.atc_capacity"))
    if len(by_fingerprint) > capacity:
        findings.append(
            _finding(
                FindingSeverity.ERROR,
                FindingCode.TOOL_CAPACITY_EXCEEDED,
                ("capacity", str(capacity)),
                ("required", str(len(by_fingerprint))),
            )
        )
    tool_error_codes = {
        FindingCode.TOOL_INPUT_MISSING,
        FindingCode.TOOL_FINGERPRINT_STALE,
        FindingCode.TOOL_NUMBER_INVALID,
        FindingCode.TOOL_NUMBER_CONFLICT,
        FindingCode.TOOL_CAPACITY_EXCEEDED,
        FindingCode.H_MAPPING_MISSING,
        FindingCode.H_MAPPING_CONFLICT,
        FindingCode.D_MAPPING_MISSING,
        FindingCode.D_MAPPING_CONFLICT,
    }
    if not any(item.code in tool_error_codes for item in findings):
        findings.extend(
            (
                _finding(FindingSeverity.INFO, FindingCode.TOOL_NUMBER_MAPPING_VALIDATED),
                _finding(FindingSeverity.INFO, FindingCode.H_MAPPING_STATICALLY_VALIDATED),
                _finding(FindingSeverity.INFO, FindingCode.D_MAPPING_STATICALLY_VALIDATED),
            )
        )
    findings.append(_finding(FindingSeverity.WARNING, FindingCode.OFFSET_NAMESPACE_UNVERIFIED))
    return tuple(findings)


def _physical_level(
    base_level: QualificationLevel,
    evidence: PhysicalEvidence | None,
    *,
    nc_sha256: str,
    contract: MachineQualificationContract,
) -> tuple[QualificationLevel, tuple[QualificationFinding, ...]]:
    if base_level is QualificationLevel.UNQUALIFIED or evidence is None:
        return base_level, ()
    if evidence.nc_sha256 != nc_sha256 or evidence.contract_fingerprint != contract.fingerprint:
        return (
            base_level,
            (_finding(FindingSeverity.WARNING, FindingCode.PHYSICAL_EVIDENCE_STALE),),
        )
    if not evidence.authority or not evidence.record_reference:
        if any(
            value is EvidenceResult.PASS
            for value in (
                evidence.dry_run, evidence.single_block,
                evidence.air_cut, evidence.machine_acceptance,
            )
        ):
            return (
                base_level,
                (_finding(FindingSeverity.WARNING, FindingCode.PHYSICAL_EVIDENCE_INCOMPLETE),),
            )
        return base_level, ()
    if all(
        value is EvidenceResult.PASS
        for value in (evidence.dry_run, evidence.single_block, evidence.air_cut)
    ):
        if evidence.machine_acceptance is EvidenceResult.PASS:
            return QualificationLevel.MACHINE_ACCEPTED, ()
        return QualificationLevel.DRY_RUN_QUALIFIED, ()
    return base_level, ()


def qualify_static_nc(value: StaticQualificationInput) -> QualificationReport:
    """Qualify one existing deterministic assembly without generating new NC."""

    result = value.assembly_result
    contract = value.machine_contract
    findings: list[QualificationFinding] = []
    if contract.profile_id != ROBODRILL_ALPHA_D21MIB_PROFILE_ID:
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.PROFILE_MISMATCH))
    actual_sha = hashlib.sha256(result.canonical_text.encode("utf-8")).hexdigest()
    if actual_sha != result.output_checksum:
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.NC_CHECKSUM_MISMATCH))
    if result.plan.shared_context.work_offset_code != "G54":
        findings.append(_finding(FindingSeverity.ERROR, FindingCode.WORK_OFFSET_UNSUPPORTED))
    else:
        findings.append(
            _finding(FindingSeverity.WARNING, FindingCode.PHYSICAL_G54_TRANSFORM_UNVERIFIED)
        )
    if any(section.program_ir.strategy_key == "tapping_v1" for section in result.plan.sections):
        findings.append(
            _finding(
                FindingSeverity.ERROR,
                FindingCode.TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED,
            )
        )
    findings.extend(_validate_spans(result, contract))
    findings.extend(_validate_stock(value.stock_envelope, contract))
    findings.extend(_validate_process_limits(result, contract))
    findings.extend(_validate_tools(result, value.tools, contract))
    findings.extend(validate_fanuc_modal_sequence(result.canonical_text))
    findings.extend(
        (
            _finding(FindingSeverity.WARNING, FindingCode.COOLANT_PHYSICAL_STATE_UNVERIFIED),
            _finding(FindingSeverity.WARNING, FindingCode.GOLDEN_SAMPLE_OWNER_APPROVAL_PENDING),
        )
    )
    base_level = (
        QualificationLevel.UNQUALIFIED
        if any(item.severity is FindingSeverity.ERROR for item in findings)
        else QualificationLevel.STATICALLY_VALIDATED
    )
    level, evidence_findings = _physical_level(
        base_level,
        value.physical_evidence,
        nc_sha256=actual_sha,
        contract=contract,
    )
    findings.extend(evidence_findings)
    plan = result.plan
    return QualificationReport(
        project_id=str(result.project_id),
        program_fingerprint=result.result_fingerprint,
        operation_ids=tuple(str(item.operation_id) for item in plan.sections),
        tool_binding_fingerprints=tuple(item.tool_binding.fingerprint for item in plan.sections),
        machine_profile_id=contract.profile_id,
        machine_contract_fingerprint=contract.fingerprint,
        post_profile_id=str(plan.production_profile_id),
        post_profile_version=plan.production_profile_version,
        post_profile_fingerprint=plan.production_profile_fingerprint,
        nc_sha256=actual_sha,
        qualification_level=level,
        findings=tuple(findings),
        physical_evidence=value.physical_evidence,
    )


__all__ = [
    "StaticQualificationInput",
    "qualify_static_nc",
    "validate_fanuc_modal_sequence",
]

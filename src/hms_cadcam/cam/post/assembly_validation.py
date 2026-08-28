"""Fail-closed validation for explicit-order FANUC program assembly."""

from __future__ import annotations

import math
import re

from hms_cadcam.cam.domain.operation import ArtifactStatus, DiagnosticSeverity
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.setup import WorkOffset
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.post.assembly_model import (
    ProgramAssemblyDiagnostic,
    ProgramAssemblyDiagnosticCode,
    ProgramAssemblyOrderingPolicy,
    ProgramAssemblyPlan,
    ProgramAssemblyRequest,
)
from hms_cadcam.cam.post.fanuc_robodrill_21i import (
    ADAPTER_KEY_V2,
    FanucRobodrill21iAdapter,
    has_canonical_robodrill_contract,
)
from hms_cadcam.cam.post.fanuc_validation import validate_fanuc_program
from hms_cadcam.cam.post.lowering import validate_post_source
from hms_cadcam.cam.post.model import CoordinateMode, Plane, PostDiagnosticCode
from hms_cadcam.cam.post.profile import CutterCompensationPolicy
from hms_cadcam.cam.simulation.model import SimulationStatus


_COMMENT = re.compile(r"\([^()\r\n]*\)")
_TOOL = re.compile(r"M06T([1-9]\d{0,3})")
_LENGTH = re.compile(r"G43Z-?(?:\d+(?:\.\d*)?|\.\d+)H([1-9]\d{0,3})")
_CUTTER = re.compile(r"G41D([1-9]\d{0,3})")
_SECTION_MARKER = re.compile(
    r"\(OPERATION=(operation:[0-9a-f-]{36}),SECTION=(\d+)\)"
)
_SUPPORTED = {
    "facing_2_5d",
    "contour_2d",
    "pocket_2_5d",
    "drilling_v1",
    "reaming_v1",
    "boring_v1",
}
_REST_SUPPORTED = {"rest_contour_3axis", "rest_finishing_3axis"}
_DRILLING = {"drilling_v1", "reaming_v1", "boring_v1", "tapping_v1"}


def _diag(
    code: ProgramAssemblyDiagnosticCode,
    key: str,
    *,
    operation_id=None,
    section_id=None,
    section_index: int | None = None,
    record_index: int | None = None,
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR,
    evidence: tuple[tuple[str, str], ...] = (),
) -> ProgramAssemblyDiagnostic:
    return ProgramAssemblyDiagnostic(
        severity,
        code,
        key,
        operation_id,
        section_id,
        section_index,
        record_index,
        evidence,
    )


def validate_assembly_request(
    request: ProgramAssemblyRequest,
) -> tuple[ProgramAssemblyDiagnostic, ...]:
    diagnostics: list[ProgramAssemblyDiagnostic] = []
    operations = request.operations
    if not operations:
        diagnostics.append(_diag(ProgramAssemblyDiagnosticCode.EMPTY, "assembly.empty"))
        return tuple(diagnostics)
    if request.ordering_policy is not ProgramAssemblyOrderingPolicy.EXPLICIT_OPERATION_ORDER:
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.INVALID_ORDER,
                "assembly.ordering_policy_unsupported",
            )
        )
    operation_ids = tuple(item.operation_id for item in operations)
    if len(set(operation_ids)) != len(operation_ids):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.DUPLICATE_OPERATION,
                "assembly.duplicate_operation",
            )
        )
    indices = tuple(item.order_index for item in operations)
    if indices != tuple(range(len(operations))):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.INVALID_ORDER,
                "assembly.explicit_order_not_contiguous",
                evidence=(("received", ",".join(str(item) for item in indices)),),
            )
        )
    profile = request.post_definition.production_profile
    if profile is None or not has_canonical_robodrill_contract(
        request.post_definition
    ):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.PROFILE_MISMATCH,
                "assembly.production_profile_required",
            )
        )
        return _sorted(diagnostics)
    if (
        request.shared_context.unit is not LengthUnit.MM
        or request.shared_context.coordinate_mode is not CoordinateMode.ABSOLUTE
        or request.shared_context.plane is not Plane.XY
    ):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.UNIT_MISMATCH,
                "assembly.program_mode_unsupported",
            )
        )
    if request.shared_context.work_offset_code != "G54":
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.WORK_OFFSET_MISMATCH,
                "assembly.g54_required",
            )
        )

    station_semantics: dict[int, ContentFingerprint] = {}
    length_semantics: dict[int, ContentFingerprint] = {}
    diameter_semantics: dict[int, ContentFingerprint] = {}
    for item in operations:
        source = item.source_snapshot
        operation = source.operation
        artifact = source.artifact
        index = item.order_index
        identity = {"operation_id": item.operation_id, "section_index": index}
        if source.project_id != request.project_id:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.INVALID_REQUEST,
                    "assembly.project_mismatch",
                    **identity,
                )
            )
        if operation.operation_id != item.operation_id:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.OPERATION_MISSING,
                    "assembly.operation_snapshot_mismatch",
                    **identity,
                )
            )
        if not operation.enabled:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.OPERATION_DISABLED,
                    "assembly.operation_disabled",
                    **identity,
                )
            )
        if operation.artifact_state.status is not ArtifactStatus.VALID:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.OPERATION_INVALID,
                    "assembly.operation_invalid",
                    **identity,
                )
            )
        if operation.setup_id != request.setup_id or source.setup.setup_id != request.setup_id:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.SETUP_MISMATCH,
                    "assembly.setup_mismatch",
                    **identity,
                )
            )
        if not source.setup.enabled:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.OPERATION_INVALID,
                    "assembly.setup_disabled",
                    **identity,
                )
            )
        work_offset = source.setup.work_offset
        if not isinstance(work_offset, WorkOffset) or (
            work_offset.name.upper(), work_offset.numeric_slot
        ) != ("PRIMARY", 1):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.WORK_OFFSET_MISMATCH,
                    "assembly.g54_mapping_required",
                    **identity,
                )
            )
        if (
            item.artifact_id != artifact.artifact_id
            or item.artifact_fingerprint != artifact.artifact_fingerprint
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.ARTIFACT_STALE,
                    "assembly.artifact_snapshot_mismatch",
                    **identity,
                )
            )
        if artifact.artifact_fingerprint is None:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.ARTIFACT_MISSING,
                    "assembly.artifact_missing",
                    **identity,
                )
            )
        if artifact.unit is not LengthUnit.MM:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.UNIT_MISMATCH,
                    "assembly.mm_required",
                    **identity,
                )
            )
        machine = source.machine
        if (
            machine is None
            or machine.machine_id != request.machine_id
            or machine.content_fingerprint != request.machine_fingerprint
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.MACHINE_MISMATCH,
                    "assembly.machine_mismatch",
                    **identity,
                )
            )
        if (
            item.tool_assembly_fingerprint != source.assembly.content_fingerprint
            or item.tool_binding.tool_assembly_fingerprint
            != item.tool_assembly_fingerprint
            or item.program_context.tool_binding != item.tool_binding
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.TOOL_BINDING_MISSING,
                    "assembly.tool_binding_stale",
                    **identity,
                )
            )
        if item.program_context.file_name.casefold() != request.shared_context.file_name.casefold():
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.PROFILE_MISMATCH,
                    "assembly.section_filename_mismatch",
                    **identity,
                )
            )
        if (
            item.program_context.safe_z.unit is not LengthUnit.MM
            or not math.isfinite(item.program_context.safe_z.value)
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.SAFE_Z_INVALID,
                    "assembly.safe_z_invalid",
                    **identity,
                )
            )
        if operation.strategy_key == "tapping_v1":
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.UNSUPPORTED_TAPPING,
                    "assembly.unsupported_tapping",
                    **identity,
                )
            )
        elif operation.strategy_key not in _SUPPORTED and not (
            request.post_definition.adapter_key == ADAPTER_KEY_V2
            and operation.strategy_key in _REST_SUPPORTED
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.UNSUPPORTED_OPERATION,
                    "assembly.unsupported_operation",
                    **identity,
                )
            )
        legacy = item.cutter_compensation_policy is CutterCompensationPolicy.LEGACY_WORKNC_LEFT
        if (
            legacy != item.program_context.use_legacy_cutter_compensation
            or (legacy and operation.strategy_key != "contour_2d")
            or (legacy and item.tool_binding.diameter_offset is None)
            or (operation.strategy_key in _DRILLING and legacy)
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.COMPENSATION_INVALID,
                    "assembly.compensation_invalid",
                    **identity,
                )
            )
        if operation.strategy_key in _REST_SUPPORTED and (
            item.cutter_compensation_policy is not CutterCompensationPolicy.DISABLED
            or item.program_context.use_legacy_cutter_compensation
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.COMPENSATION_INVALID,
                    "assembly.rest_compensation_must_be_disabled",
                    **identity,
                )
            )
        explicit_simulation = item.simulation_result
        if source.simulation_result != explicit_simulation:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.SIMULATION_BLOCKED,
                    "assembly.simulation_snapshot_mismatch",
                    **identity,
                )
            )
        source_diagnostics = validate_post_source(
            source, request.simulation_gate_policy
        )
        for post_diagnostic in source_diagnostics:
            if post_diagnostic.severity is DiagnosticSeverity.WARNING:
                diagnostics.append(
                    _diag(
                        ProgramAssemblyDiagnosticCode.SIMULATION_BLOCKED,
                        "assembly.simulation_warning_allowed",
                        severity=DiagnosticSeverity.WARNING,
                        **identity,
                    )
                )
                continue
            code = _map_post_code(post_diagnostic.code)
            diagnostics.append(
                _diag(
                    code,
                    "assembly.source_preflight_blocked",
                    record_index=post_diagnostic.record_index,
                    evidence=(("post_code", post_diagnostic.code.value),),
                    **identity,
                )
            )
        for mapping, number in (
            (station_semantics, item.tool_binding.tool_station),
            (length_semantics, item.tool_binding.length_offset),
        ):
            previous = mapping.get(number)
            if previous is not None and previous != item.tool_assembly_fingerprint:
                diagnostics.append(
                    _diag(
                        ProgramAssemblyDiagnosticCode.TOOL_BINDING_CONFLICT,
                        "assembly.tool_binding_conflict",
                        evidence=(("address", str(number)),),
                        **identity,
                    )
                )
            mapping[number] = item.tool_assembly_fingerprint
        if item.tool_binding.diameter_offset is not None:
            number = item.tool_binding.diameter_offset
            previous = diameter_semantics.get(number)
            if previous is not None and previous != item.tool_assembly_fingerprint:
                diagnostics.append(
                    _diag(
                        ProgramAssemblyDiagnosticCode.TOOL_BINDING_CONFLICT,
                        "assembly.tool_binding_conflict",
                        evidence=(("address", str(number)),),
                        **identity,
                    )
                )
            diameter_semantics[number] = item.tool_assembly_fingerprint
    return _sorted(diagnostics)


def validate_assembly_plan(
    plan: ProgramAssemblyPlan, definition
) -> tuple[ProgramAssemblyDiagnostic, ...]:
    diagnostics: list[ProgramAssemblyDiagnostic] = []
    profile = definition.production_profile
    if (
        profile is None
        or not has_canonical_robodrill_contract(definition)
        or plan.post_definition_id != definition.definition_id
        or plan.post_definition_fingerprint != definition.fingerprint
        or plan.production_profile_id != profile.profile_id
        or plan.production_profile_version != profile.profile_version
        or plan.production_profile_fingerprint != profile.fingerprint
        or plan.adapter_key != definition.adapter_key
        or plan.adapter_version != definition.adapter_version
    ):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.PROFILE_MISMATCH,
                "assembly.plan_profile_mismatch",
            )
        )
        return tuple(diagnostics)
    for section in plan.sections:
        index = section.order_index
        program = section.program_ir
        if (
            program.project_id != plan.project_id
            or program.setup_id != plan.setup_id
            or program.unit is not plan.shared_context.unit
            or program.coordinate_mode is not plan.shared_context.coordinate_mode
            or program.plane is not plan.shared_context.plane
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.SECTION_INVALID,
                    "assembly.section_compatibility_invalid",
                    operation_id=section.operation_id,
                    section_id=section.section_id,
                    section_index=index,
                )
            )
        for post_diagnostic in validate_fanuc_program(program, definition):
            diagnostics.append(
                _diag(
                    _map_post_code(post_diagnostic.code),
                    "assembly.section_invalid",
                    operation_id=section.operation_id,
                    section_id=section.section_id,
                    section_index=index,
                    record_index=post_diagnostic.record_index,
                    evidence=(("post_code", post_diagnostic.code.value),),
                )
            )
    return _sorted(diagnostics)


def validate_assembly_output(
    text: str,
    plan: ProgramAssemblyPlan,
    definition,
    adapter: FanucRobodrill21iAdapter,
) -> tuple[ProgramAssemblyDiagnostic, ...]:
    diagnostics: list[ProgramAssemblyDiagnostic] = []
    profile = definition.production_profile
    if profile is None or not isinstance(text, str):
        return (
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.output_invalid",
            ),
        )
    try:
        payload = text.encode(profile.encoding)
    except (LookupError, UnicodeEncodeError):
        return (
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.output_encoding_invalid",
            ),
        )
    if payload.startswith(b"\xef\xbb\xbf"):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.output_bom_forbidden",
            )
        )
    if len(payload) > profile.maximum_program_size:
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.program_too_large",
            )
        )
    if not text.endswith("\r\n") or "\n" in text.replace("\r\n", "") or "\r" in text.replace("\r\n", ""):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.crlf_required",
            )
        )
    lines = text.splitlines()
    if (
        lines.count("%") != 2
        or not lines
        or lines[0] != "%"
        or lines[-1] != "%"
    ):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.delimiters_invalid",
            )
        )
    if lines.count("M30") != 1 or len(lines) < 2 or lines[-2] != "M30":
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.m30_invalid",
            )
        )
    if lines.count("(SHL-TECH)") != 1 or lines.count(
        f"(FileName={plan.shared_context.file_name})"
    ) != 1:
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.header_repeated_or_missing",
            )
        )
    if lines.count("G28Y0.") != 1 or len(lines) < 3 or lines[-3:] != [
        "G28Y0.",
        "M30",
        "%",
    ]:
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.footer_invalid",
            )
        )
    markers = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := _SECTION_MARKER.fullmatch(line)) is not None
    ]
    if len(markers) != len(plan.sections):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.section_count_invalid",
            )
        )
    for expected, marker in zip(plan.sections, markers):
        line_index, match = marker
        assert match is not None
        if match.group(1) != str(expected.operation_id) or int(match.group(2)) != expected.order_index:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.SECTION_INVALID,
                    "assembly.section_order_invalid",
                    operation_id=expected.operation_id,
                    section_id=expected.section_id,
                    section_index=expected.order_index,
                )
            )
        end = (
            markers[expected.order_index + 1][0]
            if expected.order_index + 1 < len(markers)
            else len(lines) - 3
        )
        section_start = line_index + 1
        while section_start < end and lines[section_start].startswith("("):
            section_start += 1
        section_lines = lines[section_start:end]
        binding = expected.tool_binding
        if (
            len(section_lines) < 7
            or section_lines[0] != "G91G28G0Z0"
            or section_lines[1] != f"M06T{binding.tool_station}"
            or section_lines[2] != "G90G40G54X0.Y0."
            or _LENGTH.fullmatch(section_lines[3]) is None
            or section_lines[-3:] != ["M09", "M05", "G91G28G0Z0"]
            or "%" in section_lines
            or "M30" in section_lines
            or "G28Y0." in section_lines
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.SECTION_INVALID,
                    "assembly.section_boundary_invalid",
                    operation_id=expected.operation_id,
                    section_id=expected.section_id,
                    section_index=expected.order_index,
                )
            )
        cutter_on = [line for line in section_lines if _CUTTER.fullmatch(line)]
        cutter_off = [line for line in section_lines if line == "G40"]
        legacy = expected.program_ir.production_context.use_legacy_cutter_compensation
        if (legacy and (len(cutter_on) != 1 or len(cutter_off) != 1)) or (
            not legacy and (cutter_on or cutter_off)
        ):
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.COMPENSATION_INVALID,
                    "assembly.section_compensation_unbalanced",
                    operation_id=expected.operation_id,
                    section_id=expected.section_id,
                    section_index=expected.order_index,
                )
            )
    if len([line for line in lines if _TOOL.fullmatch(line)]) != len(plan.sections):
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.tool_change_count_invalid",
            )
        )
    for line in lines:
        if len(line) > profile.maximum_line_length:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                    "assembly.line_too_long",
                )
            )
        if line.startswith("(") and _COMMENT.fullmatch(line) is None:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                    "assembly.comment_invalid",
                )
            )
    try:
        expected_text = adapter.format_assembly(plan, definition)
    except ValueError:
        diagnostics.append(
            _diag(
                ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                "assembly.expected_output_unavailable",
            )
        )
    else:
        if text != expected_text:
            diagnostics.append(
                _diag(
                    ProgramAssemblyDiagnosticCode.OUTPUT_INVALID,
                    "assembly.output_semantic_mismatch",
                )
            )
    return _sorted(diagnostics)


def _map_post_code(code: PostDiagnosticCode) -> ProgramAssemblyDiagnosticCode:
    if code in {PostDiagnosticCode.SIMULATION_FAILED, PostDiagnosticCode.SIMULATION_MISSING, PostDiagnosticCode.SIMULATION_STALE}:
        return ProgramAssemblyDiagnosticCode.SIMULATION_BLOCKED
    if code in {PostDiagnosticCode.SOURCE_MISSING}:
        return ProgramAssemblyDiagnosticCode.ARTIFACT_MISSING
    if code in {PostDiagnosticCode.SOURCE_STALE, PostDiagnosticCode.MIXED_PROVENANCE}:
        return ProgramAssemblyDiagnosticCode.ARTIFACT_STALE
    if code is PostDiagnosticCode.SETUP_INVALID:
        return ProgramAssemblyDiagnosticCode.SETUP_MISMATCH
    if code is PostDiagnosticCode.MACHINE_INCOMPATIBLE:
        return ProgramAssemblyDiagnosticCode.MACHINE_MISMATCH
    if code is PostDiagnosticCode.UNIT_MISMATCH:
        return ProgramAssemblyDiagnosticCode.UNIT_MISMATCH
    if code in {PostDiagnosticCode.TOOL_MISSING, PostDiagnosticCode.TOOL_STALE}:
        return ProgramAssemblyDiagnosticCode.TOOL_BINDING_MISSING
    if code is PostDiagnosticCode.UNSUPPORTED_CYCLE:
        return ProgramAssemblyDiagnosticCode.UNSUPPORTED_OPERATION
    if code is PostDiagnosticCode.RAPID_UNSAFE:
        return ProgramAssemblyDiagnosticCode.SAFE_Z_INVALID
    return ProgramAssemblyDiagnosticCode.SECTION_INVALID


def _sorted(
    diagnostics: list[ProgramAssemblyDiagnostic],
) -> tuple[ProgramAssemblyDiagnostic, ...]:
    return tuple(
        sorted(
            set(diagnostics),
            key=lambda item: (
                item.severity.value,
                item.code.value,
                item.section_index if item.section_index is not None else -1,
                str(item.operation_id) if item.operation_id is not None else "",
                item.record_index if item.record_index is not None else -1,
                item.message_key,
                item.evidence,
            ),
        )
    )

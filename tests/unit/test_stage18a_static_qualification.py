"""Static machine-envelope, Tool, modal, and qualification-level tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import (
    CamValidationError,
    ContentFingerprint,
    FeedRate,
    FeedUnit,
    Point3,
    SpindleSpeed,
)
from hms_cadcam.cam.post.model import LinearMotionRecord, SpindleStartRecord
from hms_cadcam.cam.post import (
    CutterCompensationPolicy,
    ProgramAssemblyService,
)
from hms_cadcam.cam.qualification import (
    EvidenceResult,
    FindingCode,
    FindingSeverity,
    PhysicalEvidence,
    QualificationLevel,
    StaticQualificationInput,
    StockEnvelope,
    ToolQualificationInput,
    qualify_static_nc,
    robodrill_alpha_d21mib_contract,
)
from hms_cadcam.cam.qualification.codec import report_from_dict
from tests.unit._stage18a_qualification_fixtures import (
    assembly_result,
    mutate_first_program,
    mutate_first_record,
    mutate_text,
    qualification_input,
    tool_inputs,
)
from tests.unit._fanuc_fixtures import fixture_context
from tests.unit.test_fanuc_robodrill_21i_runtime import _runtime_source
from tests.unit.test_program_assembly import _request, _source_variant


def _codes(report):
    return {item.code for item in report.findings}


def test_bounded_engineering_program_reaches_level1_but_never_machine_ready():
    report = qualify_static_nc(qualification_input())

    assert report.qualification_level is QualificationLevel.STATICALLY_VALIDATED
    assert not report.machine_ready
    assert not report.has_errors
    assert FindingCode.PHYSICAL_TRAVEL_NOT_FULLY_VERIFIED in _codes(report)
    assert FindingCode.PHYSICAL_G54_TRANSFORM_UNVERIFIED in _codes(report)
    assert FindingCode.GOLDEN_SAMPLE_OWNER_APPROVAL_PENDING in _codes(report)
    assert FindingCode.TOOL_NUMBER_MAPPING_VALIDATED in _codes(report)
    assert FindingCode.H_MAPPING_STATICALLY_VALIDATED in _codes(report)
    assert FindingCode.D_MAPPING_STATICALLY_VALIDATED in _codes(report)
    assert FindingCode.POST_SEQUENCE_VALID in _codes(report)


@pytest.mark.parametrize(
    ("axis", "limit", "code"),
    (
        ("x", 501.0, FindingCode.X_SPAN_EXCEEDED),
        ("y", 401.0, FindingCode.Y_SPAN_EXCEEDED),
        ("z", 331.0, FindingCode.Z_SPAN_EXCEEDED),
    ),
)
def test_axis_span_exceedance_is_hard_failure(axis, limit, code):
    result = assembly_result()

    def change(record):
        point = record.end.position
        values = {"x": point.x, "y": point.y, "z": point.z}
        values[axis] = limit
        return replace(
            record,
            end=replace(record.end, position=Point3(values["x"], values["y"], values["z"], point.unit)),
        )

    changed = mutate_first_record(result, lambda value: isinstance(value, LinearMotionRecord), change)
    report = qualify_static_nc(qualification_input(changed))

    assert report.qualification_level is QualificationLevel.UNQUALIFIED
    assert code in _codes(report)


def test_stock_larger_than_table_is_rejected_and_missing_stock_is_unqualified():
    oversized = qualify_static_nc(
        qualification_input(stock=StockEnvelope(651.0, 100.0, 50.0))
    )
    missing = qualify_static_nc(qualification_input(stock=None))

    assert FindingCode.STOCK_EXCEEDS_TABLE in _codes(oversized)
    assert FindingCode.STOCK_ENVELOPE_MISSING in _codes(missing)
    assert oversized.has_errors and missing.has_errors


def test_spindle_and_feed_envelopes_fail_closed():
    base = assembly_result()
    spindle = mutate_first_record(
        base,
        lambda value: isinstance(value, SpindleStartRecord),
        lambda value: replace(value, speed=SpindleSpeed(24001.0)),
    )
    feed = mutate_first_record(
        base,
        lambda value: isinstance(value, LinearMotionRecord),
        lambda value: replace(
            value,
            feed_rate=FeedRate(30001.0, FeedUnit.MM_PER_MINUTE),
        ),
    )

    assert FindingCode.SPINDLE_LIMIT_EXCEEDED in _codes(
        qualify_static_nc(qualification_input(spindle))
    )
    assert FindingCode.FEED_LIMIT_EXCEEDED in _codes(
        qualify_static_nc(qualification_input(feed))
    )


@pytest.mark.parametrize(
    ("diameter", "length", "taper", "code"),
    (
        (80.01, 100.0, "BT30", FindingCode.TOOL_DIAMETER_EXCEEDED),
        (10.0, 250.01, "BT30", FindingCode.TOOL_LENGTH_EXCEEDED),
        (10.0, 100.0, "HSK63", FindingCode.TOOL_TAPER_MISMATCH),
    ),
)
def test_tool_geometry_and_taper_limits_are_enforced(diameter, length, taper, code):
    result = assembly_result()
    tools = tool_inputs(result, diameter_mm=diameter, overall_length_mm=length, taper=taper)
    report = qualify_static_nc(qualification_input(result, tools=tools))

    assert code in _codes(report)
    assert report.has_errors


def test_missing_h_and_conflicting_tool_inputs_fail_closed():
    result = assembly_result()
    original = tool_inputs(result)[0]
    missing_h = replace(original, h_offset=None)
    duplicate = replace(original, tool_number=2)

    missing_report = qualify_static_nc(qualification_input(result, tools=(missing_h,)))
    conflict_report = qualify_static_nc(
        qualification_input(result, tools=(original, duplicate))
    )

    assert FindingCode.H_MAPPING_MISSING in _codes(missing_report)
    assert FindingCode.TOOL_NUMBER_CONFLICT in _codes(conflict_report)


def test_missing_d_mapping_under_active_cutter_compensation_fails_closed():
    source = _source_variant(_runtime_source(), "contour_2d")
    request = _request([source])
    context = fixture_context(source, file_name="ASSEMBLY.fn", cutter=True)
    operation = replace(
        request.operations[0],
        tool_binding=context.tool_binding,
        program_context=context,
        cutter_compensation_policy=CutterCompensationPolicy.LEGACY_WORKNC_LEFT,
    )
    execution = ProgramAssemblyService().assemble(replace(request, operations=(operation,)))
    assert execution.accepted and execution.result is not None
    original = tool_inputs(execution.result)[0]

    report = qualify_static_nc(
        qualification_input(execution.result, tools=(replace(original, d_offset=None),))
    )

    assert FindingCode.D_MAPPING_MISSING in _codes(report)
    assert report.has_errors


def test_more_than_21_assigned_unique_tools_is_rejected_without_guessing_t_range():
    result = assembly_result()
    base = tool_inputs(result)[0]
    tools = tuple(
        replace(
            base,
            tool_assembly_fingerprint=ContentFingerprint.from_payload({"tool": index}),
            tool_number=index + 1,
            h_offset=index + 1,
        )
        for index in range(22)
    ) + (base,)

    report = qualify_static_nc(qualification_input(result, tools=tools))

    assert FindingCode.TOOL_CAPACITY_EXCEEDED in _codes(report)


def test_g55_and_canned_cycle_substitution_are_rejected():
    result = assembly_result()
    g55 = mutate_text(result, result.canonical_text.replace("G90G40G54", "G90G40G55"))
    g81 = mutate_text(result, result.canonical_text.replace("M09\r\n", "G81X1.Y1.Z-1.R1.F100\r\nM09\r\n", 1))

    assert FindingCode.WORK_OFFSET_UNSUPPORTED in _codes(
        qualify_static_nc(qualification_input(g55))
    )
    assert FindingCode.CANNED_CYCLE_SUBSTITUTION_UNQUALIFIED in _codes(
        qualify_static_nc(qualification_input(g81))
    )


def test_tapping_strategy_remains_unqualified_even_if_ir_is_forced():
    result = assembly_result()
    tapping = mutate_first_program(
        result,
        lambda program: replace(program, strategy_key="tapping_v1", program_fingerprint=None),
    )

    report = qualify_static_nc(qualification_input(tapping))

    assert FindingCode.TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED in _codes(report)
    assert report.qualification_level is QualificationLevel.UNQUALIFIED


def test_level2_and_level3_require_exact_external_evidence_and_authority():
    base = qualification_input()
    report = qualify_static_nc(base)
    incomplete = PhysicalEvidence(
        report.nc_sha256,
        base.machine_contract.fingerprint,
        EvidenceResult.PASS,
        EvidenceResult.PASS,
        EvidenceResult.PASS,
    )
    accepted_dry = replace(
        incomplete,
        authority="Owner acceptance authority",
        record_reference="R218-EXTERNAL-DRY-RUN-001",
    )
    accepted_machine = replace(
        accepted_dry,
        machine_acceptance=EvidenceResult.PASS,
    )

    incomplete_report = qualify_static_nc(replace(base, physical_evidence=incomplete))
    dry_report = qualify_static_nc(replace(base, physical_evidence=accepted_dry))
    machine_report = qualify_static_nc(replace(base, physical_evidence=accepted_machine))

    assert incomplete_report.qualification_level is QualificationLevel.STATICALLY_VALIDATED
    assert FindingCode.PHYSICAL_EVIDENCE_INCOMPLETE in _codes(incomplete_report)
    assert dry_report.qualification_level is QualificationLevel.DRY_RUN_QUALIFIED
    assert not dry_report.machine_ready
    assert machine_report.qualification_level is QualificationLevel.MACHINE_ACCEPTED
    assert machine_report.machine_ready


def test_stale_physical_evidence_cannot_promote_and_false_ready_payload_is_rejected():
    base = qualification_input()
    report = qualify_static_nc(base)
    stale = PhysicalEvidence(
        report.nc_sha256,
        ContentFingerprint.from_payload({"other": "machine"}),
        EvidenceResult.PASS,
        EvidenceResult.PASS,
        EvidenceResult.PASS,
        EvidenceResult.PASS,
        "Authority",
        "record-1",
    )
    stale_report = qualify_static_nc(replace(base, physical_evidence=stale))
    payload = report.to_dict()
    payload["machine_ready"] = True

    assert stale_report.qualification_level is QualificationLevel.STATICALLY_VALIDATED
    assert FindingCode.PHYSICAL_EVIDENCE_STALE in _codes(stale_report)
    with pytest.raises(CamValidationError):
        report_from_dict(payload)

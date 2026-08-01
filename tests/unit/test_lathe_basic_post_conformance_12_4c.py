"""Focused Qt-free Stage 12.4C sample conformance tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
from pathlib import Path

import pytest

from hms_cadcam.cam.lathe.lathe_post import (
    BasicFinalSafeTool,
    ExternalSampleDiscoveryStatus,
    LatheBasicFanucPostRendererV1,
    LatheBasicNcService,
    LatheNcConformanceAnalyzerV1,
    LatheNcConformanceCategory,
    LatheNcConformanceStatus,
    discover_external_samples,
    lathe_sample_contract_v1,
    basic_lathe_post_profile,
)
from hms_cadcam.cam.lathe.lathe_post.formatting import format_number, sanitize_comment
from tests.unit._lathe_post_conformance_fixtures import (
    SCENARIO_A_STRATEGIES,
    SCENARIO_B_STRATEGIES,
    SCENARIO_C_STRATEGIES,
    representative_program,
)


EXPECTED_SAMPLES = (
    (
        "SAMPLE_A",
        "260516---CTS26079-M001-24--25X489_9-L2.NC",
        "942741ac0e02aacbd1f9a8a966ed2204b74b9e12b51ffd8d8785473ef10ccf32",
        905,
        60,
    ),
    (
        "SAMPLE_B",
        "260516---CTS26079-M001-40--20X8-L1.NC",
        "805d9d97c247bfb318a1a67c87ada1d3eca9d2d671c40fcb91d314ed9107a92e",
        2779,
        211,
    ),
    (
        "SAMPLE_C",
        "260516---CTS26079-M001-24--25X489_9-L1.NC",
        "cd99df3a8a941e6417b7ef04e02af3e74f1229df3eb6a18bdc6d8811ecb01488",
        989,
        88,
    ),
)


def _render(scenario: str):
    program, mappings, metadata = representative_program(scenario)
    result = LatheBasicFanucPostRendererV1().render(program, mappings, metadata)
    assert result.accepted, result.diagnostics
    assert result.snapshot is not None
    return program, mappings, metadata, result.snapshot


def test_exact_private_sample_contract_is_immutable_and_sanitized() -> None:
    contract = lathe_sample_contract_v1()
    actual = tuple(
        (item.alias, item.filename, item.sha256, item.byte_count, item.line_count)
        for item in contract.signatures
    )
    assert actual == EXPECTED_SAMPLES
    assert all(len(item.filename_sha256) == 64 for item in contract.signatures)
    assert all(item.contains_arc_ik for item in contract.signatures)
    assert contract.no_owner_sample_coverage_strategies == (
        "lathe.od_thread.v1",
        "lathe.id_thread.v1",
    )
    with pytest.raises(FrozenInstanceError):
        contract.schema_version = "changed"  # type: ignore[misc]
    serialized = repr(contract)
    assert "CTS26079" in serialized
    assert "G0 T0101\nM8" not in serialized
    assert "proprietary tool" not in serialized.casefold()


def test_external_sample_directory_is_optional_exact_and_hash_checked(tmp_path: Path) -> None:
    unavailable = discover_external_samples(None)
    assert unavailable.status is ExternalSampleDiscoveryStatus.EXTERNAL_SAMPLE_NOT_AVAILABLE
    assert unavailable.directory is None and unavailable.files == ()
    (tmp_path / "unrelated.NC").write_text("%\nM30\n%\n", encoding="ascii")
    missing = discover_external_samples(tmp_path)
    assert missing.status is ExternalSampleDiscoveryStatus.SAMPLE_HASH_MISMATCH
    assert len(missing.files) == 3
    assert all(item.state == "MISSING" for item in missing.files)
    assert missing.filesystem_scan_performed is False
    exact = tmp_path / EXPECTED_SAMPLES[0][1]
    exact.write_bytes(b"%\r\nM30\r\n%\r\n")
    mismatch = discover_external_samples(tmp_path)
    assert mismatch.status is ExternalSampleDiscoveryStatus.SAMPLE_HASH_MISMATCH
    assert mismatch.files[0].state == "HASH_OR_METADATA_MISMATCH"
    assert mismatch.files[0].newline == "CRLF"
    assert mismatch.files[0].encoding == "ASCII"


def test_exact_status_enum_contains_no_machine_claim() -> None:
    values = tuple(item.value for item in LatheNcConformanceStatus)
    assert values == (
        "CONFORMANT",
        "CONFORMANT_WITH_INTENTIONAL_SAFE_DEVIATIONS",
        "PARTIALLY_CONFORMANT",
        "NONCONFORMANT",
        "NO_SAMPLE_COVERAGE",
        "INVALID_INPUT",
    )
    forbidden = ("MACHINE_VERIFIED", "MACHINE_READY", "CERTIFIED", "SAFE_TO_RUN")
    assert not any(word in " ".join(values) for word in forbidden)


def test_analyzer_classifies_envelope_modal_motion_and_numeric_lines() -> None:
    _, _, _, snapshot = _render("A")
    report = LatheNcConformanceAnalyzerV1().analyze(
        snapshot.text,
        strategy_ids=tuple(item.value for item in SCENARIO_A_STRATEGIES),
    )
    assert report.status is LatheNcConformanceStatus.CONFORMANT_WITH_INTENTIONAL_SAFE_DEVIATIONS
    categories = {category for line in report.line_classifications for category in line.categories}
    assert {
        LatheNcConformanceCategory.PROGRAM_ENVELOPE,
        LatheNcConformanceCategory.COMMENTS,
        LatheNcConformanceCategory.UNITS,
        LatheNcConformanceCategory.TOOL_CALL,
        LatheNcConformanceCategory.SPINDLE,
        LatheNcConformanceCategory.COOLANT,
        LatheNcConformanceCategory.WORK_OFFSET,
        LatheNcConformanceCategory.FEED_MODE,
        LatheNcConformanceCategory.MOTION,
        LatheNcConformanceCategory.REFERENCE_RETURN,
        LatheNcConformanceCategory.OPTIONAL_STOP,
        LatheNcConformanceCategory.PROGRAM_END,
        LatheNcConformanceCategory.NUMERIC_FORMAT,
    } <= categories
    assert not report.mandatory_findings
    assert {item.code for item in report.intentional_safe_deviations} == {
        "INTENTIONAL_SAFE_DEVIATION_WARNING_HEADER",
        "INTENTIONAL_SAFE_DEVIATION_SPINDLE_STOP",
    }


@pytest.mark.parametrize(
    ("text", "status", "code"),
    [
        ("", LatheNcConformanceStatus.INVALID_INPUT, "INVALID_OR_EMPTY_TEXT"),
        ("%\nO0000\nG21\t\nM30\n%\n", LatheNcConformanceStatus.INVALID_INPUT, "UNSUPPORTED_CONTROL_CHARACTER"),
        ("%\nO0000\nG21\n#100=1\nM30\n%\n", LatheNcConformanceStatus.NONCONFORMANT, "RAW_CODE_INJECTION_REJECTED"),
        ("%\nO0000\nG21\nG76 X1 Z-1\nM30\n%\n", LatheNcConformanceStatus.NONCONFORMANT, "G76_FORBIDDEN"),
    ],
)
def test_invalid_unbounded_or_unsafe_text_fails_closed(text: str, status: LatheNcConformanceStatus, code: str) -> None:
    report = LatheNcConformanceAnalyzerV1().analyze(text)
    assert report.status is status
    assert code in {item.code for item in report.findings}


def test_numeric_and_comment_contract_matches_observed_notation() -> None:
    assert format_number(0.315, 3) == ".315"
    assert format_number(0.25, 4) == ".25"
    assert format_number(-0.0, 4) == "0"
    assert format_number(-0.6, 3) == "-.6"
    assert format_number(12.3400, 4) == "12.34"
    assert "e" not in format_number(0.0001, 4).casefold()
    assert sanitize_comment("Mũi (dao)\x01") == "MUI [DAO]"


@pytest.mark.parametrize("scenario", ("A", "B", "C"))
def test_representative_scenarios_are_deterministic_crlf_and_structural(scenario: str) -> None:
    program, mappings, metadata, first = _render(scenario)
    second_result = LatheBasicFanucPostRendererV1().render(program, mappings, metadata)
    assert second_result.snapshot is not None
    second = second_result.snapshot
    assert first.text == second.text
    assert first.sha256 == second.sha256 == hashlib.sha256(first.text.encode("ascii")).hexdigest()
    assert "\n" not in first.text.replace("\r\n", "")
    assert first.text.startswith("%\r\nO0000\r\n")
    assert first.text.endswith("T0303\r\nM30\r\n%\r\n")
    assert "UNVERIFIED OUTPUT" in first.text
    assert "MACHINE VERIFIED" not in first.text
    assert "G76" not in first.text
    assert "G2 " not in first.text and "G3 " not in first.text
    assert program.fingerprint == program.semantic_fingerprint()


def test_all_eleven_strategies_and_operation_boundaries_are_covered() -> None:
    strategies = SCENARIO_A_STRATEGIES + SCENARIO_B_STRATEGIES + SCENARIO_C_STRATEGIES
    assert len(strategies) == len(set(strategies)) == 11
    assert {item.value for item in strategies} == {
        "lathe.face.v1",
        "lathe.od_rough.v1",
        "lathe.od_finish.v1",
        "lathe.id_rough.v1",
        "lathe.id_finish.v1",
        "lathe.od_groove.v1",
        "lathe.id_groove.v1",
        "lathe.part_off.v1",
        "lathe.od_thread.v1",
        "lathe.id_thread.v1",
        "lathe.axial_drill.v1",
    }
    _, _, _, scenario_a = _render("A")
    _, _, _, scenario_b = _render("B")
    assert scenario_a.lines.count("M01") == 1
    assert scenario_b.lines.count("M01") == 6
    for snapshot in (scenario_a, scenario_b):
        assert sum(line.startswith("G99 G1 ") for line in snapshot.lines) == snapshot.lines.count("M8")
        assert all("G99" not in line for line in snapshot.lines[: snapshot.lines.index("G21")])


def test_thread_scenario_is_contract_derived_without_owner_sample_coverage() -> None:
    _, _, _, snapshot = _render("C")
    report = LatheNcConformanceAnalyzerV1().analyze(
        snapshot.text,
        strategy_ids=tuple(item.value for item in SCENARIO_C_STRATEGIES),
    )
    assert report.status is LatheNcConformanceStatus.NO_SAMPLE_COVERAGE
    assert all(state == "CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE" for _, state in report.strategy_coverage)
    assert "G32" in snapshot.text
    assert "SPINDLE PHASE NOT VERIFIED" in snapshot.text
    assert "G76" not in snapshot.text
    assert any(line.startswith(("G99 G1 ", "G99 G32 ")) for line in snapshot.lines)
    assert any(line.startswith("G32 ") for line in snapshot.lines)
    assert {item.code for item in report.unsupported_sample_features} >= {
        "SAMPLE_FEATURE_NOT_REPRESENTABLE_CURRENT_IR_ARC_IK",
        "BASIC_POST_DWELL_SYNTAX_UNDEFINED",
        "CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE",
    }


def test_optional_machine_tokens_default_off_and_require_typed_options() -> None:
    profile = basic_lathe_post_profile()
    assert not profile.optional_setup_m73
    assert not profile.optional_setup_m74
    assert not profile.optional_secondary_work_offset_g55
    assert profile.optional_initial_tool_call is None
    assert not profile.optional_manual_stop_after_initial_tool
    with pytest.raises(ValueError):
        replace(profile, optional_raw_setup_sequence=("M73",))
    typed = replace(
        profile,
        optional_setup_m73=True,
        optional_setup_m74=True,
        optional_secondary_work_offset_g55=True,
        optional_initial_tool_call=BasicFinalSafeTool(3, 3),
        optional_manual_stop_after_initial_tool=True,
    )
    program, mappings, metadata = representative_program("A")
    result = LatheBasicFanucPostRendererV1(typed).render(program, mappings, metadata)
    assert result.snapshot is not None
    assert all(token in result.snapshot.lines for token in ("M73", "M74", "G55", "G0 T0303", "M0"))


def test_behavior_revision_changes_output_identity_without_mutating_program_ir() -> None:
    program, mappings, metadata = representative_program("A")
    original_fingerprint = program.fingerprint
    first = LatheBasicFanucPostRendererV1().render(program, mappings, metadata)
    profile_v2 = replace(
        basic_lathe_post_profile(),
        sample_contract_revision=2,
        renderer_algorithm_version="lathe.basic_fanuc.renderer.v1.2",
    )
    second = LatheBasicFanucPostRendererV1(profile_v2).render(program, mappings, metadata)
    assert first.snapshot is not None and second.snapshot is not None
    assert first.snapshot.sha256 != second.snapshot.sha256
    assert "PROFILE REVISION = 2" in second.snapshot.text
    assert program.fingerprint == original_fingerprint


def test_service_review_is_explicit_read_only_and_language_neutral() -> None:
    program, mappings, metadata = representative_program("A")
    service = LatheBasicNcService(tool_mappings=mappings, metadata=metadata)
    generated = service.generate(program)
    assert generated.snapshot is not None
    assert service.state.conformance_report is None
    before = generated.snapshot.text, generated.snapshot.sha256
    report = service.review_latest()
    assert service.state.conformance_report is report
    assert (service.latest.text, service.latest.sha256) == before  # type: ignore[union-attr]
    assert report.status is LatheNcConformanceStatus.CONFORMANT_WITH_INTENTIONAL_SAFE_DEVIATIONS
    service.clear()
    assert service.review_latest().status is LatheNcConformanceStatus.INVALID_INPUT


def test_analyzer_and_renderer_repeat_deterministically() -> None:
    program, mappings, metadata, snapshot = _render("A")
    analyzer = LatheNcConformanceAnalyzerV1()
    expected_report = analyzer.analyze(
        snapshot.text,
        strategy_ids=tuple(item.value for item in SCENARIO_A_STRATEGIES),
    )
    for _ in range(20):
        rendered = LatheBasicFanucPostRendererV1().render(program, mappings, metadata)
        assert rendered.snapshot is not None
        assert rendered.snapshot.sha256 == snapshot.sha256
        assert analyzer.analyze(
            rendered.snapshot.text,
            strategy_ids=tuple(item.value for item in SCENARIO_A_STRATEGIES),
        ) == expected_report

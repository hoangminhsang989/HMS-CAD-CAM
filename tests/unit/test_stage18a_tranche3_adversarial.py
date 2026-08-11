"""Permanent R223 adversarial and engineering-sample matrix."""

from dataclasses import replace
import json

import pytest

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.domain.errors import CamInvariantError
from hms_cadcam.cam.qualification import (
    DryRunHandoffPackageBuilder,
    HandoffPackageError,
    OfflineFindingSeverity,
    OperatorAcknowledgement,
    ReleaseAssessment,
    ReleaseState,
    analyze_nc_bytes,
    current_sources,
    tranche3_engineering_samples,
)
from tests.unit._stage18a_tranche3_fixtures import BASE_INPUT, BASE_REPORT, release_context


def test_exact_ten_engineering_samples_are_frozen_and_nonphysical():
    samples = tranche3_engineering_samples()
    assert tuple(item.sample_id for item in samples) == (
        "clean_multi_operation_handoff", "stale_nc_revision", "stale_setup", "changed_tool",
        "warning_only_physical_unknowns", "hard_blocker", "tapping_blocked",
        "canned_cycle_blocked", "operator_rejected", "package_tamper",
    )
    assert all(item.authority == "ENGINEERING_REGRESSION_SAMPLE" for item in samples)


@pytest.mark.parametrize(
    ("payload", "code"),
    (
        (b"%\nG55\nM30\n%", "UNSUPPORTED_WORK_OFFSET"),
        (b"%\nG54\nG90G81\nM30\n%", "UNSUPPORTED_CANNED_CYCLE_TOKEN"),
        (b"%\nG54\nG84Z-1.\nM30\n%", "TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED"),
        (b"%\nG54\nQUNRESOLVED\nM30\n%", "UNRESOLVED_BLOCK_TOKEN"),
    ),
)
def test_negative_nc_paths_are_blockers(payload, code):
    findings = analyze_nc_bytes(payload).findings
    assert any(item.code == code and item.severity is OfflineFindingSeverity.BLOCKER for item in findings)


def test_filename_replacement_cannot_preserve_release_identity():
    service, payload, setup, readiness, session, candidate, review, ack, _ = release_context()
    replacement = payload.replace(b"G01", b"G1 ", 1)
    assessment = service.assess_release(
        session=session, candidate=candidate, level1_report=BASE_REPORT,
        physical_readiness=readiness, review=review, acknowledgement=ack,
        current=current_sources(replacement, setup, BASE_INPUT.machine_contract),
    )
    assert "NC_HASH_MISMATCH" in assessment.blocker_codes


def test_holder_change_is_a_tool_set_change_even_when_tool_number_is_same():
    *_prefix, candidate, _review, _ack, _assessment = release_context()[4:]
    reasons = __import__(
        "hms_cadcam.cam.qualification", fromlist=["package_stale_reasons"]
    ).package_stale_reasons(
        candidate, nc_sha256=candidate.nc_sha256,
        setup_fingerprint=candidate.setup_fingerprint,
        tool_set_fingerprint=ContentFingerprint.from_payload({"holder": "changed"}),
        machine_profile_fingerprint=candidate.machine_profile_fingerprint,
        post_fingerprint=candidate.post_fingerprint,
    )
    assert reasons == ("TOOL_SET_CHANGED",)


def test_release_state_and_physical_flags_are_derived_not_mutable():
    with pytest.raises(CamInvariantError):
        ReleaseAssessment(
            ReleaseState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF,
            ("MANUAL_BLOCKER",), (),
        )
    with pytest.raises(CamInvariantError):
        ReleaseAssessment(ReleaseState.DRAFT, (), (), level3_achieved=True)


def test_manifest_edit_is_detected(tmp_path):
    _service, payload, setup, _ready, session, candidate, review, ack, assessment = release_context()
    root, _digest = DryRunHandoffPackageBuilder().build(
        tmp_path / "package", project_name="P", program_name="PROGRAM",
        nc_filename="PROGRAM.fn", nc_bytes=payload, contract=BASE_INPUT.machine_contract,
        setup=setup, session=session, candidate=candidate, review=review,
        acknowledgement=ack, assessment=assessment,
    )
    manifest_path = root / "package-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "DRAFT"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(HandoffPackageError):
        DryRunHandoffPackageBuilder().validate(root)


def test_acknowledgement_text_tamper_is_rejected():
    *_prefix, candidate, _review, _ack, _assessment = release_context()[4:]
    with pytest.raises(CamInvariantError):
        OperatorAcknowledgement(
            candidate.candidate_fingerprint, "operator", "2026-08-11T18:10:00+07:00",
            OperatorAcknowledgement.REQUIRED_SOFTWARE_STATEMENT,
            "Export means MACHINE READY",
        )

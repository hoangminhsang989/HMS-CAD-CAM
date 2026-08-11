"""R223 revision locking, structured comparison, and release-gate tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.domain.errors import CamInvariantError
from hms_cadcam.cam.qualification import (
    CurrentReleaseSources,
    OperatorAcknowledgement,
    OperatorReview,
    OperatorReviewResult,
    ReleaseState,
    compare_releases,
    current_sources,
)
from tests.unit._stage18a_tranche3_fixtures import BASE_INPUT, BASE_REPORT, release_context


def test_release_gate_reaches_handoff_but_never_level2_or_machine_ready():
    *_, assessment = release_context()

    assert assessment.state is ReleaseState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF
    assert not assessment.level2_achieved
    assert not assessment.level3_achieved
    assert not assessment.machine_ready


def test_every_current_source_drift_blocks_release():
    service, payload, setup, readiness, session, candidate, review, ack, _assessment = release_context()
    current = current_sources(payload, setup, BASE_INPUT.machine_contract)
    changes = {
        "nc_sha256": "0" * 64,
        "machine_profile_fingerprint": ContentFingerprint.from_payload({"changed": "machine"}),
        "setup_fingerprint": ContentFingerprint.from_payload({"changed": "setup"}),
        "tool_set_fingerprint": ContentFingerprint.from_payload({"changed": "tool"}),
        "post_fingerprint": ContentFingerprint.from_payload({"changed": "post"}),
        "qualification_contract_version": current.qualification_contract_version + 1,
    }
    expected = {
        "nc_sha256": "NC_HASH_MISMATCH",
        "machine_profile_fingerprint": "STALE_MACHINE_FINGERPRINT",
        "setup_fingerprint": "STALE_SETUP",
        "tool_set_fingerprint": "STALE_TOOL_FINGERPRINT",
        "post_fingerprint": "STALE_POST_FINGERPRINT",
        "qualification_contract_version": "STALE_QUALIFICATION_CONTRACT",
    }
    for field, value in changes.items():
        assessment = service.assess_release(
            session=session, candidate=candidate, level1_report=BASE_REPORT,
            current_nc_bytes=payload, machine_contract=BASE_INPUT.machine_contract,
            setup=setup,
            physical_readiness=readiness, review=review, acknowledgement=ack,
            current=replace(current, **{field: value}),
        )
        assert assessment.state is ReleaseState.BLOCKED
        assert expected[field] in assessment.blocker_codes


def test_rejected_or_detached_operator_records_block_handoff():
    service, payload, setup, readiness, session, candidate, review, ack, _ = release_context()
    rejected = replace(review, result=OperatorReviewResult.REJECT)
    result = service.assess_release(
        session=session, candidate=candidate, level1_report=BASE_REPORT,
        current_nc_bytes=payload, machine_contract=BASE_INPUT.machine_contract,
        setup=setup,
        physical_readiness=readiness, review=rejected, acknowledgement=ack,
        current=current_sources(payload, setup, BASE_INPUT.machine_contract),
    )
    assert "OPERATOR_REVIEW_REJECTED" in result.blocker_codes
    with pytest.raises(CamInvariantError):
        OperatorAcknowledgement(
            candidate.candidate_fingerprint, "operator", "2026-08-11T18:03:00+07:00",
            "altered", OperatorAcknowledgement.REQUIRED_MACHINE_READY_STATEMENT,
        )


def test_structured_revision_comparison_reports_semantic_bindings():
    service, payload, setup, _ready, session, candidate, *_rest = release_context()
    changed_session = session.stale("setup changed", "2026-08-11T18:05:00+07:00")
    changed_candidate = replace(
        candidate, release_revision=2,
        setup_fingerprint=ContentFingerprint.from_payload({"setup": "changed"}),
        verification_session_fingerprint=changed_session.session_fingerprint,
        candidate_fingerprint=None,
    )
    comparison = compare_releases(candidate, changed_candidate, session, changed_session)

    assert comparison.setup_changed
    assert comparison.qualification_findings_changed
    assert not comparison.nc_sha_changed


def test_assessment_rejects_manual_level2_or_machine_ready_promotion():
    from hms_cadcam.cam.qualification import ReleaseAssessment

    with pytest.raises(CamInvariantError):
        ReleaseAssessment(ReleaseState.DRAFT, (), (), level2_achieved=True)
    with pytest.raises(CamInvariantError):
        ReleaseAssessment(ReleaseState.DRAFT, (), (), machine_ready=True)

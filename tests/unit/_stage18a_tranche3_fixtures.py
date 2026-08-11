"""Deterministic engineering-only fixtures for Stage18A Tranche3."""

from __future__ import annotations

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.qualification import (
    NCReleaseCandidate,
    OfflineNCVerificationService,
    OperatorAcknowledgement,
    OperatorReview,
    OperatorReviewResult,
    ReleaseAssessment,
    current_sources,
)
from tests.unit._stage18a_tranche2_fixtures import (
    BASE_INPUT,
    BASE_REPORT,
    physical_readiness,
    setup_qualification,
)


NOW = "2026-08-11T18:00:00+07:00"


def release_context():
    setup = setup_qualification()
    readiness = physical_readiness(setup)
    payload = BASE_INPUT.assembly_result.canonical_text.encode("utf-8")
    service = OfflineNCVerificationService()
    session = service.create_session(
        session_id="r223-session-1",
        project_fingerprint=ContentFingerprint.from_payload({"project": "r223"}),
        program_fingerprint=BASE_REPORT.program_fingerprint,
        nc_artifact_id=setup.nc_artifact_id,
        nc_bytes=payload,
        contract=BASE_INPUT.machine_contract,
        setup=setup,
        level1_report=BASE_REPORT,
        physical_readiness=readiness,
        finalized_at=NOW,
    )
    candidate = service.create_release_candidate(
        session, BASE_REPORT, release_revision=1,
        created_at="2026-08-11T18:01:00+07:00",
    )
    review = OperatorReview(
        "operator-r223", "NC reviewer", "2026-08-11T18:02:00+07:00",
        candidate.candidate_fingerprint,
        tuple(sorted(item.finding_id for item in session.findings)),
        OperatorReviewResult.ACCEPT_FOR_EXTERNAL_DRY_RUN,
        "Accepted for controlled external dry-run preparation only.",
    )
    acknowledgement = OperatorAcknowledgement(
        candidate.candidate_fingerprint, "operator-r223", "2026-08-11T18:02:00+07:00",
        OperatorAcknowledgement.REQUIRED_SOFTWARE_STATEMENT,
        OperatorAcknowledgement.REQUIRED_MACHINE_READY_STATEMENT,
    )
    assessment = service.assess_release(
        session=session, candidate=candidate, level1_report=BASE_REPORT,
        physical_readiness=readiness, review=review, acknowledgement=acknowledgement,
        current=current_sources(payload, setup, BASE_INPUT.machine_contract),
    )
    return (
        service, payload, setup, readiness, session, candidate, review,
        acknowledgement, assessment,
    )


__all__ = ["BASE_INPUT", "BASE_REPORT", "NOW", "release_context"]

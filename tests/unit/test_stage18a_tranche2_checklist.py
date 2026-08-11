"""ROBODRILL checklist and explicit golden-sample authority boundaries."""

import pytest

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.qualification import (
    EvidenceState,
    GoldenSampleApproval,
    OwnerAcceptanceRecord,
    ROBODRILL_CHECKLIST_KEYS,
    RobodrillPhysicalChecklist,
    SampleAuthority,
)


def test_robodrill_checklist_is_reusable_and_excludes_tapping():
    checklist = RobodrillPhysicalChecklist.default()
    assert tuple(item.key for item in checklist.items) == ROBODRILL_CHECKLIST_KEYS
    assert "tapping" not in ROBODRILL_CHECKLIST_KEYS
    assert not checklist.complete
    for key in ROBODRILL_CHECKLIST_KEYS:
        checklist = checklist.with_result(key, EvidenceState.PASS, "owner-entered check")
    assert checklist.complete
    assert not checklist.failed
    assert RobodrillPhysicalChecklist.from_dict(checklist.to_dict()) == checklist


def test_checklist_fail_is_visible_and_unknown_key_is_rejected():
    checklist = RobodrillPhysicalChecklist.default().with_result(
        "first_motion", EvidenceState.FAIL, "unexpected retract"
    )
    assert checklist.failed
    assert not checklist.complete
    with pytest.raises(CamValidationError):
        checklist.with_result("tapping", EvidenceState.PASS)


def test_engineering_sample_requires_explicit_owner_acceptance_before_conversion():
    acceptance = OwnerAcceptanceRecord(
        "operator", "verifier", "owner", EvidenceState.PASS,
        "2026-08-11T10:00:00+07:00", "approved for machine sample review",
    )
    approval = GoldenSampleApproval(
        "sample-r221", SampleAuthority.ENGINEERING_REGRESSION_SAMPLE,
        SampleAuthority.OWNER_APPROVED_MACHINE_SAMPLE, acceptance,
        "2026-08-11T10:00:00+07:00",
    )
    assert approval.target_authority is SampleAuthority.OWNER_APPROVED_MACHINE_SAMPLE
    assert approval.fingerprint.digest

    failed = OwnerAcceptanceRecord(
        "operator", "verifier", None, EvidenceState.PASS,
        "2026-08-11T10:00:00+07:00", "owner missing",
    )
    with pytest.raises(CamInvariantError, match="Owner approval"):
        GoldenSampleApproval(
            "sample-r221", SampleAuthority.ENGINEERING_REGRESSION_SAMPLE,
            SampleAuthority.OWNER_APPROVED_MACHINE_SAMPLE, failed,
            "2026-08-11T10:00:00+07:00",
        )

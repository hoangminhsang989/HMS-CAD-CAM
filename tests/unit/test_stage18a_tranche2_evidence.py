"""Stage18A Tranche2 evidence chronology, staleness, and promotion tests."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.qualification import (
    DryRunMode,
    EvidenceAttachment,
    EvidenceAttachmentRole,
    EvidenceState,
    Level2WorkflowState,
    OwnerAcceptanceRecord,
    PhysicalAcceptancePolicy,
    assess_level2_readiness,
    level2_status_vi,
)
from tests.unit._stage18a_qualification_fixtures import qualification_input
from tests.unit._stage18a_tranche2_fixtures import (
    NOW,
    acceptance_policy,
    dry_run_attempt,
    fingerprint,
    level1_report,
    level2_record,
    physical_readiness,
    setup_qualification,
)


def assess(record, readiness=None, **changes):
    setup = record.setup
    values = {
        "level1_report": level1_report(),
        "record": record,
        "physical_readiness": readiness or physical_readiness(setup),
        "current_nc_sha256": setup.nc_sha256,
        "current_machine_profile_fingerprint": setup.machine_profile_fingerprint,
        "current_post_fingerprint": setup.post_fingerprint,
        "current_qualification_contract_fingerprint": qualification_input().machine_contract.fingerprint,
    }
    values.update(changes)
    return assess_level2_readiness(**values)


def test_default_policy_is_fail_closed_and_does_not_guess_shop_procedure():
    policy = PhysicalAcceptancePolicy("undecided", 1)
    record = level2_record(policy=policy)
    result = assess(record)

    assert not policy.confirmed
    assert policy.required_modes == ()
    assert result.workflow_state is Level2WorkflowState.LEVEL1_STATICALLY_VALIDATED
    assert "OWNER_DEFINED_EVIDENCE_POLICY" in result.missing
    assert not result.machine_ready


def test_complete_setup_reaches_ready_for_external_evidence_but_not_level2():
    result = assess(level2_record())

    assert result.workflow_state is Level2WorkflowState.READY_FOR_EXTERNAL_LEVEL2_EVIDENCE
    assert not result.level2_achieved
    assert not result.machine_ready
    assert level2_status_vi(result) == "Sẵn sàng kiểm tra trên máy"


def test_required_external_pass_promotes_only_level2_and_never_level3():
    setup = setup_qualification()
    record = level2_record(setup=setup, attempts=(dry_run_attempt(setup),))
    result = assess(record)

    assert result.workflow_state is Level2WorkflowState.DRY_RUN_QUALIFIED
    assert result.level2_achieved
    assert not result.machine_ready
    assert level2_status_vi(result) == "Dry-run đạt"


@pytest.mark.parametrize(
    ("changed", "reason"),
    (
        ({"current_nc_sha256": "0" * 64}, "NC_SHA_CHANGED"),
        ({"current_machine_profile_fingerprint": fingerprint("wrong-machine")}, "MACHINE_PROFILE_CHANGED"),
        ({"current_post_fingerprint": fingerprint("wrong-post")}, "POST_CHANGED"),
        ({"current_qualification_contract_fingerprint": fingerprint("wrong-contract")}, "QUALIFICATION_CONTRACT_CHANGED"),
    ),
)
def test_current_identity_drift_stales_physical_evidence(changed, reason):
    setup = setup_qualification()
    record = level2_record(setup=setup, attempts=(dry_run_attempt(setup),))
    result = assess(record, **changed)

    assert result.workflow_state is Level2WorkflowState.LEVEL2_EVIDENCE_STALE
    assert reason in result.stale_reasons
    assert not result.machine_ready


def test_changed_g54_stock_fixture_or_holder_changes_setup_fingerprint():
    setup = setup_qualification()
    fingerprints = {
        replace(
            setup,
            work_offset_transform=replace(
                setup.work_offset_transform,
                translation_mm=replace(setup.work_offset_transform.translation_mm, x=101),
            ),
        ).fingerprint,
        replace(setup, stock=replace(setup.stock, dimensions=replace(setup.stock.dimensions, x_mm=101))).fingerprint,
        replace(setup, fixture=replace(setup.fixture, fixture_id="fixture-changed")).fingerprint,
        replace(setup, tools=(replace(setup.tools[0], holder_fingerprint=fingerprint("holder-changed")),)).fingerprint,
    }

    assert setup.fingerprint not in fingerprints
    assert len(fingerprints) == 4


def test_missing_or_changed_attachment_bytes_are_invalid_or_stale(tmp_path):
    evidence_file = tmp_path / "controller.png"
    evidence_file.write_bytes(b"original")
    attachment = EvidenceAttachment.from_local_file(
        evidence_file,
        role=EvidenceAttachmentRole.CONTROLLER_SCREENSHOT,
        captured_at=NOW,
        provenance="external controller screenshot",
    )
    assert attachment.current_state() is EvidenceState.PASS
    evidence_file.write_bytes(b"changed")
    assert attachment.current_state() is EvidenceState.STALE
    evidence_file.unlink()
    assert attachment.current_state() is EvidenceState.INVALID


def test_changed_attachment_prevents_level2_promotion(tmp_path):
    evidence_file = tmp_path / "dry-run.txt"
    evidence_file.write_text("pass", encoding="utf-8")
    attachment = EvidenceAttachment.from_local_file(
        evidence_file,
        role=EvidenceAttachmentRole.NOTES,
        captured_at=NOW,
        provenance="external operator note",
    )
    setup = setup_qualification()
    record = level2_record(
        setup=setup,
        attempts=(dry_run_attempt(setup, attachments=(attachment,)),),
    )
    evidence_file.write_text("mutated", encoding="utf-8")
    result = assess(record)

    assert result.workflow_state is Level2WorkflowState.LEVEL2_EVIDENCE_STALE
    assert any("ATTACHMENT_BYTES_CHANGED" in item for item in result.stale_reasons)


def test_fail_attempt_is_preserved_and_later_pass_requires_remediation():
    setup = setup_qualification()
    failed = dry_run_attempt(setup, result=EvidenceState.FAIL, evidence_id="attempt-fail")
    record = level2_record(setup=setup, attempts=(failed,))
    without_remediation = dry_run_attempt(
        setup, evidence_id="attempt-pass", performed_at="2026-08-11T10:20:00+07:00"
    )
    with pytest.raises(CamInvariantError, match="remediation"):
        record.append_attempt(without_remediation)

    passed = replace(without_remediation, remediation="Corrected fixture clamp position")
    remediated = record.append_attempt(passed)
    result = assess(remediated)

    assert [item.result for item in remediated.attempts] == [EvidenceState.FAIL, EvidenceState.PASS]
    assert result.workflow_state is Level2WorkflowState.DRY_RUN_QUALIFIED


def test_invalid_operator_and_mismatched_acceptance_are_rejected():
    with pytest.raises(CamValidationError, match="Operator"):
        OwnerAcceptanceRecord("", None, None, EvidenceState.PENDING, NOW, "pending")

    setup = setup_qualification()
    attempt = dry_run_attempt(setup)
    with pytest.raises(CamInvariantError, match="agree"):
        replace(
            attempt,
            acceptance=replace(attempt.acceptance, result=EvidenceState.FAIL),
        )


def test_policy_controls_modes_without_assuming_all_are_required():
    policy = acceptance_policy()
    assert policy.required_modes == (DryRunMode.DRY_RUN,)

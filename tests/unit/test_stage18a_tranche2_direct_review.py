"""R222 adversarial review regressions for Level2 and Level3 boundaries."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.qualification import (
    ClearanceState,
    DryRunMode,
    EvidenceAttachment,
    EvidenceAttachmentRole,
    EvidenceState,
    HolderFixtureClearanceEvidence,
    Level2QualificationRecord,
    Level2Readiness,
    Level2WorkflowState,
    Orientation3D,
    PartialCoordinate3D,
    PlacementState,
    assess_level2_readiness,
    clearance_state_for_setup,
)
from tests.unit._stage18a_qualification_fixtures import qualification_input
from tests.unit._stage18a_tranche2_fixtures import (
    NOW,
    acceptance_policy,
    dry_run_attempt,
    level1_report,
    level2_record,
    physical_readiness,
    setup_qualification,
)


def _assess(record: Level2QualificationRecord):
    setup = record.setup
    return assess_level2_readiness(
        level1_report=level1_report(),
        record=record,
        physical_readiness=physical_readiness(setup),
        current_nc_sha256=setup.nc_sha256,
        current_machine_profile_fingerprint=setup.machine_profile_fingerprint,
        current_post_fingerprint=setup.post_fingerprint,
        current_qualification_contract_fingerprint=qualification_input().machine_contract.fingerprint,
        current_controller_identity="FANUC 31i-B",
    )


def test_pass_requires_attachment_and_matching_operator():
    setup = setup_qualification()
    attempt = dry_run_attempt(setup)

    with pytest.raises(CamInvariantError, match="at least one attachment"):
        replace(attempt, attachments=())
    with pytest.raises(CamInvariantError, match="operators must agree"):
        replace(
            attempt,
            acceptance=replace(attempt.acceptance, operator="different-operator"),
        )


def test_duplicate_attachment_role_is_rejected(tmp_path: Path):
    setup = setup_qualification()
    first = dry_run_attempt(setup).attachments[0]
    second_path = tmp_path / "second-note.txt"
    second_path.write_text("second attributable note", encoding="utf-8")
    second = EvidenceAttachment.from_local_file(
        second_path,
        role=EvidenceAttachmentRole.NOTES,
        captured_at=NOW,
        provenance="second external note",
    )

    with pytest.raises(CamInvariantError, match="roles must be unique"):
        replace(dry_run_attempt(setup), attachments=(first, second))


def test_renamed_reference_with_unchanged_bytes_is_stale(tmp_path: Path):
    original = tmp_path / "controller-original.png"
    renamed = tmp_path / "controller-renamed.png"
    original.write_bytes(b"controller screenshot bytes")
    attachment = EvidenceAttachment.from_local_file(
        original,
        role=EvidenceAttachmentRole.CONTROLLER_SCREENSHOT,
        captured_at=NOW,
        provenance="external controller screenshot",
    )
    original.rename(renamed)

    assert replace(attachment, reference=str(renamed)).current_state() is EvidenceState.STALE


def test_wrong_machine_and_policy_drift_stale_pass_evidence():
    setup = setup_qualification()
    policy = acceptance_policy()
    attempt = dry_run_attempt(setup, policy=policy)
    wrong_machine = replace(attempt, machine_identity="foreign-machine-profile")
    wrong_result = _assess(level2_record(setup=setup, policy=policy, attempts=(wrong_machine,)))
    assert wrong_result.workflow_state is Level2WorkflowState.LEVEL2_EVIDENCE_STALE

    wrong_controller = replace(attempt, controller_identity="foreign-controller")
    controller_result = _assess(
        level2_record(setup=setup, policy=policy, attempts=(wrong_controller,))
    )
    assert controller_result.workflow_state is Level2WorkflowState.LEVEL2_EVIDENCE_STALE

    changed_policy = replace(policy, policy_revision=2)
    drift_result = _assess(
        level2_record(setup=setup, policy=changed_policy, attempts=(attempt,))
    )
    assert drift_result.workflow_state is Level2WorkflowState.LEVEL2_EVIDENCE_STALE
    assert any("PHYSICAL_EVIDENCE_STALE" in item for item in drift_result.stale_reasons)


def test_attempt_timestamps_are_strictly_increasing():
    setup = setup_qualification()
    policy = acceptance_policy()
    first = dry_run_attempt(
        setup, result=EvidenceState.PENDING, evidence_id="pending-1", policy=policy
    )
    second = replace(first, evidence_id="pending-2", run_mode=DryRunMode.SINGLE_BLOCK)

    with pytest.raises(CamInvariantError, match="strictly increasing"):
        level2_record(setup=setup, policy=policy, attempts=(first, second))


def test_historical_stale_attempt_does_not_block_later_current_evidence():
    policy = acceptance_policy()
    old_setup = setup_qualification()
    old_attempt = dry_run_attempt(old_setup, evidence_id="old", policy=policy)
    new_base = replace(
        old_setup,
        setup_timestamp="2026-08-11T10:30:00+07:00",
        clearance_evidence=None,
    )
    new_clearance = replace(
        old_setup.clearance_evidence,
        setup_fingerprint=new_base.binding_fingerprint,
        tool_set_fingerprint=new_base.tool_set_fingerprint,
        fixture_fingerprint=new_base.fixture.fingerprint,
    )
    new_setup = replace(new_base, clearance_evidence=new_clearance)
    new_attempt = dry_run_attempt(
        new_setup,
        evidence_id="current",
        performed_at="2026-08-11T10:40:00+07:00",
        policy=policy,
    )
    record = level2_record(
        setup=new_setup,
        policy=policy,
        attempts=(old_attempt, new_attempt),
    )

    result = _assess(record)
    assert result.workflow_state is Level2WorkflowState.DRY_RUN_QUALIFIED
    assert result.stale_reasons == ()
    assert [item.evidence_id for item in record.attempts] == ["old", "current"]


def test_unknown_holder_cannot_reuse_clearance_pass():
    setup = setup_qualification()
    unknown_tool = replace(setup.tools[0], holder_fingerprint=None)
    base = replace(setup, tools=(unknown_tool,), clearance_evidence=None)
    clearance = HolderFixtureClearanceEvidence(
        base.binding_fingerprint,
        base.tool_set_fingerprint,
        base.fixture.fingerprint,
        ClearanceState.HOLDER_FIXTURE_CLEARANCE_STATICALLY_VALIDATED,
        "stale simulation result",
        setup.clearance_evidence.authority,
    )
    changed = replace(base, clearance_evidence=clearance)

    assert clearance_state_for_setup(changed) is ClearanceState.HOLDER_CLEARANCE_UNVERIFIED


def test_stock_footprint_applies_full_xyz_orientation():
    setup = setup_qualification()
    tilted_stock = replace(
        setup.stock,
        dimensions=replace(setup.stock.dimensions, z_mm=200.0),
        origin_machine_mm=PartialCoordinate3D(50.0, 100.0, 0.0),
        orientation_deg=Orientation3D(90.0, 0.0, 0.0),
    )
    changed = replace(setup, stock=tilted_stock, clearance_evidence=None)
    result = physical_readiness(changed)

    assert PlacementState.PLACEMENT_OUTSIDE_TABLE_ENVELOPE in result.placement_states
    assert "PLACEMENT_OUTSIDE_TABLE_ENVELOPE" in result.blockers


def test_level2_and_machine_ready_flags_cannot_be_deserialized_or_mutated():
    record = level2_record()
    readiness = _assess(record)
    payload = readiness.to_dict()
    payload["machine_ready"] = True
    with pytest.raises(CamInvariantError, match="MACHINE_READY"):
        Level2Readiness.from_dict(payload)

    payload = readiness.to_dict()
    payload["level2_achieved"] = True
    with pytest.raises(CamInvariantError, match="derived"):
        Level2Readiness.from_dict(payload)

    payload = readiness.to_dict()
    payload["workflow_state"] = "MACHINE_ACCEPTED"
    with pytest.raises(CamValidationError, match="enum"):
        Level2Readiness.from_dict(payload)


def test_tranche2_modules_introduce_no_cnc_control_import_or_callable():
    root = Path("src/hms_cadcam")
    paths = (
        root / "cam" / "qualification" / "physical_model.py",
        root / "cam" / "qualification" / "evidence_model.py",
        root / "cam" / "qualification" / "tranche2_service.py",
        root / "cam" / "qualification" / "tranche2_store.py",
        root / "ui" / "physical_qualification_wizard.py",
    )
    forbidden_modules = {"socket", "requests", "urllib", "http", "ftplib", "serial", "modbus", "opcua"}
    forbidden_callables = {
        "upload_nc", "send_nc", "start_cycle", "start_spindle",
        "write_controller", "write_machine_parameter", "connect_cnc",
    }
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports = {
            node.names[0].name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import) and node.names
        } | {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        callables = {
            node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert imports.isdisjoint(forbidden_modules)
        assert callables.isdisjoint(forbidden_callables)

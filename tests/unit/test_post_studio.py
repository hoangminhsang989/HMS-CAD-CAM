"""R237 Post Processor Studio core lifecycle and artifact safety tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.post_studio import (
    PostDefinition, PostDeploymentState, PostMachineBinding, PostSourceFormat,
    PostStudioService, PostStudioStore, PostTestState, WorkNC2021PostProvider,
    RuleMappingState, project_visual_rules,
)


AT = "2026-08-12T12:00:00+07:00"
ORIGINAL = b"(FANUC-SHL)\r\nG41D1\r\nM09\r\nM05\r\nG91G28G0Z0\r\n"
CANDIDATE = b"(FANUC-SHL)\r\nG41D1\r\nG40\r\nM09\r\nM05\r\nG91G28G0Z0\r\n"


def _definition() -> PostDefinition:
    binding = PostMachineBinding(
        "fanuc_robodrill_alpha_d21mib", "fanuc_31i_b", "BT30", "FANUC-SHL",
        ContentFingerprint.from_payload({"machine": "fanuc-robodrill-alpha-d21mib"}),
    )
    return PostDefinition("fanuc-shl", "FANUC-SHL", PostSourceFormat.WORKNC_DAT, binding, AT, "R237 owner")


def _lineage() -> tuple[PostStudioService, object, object]:
    service = PostStudioService()
    original = service.import_source(_definition(), ORIGINAL, revision_id="fanuc-shl.original", created_at=AT, created_by="R237 import", notes="Original active WorkNC FANUC-SHL reference")
    candidate = service.create_candidate(original.revision_id, CANDIDATE, revision_id="fanuc-shl.r233-g40", created_at=AT, created_by="R233 recovery", notes="Insert G40 before M09/M05/G91G28G0Z0")
    return service, original, candidate


def test_r233_lineage_preserves_immutable_source_and_g40_diff() -> None:
    service, original, candidate = _lineage()
    assert len(original.source_sha256) == 64
    assert candidate.parent_revision_id == original.revision_id
    assert service.source_bytes(original.revision_id) == ORIGINAL
    diff = service.source_diff(candidate.revision_id)
    assert diff.added_lines == ("G40",)
    assert "G40_CANCELLATION" in diff.semantic_changes
    with pytest.raises(CamInvariantError):
        service.create_candidate(original.revision_id, ORIGINAL, revision_id="fanuc-shl.invalid", created_at=AT, created_by="test", notes="same bytes")


def test_approval_requires_current_validation_and_clean_regression() -> None:
    service, original, candidate = _lineage()
    with pytest.raises(CamInvariantError):
        service.approve(candidate.revision_id, approval_identity="owner", approved_at=AT)
    validation = service.validate(candidate.revision_id, validated_at=AT)
    regression = service.record_regression(candidate.revision_id, corpus_id="r233-known-g40", baseline_nc=b"G41D1\nM09\n", candidate_nc=b"G41D1\nG40\nM09\n", completed_at=AT)
    assert validation.state is PostTestState.PASS
    assert regression.state is PostTestState.PASS
    original_fingerprint = candidate.revision_fingerprint
    service.approve(candidate.revision_id, approval_identity="owner", approved_at=AT)
    assert service.revision(candidate.revision_id).revision_fingerprint == original_fingerprint
    plan = service.activation_plan(candidate.revision_id, expected_parent_sha256=original.source_sha256, target_reference="C:/ProgramData/WORKNC/2021.0/pospro/FANUC-SHL.dat")
    assert plan.deployment_state is PostDeploymentState.NOT_ACTIVE_GLOBALLY


def test_regression_rejects_unexpected_nc_change() -> None:
    service, _original, candidate = _lineage()
    result = service.record_regression(candidate.revision_id, corpus_id="r233-known-g40", baseline_nc=b"G41D1\nM09\n", candidate_nc=b"G42D1\nG40\nM09\n", completed_at=AT)
    assert result.state is PostTestState.FAIL
    assert result.unexpected_change_count == 2


def test_project_store_is_immutable_and_package_is_verified(tmp_path) -> None:
    service, original, candidate = _lineage(); store = PostStudioStore()
    store.publish_revision(tmp_path, _definition(), original, ORIGINAL)
    store.publish_revision(tmp_path, _definition(), candidate, CANDIDATE)
    assert store.verify_manifest(tmp_path)["files"]
    package = tmp_path / "fanuc-shl.hps.zip"; exported = store.export_package(tmp_path, package)
    assert exported["path"] == str(package)
    destination = tmp_path / "imported"; store.import_package(destination, package)
    assert store.verify_manifest(destination)["files"]
    source = store._path(tmp_path, f"sources/{candidate.revision_id}.dat")
    source.write_bytes(b"tampered")
    with pytest.raises(CamValidationError):
        store.verify_manifest(tmp_path)


def test_worknc_provider_fails_cleanly_without_configured_chain(tmp_path) -> None:
    provider = WorkNC2021PostProvider()
    status = provider.status()
    assert not status.available and "machine8_932.exe" in status.missing_dependencies
    run = provider.prepare(tmp_path, post_source=CANDIDATE, input_manifest={"candidate": "r233"})
    assert run.state == "UNAVAILABLE"
    assert run.manifest["global_post_write"] is False
    WorkNC2021PostProvider.cleanup(run)
    assert not Path(run.workspace).exists()


def test_real_r233_worknc_lineage_import_is_exact_and_never_global_write() -> None:
    original_path = Path(r"C:\ProgramData\WORKNC\2021.0\pospro\FANUC-SHL.dat")
    candidate_path = Path(r"E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R233_FANUC_SHL_COMPLETE_CONTEXT_AND_ISOLATED_G40_REMEDIATION\R233_CANDIDATE\FANUC-SHL.dat")
    if not original_path.is_file() or not candidate_path.is_file():
        pytest.skip("R237 real WorkNC lineage is unavailable")
    original, candidate = original_path.read_bytes(), candidate_path.read_bytes()
    before = original_path.read_bytes()
    assert __import__("hashlib").sha256(original).hexdigest() == "d0aa7518d669283be8aad6e92ffdec4dae8785abb7fdb2895cac0ab46cb51da3"
    assert __import__("hashlib").sha256(candidate).hexdigest() == "1160411dea6a5f104085747b4deac151fbd6b103b5930f39b11e8be358b67039"
    service = PostStudioService()
    root = service.import_source(_definition(), original, revision_id="fanuc-shl.real-original", created_at=AT, created_by="R237 import", notes="Immutable active source reference")
    child = service.create_candidate(root.revision_id, candidate, revision_id="fanuc-shl.real-r233-g40", created_at=AT, created_by="R233 recovery", notes="Isolated G40 candidate")
    assert "G40_CANCELLATION" in service.source_diff(child.revision_id).semantic_changes
    assert original_path.read_bytes() == before


def test_real_r233_generated_nc_is_consumed_for_validation_and_regression() -> None:
    baseline_path = Path(r"D:\WORK-CAM\2026\MR-NAM-SJ\260601-BL-CUM-DAN-DONG\260601---BL-CUM-DAN-DONG--25X226_5-L1\SHEET\260601---BL-CUM-DAN-DONG--25X226_5-L1_01.fn")
    candidate_path = Path(r"E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R233_FANUC_SHL_COMPLETE_CONTEXT_AND_ISOLATED_G40_REMEDIATION\generated_nc\260601---BL-CUM-DAN-DONG--25X226_5-L1_R233_G40.fn")
    source_path = Path(r"E:\FILE\FILE-CHAY-TEST-HMS-CAD-CAM\EVIDENCE\R233_FANUC_SHL_COMPLETE_CONTEXT_AND_ISOLATED_G40_REMEDIATION\R233_CANDIDATE\FANUC-SHL.dat")
    if not all(path.is_file() for path in (baseline_path, candidate_path, source_path)):
        pytest.skip("R233 generated NC evidence is unavailable")
    baseline, candidate = baseline_path.read_bytes(), candidate_path.read_bytes()
    service = PostStudioService()
    original = service.import_source(_definition(), source_path.read_bytes(), revision_id="fanuc-shl.r233-source", created_at=AT, created_by="R233", notes="isolated candidate source")
    validation = service.validate_generated_nc(original.revision_id, candidate, validated_at=AT)
    regression = service.record_regression(original.revision_id, corpus_id="r233-260601-bl-cum-dan-dong", baseline_nc=baseline, candidate_nc=candidate, completed_at=AT)
    assert validation.state in {PostTestState.PASS, PostTestState.WARNING}
    assert regression.state is PostTestState.PASS
    assert regression.candidate_nc_sha256 == "1bb0690a9f95e197dd26ead70d4447855ff0a3e1ee6119a85bc96d05847a9f67"
    assert regression.baseline_nc_sha256 == "8ea6a6c432d74581e36d69cd22a43d287cd22345ec2ba402185d5a575af80774"


def test_visual_rule_projection_preserves_unknown_legacy_source() -> None:
    projection = project_visual_rules(b"CUSTOM_WORKNC_DIRECTIVE\r\nG40\r\nG28Z0\r\n")
    assert projection.raw_source_required
    assert any(item.key == "cutter_compensation_cancel" for item in projection.rules)
    assert any(item.state is RuleMappingState.RAW_SOURCE_REQUIRED for item in projection.rules)

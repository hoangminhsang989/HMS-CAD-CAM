"""Independent R224 adversarial review of Tranche3 release boundaries."""

from __future__ import annotations

import ast
from dataclasses import replace
import json
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.domain.errors import CamInvariantError
from hms_cadcam.cam.qualification import (
    DryRunHandoffPackageBuilder,
    HandoffPackageError,
    MotionClass,
    OfflineFindingSeverity,
    OfflineReleaseRecord,
    OperatorReview,
    PackageStatus,
    ReleaseAssessment,
    ReleaseState,
    VerificationSessionState,
    analyze_nc_bytes,
    current_sources,
)
from hms_cadcam.cam.qualification.model import canonical_json_bytes, sha256_bytes
from hms_cadcam.cam.qualification.offline_reports import (
    UNKNOWN_VI,
    boundary_review,
    motion_reviews,
    operation_summary,
    render_setup_sheet_vi,
)
from hms_cadcam.ui.nc_release_center import NCReleaseCenter
from tests.unit._stage18a_tranche2_fixtures import acceptance_policy
from tests.unit._stage18a_tranche3_fixtures import BASE_INPUT, BASE_REPORT, release_context


def _build(target: Path):
    service, payload, setup, readiness, session, candidate, review, ack, assessment = release_context()
    current = current_sources(payload, setup, BASE_INPUT.machine_contract)
    root, digest = DryRunHandoffPackageBuilder().build(
        target, project_name="R224 direct review", program_name="PROGRAM",
        nc_filename="PROGRAM.nc", nc_bytes=payload, contract=BASE_INPUT.machine_contract,
        setup=setup, level1_report=BASE_REPORT, physical_readiness=readiness,
        current_sources=current, level2_policy_fingerprint=acceptance_policy().fingerprint,
        session=session, candidate=candidate, review=review, acknowledgement=ack,
        assessment=assessment,
    )
    return (
        root, digest, service, payload, setup, readiness, session, candidate,
        review, ack, assessment, current,
    )


def test_session_state_skipping_and_invalid_terminal_states_fail():
    *_prefix, session, _candidate, _review, _ack, _assessment = release_context()[2:]
    with pytest.raises(CamInvariantError, match="Post-analysis workflow states"):
        replace(
            session, state=VerificationSessionState.OPERATOR_REVIEWED,
            session_fingerprint=None,
        )
    with pytest.raises(CamInvariantError, match="Post-analysis workflow states"):
        replace(
            session, state=VerificationSessionState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF,
            session_fingerprint=None,
        )
    with pytest.raises(CamInvariantError, match="PRECHECK_FAILED"):
        replace(session, state=VerificationSessionState.PRECHECK_FAILED, session_fingerprint=None)


def test_ready_assessment_cannot_be_forged_without_bound_evidence():
    with pytest.raises(CamInvariantError, match="complete bound evidence"):
        ReleaseAssessment(ReleaseState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF, (), ())


def test_package_reassesses_and_rejects_manually_changed_assessment(tmp_path):
    service, payload, setup, readiness, session, candidate, review, ack, assessment = release_context()
    forged = replace(assessment, state=ReleaseState.BLOCKED, assessment_fingerprint=None)
    with pytest.raises(HandoffPackageError, match="does not authorize"):
        DryRunHandoffPackageBuilder().build(
            tmp_path / "forged", project_name="R224", program_name="PROGRAM",
            nc_filename="PROGRAM.nc", nc_bytes=payload, contract=BASE_INPUT.machine_contract,
            setup=setup, level1_report=BASE_REPORT, physical_readiness=readiness,
            current_sources=current_sources(payload, setup, BASE_INPUT.machine_contract),
            level2_policy_fingerprint=acceptance_policy().fingerprint,
            session=session, candidate=candidate, review=review, acknowledgement=ack,
            assessment=forged,
        )


def test_operator_must_acknowledge_exact_complete_finding_set():
    service, payload, setup, readiness, session, candidate, review, ack, _assessment = release_context()
    incomplete = replace(review, acknowledged_finding_ids=())
    result = service.assess_release(
        session=session, candidate=candidate, level1_report=BASE_REPORT,
        current_nc_bytes=payload, machine_contract=BASE_INPUT.machine_contract,
        setup=setup,
        physical_readiness=readiness, review=incomplete, acknowledgement=ack,
        current=current_sources(payload, setup, BASE_INPUT.machine_contract),
    )
    assert "OPERATOR_FINDING_ACKNOWLEDGEMENT_INCOMPLETE" in result.blocker_codes


def test_imported_tapping_bytes_cannot_reuse_a_clean_session_or_release():
    service, _payload, setup, readiness, session, candidate, review, ack, _assessment = release_context()
    tapping = b"%\nG90G80G49G40G17\nM06T1\nG90G54\nG84Z-5.R2.F100\nM30\n%"
    result = service.assess_release(
        session=session, candidate=candidate, level1_report=BASE_REPORT,
        current_nc_bytes=tapping, machine_contract=BASE_INPUT.machine_contract,
        setup=setup, physical_readiness=readiness, review=review,
        acknowledgement=ack,
        current=current_sources(tapping, setup, BASE_INPUT.machine_contract),
    )
    assert result.state is ReleaseState.BLOCKED
    assert "VERIFICATION_ANALYSIS_MISMATCH" in result.blocker_codes
    assert "NC_HASH_MISMATCH" in result.blocker_codes


def test_session_and_physical_flags_cannot_be_injected_through_json():
    *_prefix, session, _candidate, _review, _ack, assessment = release_context()[2:]
    session_payload = session.to_dict()
    session_payload["state"] = VerificationSessionState.READY_FOR_EXTERNAL_DRY_RUN_HANDOFF.value
    with pytest.raises(CamInvariantError):
        type(session).from_dict(session_payload)
    assessment_payload = assessment.to_dict()
    assessment_payload["level2_achieved"] = True
    with pytest.raises(CamInvariantError):
        ReleaseAssessment.from_dict(assessment_payload)
    assessment_payload = assessment.to_dict()
    assessment_payload["level3_achieved"] = True
    with pytest.raises(CamInvariantError):
        ReleaseAssessment.from_dict(assessment_payload)
    assessment_payload = assessment.to_dict()
    assessment_payload["machine_ready"] = True
    with pytest.raises(CamInvariantError):
        ReleaseAssessment.from_dict(assessment_payload)


@pytest.mark.parametrize(
    "program",
    (
        b"%\nG54\nG90G91X1.\nM30\n%",
        b"%\nG54\nM05M03\nM30\n%",
        b"%\nG54\nG01G02X1.\nM30\n%",
    ),
)
def test_conflicting_modal_or_control_words_are_unresolved_blockers(program):
    result = analyze_nc_bytes(program)
    assert any(
        item.code == "CONFLICTING_MODAL_WORDS"
        and item.severity is OfflineFindingSeverity.BLOCKER
        for item in result.findings
    )
    assert any(item.motion_class is MotionClass.UNRESOLVED for item in result.blocks)


def test_repeated_identical_modal_word_is_visible_and_deterministic():
    result = analyze_nc_bytes(b"%\nG54\nG90G90\nM30\n%")
    assert any(item.code == "REPEATED_MODAL_WORD" for item in result.findings)


@pytest.mark.parametrize("cycle", tuple(range(81, 90)))
@pytest.mark.parametrize("shape", ("G90G{cycle}", "n120 g90 g{cycle} (review)"))
def test_every_g81_to_g89_shape_remains_blocked(cycle, shape):
    payload = ("%\nG54\n" + shape.format(cycle=cycle) + "\nM30\n%").encode()
    result = analyze_nc_bytes(payload)
    assert any(
        item.code == "UNSUPPORTED_CANNED_CYCLE_TOKEN"
        and item.severity is OfflineFindingSeverity.BLOCKER
        for item in result.findings
    )
    if cycle == 84:
        assert any(
            item.code == "TAPPING_MACHINE_READY_OUTPUT_NOT_QUALIFIED"
            for item in result.findings
        )


def test_start_end_and_tool_change_review_bind_resolved_state_and_tooling():
    _service, _payload, setup, _ready, session, *_rest = release_context()
    boundary = boundary_review(session)
    assert boundary["start"] == {
        "units": "QUALIFIED_POST_PROFILE_MM", "units_source": "LEVEL1_POST_PROFILE",
        "positioning": "G90", "plane": "G17", "compensation": "G40",
        "work_offset": "G54", "tool": 1, "spindle_on": True, "coolant_on": False,
    }
    assert boundary["end"]["spindle_stopped"]
    assert boundary["end"]["coolant_off"]
    assert boundary["end"]["program_end"]
    change = motion_reviews(session, setup)["tool_changes"][0]
    assert change["logical_tool_id"] == "T1"
    assert change["tool_fingerprint"] == setup.tools[0].cutter_fingerprint.to_dict()
    assert change["holder_fingerprint"] == setup.tools[0].holder_fingerprint.to_dict()
    assert change["h"] == 1
    assert change["physical_position"] == "PHYSICAL_TOOL_CHANGE_POSITION_UNVERIFIED"


def test_setup_sheet_and_operation_summary_are_complete_without_fake_time():
    _service, _payload, setup, _ready, session, candidate, *_rest = release_context()
    text = render_setup_sheet_vi(
        project_name="R224", program_name="PROGRAM", candidate=candidate,
        session=session, setup=setup, contract=BASE_INPUT.machine_contract,
    )
    assert "Giới hạn trục chính: 24000.0 rpm" in text
    assert "Bao lượng chạy dao: 30000.0 mm/min" in text
    assert "## Danh sách Tool" in text and "T1:" in text
    assert "MACHINE_READY: KHÔNG" in text
    operations = operation_summary(session)
    assert operations and "finding_ids" in operations[0]
    assert operations[0]["path_metadata"]["machining_time"] == UNKNOWN_VI


def test_handoff_package_requires_nc_extension(tmp_path):
    service, payload, setup, readiness, session, candidate, review, ack, assessment = release_context()
    with pytest.raises(HandoffPackageError, match=".nc extension"):
        DryRunHandoffPackageBuilder().build(
            tmp_path / "fn", project_name="R224", program_name="PROGRAM",
            nc_filename="PROGRAM.fn", nc_bytes=payload, contract=BASE_INPUT.machine_contract,
            setup=setup, level1_report=BASE_REPORT, physical_readiness=readiness,
            current_sources=current_sources(payload, setup, BASE_INPUT.machine_contract),
            level2_policy_fingerprint=acceptance_policy().fingerprint,
            session=session, candidate=candidate, review=review, acknowledgement=ack,
            assessment=assessment,
        )


def test_copied_old_package_is_stale_against_current_sources(tmp_path):
    root, _digest, _service, _payload, _setup, _ready, session, candidate, *_tail, current = _build(
        tmp_path / "package"
    )
    changed = replace(current, nc_sha256="0" * 64)
    with pytest.raises(HandoffPackageError, match="DRY_RUN_HANDOFF_PACKAGE_STALE"):
        DryRunHandoffPackageBuilder().validate_current(
            root, candidate=candidate, session=session, current_sources=changed,
            level1_report=BASE_REPORT,
        )


def test_semantic_release_state_tamper_fails_even_if_hashes_are_rebuilt(tmp_path):
    root, *_rest = _build(tmp_path / "package")
    release_path = root / "release-identity.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["release_assessment"]["state"] = ReleaseState.DRAFT.value
    release_bytes = canonical_json_bytes(release)
    release_path.write_bytes(release_bytes)
    manifest_path = root / "package-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["entries"] if item["path"] == "release-identity.json")
    entry["size"] = len(release_bytes)
    entry["sha256"] = sha256_bytes(release_bytes)
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)
    (root / "package-manifest.json.sha256").write_bytes(
        f"{sha256_bytes(manifest_bytes)}  package-manifest.json\n".encode("utf-8")
    )
    with pytest.raises(HandoffPackageError, match="semantic identity"):
        DryRunHandoffPackageBuilder().validate(root)


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (b"MZdisguised executable", "Executable/binary content"),
        (b"password=not-allowed-in-handoff", "Credential finding"),
    ),
)
def test_disguised_executable_or_credential_content_fails_after_rehash(
    tmp_path, payload, message,
):
    root, *_rest = _build(tmp_path / "package")
    target = root / "tool-list.csv"
    target.write_bytes(payload)
    manifest_path = root / "package-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["entries"] if item["path"] == "tool-list.csv")
    entry["size"] = len(payload)
    entry["sha256"] = sha256_bytes(payload)
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_path.write_bytes(manifest_bytes)
    (root / "package-manifest.json.sha256").write_bytes(
        f"{sha256_bytes(manifest_bytes)}  package-manifest.json\n".encode("utf-8")
    )
    with pytest.raises(HandoffPackageError, match=message):
        DryRunHandoffPackageBuilder().validate(root)


def test_persisted_released_status_requires_ready_assessment_and_no_staleness(tmp_path):
    root, _digest, _service, _payload, _setup, _ready, session, candidate, review, ack, assessment, _current = _build(
        tmp_path / "package"
    )
    package_id = ContentFingerprint.from_dict(
        json.loads((root / "package-manifest.json").read_text(encoding="utf-8"))["package_id"]
    )
    with pytest.raises(CamInvariantError, match="inconsistent"):
        OfflineReleaseRecord(
            "invalid", session, candidate, review, ack, assessment, package_id,
            PackageStatus.DRAFT, (),
        )
    with pytest.raises(CamInvariantError, match="inconsistent"):
        OfflineReleaseRecord(
            "stale-released", session, candidate, review, ack, assessment, package_id,
            PackageStatus.RELEASED_FOR_EXTERNAL_DRY_RUN, ("NC_CHANGED",),
        )


def test_release_center_emits_typed_review_and_acknowledgement(qtbot):
    _service, _payload, setup, _ready, session, candidate, _review, _ack, assessment = release_context()
    panel = NCReleaseCenter()
    qtbot.addWidget(panel)
    panel.set_release(session, candidate, assessment, filename="PROGRAM.nc", setup=setup)
    emitted: list[tuple[object, object]] = []
    panel.operator_review_submitted.connect(lambda review, ack: emitted.append((review, ack)))
    panel.reviewer_name.setText("operator-r224")
    panel.reviewer_role.setText("NC reviewer")
    panel.review_notes.setText("Reviewed for external preparation only")
    panel.findings_ack.setChecked(True)
    panel.software_ack.setChecked(True)
    panel.machine_ack.setChecked(True)
    qtbot.mouseClick(panel.review_button, Qt.MouseButton.LeftButton)
    assert len(emitted) == 1
    review, acknowledgement = emitted[0]
    assert isinstance(review, OperatorReview)
    assert set(review.acknowledged_finding_ids) == {item.finding_id for item in session.findings}
    assert review.release_candidate_fingerprint == candidate.candidate_fingerprint
    assert acknowledgement.release_candidate_fingerprint == candidate.candidate_fingerprint
    assert panel.setup_g54.text() == "G54"
    assert panel.setup_tools.text() == "1"


def test_tranche3_modules_have_no_transport_or_controller_imports():
    root = Path(__file__).parents[2]
    files = tuple((root / "src/hms_cadcam/cam/qualification").glob("offline_*.py")) + (
        root / "src/hms_cadcam/ui/nc_release_center.py",
    )
    forbidden = {
        "socket", "requests", "httpx", "serial", "ftplib", "smb", "ctypes",
        "subprocess", "importlib",
    }
    imports: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
    assert imports.isdisjoint(forbidden)

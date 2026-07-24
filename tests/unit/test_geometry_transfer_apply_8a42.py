"""Focused apply, rollback, recovery, and lineage tests for Stage 8A.4.2."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from hms_cadcam.project.exceptions import GeometryTransferApplyError
from hms_cadcam.project.filesystem import sha256_file
from hms_cadcam.project.geometry_transfer import (
    GeometryApplyChoice,
    GeometryTransferStatus,
)
from hms_cadcam.project.models import UnitSystem
from hms_cadcam.project.service import ProjectService


@pytest.fixture
def safe_apply_parent() -> Path:
    root = Path(tempfile.gettempdir()) / f"HMS-APPLY-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _saved_document(
    tmp_path: Path,
    *,
    name: str = "Chi tiết chính",
    payload: bytes = b"exact brep geometry version one",
    units: str = "mm",
) -> ProjectService:
    source = tmp_path / f"{name}.brep"
    source.write_bytes(payload)
    service = ProjectService.create_default(
        tmp_path / f"sender-config-{uuid4().hex}"
    )
    service.commit_document_open(service.prepare_document_open(source))
    service.record_document_geometry_metadata(
        {
            "units": units,
            "topology_counts": {"solids": 1, "faces": 12, "edges": 30},
        }
    )
    service.save_document(tmp_path / f"{name}.HMS")
    return service


def _target(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> tuple[ProjectService, Path]:
    service = ProjectService.create_default(
        tmp_path / f"target-config-{uuid4().hex}"
    )
    session = service.create_cam_workspace(
        safe_apply_parent,
        "Dự án áp dụng",
        UnitSystem.MILLIMETER,
    )
    return service, session.root_path


def _send_and_apply_add(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> tuple[ProjectService, ProjectService, Path]:
    sender = _saved_document(tmp_path)
    target, root = _target(tmp_path, safe_apply_parent)
    request = sender.send_document_geometry(root)
    target.apply_incoming_geometry(
        request.request_id,
        GeometryApplyChoice.ADD_NEW,
    )
    return sender, target, root


def test_add_new_commits_independent_source_and_working_geometry(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    target, root = _target(tmp_path, safe_apply_parent)
    request = sender.send_document_geometry(root)

    result = target.apply_incoming_geometry(
        request.request_id,
        GeometryApplyChoice.ADD_NEW,
    )

    session = target.current_project
    assert session is not None and not session.is_dirty
    assert result.request.status is GeometryTransferStatus.APPLIED
    assert result.affected_operation_ids == ()
    assert target.scan_incoming_geometry() == ()
    assert len(session.manifest.source_files) == 1
    record = session.manifest.source_files[0]
    assert record.source_id == result.source_id
    assert record.transfer_request_id == request.request_id
    assert record.source_document_id == request.source_document_id
    assert record.source_container_id == request.source_container_id
    source = root / record.stored_path
    working = root / str(record.working_geometry_path)
    assert source.is_file() and working.is_file()
    assert sha256_file(source) == request.payload_checksum
    assert sha256_file(working) == request.payload_checksum
    assert source != working
    assert (
        root
        / "incoming-geometry"
        / "applied"
        / f"request-{request.request_id}"
    ).is_dir()


def test_replace_requires_explicit_existing_asset(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> None:
    sender, target, root = _send_and_apply_add(tmp_path, safe_apply_parent)
    other = _saved_document(
        tmp_path,
        name="Chi tiết thay thế",
        payload=b"replacement exact brep",
    )
    request = other.send_document_geometry(root)

    with pytest.raises(GeometryTransferApplyError):
        target.apply_incoming_geometry(
            request.request_id,
            GeometryApplyChoice.REPLACE_EXISTING,
        )

    session = target.current_project
    assert session is not None
    assert len(session.manifest.source_files) == 1
    assert sender.current_document is not None
    failed = target._geometry_inbox.request(root, request.request_id)
    assert failed.status is GeometryTransferStatus.FAILED


def test_replace_preserves_source_identity_and_archives_old_working_geometry(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> None:
    _sender, target, root = _send_and_apply_add(tmp_path, safe_apply_parent)
    session = target.current_project
    assert session is not None
    old = session.manifest.source_files[0]
    old_working = root / str(old.working_geometry_path)
    old_checksum = sha256_file(old_working)
    replacement = _saved_document(
        tmp_path,
        name="Khối thay thế",
        payload=b"replacement exact geometry version two",
    )
    request = replacement.send_document_geometry(root)

    result = target.apply_incoming_geometry(
        request.request_id,
        GeometryApplyChoice.REPLACE_EXISTING,
        target_source_id=old.source_id,
    )

    changed = target.current_project
    assert changed is not None
    record = changed.manifest.source_files[0]
    assert result.source_id == old.source_id == record.source_id
    assert record.geometry_version > old.geometry_version
    assert record.sha256 == request.payload_checksum
    assert not old_working.exists()
    archived = tuple((root / "replaced").glob("*"))
    assert len(archived) == 1
    assert sha256_file(archived[0]) == old_checksum


def test_update_matching_uses_lineage_not_filename(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> None:
    sender, target, root = _send_and_apply_add(tmp_path, safe_apply_parent)
    session = target.current_project
    assert session is not None
    original = session.manifest.source_files[0]
    document = sender.current_document
    assert document is not None
    document.geometry_path.write_bytes(b"same lineage, exact geometry version two")
    document.geometry_version += 1
    sender.save_document()
    request = sender.send_document_geometry(root)
    preview = target.incoming_geometry_preview(request.request_id)

    assert preview.update_matching_allowed
    assert preview.deterministic_match_id == original.source_id
    result = target.apply_incoming_geometry(
        request.request_id,
        GeometryApplyChoice.UPDATE_MATCHING,
    )

    changed = target.current_project
    assert changed is not None
    record = changed.manifest.source_files[0]
    assert result.source_id == original.source_id
    assert record.geometry_version == 2
    assert record.source_geometry_fingerprint == request.payload_checksum


def test_unknown_units_fail_closed_and_leave_existing_geometry_unchanged(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> None:
    sender = _saved_document(tmp_path, units="unknown")
    target, root = _target(tmp_path, safe_apply_parent)
    request = sender.send_document_geometry(root)
    manifest_before = sha256_file(root / "manifest.json")
    database_before = sha256_file(root / "project.db")

    with pytest.raises(GeometryTransferApplyError):
        target.apply_incoming_geometry(
            request.request_id,
            GeometryApplyChoice.ADD_NEW,
        )

    assert sha256_file(root / "manifest.json") == manifest_before
    assert sha256_file(root / "project.db") == database_before
    assert not tuple((root / "source").iterdir())
    assert not tuple((root / "working-geometry").iterdir())


def test_tampered_payload_is_not_scanned_or_applied(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    target, root = _target(tmp_path, safe_apply_parent)
    request = sender.send_document_geometry(root)
    request_root = (
        root
        / "incoming-geometry"
        / "pending"
        / f"request-{request.request_id}"
    )
    payload = next((request_root / "geometry").iterdir())
    payload.write_bytes(b"tampered geometry")

    assert target.scan_incoming_geometry() == ()
    with pytest.raises(GeometryTransferApplyError):
        target.apply_incoming_geometry(
            request.request_id,
            GeometryApplyChoice.ADD_NEW,
        )
    assert not tuple((root / "source").iterdir())
    assert not tuple((root / "working-geometry").iterdir())


def test_persistence_failure_rolls_back_manifest_database_and_new_assets(
    tmp_path: Path,
    safe_apply_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _saved_document(tmp_path)
    target, root = _target(tmp_path, safe_apply_parent)
    request = sender.send_document_geometry(root)
    manifest_before = (root / "manifest.json").read_bytes()

    def fail_save(_session):
        raise OSError("simulated persistence failure")

    monkeypatch.setattr(target._saver, "save", fail_save)
    with pytest.raises(GeometryTransferApplyError):
        target.apply_incoming_geometry(
            request.request_id,
            GeometryApplyChoice.ADD_NEW,
        )

    assert (root / "manifest.json").read_bytes() == manifest_before
    assert sha256_file(root / "project.db") != ""
    target._database.validate(root / "project.db")
    target._database.validate_project_identity(
        root / "project.db",
        target.current_project.manifest.project_id,
    )
    assert not tuple((root / "source").iterdir())
    assert not tuple((root / "working-geometry").iterdir())
    assert target.current_project is not None
    assert target.current_project.manifest.source_files == ()
    failed = target._geometry_inbox.request(root, request.request_id)
    assert failed.status is GeometryTransferStatus.FAILED


def test_reopen_returns_unstarted_claim_to_pending(
    tmp_path: Path,
    safe_apply_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    target, root = _target(tmp_path, safe_apply_parent)
    request = sender.send_document_geometry(root)
    target._geometry_inbox.claim(root, request.request_id)
    target.close_project()

    reopened = ProjectService.create_default(tmp_path / "recovery-config")
    reopened.open_project(root)

    pending = reopened.scan_incoming_geometry()
    assert len(pending) == 1
    assert pending[0].request_id == request.request_id
    assert pending[0].status is GeometryTransferStatus.PENDING
    metadata = json.loads(
        (
            root
            / "incoming-geometry"
            / "pending"
            / f"request-{request.request_id}"
            / "request.json"
        ).read_text(encoding="utf-8")
    )
    assert "phục hồi" in metadata["error_message"]


def test_reopen_rolls_back_crash_after_geometry_files_before_persistence(
    tmp_path: Path,
    safe_apply_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _saved_document(tmp_path)
    target, root = _target(tmp_path, safe_apply_parent)
    request = sender.send_document_geometry(root)

    def crash_before_persist(_session):
        raise SystemExit("simulated process termination before persistence")

    monkeypatch.setattr(target._saver, "save", crash_before_persist)
    with pytest.raises(SystemExit):
        target.apply_incoming_geometry(
            request.request_id,
            GeometryApplyChoice.ADD_NEW,
        )
    applying = tuple((root / "incoming-geometry" / "staging").iterdir())
    assert len(applying) == 1
    assert tuple((root / "source").iterdir())
    assert tuple((root / "working-geometry").iterdir())
    target.close_project()

    reopened = ProjectService.create_default(tmp_path / "crash-rollback-config")
    session = reopened.open_project(root)

    assert session.manifest.source_files == ()
    assert not tuple((root / "source").iterdir())
    assert not tuple((root / "working-geometry").iterdir())
    pending = reopened.scan_incoming_geometry()
    assert len(pending) == 1
    assert pending[0].request_id == request.request_id
    assert pending[0].status is GeometryTransferStatus.PENDING


def test_reopen_completes_crash_after_persistence_before_applied_move(
    tmp_path: Path,
    safe_apply_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _saved_document(tmp_path)
    target, root = _target(tmp_path, safe_apply_parent)
    request = sender.send_document_geometry(root)

    def crash_after_persist(*_args, **_kwargs):
        raise SystemExit("simulated process termination after persistence")

    monkeypatch.setattr(
        target._geometry_inbox,
        "finish_applied",
        crash_after_persist,
    )
    with pytest.raises(SystemExit):
        target.apply_incoming_geometry(
            request.request_id,
            GeometryApplyChoice.ADD_NEW,
        )
    assert len(tuple((root / "source").iterdir())) == 1
    assert len(tuple((root / "working-geometry").iterdir())) == 1
    target.close_project()

    reopened = ProjectService.create_default(tmp_path / "crash-finish-config")
    session = reopened.open_project(root)

    assert len(session.manifest.source_files) == 1
    assert session.manifest.source_files[0].transfer_request_id == request.request_id
    assert reopened.scan_incoming_geometry() == ()
    applied = reopened._geometry_inbox.request(root, request.request_id)
    assert applied.status is GeometryTransferStatus.APPLIED
    with pytest.raises(GeometryTransferApplyError):
        reopened.apply_incoming_geometry(
            request.request_id,
            GeometryApplyChoice.ADD_NEW,
        )

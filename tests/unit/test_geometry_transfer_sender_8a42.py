"""Focused sender, target validation, inbox, and idempotency tests."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from hms_cadcam.project.exceptions import (
    GeometryTransferDuplicateError,
    GeometryTransferTargetError,
    ProjectPermissionError,
)
from hms_cadcam.project.filesystem import sha256_file
from hms_cadcam.project.geometry_transfer import (
    GeometryRepresentation,
    GeometryTransferStatus,
)
from hms_cadcam.project.service import ProjectService


@pytest.fixture
def safe_transfer_parent() -> Path:
    root = Path(tempfile.gettempdir()) / f"HMS-TRANSFER-{uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _saved_document(
    tmp_path: Path,
    *,
    filename: str = "Khuôn truyền dữ liệu.brep",
    payload: bytes = b"exact brep geometry",
) -> ProjectService:
    source = tmp_path / filename
    source.write_bytes(payload)
    service = ProjectService.create_default(tmp_path / f"config-{uuid4().hex}")
    service.commit_document_open(service.prepare_document_open(source))
    service.record_document_geometry_metadata(
        {
            "units": "mm",
            "topology_counts": {"solids": 2, "faces": 18, "edges": 42},
        }
    )
    service.save_document(tmp_path / f"{source.stem}.HMS")
    return service


def _target_project(
    tmp_path: Path,
    safe_transfer_parent: Path,
    *,
    name: str = "Dự án nhận",
) -> tuple[ProjectService, Path]:
    service = ProjectService.create_default(tmp_path / f"target-{uuid4().hex}")
    session = service.create_cam_workspace(safe_transfer_parent, name)
    return service, session.root_path


def test_target_validation_reports_project_identity_and_active_lock(
    tmp_path: Path,
    safe_transfer_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    target_service, root = _target_project(tmp_path, safe_transfer_parent)

    result = sender.inspect_geometry_transfer_target(root)

    assert result.valid
    assert result.project_id == target_service.current_project.manifest.project_id
    assert result.workspace_version == 1
    assert result.project_name == "Dự án nhận"
    assert result.active_session_detected
    assert result.status_text == "Dự án hợp lệ"


def test_sender_publishes_complete_request_atomically_and_preserves_hms(
    tmp_path: Path,
    safe_transfer_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    target_service, root = _target_project(tmp_path, safe_transfer_parent)
    document = sender.current_document
    assert document is not None and document.state.physical_path is not None
    before_hms = sha256_file(document.state.physical_path)
    before_source = tuple((root / "source").iterdir())
    before_working = tuple((root / "working-geometry").iterdir())

    request = sender.send_document_geometry(root)

    assert sender.current_document is document
    assert sha256_file(document.state.physical_path) == before_hms
    assert request.status is GeometryTransferStatus.PENDING
    assert request.geometry_representation is GeometryRepresentation.EXACT_BREP
    assert (request.solid_count, request.face_count, request.edge_count) == (
        2,
        18,
        42,
    )
    incoming = root / "incoming-geometry"
    assert not tuple((incoming / "staging").iterdir())
    request_root = incoming / "pending" / f"request-{request.request_id}"
    assert (request_root / "request.json").is_file()
    assert (request_root / "checksums.json").is_file()
    assert (request_root / "preview").is_dir()
    assert len(tuple((request_root / "geometry").iterdir())) == 1
    assert tuple((root / "source").iterdir()) == before_source
    assert tuple((root / "working-geometry").iterdir()) == before_working
    assert target_service.scan_incoming_geometry() == (request,)


def test_duplicate_geometry_is_not_published_twice(
    tmp_path: Path,
    safe_transfer_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    _target_service, root = _target_project(tmp_path, safe_transfer_parent)
    first = sender.send_document_geometry(root)

    with pytest.raises(GeometryTransferDuplicateError) as captured:
        sender.send_document_geometry(root)

    assert captured.value.request.request_id == first.request_id
    assert len(tuple((root / "incoming-geometry" / "pending").iterdir())) == 1


def test_closed_project_retains_pending_request_until_reopen(
    tmp_path: Path,
    safe_transfer_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    target_service, root = _target_project(tmp_path, safe_transfer_parent)
    target_service.close_project()

    request = sender.send_document_geometry(root)

    assert request.status is GeometryTransferStatus.PENDING
    assert (root / "incoming-geometry" / "pending").is_dir()
    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    assert reopened.scan_incoming_geometry() == (request,)


def test_target_validation_rejects_arbitrary_directory(tmp_path: Path) -> None:
    sender = _saved_document(tmp_path)
    arbitrary = tmp_path / "arbitrary"
    arbitrary.mkdir()

    result = sender.inspect_geometry_transfer_target(arbitrary)

    assert not result.valid
    assert result.status_text == "Dự án CAM không hợp lệ"
    with pytest.raises(GeometryTransferTargetError):
        sender.send_document_geometry(arbitrary)


def test_target_validation_rejects_database_project_id_mismatch(
    tmp_path: Path,
    safe_transfer_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    target_service, root = _target_project(tmp_path, safe_transfer_parent)
    target_service.close_project()
    target_service._database.bind_project_identity(root / "project.db", uuid4())

    result = sender.inspect_geometry_transfer_target(root)

    assert not result.valid
    assert "identity" in result.reason
    assert not tuple((root / "incoming-geometry" / "pending").iterdir())


def test_target_validation_rejects_read_only_probe(
    tmp_path: Path,
    safe_transfer_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _saved_document(tmp_path)
    _target_service, root = _target_project(tmp_path, safe_transfer_parent)

    def deny(_root: Path) -> None:
        raise ProjectPermissionError("read only")

    monkeypatch.setattr(sender._geometry_inbox, "_probe_write_access", deny)
    result = sender.inspect_geometry_transfer_target(root)
    assert not result.valid
    assert "read only" in result.reason


def test_sender_failure_removes_only_owned_staging_request(
    tmp_path: Path,
    safe_transfer_parent: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sender = _saved_document(tmp_path)
    _target_service, root = _target_project(tmp_path, safe_transfer_parent)
    staging = root / "incoming-geometry" / "staging"
    unrelated = staging / "request-unrelated.tmp"
    unrelated.mkdir()

    def fail_validation(*_args, **_kwargs):
        raise RuntimeError("simulated validation failure")

    monkeypatch.setattr(
        sender._geometry_inbox,
        "validate_request_directory",
        fail_validation,
    )
    with pytest.raises(RuntimeError, match="simulated"):
        sender.send_document_geometry(root)

    assert unrelated.is_dir()
    assert tuple(staging.iterdir()) == (unrelated,)
    assert not tuple((root / "incoming-geometry" / "pending").iterdir())


def test_unicode_hms_path_is_preserved_in_request(
    tmp_path: Path,
    safe_transfer_parent: Path,
) -> None:
    sender = _saved_document(
        tmp_path,
        filename="Đồ gá phiên bản mới.brep",
    )
    _target_service, root = _target_project(tmp_path, safe_transfer_parent)

    request = sender.send_document_geometry(root)

    assert request.source_hms_path.name == "Đồ gá phiên bản mới.HMS"
    metadata = json.loads(
        (
            root
            / "incoming-geometry"
            / "pending"
            / f"request-{request.request_id}"
            / "request.json"
        ).read_text(encoding="utf-8")
    )
    assert metadata["source_hms_path"].endswith("Đồ gá phiên bản mới.HMS")


def test_defer_and_reject_are_durable_non_delete_transitions(
    tmp_path: Path,
    safe_transfer_parent: Path,
) -> None:
    sender = _saved_document(tmp_path)
    target_service, root = _target_project(tmp_path, safe_transfer_parent)
    request = sender.send_document_geometry(root)

    deferred = target_service.defer_incoming_geometry(request.request_id)
    assert deferred.status is GeometryTransferStatus.DEFERRED
    assert target_service.scan_incoming_geometry() == (deferred,)

    rejected = target_service.reject_incoming_geometry(request.request_id)
    assert rejected.status is GeometryTransferStatus.REJECTED
    assert target_service.scan_incoming_geometry() == ()
    assert (
        root
        / "incoming-geometry"
        / "rejected"
        / f"request-{request.request_id}"
    ).is_dir()


def test_mesh_request_is_explicitly_view_only_and_apply_preview_fails_closed(
    tmp_path: Path,
    safe_transfer_parent: Path,
) -> None:
    sender = _saved_document(
        tmp_path,
        filename="Lưới xem trước.stl",
        payload=b"solid mesh\nendsolid mesh\n",
    )
    target_service, root = _target_project(tmp_path, safe_transfer_parent)

    request = sender.send_document_geometry(root)
    preview = target_service.incoming_geometry_preview(request.request_id)

    assert request.geometry_representation is GeometryRepresentation.MESH_ONLY
    assert not preview.update_matching_allowed
    assert "Không đủ dữ liệu chính xác" in preview.update_matching_reason

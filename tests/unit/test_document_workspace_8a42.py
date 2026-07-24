"""Focused Stage 8A.4.2 domain, container, path, and lifecycle tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest

from hms_cadcam.project.document_container import HmsDocumentContainer
from hms_cadcam.project.exceptions import (
    HmsContainerDamagedError,
    HmsContainerError,
    HmsContainerSecurityError,
    InvalidHmsFilenameError,
    UnsafeWorkspacePathError,
)
from hms_cadcam.project.path_policy import (
    ensure_hms_suffix,
    normalize_cam_project_name,
    normalize_internal_source_filename,
    validate_hms_filename,
    validate_parent_path,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.workspace import DocumentMode


@pytest.mark.parametrize(
    ("display", "physical"),
    [
        ("Khuôn DNM 6700 #1", "Khuon-DNM-6700-1"),
        ("  Đồ gá   lần 2 (đã sửa) ", "Do-ga-lan-2-da-sua"),
        ("Sản phẩm_A-01", "San-pham-A-01"),
    ],
)
def test_cam_physical_name_is_deterministic_ascii(
    display: str, physical: str
) -> None:
    assert normalize_cam_project_name(display) == physical


@pytest.mark.parametrize("name", ["", "###", "CON", "LPT1"])
def test_cam_physical_name_rejects_empty_or_reserved(name: str) -> None:
    with pytest.raises(UnsafeWorkspacePathError):
        normalize_cam_project_name(name)


@pytest.mark.parametrize(
    "name",
    [
        "Khuôn trên DNM 6700.HMS",
        "Đồ gá lần 2 (đã sửa).HMS",
        "Sản phẩm_A-01.HMS",
    ],
)
def test_hms_filename_preserves_unicode_spaces_and_parentheses(name: str) -> None:
    assert validate_hms_filename(name) == name
    assert ensure_hms_suffix(name) == name


@pytest.mark.parametrize(
    "name",
    ["bad:name.HMS", "bad?.HMS", "CON.HMS", "tail. ", "NUL"],
)
def test_hms_filename_rejects_windows_invalid_values(name: str) -> None:
    with pytest.raises(InvalidHmsFilenameError):
        validate_hms_filename(name)


def test_internal_source_name_is_sanitized_without_changing_external() -> None:
    external = "Khuôn trên DNM #1.step"
    assert normalize_internal_source_filename(external) == "Khuon-tren-DNM-1.step"
    assert external == "Khuôn trên DNM #1.step"


def test_parent_policy_blocks_spaces_and_unsafe_segments(tmp_path: Path) -> None:
    unsafe = tmp_path.parent / "Work CAM"
    unsafe.mkdir(exist_ok=True)
    result = validate_parent_path(unsafe, "Project-1", check_access=False)
    assert not result.valid
    assert "dấu cách" in result.reason


def test_document_container_round_trip_and_deterministic_save(tmp_path: Path) -> None:
    source = tmp_path / "Khuôn trên.step"
    source.write_bytes(b"STEP geometry payload")
    container_service = HmsDocumentContainer(
        tmp_path / "runtime",
        tmp_path,
    )
    prepared = container_service.prepare_source(source)
    target = tmp_path / "Đồ gá lần 2 (đã sửa).HMS"

    first_path = container_service.save(prepared.session, target)
    first_digest = hashlib.sha256(first_path.read_bytes()).hexdigest()
    second_path = container_service.save(prepared.session)
    second_digest = hashlib.sha256(second_path.read_bytes()).hexdigest()

    assert first_digest == second_digest
    reopened = container_service.prepare_container(target)
    assert reopened.session.state.mode is DocumentMode.CAD_DOCUMENT
    assert reopened.session.state.physical_path == target
    assert reopened.session.provenance.original_filename == source.name
    assert reopened.session.geometry_path.read_bytes() == source.read_bytes()
    assert reopened.session.state.suggested_save_directory == target.parent
    container_service.close(reopened.session)


def test_document_container_rejects_path_traversal(tmp_path: Path) -> None:
    target = tmp_path / "unsafe.HMS"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("../escape.step", b"escape")
    service = HmsDocumentContainer(tmp_path / "runtime", tmp_path)
    with pytest.raises(HmsContainerSecurityError):
        service.prepare_container(target)
    assert not (tmp_path / "escape.step").exists()


def test_document_container_rejects_damaged_archive(tmp_path: Path) -> None:
    target = tmp_path / "damaged.HMS"
    target.write_bytes(b"not a zip container")
    service = HmsDocumentContainer(tmp_path / "runtime", tmp_path)
    with pytest.raises(HmsContainerDamagedError):
        service.prepare_container(target)


def test_document_container_rejects_checksum_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"original geometry")
    service = HmsDocumentContainer(tmp_path / "runtime", tmp_path)
    prepared = service.prepare_source(source)
    target = tmp_path / "part.HMS"
    service.save(prepared.session, target)
    with zipfile.ZipFile(target, "r") as archive:
        entries = {
            info.filename: archive.read(info)
            for info in archive.infolist()
            if not info.is_dir()
        }
    entries["geometry/model.step"] = b"tampered geometry"
    mismatch = tmp_path / "mismatch.HMS"
    with zipfile.ZipFile(mismatch, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    with pytest.raises(HmsContainerDamagedError, match="Checksum"):
        service.prepare_container(mismatch)


def test_failed_document_save_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "part.brep"
    source.write_bytes(b"brep")
    service = HmsDocumentContainer(tmp_path / "runtime", tmp_path)
    prepared = service.prepare_source(source)
    target = tmp_path / "part.HMS"
    service.save(prepared.session, target)
    old_payload = target.read_bytes()

    def fail_build(_session):
        raise OSError("simulated")

    monkeypatch.setattr(service, "_build_entries", fail_build)
    with pytest.raises(HmsContainerError, match="simulated"):
        service.save(prepared.session)
    assert target.read_bytes() == old_payload
    assert target.stat().st_size > 0


def test_source_open_suggests_its_directory(tmp_path: Path) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"step")
    prepared = HmsDocumentContainer(
        tmp_path / "runtime",
        tmp_path / "fallback",
    ).prepare_source(source)
    assert prepared.session.state.physical_path is None
    assert prepared.session.state.source_path == source
    assert prepared.session.state.suggested_save_directory == source.parent
    assert prepared.session.state.dirty


def test_document_save_as_preserves_provenance_and_moves_suggestion(
    tmp_path: Path,
) -> None:
    source = tmp_path / "original.step"
    source.write_bytes(b"geometry")
    other = tmp_path / "other"
    other.mkdir()
    service = HmsDocumentContainer(tmp_path / "runtime", tmp_path)
    prepared = service.prepare_source(source)
    target = other / "Bản sao tài liệu.HMS"
    service.save(prepared.session, target)
    assert prepared.session.state.physical_path == target
    assert prepared.session.state.suggested_save_directory == other
    assert prepared.session.provenance.original_path == source
    reopened = service.prepare_container(target)
    assert reopened.session.provenance.original_path == source
    assert reopened.session.provenance.original_filename == source.name


def test_document_service_mode_switch_commits_only_after_success(tmp_path: Path) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"step")
    service = ProjectService.create_default(tmp_path / "config")
    prepared = service.prepare_document_open(source)
    assert service.current_workspace is None
    state = service.commit_document_open(prepared)
    assert state.mode is DocumentMode.CAD_DOCUMENT
    assert service.current_workspace == state
    assert service.current_project is None


def test_document_autosave_keeps_dirty_and_physical_path(tmp_path: Path) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"step")
    service = ProjectService.create_default(tmp_path / "config")
    service.commit_document_open(service.prepare_document_open(source))
    before = service.current_workspace
    assert before is not None
    recovery = service.autosave_workspace(expected_identity=before.identity)
    assert isinstance(recovery, Path) and recovery.is_file()
    after = service.current_workspace
    assert after is not None and after.dirty
    assert after.physical_path is None


def test_cam_workspace_create_has_root_boundary_and_unpacked_geometry(
    tmp_path: Path,
    safe_cam_parent: Path,
) -> None:
    parent = safe_cam_parent
    source = tmp_path / "Khuôn trên DNM #1.step"
    source.write_bytes(b"exact source-compatible geometry")
    service = ProjectService.create_default(tmp_path / "config")
    session = service._creator.create_cam_workspace(
        parent,
        "Khuôn DNM 6700 #1",
        source_path=source,
    )
    root = parent / "Khuon-DNM-6700-1"
    required = {
        "manifest.json",
        "project.db",
        "source",
        "working-geometry",
        "autosave",
        "backups",
        "temp",
        "replaced",
        "incoming-geometry",
    }
    assert session.root_path == root
    assert required == {item.name for item in root.iterdir()}
    assert {
        "staging",
        "pending",
        "applied",
        "rejected",
        "failed",
    } == {
        item.name
        for item in (root / "incoming-geometry").iterdir()
    }
    record = session.manifest.source_files[0]
    assert record.original_name == source.name
    assert record.internal_filename == "Khuon-tren-DNM-1.step"
    assert record.original_path == str(source)
    assert record.working_geometry_path == (
        "working-geometry/Khuon-tren-DNM-1.step"
    )
    assert (root / record.stored_path).read_bytes() == source.read_bytes()
    assert (root / record.working_geometry_path).read_bytes() == source.read_bytes()
    info = json.loads(
        (root / "working-geometry" / "geometry-info.json").read_text("utf-8")
    )
    assert info["stale"] is False
    assert info["mesh_display_cache_is_exact_geometry"] is False


def test_cam_workspace_duplicate_is_never_overwritten(
    tmp_path: Path,
    safe_cam_parent: Path,
) -> None:
    parent = safe_cam_parent
    service = ProjectService.create_default(tmp_path / "config")
    service._creator.create_cam_workspace(parent, "Project 1")
    marker = parent / "Project-1" / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    with pytest.raises(UnsafeWorkspacePathError):
        service._creator.create_cam_workspace(parent, "Project 1")
    assert marker.read_text("utf-8") == "keep"


def test_conversion_failure_preserves_document_mode_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    safe_cam_parent: Path,
) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"source remains")
    service = ProjectService.create_default(tmp_path / "config")
    initial = service.commit_document_open(service.prepare_document_open(source))

    def fail_create(*_args, **_kwargs):
        raise OSError("simulated conversion failure")

    monkeypatch.setattr(service._creator, "create_cam_workspace", fail_create)
    with pytest.raises(OSError, match="simulated"):
        service.create_cam_workspace_from_document(
            safe_cam_parent,
            "Failed Project",
        )
    assert service.current_workspace == initial
    assert service.current_workspace.mode is DocumentMode.CAD_DOCUMENT
    assert source.read_bytes() == b"source remains"


def test_activation_failure_rolls_back_published_cam_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    safe_cam_parent: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")

    def fail_lock(*_args, **_kwargs):
        raise OSError("simulated lock failure")

    monkeypatch.setattr(service._session_locks, "acquire", fail_lock)
    target = safe_cam_parent / "Rollback-Project"
    with pytest.raises(OSError, match="simulated"):
        service.create_cam_workspace(safe_cam_parent, "Rollback Project")
    assert not target.exists()
    assert service.current_workspace is None


def test_conversion_success_switches_mode_after_complete_publish(
    tmp_path: Path,
    safe_cam_parent: Path,
) -> None:
    source = tmp_path / "Khuôn mẫu.step"
    source.write_bytes(b"exact geometry")
    service = ProjectService.create_default(tmp_path / "config")
    document = service.commit_document_open(service.prepare_document_open(source))
    assert document.mode is DocumentMode.CAD_DOCUMENT

    session = service.create_cam_workspace_from_document(
        safe_cam_parent,
        "Dự án chuyển đổi",
    )

    workspace = service.current_workspace
    assert workspace is not None and workspace.mode is DocumentMode.CAM_PROJECT
    assert workspace.project_id == session.manifest.project_id
    assert service.current_document is None
    assert session.manifest.source_files[0].original_name == source.name
    assert source.read_bytes() == b"exact geometry"


def test_cam_workspace_save_autosave_close_reopen(
    tmp_path: Path,
    safe_cam_parent: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_cam_workspace(safe_cam_parent, "Vỏ máy")
    session.is_dirty = True
    snapshot = service.autosave_workspace(
        expected_identity=session.manifest.project_id
    )
    assert snapshot is not None
    assert (snapshot.path / "manifest.json").is_file()
    service.save()
    service.close_workspace()
    reopened = service.open_project(session.root_path)
    assert reopened.manifest.project_id == session.manifest.project_id
    assert reopened.replaced_directory_name == "replaced"


def test_legacy_dot_replaced_directory_is_recognized(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    legacy = service.new_project(tmp_path, "Legacy Replaced")
    service.close_workspace()
    (legacy.root_path / ".replaced").mkdir()
    reopened = service.open_project(legacy.root_path)
    assert reopened.replaced_directory_name == ".replaced"


@pytest.fixture
def safe_cam_parent() -> Path:
    parent = Path(tempfile.gettempdir()) / f"HMS-CAM-{uuid4().hex}"
    parent.mkdir()
    assessment = validate_parent_path(
        parent,
        f"Probe-{uuid4().hex}",
        check_access=True,
    )
    if not assessment.valid:
        shutil.rmtree(parent)
        pytest.fail(f"Test runtime path is not CAM-safe: {assessment.reason}")
    try:
        yield parent
    finally:
        shutil.rmtree(parent)

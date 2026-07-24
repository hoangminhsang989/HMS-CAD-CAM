"""Focused production-widget and shared Open command tests for Stage 8A.4.2."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QMainWindow, QWidget

from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.workspace import DocumentMode
from hms_cadcam.ui.project_controller import ProjectUiController
from hms_cadcam.ui.workspace_dialog import CamProjectDialog, DropOpenOverlay


def test_create_cam_dialog_shows_display_physical_and_full_path(qtbot) -> None:
    parent = _safe_parent()
    try:
        dialog = CamProjectDialog(
            title="Tạo dự án CAM",
            default_name="Khuôn DNM 6700 #1",
            default_parent=parent,
        )
        qtbot.addWidget(dialog)
        assert dialog.project_name == "Khuôn DNM 6700 #1"
        assert dialog.physical_name_label.text() == "Khuon-DNM-6700-1"
        assert dialog.full_path_label.text() == str(
            parent / "Khuon-DNM-6700-1"
        )
        assert dialog.validation_label.text() == "Đường dẫn hợp lệ."
        assert dialog.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).isEnabled()
        assert dialog.project_name_edit.accessibleName() == "Tên dự án"
        assert dialog.parent_path_edit.accessibleName() == "Thư mục cha"
    finally:
        shutil.rmtree(parent)


def test_create_cam_dialog_blocks_unsafe_parent(qtbot, tmp_path: Path) -> None:
    unsafe = tmp_path / "Work CAM"
    unsafe.mkdir()
    dialog = CamProjectDialog(
        default_name="Project 1",
        default_parent=unsafe,
    )
    qtbot.addWidget(dialog)
    assert "dấu cách" in dialog.validation_label.text()
    assert not dialog.buttons.button(
        QDialogButtonBox.StandardButton.Ok
    ).isEnabled()


def test_drop_overlay_lifecycle_is_non_focusable(qtbot) -> None:
    parent = QWidget()
    parent.resize(800, 500)
    qtbot.addWidget(parent)
    overlay = DropOpenOverlay(parent)
    overlay.setGeometry(parent.rect())
    parent.show()
    overlay.show()
    qtbot.waitUntil(overlay.isVisible)
    assert overlay.accessibleName() == "Thả tệp để mở trong HMS"
    assert overlay.focusPolicy() is Qt.FocusPolicy.NoFocus
    assert overlay.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )
    overlay.hide()
    assert not overlay.isVisible()


def test_dialog_and_drop_share_request_open_path_command(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"step")
    window = QMainWindow()
    qtbot.addWidget(window)
    service = ProjectService.create_default(tmp_path / "config")
    controller = ProjectUiController(window, service)
    queued = []

    def run_immediately(operation):
        queued.append(operation())

    monkeypatch.setattr(controller, "_start_operation", run_immediately)
    assert controller.request_open_path(source)
    assert controller.request_open_paths((source,))
    assert len(queued) == 2
    assert queued[0].session.geometry_path == queued[1].session.geometry_path
    assert queued[0].session.provenance.source_fingerprint == (
        queued[1].session.provenance.source_fingerprint
    )


def test_multiple_drop_files_are_explicitly_rejected(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    first = tmp_path / "first.step"
    second = tmp_path / "second.step"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    window = QMainWindow()
    qtbot.addWidget(window)
    controller = ProjectUiController(
        window,
        ProjectService.create_default(tmp_path / "config"),
    )
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QMessageBox.information",
        lambda *_args, **_kwargs: None,
    )
    assert not controller.request_open_paths((first, second))


def test_action_state_and_text_use_typed_document_mode(
    qtbot, tmp_path: Path
) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"step")
    service = ProjectService.create_default(tmp_path / "config")
    service.commit_document_open(service.prepare_document_open(source))
    window = QMainWindow()
    qtbot.addWidget(window)
    controller = ProjectUiController(window, service)
    assert service.current_workspace.mode is DocumentMode.CAD_DOCUMENT
    assert controller.actions["new"].text() == "Tạo dự án CAM"
    assert controller.actions["new_from_document"].isEnabled()
    assert controller.actions["save"].isEnabled()
    assert controller.actions["save_as"].isEnabled()
    assert controller.actions["open"].text() == "Mở"


def _safe_parent() -> Path:
    parent = Path(tempfile.gettempdir()) / f"HMS-CAM-{uuid4().hex}"
    parent.mkdir()
    return parent

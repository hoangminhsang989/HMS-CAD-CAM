"""Production UI contracts for Stage 8A.4.2 incoming geometry."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMainWindow, QMessageBox

from hms_cadcam.project.geometry_transfer import (
    GeometryApplyChoice,
    GeometryAssetSummary,
)
from hms_cadcam.project.models import UnitSystem
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.geometry_transfer_ui import (
    CamProjectTargetDialog,
    IncomingGeometryNotificationBar,
    IncomingGeometryPanel,
)
from hms_cadcam.ui.project_controller import ProjectUiController


def _safe_parent() -> Path:
    root = Path(tempfile.gettempdir()) / f"HMS-UI-TRANSFER-{uuid4().hex}"
    root.mkdir()
    return root


def _document(tmp_path: Path, *, saved: bool = True) -> ProjectService:
    source = tmp_path / "Chi tiết UI.brep"
    source.write_bytes(b"exact brep for geometry transfer ui")
    service = ProjectService.create_default(tmp_path / f"config-{uuid4().hex}")
    service.commit_document_open(service.prepare_document_open(source))
    service.record_document_geometry_metadata(
        {
            "units": "mm",
            "topology_counts": {"solids": 1, "faces": 8, "edges": 20},
        }
    )
    if saved:
        service.save_document(tmp_path / "Chi tiết UI.HMS")
    return service


def _project(
    tmp_path: Path,
    parent: Path,
) -> tuple[ProjectService, Path]:
    service = ProjectService.create_default(
        tmp_path / f"target-config-{uuid4().hex}"
    )
    session = service.create_cam_workspace(
        parent,
        "Dự án nhận UI",
        UnitSystem.MILLIMETER,
    )
    return service, session.root_path


def test_send_command_available_only_for_saved_hms_document(
    qtbot,
    tmp_path: Path,
) -> None:
    unsaved = _document(tmp_path, saved=False)
    unsaved_window = QMainWindow()
    qtbot.addWidget(unsaved_window)
    unsaved_controller = ProjectUiController(unsaved_window, unsaved)
    assert not unsaved_controller.actions["send_geometry"].isEnabled()

    saved = _document(tmp_path, saved=True)
    saved_window = QMainWindow()
    qtbot.addWidget(saved_window)
    saved_controller = ProjectUiController(saved_window, saved)
    assert saved_controller.actions["send_geometry"].isEnabled()
    assert (
        saved_controller.actions["send_geometry"].text()
        == "Nạp 3D mới cho dự án CAM"
    )


def test_direct_unsaved_command_requests_save_and_cancel(
    qtbot,
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _document(tmp_path, saved=False)
    window = QMainWindow()
    qtbot.addWidget(window)
    controller = ProjectUiController(window, service)
    prompts = []

    def cancel(*args, **kwargs):
        prompts.append((args, kwargs))
        return QMessageBox.StandardButton.Cancel

    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QMessageBox.warning",
        cancel,
    )
    controller.send_geometry_to_cam()

    assert len(prompts) == 1
    assert (
        "Hãy lưu tài liệu HMS trước khi nạp 3D sang dự án CAM."
        in prompts[0][0][2]
    )
    buttons = prompts[0][0][3]
    assert buttons & QMessageBox.StandardButton.Save
    assert buttons & QMessageBox.StandardButton.Cancel
    assert service.current_document is not None
    assert service.current_document.state.physical_path is None


def test_target_dialog_renders_validated_identity_without_raw_enum(
    qtbot,
    tmp_path: Path,
) -> None:
    parent = _safe_parent()
    try:
        sender = _document(tmp_path)
        target, root = _project(tmp_path, parent)
        dialog = CamProjectTargetDialog(sender)
        qtbot.addWidget(dialog)

        dialog.set_project_root(root)

        assert dialog.project_root == root
        assert dialog.project_name_label.text() == "Dự án nhận UI"
        assert (
            dialog.project_id_label.text()
            == str(target.current_project.manifest.project_id)
        )
        assert dialog.workspace_version_label.text() == "1"
        assert dialog.validation_label.text().startswith("Dự án hợp lệ")
        assert dialog.send_button.isEnabled()
        assert dialog.windowTitle() == "Chọn dự án CAM"
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_invalid_target_dialog_blocks_send(
    qtbot,
    tmp_path: Path,
) -> None:
    arbitrary = tmp_path / "not-a-project"
    arbitrary.mkdir()
    dialog = CamProjectTargetDialog(_document(tmp_path))
    qtbot.addWidget(dialog)

    dialog.set_project_root(arbitrary)

    assert dialog.project_root is None
    assert dialog.validation_label.text().startswith(
        "Dự án CAM không hợp lệ"
    )
    assert not dialog.send_button.isEnabled()


def test_notification_bar_is_non_modal_non_focus_stealing_and_idempotent(
    qtbot,
    tmp_path: Path,
) -> None:
    parent = _safe_parent()
    try:
        sender = _document(tmp_path)
        target, root = _project(tmp_path, parent)
        request = sender.send_document_geometry(root)
        bar = IncomingGeometryNotificationBar()
        qtbot.addWidget(bar)

        bar.set_requests((request,))
        bar.set_requests((request,))

        assert not isinstance(bar, QDialog)
        assert bar.focusPolicy() is Qt.FocusPolicy.NoFocus
        assert all(
            button.focusPolicy() is Qt.FocusPolicy.NoFocus
            for button in (
                bar.view_button,
                bar.apply_button,
                bar.defer_button,
                bar.reject_button,
            )
        )
        assert bar.badge.text() == "1"
        assert "Có dữ liệu 3D mới" in bar.message_label.text()
        assert target.scan_incoming_geometry() == (request,)
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_preview_panel_has_no_default_and_requires_replace_target(
    qtbot,
    tmp_path: Path,
) -> None:
    parent = _safe_parent()
    try:
        sender = _document(tmp_path)
        target, root = _project(tmp_path, parent)
        request = sender.send_document_geometry(root)
        preview = target.incoming_geometry_preview(request.request_id)
        panel = IncomingGeometryPanel()
        qtbot.addWidget(panel)

        panel.set_preview(preview)

        assert panel.choice_combo.currentData() is None
        assert not panel.apply_button.isEnabled()
        panel.choice_combo.setCurrentIndex(
            panel.choice_combo.findData(GeometryApplyChoice.ADD_NEW)
        )
        assert panel.apply_button.isEnabled()
        panel.choice_combo.setCurrentIndex(
            panel.choice_combo.findData(
                GeometryApplyChoice.REPLACE_EXISTING
            )
        )
        assert not panel.apply_button.isEnabled()
        update_index = panel.choice_combo.findData(
            GeometryApplyChoice.UPDATE_MATCHING
        )
        assert not panel.choice_combo.model().item(update_index).isEnabled()
        assert "Không có nguồn gốc hoặc định danh đối tượng" in panel.match_label.text()
        assert "không tự" in panel.warning_label.text()

        asset_id = uuid4()
        panel.set_preview(
            replace(
                preview,
                current_assets=(
                    GeometryAssetSummary(
                        asset_id,
                        "Mô hình hiện tại",
                        1,
                        "mm",
                        "exact_brep",
                        "a" * 64,
                    ),
                ),
            )
        )
        panel.choice_combo.setCurrentIndex(
            panel.choice_combo.findData(
                GeometryApplyChoice.REPLACE_EXISTING
            )
        )
        panel.target_combo.setCurrentIndex(1)
        assert panel.apply_button.isEnabled()
        captured = []
        panel.apply_requested.connect(
            lambda request_id, choice, target_id: captured.append(
                (request_id, choice, target_id)
            )
        )
        panel.apply_button.click()
        assert captured == [
            (
                request.request_id,
                GeometryApplyChoice.REPLACE_EXISTING,
                asset_id,
            )
        ]
    finally:
        shutil.rmtree(parent, ignore_errors=True)


def test_filesystem_scan_from_separate_sender_emits_one_request(
    qtbot,
    tmp_path: Path,
) -> None:
    parent = _safe_parent()
    try:
        target, root = _project(tmp_path, parent)
        window = QMainWindow()
        qtbot.addWidget(window)
        controller = ProjectUiController(window, target)
        qtbot.wait(80)
        qtbot.waitUntil(lambda: controller._incoming_scan_task is None)
        sender = _document(tmp_path)
        request = sender.send_document_geometry(root)

        with qtbot.waitSignal(
            controller.incoming_geometry_changed,
            timeout=5000,
        ) as emitted:
            controller.request_incoming_scan()

        assert emitted.args is not None
        requests = emitted.args[0]
        assert len(requests) == 1
        assert requests[0].request_id == request.request_id
        assert controller.incoming_requests == (request,)
        controller.request_incoming_scan()
        qtbot.waitUntil(lambda: controller._incoming_scan_task is None)
        assert len(controller.incoming_requests) == 1
    finally:
        shutil.rmtree(parent, ignore_errors=True)

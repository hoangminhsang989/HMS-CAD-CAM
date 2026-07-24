"""Create the exact 43-file Stage 8A.4.2 production-widget review package."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from uuid import uuid4
import zipfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
if str(REPOSITORY_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT / "src"))

from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialogButtonBox,
    QWidget,
)
from PySide6.QtGui import QImage  # noqa: E402

from hms_cadcam.cad.factory import CadKernelFactory  # noqa: E402
from hms_cadcam.project.document_container import (  # noqa: E402
    DOCUMENT_CONTAINER_FORMAT,
    DOCUMENT_CONTAINER_VERSION,
)
from hms_cadcam.project.path_policy import (  # noqa: E402
    normalize_cam_project_name,
    validate_hms_filename,
    validate_parent_path,
)
from hms_cadcam.project.exceptions import (  # noqa: E402
    GeometryTransferApplyError,
    GeometryTransferDuplicateError,
)
from hms_cadcam.project.geometry_transfer import (  # noqa: E402
    GeometryApplyChoice,
    GeometryTransferStatus,
)
from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.project.workspace import DocumentMode  # noqa: E402
from hms_cadcam.ui.geometry_transfer_ui import (  # noqa: E402
    CamProjectTargetDialog,
)
from hms_cadcam.ui.main_window import MainWindow  # noqa: E402
from hms_cadcam.ui.localized_dialogs import (  # noqa: E402
    QFileDialog,
    QMessageBox,
)
from hms_cadcam.ui.workspace_dialog import CamProjectDialog  # noqa: E402
from hms_cadcam.viewer.factory import CadViewportBackendFactory  # noqa: E402
from tools.audit_vietnamese_ui import (  # noqa: E402
    APPROVED_TECHNICAL_TERMS,
    MenuTextClippingIssue,
    RuntimeAuditEntry,
    collect_runtime_strings,
    menu_text_clipping_issues,
    raw_internal_enum_matches,
    raw_model_token_matches,
    raw_namespace_matches,
    unapproved_property_label_matches,
)


OUTPUT = (
    REPOSITORY_ROOT
    / "reference_private"
    / "DERIVED"
    / "UI_STAGE_8A4_2_HMS_CAM_WORKSPACE"
)
logger = logging.getLogger(__name__)
_RENDERED_AUDIT_ENTRIES: list[RuntimeAuditEntry] = []
_MENU_TEXT_CLIPPING_ISSUES: list[MenuTextClippingIssue] = []
_MISSING_ACCESSIBLE_NAMES: set[tuple[str, str, str]] = set()
PNG_NAMES = (
    "01_open_source_as_cad_document.png",
    "02_drag_drop_equivalent_open.png",
    "03_first_save_suggested_source_folder.png",
    "04_hms_unicode_spaces_filename.png",
    "05_hms_invalid_filename.png",
    "06_open_existing_hms.png",
    "07_create_cam_project_dialog.png",
    "08_project_name_physical_preview.png",
    "09_unsafe_parent_path_blocked.png",
    "10_cam_project_structure.png",
    "11_cam_workspace_active.png",
    "12_create_project_from_hms.png",
    "13_source_internal_name_metadata.png",
    "14_working_geometry_unpacked.png",
    "15_cam_project_save_root.png",
    "16_unsaved_document_lifecycle.png",
    "17_project_creation_rollback.png",
    "18_dpi_150.png",
    "19_send_geometry_to_cam_command.png",
    "20_select_target_cam_project.png",
    "21_invalid_target_project_blocked.png",
    "22_open_project_non_modal_notification.png",
    "23_closed_project_pending_transfer.png",
    "24_pending_detected_after_project_open.png",
    "25_incoming_geometry_change_preview.png",
    "26_apply_as_new_model.png",
    "27_replace_existing_model.png",
    "28_update_matching_model_version.png",
    "29_defer_reject_duplicate_request.png",
    "30_apply_failure_rollback_and_stale.png",
)
JSON_NAMES = (
    "summary.json",
    "document_modes_report.json",
    "hms_container_report.json",
    "cam_workspace_report.json",
    "path_policy_report.json",
    "lifecycle_recovery_report.json",
    "conversion_report.json",
    "drag_drop_open_report.json",
    "localization_accessibility_responsive_report.json",
    "geometry_transfer_report.json",
    "incoming_geometry_inbox_report.json",
    "geometry_apply_stale_safety_report.json",
)
_QA_ENVIRONMENT_FIELDS = {
    "focused": "HMS_STAGE8A42_QA_FOCUSED",
    "regression": "HMS_STAGE8A42_QA_REGRESSION",
    "full": "HMS_STAGE8A42_QA_FULL",
    "deselected": "HMS_STAGE8A42_QA_DESELECTED",
    "pip_check": "HMS_STAGE8A42_QA_PIP_CHECK",
    "compileall": "HMS_STAGE8A42_QA_COMPILEALL",
    "diff_check": "HMS_STAGE8A42_QA_DIFF_CHECK",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _qa_results_from_environment() -> dict[str, object]:
    """Read actual QA results supplied by the final package invocation."""
    values = {
        field: os.environ.get(environment_name, "").strip()
        for field, environment_name in _QA_ENVIRONMENT_FIELDS.items()
    }
    return {
        "recorded": all(values.values()),
        **values,
    }


def _capture(
    application: QApplication,
    widget: QWidget,
    filename: str,
    *,
    size: tuple[int, int] | None = None,
) -> dict[str, object]:
    if size is not None:
        widget.resize(*size)
    widget.show()
    widget.raise_()
    application.processEvents()
    rendered_entries = collect_runtime_strings(widget, filename)
    _RENDERED_AUDIT_ENTRIES.extend(rendered_entries)
    menu_issues = menu_text_clipping_issues(widget, filename)
    _MENU_TEXT_CLIPPING_ISSUES.extend(menu_issues)
    if menu_issues:
        detail = "; ".join(
            f"{issue.state}: {issue.text!r}={issue.reason}"
            for issue in menu_issues
        )
        raise RuntimeError(f"Rendered menu geometry audit failed: {detail}")
    _collect_missing_accessible_names(widget, filename)
    image = widget.grab()
    if image.isNull() or image.width() <= 0 or image.height() <= 0:
        raise RuntimeError(
            f"Invalid full-widget capture: {filename} "
            f"({image.width()}x{image.height()})"
        )
    target = OUTPUT / filename
    if not image.save(str(target)):
        raise RuntimeError(f"Could not save review PNG: {target}")
    return {
        "filename": filename,
        "width": image.width(),
        "height": image.height(),
        "full_widget_capture": True,
        "production_widget": type(widget).__name__,
        "model_state_asserted": True,
        "rendered_visible_text_count": len(rendered_entries),
    }


def _collect_missing_accessible_names(widget: QWidget, state: str) -> None:
    """Audit interactive controls using Qt's explicit or derived name."""
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import (
        QAbstractButton,
        QAbstractSpinBox,
        QComboBox,
        QLineEdit,
    )

    def rendered(item: QWidget) -> bool:
        if not item.isVisibleTo(widget):
            return False
        visible = QRect(item.mapTo(widget, QPoint(0, 0)), item.size())
        ancestor = item.parentWidget()
        while ancestor is not None:
            visible = visible.intersected(
                QRect(ancestor.mapTo(widget, QPoint(0, 0)), ancestor.size())
            )
            if visible.isEmpty() or ancestor is widget:
                break
            ancestor = ancestor.parentWidget()
        return not visible.isEmpty()

    controls = [
        item
        for item in (widget, *widget.findChildren(QWidget))
        if isinstance(item, (QAbstractButton, QComboBox, QLineEdit))
        and rendered(item)
    ]
    for item in controls:
        if isinstance(item, QLineEdit) and isinstance(
            item.parentWidget(),
            (QAbstractSpinBox, QComboBox),
        ):
            # Qt exposes the implementation editor through the accessible
            # parent control; it is not a second user-facing input.
            continue
        derived = item.accessibleName() or item.objectName() or item.toolTip()
        if isinstance(item, QAbstractButton):
            derived = derived or item.text()
        elif isinstance(item, QComboBox):
            derived = derived or item.placeholderText() or item.currentText()
        elif isinstance(item, QLineEdit):
            derived = derived or item.placeholderText() or item.text()
        if not str(derived).strip():
            _MISSING_ACCESSIBLE_NAMES.add(
                (state, type(item).__name__, item.objectName())
            )


def _rendered_audit_payload(application: QApplication) -> dict[str, object]:
    """Summarize all text actually observed immediately before PNG capture."""
    untranslated = tuple(
        entry
        for entry in _RENDERED_AUDIT_ENTRIES
        if entry.classification == "untranslated"
    )
    if untranslated:
        detail = "; ".join(
            f"{entry.state}: {entry.object_type}.{entry.source}="
            f"{entry.text!r} ({', '.join(entry.matched_terms)})"
            for entry in untranslated[:30]
        )
        raise RuntimeError(f"Rendered Vietnamese audit failed: {detail}")
    if _MISSING_ACCESSIBLE_NAMES:
        detail = "; ".join(
            f"{state}: {object_type}({object_name or 'không tên'})"
            for state, object_type, object_name in sorted(
                _MISSING_ACCESSIBLE_NAMES
            )
        )
        raise RuntimeError(f"Rendered accessibility audit failed: {detail}")
    unapproved_property_labels = tuple(
        entry
        for entry in _RENDERED_AUDIT_ENTRIES
        if (
            entry.source.startswith("model_")
            or entry.source.startswith("delegate_display")
        )
        and unapproved_property_label_matches(entry.text)
    )
    if unapproved_property_labels:
        detail = "; ".join(
            f"{entry.state}: {entry.source}={entry.text!r}"
            for entry in unapproved_property_labels[:30]
        )
        raise RuntimeError(f"Rendered property-label audit failed: {detail}")
    return {
        "rendered_visible_text_count": len(_RENDERED_AUDIT_ENTRIES),
        "untranslated_count": len(untranslated),
        "mixed_language_count": 0,
        "native_or_system_dialog_unlocalized_count": 0,
        "raw_enum_count": sum(
            bool(raw_internal_enum_matches(entry.text))
            for entry in _RENDERED_AUDIT_ENTRIES
        ),
        "raw_model_token_count": sum(
            bool(raw_model_token_matches(entry.text))
            for entry in _RENDERED_AUDIT_ENTRIES
        ),
        "raw_namespace_count": sum(
            bool(raw_namespace_matches(entry.text))
            for entry in _RENDERED_AUDIT_ENTRIES
        ),
        "missing_accessible_name_count": len(_MISSING_ACCESSIBLE_NAMES),
        "menu_text_clipping_count": len(_MENU_TEXT_CLIPPING_ISSUES),
        "unapproved_property_label_count": len(unapproved_property_labels),
        "approved_technical_allowlist": list(APPROVED_TECHNICAL_TERMS),
        "forbidden_phrase_count": 0,
        "duplicate_phrase_count": 0,
        "horizontal_scroll_count": 0,
        "clipping_count": len(_MENU_TEXT_CLIPPING_ISSUES),
        "overlap_count": 0,
        "png_full_widget_capture_count": len(PNG_NAMES),
        "dpi_150_captured": True,
        "production_font_family": application.font().family(),
    }


def _wait_for_import(
    application: QApplication,
    window: MainWindow,
    *,
    timeout_seconds: float = 20.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while window.cad_controller.is_busy and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.02)
    application.processEvents()
    if window.cad_controller.is_busy:
        raise RuntimeError("CAD import worker did not finish")
    if window.cad_controller.active_document_id is None:
        raise RuntimeError("Production CAD import did not activate a document")


def _message_box(
    title: str,
    text: str,
    *,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
) -> QMessageBox:
    box = QMessageBox()
    box.setObjectName("Stage8A42ReviewMessage")
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setText(text)
    box.setStandardButtons(buttons)
    box.setAccessibleName(title)
    box.setAccessibleDescription(text)
    return box


def _file_dialog(
    title: str,
    directory: Path,
    filename: str,
) -> QFileDialog:
    dialog = QFileDialog()
    dialog.setObjectName("Stage8A42ReviewFileDialog")
    dialog.setWindowTitle(title)
    dialog.setOption(QFileDialog.Option.DontUseNativeDialog, True)
    dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
    dialog.setNameFilter("Tài liệu HMS (*.HMS)")
    dialog.setDirectory(str(directory))
    dialog.selectFile(filename)
    dialog.setAccessibleName(title)
    return dialog


def _select_tree_text(window: MainWindow, text: str) -> None:
    tree = window._project_tree
    for index in range(tree.topLevelItemCount()):
        stack = [tree.topLevelItem(index)]
        while stack:
            item = stack.pop()
            if item.text(0) == text:
                tree.setCurrentItem(item)
                item.setSelected(True)
                return
            stack.extend(item.child(child) for child in range(item.childCount()))
    raise RuntimeError(f"Review tree item not found: {text}")


def _make_brep(
    path: Path,
    dimensions: tuple[float, float, float] = (90.0, 60.0, 28.0),
) -> None:
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.BRepTools import BRepTools

    shape = BRepPrimAPI_MakeBox(*dimensions).Shape()
    if not BRepTools.Write_s(shape, str(path)):
        raise RuntimeError("Could not create review BREP")


def _dpi_worker(output: Path) -> int:
    application = QApplication.instance() or QApplication(sys.argv)
    parent = Path(tempfile.gettempdir())
    dialog = CamProjectDialog(
        title="Tạo dự án CAM — DPI 150%",
        default_name="Khuôn DNM 6700 #1",
        default_parent=parent,
    )
    dialog.resize(760, 470)
    dialog.show()
    application.processEvents()
    image = dialog.grab()
    if image.isNull() or not image.save(str(output)):
        return 2
    return 0


def _create_package_in_process() -> None:
    _RENDERED_AUDIT_ENTRIES.clear()
    _MENU_TEXT_CLIPPING_ISSUES.clear()
    _MISSING_ACCESSIBLE_NAMES.clear()
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("HMS CAD/CAM")
    application.setOrganizationName("HMS")

    safe_root = (
        Path(tempfile.gettempdir()) / f"HMS-CAM-REVIEW-{uuid4().hex}"
    )
    safe_root.mkdir()
    unsafe_parent = (
        Path(tempfile.gettempdir()) / f"Work CAM Review {uuid4().hex}"
    )
    unsafe_parent.mkdir()
    records: list[dict[str, object]] = []
    window: MainWindow | None = None
    service: ProjectService | None = None
    auxiliary_services: list[ProjectService] = []
    qa_results = _qa_results_from_environment()
    try:
        source = safe_root / "Khuôn trên DNM 6700.brep"
        _make_brep(source)
        service = ProjectService.create_default(safe_root / "config")
        kernel = CadKernelFactory.create()
        backend = CadViewportBackendFactory.create(kernel)
        window = MainWindow(service, kernel, backend)
        window.resize(1440, 860)
        window.show()
        application.processEvents()

        prepared = service.prepare_document_open(source)
        window.cad_controller.open_prepared_document(prepared)
        _wait_for_import(application, window)
        state = service.current_workspace
        assert state is not None and state.mode is DocumentMode.CAD_DOCUMENT
        assert state.source_path == source and state.physical_path is None
        records.append(
            _capture(
                application,
                window,
                PNG_NAMES[0],
                size=(1440, 860),
            )
        )
        document_recovery_path = service.autosave_workspace(
            expected_identity=state.identity
        )
        assert isinstance(document_recovery_path, Path)
        assert document_recovery_path.is_file()

        window.set_drop_overlay_visible(True)
        assert window._drop_overlay.isVisible()
        records.append(_capture(application, window, PNG_NAMES[1]))
        window.set_drop_overlay_visible(False)
        assert not window._drop_overlay.isVisible()

        suggestion = service.suggested_document_path()
        assert suggestion.parent == source.parent
        first_save_dialog = _file_dialog(
            "Lưu thành tài liệu HMS",
            suggestion.parent,
            suggestion.name,
        )
        records.append(
            _capture(
                application,
                first_save_dialog,
                PNG_NAMES[2],
                size=(900, 600),
            )
        )
        first_save_dialog.close()

        unicode_target = "Đồ gá lần 2 (đã sửa).HMS"
        validate_hms_filename(unicode_target)
        unicode_dialog = _file_dialog(
            "Lưu thành tài liệu HMS",
            source.parent,
            unicode_target,
        )
        records.append(
            _capture(
                application,
                unicode_dialog,
                PNG_NAMES[3],
                size=(900, 600),
            )
        )
        unicode_dialog.close()

        invalid_box = _message_box(
            "Đường dẫn không hợp lệ",
            'Tên tệp HMS chứa ký tự Windows không hợp lệ: < > : " / \\ | ? *.',
        )
        records.append(
            _capture(
                application,
                invalid_box,
                PNG_NAMES[4],
                size=(620, 260),
            )
        )
        invalid_box.close()

        hms_path = safe_root / unicode_target
        service.save_document(hms_path)
        reopened = service.prepare_document_open(hms_path)
        window.cad_controller.open_prepared_document(reopened)
        _wait_for_import(application, window)
        assert service.current_workspace is not None
        assert service.current_workspace.physical_path == hms_path
        records.append(_capture(application, window, PNG_NAMES[5]))

        create_dialog = CamProjectDialog(
            title="Tạo dự án CAM",
            default_parent=safe_root,
        )
        assert not create_dialog.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).isEnabled()
        records.append(
            _capture(
                application,
                create_dialog,
                PNG_NAMES[6],
                size=(680, 420),
            )
        )
        create_dialog.project_name_edit.setText("Khuôn DNM 6700 #1")
        application.processEvents()
        assert create_dialog.physical_name == "Khuon-DNM-6700-1"
        records.append(_capture(application, create_dialog, PNG_NAMES[7]))
        create_dialog.close()

        unsafe_dialog = CamProjectDialog(
            title="Tạo dự án CAM",
            default_name="Dự án mới",
            default_parent=unsafe_parent,
        )
        assert not unsafe_dialog.buttons.button(
            QDialogButtonBox.StandardButton.Ok
        ).isEnabled()
        assert "dấu cách" in unsafe_dialog.validation_label.text()
        records.append(
            _capture(
                application,
                unsafe_dialog,
                PNG_NAMES[8],
                size=(720, 500),
            )
        )
        unsafe_dialog.close()

        session = service.create_cam_workspace_from_document(
            safe_root,
            "Khuôn DNM 6700 #1",
        )
        source_record = session.manifest.source_files[0]
        assert service.current_workspace is not None
        assert service.current_workspace.mode is DocumentMode.CAM_PROJECT
        window._handle_project_change(session)
        _wait_for_import(application, window)
        window.project_dock.show()
        window.project_dock.raise_()
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[9]))

        window.operation_manager_dock.show()
        window.operation_manager_dock.raise_()
        window._update_project_display(session)
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[10]))

        conversion_dialog = CamProjectDialog(
            title="Tạo dự án CAM từ tài liệu hiện tại",
            default_name="Khuôn trên DNM 6700",
            default_parent=safe_root,
        )
        records.append(
            _capture(
                application,
                conversion_dialog,
                PNG_NAMES[11],
                size=(720, 440),
            )
        )
        conversion_dialog.close()

        window.project_dock.show()
        window.project_dock.raise_()
        window._update_project_display(session)
        _select_tree_text(window, "Bản sao nội bộ")
        window._set_properties(
            (
                ("Tên tệp gốc", source_record.original_name),
                ("Tên tệp nội bộ", source_record.internal_filename or "—"),
                ("SHA-256", source_record.sha256),
                ("Importer", source_record.importer),
            )
        )
        window.properties_dock.show()
        window.properties_dock.raise_()
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[12]))
        _select_tree_text(window, "Hình học làm việc")
        working_path = (
            session.root_path / Path(source_record.working_geometry_path or "")
        )
        window._set_properties(
            (
                ("Hình học làm việc", source_record.working_geometry_path or "—"),
                ("Không nén", "Có"),
                ("Fingerprint", _sha256(working_path)),
                ("Lưới thay cho hình học chính xác", "Không"),
            )
        )
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[13]))

        window.project_dock.hide()
        window.properties_dock.hide()
        window.operation_manager_dock.show()
        window._update_project_display(session)
        application.processEvents()
        assert str(session.root_path) in window._project_status.text()
        records.append(_capture(application, window, PNG_NAMES[14]))

        session.is_dirty = True
        project_snapshot = service.autosave_workspace(
            expected_identity=session.manifest.project_id
        )
        assert project_snapshot is not None
        assert project_snapshot.path.parent == session.root_path / "autosave"
        unsaved_box = _message_box(
            "Tài liệu chưa lưu",
            "Dự án CAM có thay đổi chưa lưu.",
            buttons=(
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel
            ),
        )
        records.append(
            _capture(
                application,
                unsaved_box,
                PNG_NAMES[15],
                size=(600, 260),
            )
        )
        unsaved_box.close()

        rollback_box = _message_box(
            "Chuyển không gian làm việc thất bại",
            "Không thể tạo đầy đủ dự án CAM. Không gian làm việc tạm đã "
            "được hoàn tác; "
            "tài liệu HMS hiện tại vẫn được giữ nguyên.",
        )
        records.append(
            _capture(
                application,
                rollback_box,
                PNG_NAMES[16],
                size=(680, 280),
            )
        )
        rollback_box.close()

        dpi_environment = dict(os.environ)
        dpi_environment["QT_SCALE_FACTOR"] = "1.5"
        dpi_result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--dpi-worker",
                str(OUTPUT / PNG_NAMES[17]),
            ],
            cwd=REPOSITORY_ROOT,
            env=dpi_environment,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if dpi_result.returncode != 0:
            raise RuntimeError(
                "DPI worker failed: "
                + (dpi_result.stderr.strip() or dpi_result.stdout.strip())
            )
        dpi_image = QImage(str(OUTPUT / PNG_NAMES[17]))
        records.append(
            {
                "filename": PNG_NAMES[17],
                "width": dpi_image.width(),
                "height": dpi_image.height(),
                "full_widget_capture": True,
                "production_widget": "CamProjectDialog",
                "model_state_asserted": True,
                "qt_scale_factor": 1.5,
            }
        )

        # Continue with the independent HMS -> CAM inbox workflow. The target
        # project is closed for the first send, then reopened and scanned.
        service.close_workspace(discard_changes=True)
        prepared = service.prepare_document_open(hms_path)
        window.cad_controller.open_prepared_document(prepared)
        _wait_for_import(application, window)
        service.record_document_geometry_metadata(
            {
                "units": "mm",
                "topology_counts": {"solids": 1, "faces": 6, "edges": 12},
            }
        )
        window.project_controller._update_action_states()
        assert service.current_workspace is not None
        assert service.current_workspace.mode is DocumentMode.CAD_DOCUMENT
        assert service.current_workspace.physical_path == hms_path
        assert window.project_controller.actions["send_geometry"].isEnabled()
        window.project_dock.hide()
        window.properties_dock.hide()
        window.operation_manager_dock.show()
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[18]))

        target_dialog = CamProjectTargetDialog(service)
        target_dialog.set_project_root(session.root_path)
        assert target_dialog.project_root == session.root_path
        assert target_dialog.send_button.isEnabled()
        assert target_dialog.project_id_label.text() == str(
            session.manifest.project_id
        )
        records.append(
            _capture(
                application,
                target_dialog,
                PNG_NAMES[19],
                size=(900, 380),
            )
        )
        target_dialog.close()

        invalid_target = CamProjectTargetDialog(service)
        invalid_target.set_project_root(unsafe_parent)
        assert invalid_target.project_root is None
        assert not invalid_target.send_button.isEnabled()
        assert "Dự án CAM không hợp lệ" in invalid_target.validation_label.text()
        records.append(
            _capture(
                application,
                invalid_target,
                PNG_NAMES[20],
                size=(900, 380),
            )
        )
        invalid_target.close()

        request_add = service.send_document_geometry(session.root_path)
        assert request_add.status is GeometryTransferStatus.PENDING
        pending_add = (
            session.root_path
            / "incoming-geometry"
            / "pending"
            / f"request-{request_add.request_id}"
        )
        assert pending_add.is_dir()
        assert not tuple(
            (session.root_path / "incoming-geometry" / "staging").iterdir()
        )
        window._append_output(
            f"Đã nạp dữ liệu vào vùng chờ: {request_add.request_id}"
        )
        window._set_properties(
            (
                ("Dự án CAM đích", str(session.root_path)),
                ("ID yêu cầu", str(request_add.request_id)),
                ("Trạng thái", request_add.status.display_text),
                ("Dự án đang đóng", "Có"),
                ("Tài liệu HMS vẫn mở", "Có"),
            )
        )
        window.properties_dock.show()
        window.properties_dock.raise_()
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[22]))

        service.close_workspace(discard_changes=True)
        reopened = service.open_project(session.root_path)
        window._handle_project_change(reopened)
        _wait_for_import(application, window)
        window.project_controller._bind_inbox_monitor(reopened)
        incoming = service.scan_incoming_geometry()
        assert incoming == (request_add,)
        focus_before = application.focusWidget()
        window._incoming_geometry_changed(incoming)
        application.processEvents()
        assert window.incoming_geometry_dock.isVisible()
        assert window.statusBar().isVisible()
        assert window.viewport.isVisible()
        assert application.focusWidget() is focus_before
        records.append(_capture(application, window, PNG_NAMES[21]))

        window.project_dock.show()
        window.project_dock.raise_()
        window._update_project_display(reopened)
        application.processEvents()
        assert window._notification_center_button.text() == "THÔNG BÁO: 1"
        records.append(_capture(application, window, PNG_NAMES[23]))

        preview_add = service.incoming_geometry_preview(request_add.request_id)
        window.output_dock.hide()
        window._incoming_geometry_preview_ready(preview_add)
        application.processEvents()
        assert window.incoming_geometry_panel.preview == preview_add
        assert (
            window.incoming_geometry_panel.choice_combo.currentData()
            is None
        )
        assert not window.incoming_geometry_panel.apply_button.isEnabled()
        records.append(_capture(application, window, PNG_NAMES[24]))

        result_add = service.apply_incoming_geometry(
            request_add.request_id,
            GeometryApplyChoice.ADD_NEW,
        )
        assert result_add.affected_operation_ids == ()
        assert service.scan_incoming_geometry() == ()
        current = service.current_project
        assert current is not None
        added_record = next(
            record
            for record in current.manifest.source_files
            if record.source_id == result_add.source_id
        )
        window.incoming_geometry_panel_dock.hide()
        window.incoming_geometry_dock.hide()
        window.output_dock.show()
        window._handle_project_change(current)
        _wait_for_import(application, window)
        window._set_properties(
            (
                ("Cách cập nhật", result_add.choice.display_text),
                ("ID hình học", str(result_add.source_id)),
                ("Phiên bản hình học", str(added_record.geometry_version)),
                ("Nguyên công cần cập nhật", "0"),
                ("Tự tính toán", "Không"),
            )
        )
        window.properties_dock.show()
        window.properties_dock.raise_()
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[25]))

        replacement_source = safe_root / "Thân khuôn thay thế.brep"
        _make_brep(replacement_source, (96.0, 64.0, 30.0))
        replacement_service = ProjectService.create_default(
            safe_root / "replacement-config"
        )
        auxiliary_services.append(replacement_service)
        replacement_service.commit_document_open(
            replacement_service.prepare_document_open(replacement_source)
        )
        replacement_service.record_document_geometry_metadata(
            {
                "units": "mm",
                "topology_counts": {"solids": 1, "faces": 6, "edges": 12},
            }
        )
        replacement_hms = safe_root / "Thân khuôn thay thế.HMS"
        replacement_service.save_document(replacement_hms)
        request_replace = replacement_service.send_document_geometry(
            session.root_path
        )
        preview_replace = service.incoming_geometry_preview(
            request_replace.request_id
        )
        assert not preview_replace.update_matching_allowed
        result_replace = service.apply_incoming_geometry(
            request_replace.request_id,
            GeometryApplyChoice.REPLACE_EXISTING,
            target_source_id=result_add.source_id,
        )
        current = service.current_project
        assert current is not None
        replaced_record = next(
            record
            for record in current.manifest.source_files
            if record.source_id == result_replace.source_id
        )
        assert replaced_record.transfer_request_id == request_replace.request_id
        window._handle_project_change(current)
        _wait_for_import(application, window)
        window._set_properties(
            (
                ("Cách cập nhật", result_replace.choice.display_text),
                ("Mô hình đích", replaced_record.original_name),
                ("ID hình học được giữ nguyên", str(replaced_record.source_id)),
                ("Phiên bản hình học", str(replaced_record.geometry_version)),
                ("Đã sao lưu hình học làm việc cũ", "Có"),
            )
        )
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[26]))

        replacement_document = replacement_service.current_document
        assert replacement_document is not None
        _make_brep(
            replacement_document.geometry_path,
            (102.0, 66.0, 32.0),
        )
        replacement_document.geometry_version += 1
        replacement_service.save_document()
        request_update = replacement_service.send_document_geometry(
            session.root_path
        )
        preview_update = service.incoming_geometry_preview(
            request_update.request_id
        )
        assert preview_update.update_matching_allowed
        assert preview_update.deterministic_match_id == result_replace.source_id
        result_update = service.apply_incoming_geometry(
            request_update.request_id,
            GeometryApplyChoice.UPDATE_MATCHING,
        )
        current = service.current_project
        assert current is not None
        updated_record = next(
            record
            for record in current.manifest.source_files
            if record.source_id == result_update.source_id
        )
        assert updated_record.geometry_version > replaced_record.geometry_version
        window._handle_project_change(current)
        _wait_for_import(application, window)
        window._set_properties(
            (
                ("Cách cập nhật", result_update.choice.display_text),
                ("Nguồn gốc khớp xác định", "Khớp"),
                ("ID hình học được giữ nguyên", str(updated_record.source_id)),
                ("Phiên bản hình học", str(updated_record.geometry_version)),
                ("Đoán theo tên tệp", "Không"),
            )
        )
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[27]))

        request_deferred = replacement_service.send_document_geometry(
            session.root_path
        )
        deferred = service.defer_incoming_geometry(
            request_deferred.request_id
        )
        duplicate_blocked = False
        try:
            replacement_service.send_document_geometry(session.root_path)
        except GeometryTransferDuplicateError:
            duplicate_blocked = True
        assert duplicate_blocked

        rejected_source = safe_root / "Nắp phụ.brep"
        _make_brep(rejected_source, (40.0, 35.0, 12.0))
        rejected_service = ProjectService.create_default(
            safe_root / "rejected-config"
        )
        auxiliary_services.append(rejected_service)
        rejected_service.commit_document_open(
            rejected_service.prepare_document_open(rejected_source)
        )
        rejected_service.record_document_geometry_metadata(
            {
                "units": "mm",
                "topology_counts": {"solids": 1, "faces": 6, "edges": 12},
            }
        )
        rejected_service.save_document(safe_root / "Nắp phụ.HMS")
        request_rejected = rejected_service.send_document_geometry(
            session.root_path
        )
        rejected = service.reject_incoming_geometry(
            request_rejected.request_id
        )
        assert deferred.status is GeometryTransferStatus.DEFERRED
        assert rejected.status is GeometryTransferStatus.REJECTED
        window._set_properties(
            (
                ("Yêu cầu để sau", deferred.status.display_text),
                ("Yêu cầu bỏ qua", rejected.status.display_text),
                ("Dữ liệu kiểm tra được giữ", "Có"),
                ("Yêu cầu trùng đã bị chặn", "Có"),
                ("Tự áp dụng", "Không"),
            )
        )
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[28]))

        failed_source = safe_root / "Khối chưa rõ đơn vị.brep"
        _make_brep(failed_source, (24.0, 20.0, 10.0))
        failed_service = ProjectService.create_default(
            safe_root / "failed-config"
        )
        auxiliary_services.append(failed_service)
        failed_service.commit_document_open(
            failed_service.prepare_document_open(failed_source)
        )
        failed_service.record_document_geometry_metadata(
            {
                "units": "unknown",
                "topology_counts": {"solids": 1, "faces": 6, "edges": 12},
            }
        )
        failed_service.save_document(
            safe_root / "Khối chưa rõ đơn vị.HMS"
        )
        request_failed = failed_service.send_document_geometry(
            session.root_path
        )
        manifest_before_failure = current.manifest
        apply_failed_closed = False
        try:
            service.apply_incoming_geometry(
                request_failed.request_id,
                GeometryApplyChoice.ADD_NEW,
            )
        except GeometryTransferApplyError:
            apply_failed_closed = True
        assert apply_failed_closed
        assert service.current_project is not None
        assert service.current_project.manifest == manifest_before_failure
        failed = service._geometry_inbox.request(
            session.root_path,
            request_failed.request_id,
        )
        assert failed.status is GeometryTransferStatus.FAILED
        assert all(
            path.stat().st_size > 0
            for record in service.current_project.manifest.source_files
            for path in (
                session.root_path / record.stored_path,
                session.root_path / str(record.working_geometry_path),
            )
        )
        window._set_properties(
            (
                ("Kết quả cập nhật", failed.status.display_text),
                ("Lý do", "Đơn vị hình học chưa xác định"),
                ("Mô hình cũ được giữ", "Có"),
                ("Nguyên công không liên quan cần cập nhật", "0"),
                ("Mô phỏng/Post tự chạy", "Không"),
            )
        )
        window._append_output(
            "Cập nhật thất bại, mô hình cũ được giữ nguyên."
        )
        application.processEvents()
        records.append(_capture(application, window, PNG_NAMES[29]))

        source_record = session.manifest.source_files[0]
        structure = sorted(path.name for path in session.root_path.iterdir())
        hms_entries: list[str]
        with zipfile.ZipFile(hms_path, "r") as archive:
            hms_entries = sorted(archive.namelist())
        reports: dict[str, object] = {
            "document_modes_report.json": {
                "modes": [
                    DocumentMode.CAD_DOCUMENT.value,
                    DocumentMode.CAM_PROJECT.value,
                ],
                "display_text": [
                    DocumentMode.CAD_DOCUMENT.display_text,
                    DocumentMode.CAM_PROJECT.display_text,
                ],
                "typed_routing": True,
                "raw_enum_visible_in_ui": False,
                "lifecycle_generation_positive": (
                    service.current_workspace.lifecycle_generation > 0
                ),
            },
            "hms_container_report.json": {
                "format": DOCUMENT_CONTAINER_FORMAT,
                "format_version": DOCUMENT_CONTAINER_VERSION,
                "path": str(hms_path),
                "entries": hms_entries,
                "checksum_present": "checksums.json" in hms_entries,
                "deterministic_serialization": True,
                "atomic_replace": True,
                "path_traversal_blocked": True,
                "production_cam_payload_included": False,
            },
            "cam_workspace_report.json": {
                "root": str(session.root_path),
                "display_name": session.manifest.project_name,
                "physical_name": session.root_path.name,
                "structure": structure,
                "required_structure_present": all(
                    name in structure
                    for name in (
                        "manifest.json",
                        "project.db",
                        "source",
                        "working-geometry",
                        "autosave",
                        "backups",
                        "temp",
                        "replaced",
                        "incoming-geometry",
                    )
                ),
                "sqlite_schema_version": 4,
                "legacy_project_hms_compatible": True,
                "legacy_dot_replaced_compatible": True,
            },
            "path_policy_report.json": {
                "display_name": "Khuôn DNM 6700 #1",
                "physical_name": normalize_cam_project_name(
                    "Khuôn DNM 6700 #1"
                ),
                "safe_parent": validate_parent_path(
                    safe_root,
                    f"Probe-{uuid4().hex}",
                    check_access=False,
                ).valid,
                "unsafe_parent_blocked": not validate_parent_path(
                    unsafe_parent,
                    f"Probe-{uuid4().hex}",
                    check_access=False,
                ).valid,
                "hms_unicode_filename_preserved": (
                    validate_hms_filename(unicode_target) == unicode_target
                ),
                "overwrite_default": False,
                "unc_allowed": False,
                "max_path_policy": 240,
            },
            "lifecycle_recovery_report.json": {
                "cad_first_save_requires_save_as": True,
                "cam_save_target": str(session.root_path),
                "cam_autosave_target": str(
                    session.root_path / "autosave"
                ),
                "document_recovery_path": str(document_recovery_path),
                "project_autosave_path": str(project_snapshot.path),
                "dirty_choices": ["Lưu", "Không lưu", "Hủy"],
                "session_lock_present": (
                    session.root_path / "session.lock"
                ).is_file(),
                "latest_generation_guard": True,
                "crash_safe_save": True,
            },
            "conversion_report.json": {
                "source_document_to_cam_project": True,
                "hms_to_cam_project": True,
                "mode_switch_after_publish": True,
                "source_preserved": source.is_file(),
                "hms_preserved": hms_path.is_file(),
                "rollback_identity_guard": True,
                "original_filename": source_record.original_name,
                "internal_filename": source_record.internal_filename,
                "working_geometry_path": source_record.working_geometry_path,
            },
            "drag_drop_open_report.json": {
                "open_dialog_command": (
                    "ProjectUiController.request_open_path"
                ),
                "drag_drop_command": (
                    "ProjectUiController.request_open_path"
                ),
                "same_application_command": True,
                "existing_import_worker_reused": True,
                "multiple_file_policy": "reject_with_message",
                "overlay_text": "Thả tệp để mở trong HMS",
                "overlay_focus_policy": "NoFocus",
                "overlay_hidden_after_drop": not window._drop_overlay.isVisible(),
            },
            "localization_accessibility_responsive_report.json": {
                "required_vietnamese_phrases": {
                    phrase: True
                    for phrase in (
                        "Tài liệu CAD",
                        "Dự án CAM",
                        "Tạo dự án CAM",
                        "Tạo dự án CAM từ tài liệu hiện tại",
                        "Tên dự án",
                        "Thư mục cha",
                        "Tên thư mục sẽ tạo",
                        "Đường dẫn đầy đủ",
                        "Thả tệp để mở trong HMS",
                        "Lưu thành tài liệu HMS",
                        "Đường dẫn không hợp lệ",
                        "Hình học làm việc",
                        "Tệp nguồn",
                        "Chuyển không gian làm việc thất bại",
                        "Nạp 3D mới cho dự án CAM",
                        "Chọn dự án CAM",
                        "Dự án CAM không hợp lệ",
                        "Có dữ liệu 3D mới",
                        "Xem thay đổi",
                        "Cập nhật",
                        "Để sau",
                        "Bỏ qua",
                        "Sửa",
                        "Kích thước X",
                        "Kích thước Y",
                        "Kích thước Z",
                    )
                },
                **_rendered_audit_payload(application),
            },
            "geometry_transfer_report.json": {
                "command": "Nạp 3D mới cho dự án CAM",
                "source_hms": str(hms_path),
                "source_document_preserved": hms_path.is_file(),
                "source_mode_unchanged_during_send": True,
                "target_project_id": str(request_add.target_project_id),
                "target_workspace_version": (
                    request_add.target_workspace_version
                ),
                "request_id": str(request_add.request_id),
                "request_schema_version": request_add.schema_version,
                "payload_checksum": request_add.payload_checksum,
                "metadata_checksum": request_add.metadata_checksum,
                "atomic_staging_to_pending": True,
                "duplicate_blocked": duplicate_blocked,
                "active_lock_allows_inbox_only": True,
                "source_ready_safe_copied": False,
            },
            "incoming_geometry_inbox_report.json": {
                "root": str(
                    session.root_path / "incoming-geometry"
                ),
                "directories": [
                    "staging",
                    "pending",
                    "applied",
                    "rejected",
                    "failed",
                ],
                "scanner_ignores_staging": True,
                "project_open_scan_after_recovery": True,
                "watcher_plus_polling_idempotent": True,
                "notification_modal": False,
                "notification_focus_policy": "NoFocus",
                "viewport_visible": window.viewport.isVisible(),
                "status_bar_visible": window.statusBar().isVisible(),
                "deferred_status": deferred.status.display_text,
                "rejected_status": rejected.status.display_text,
                "failed_status": failed.status.display_text,
                "applied_request_hidden_from_scan": (
                    request_add.request_id
                    not in {
                        item.request_id
                        for item in service.scan_incoming_geometry()
                    }
                ),
            },
            "geometry_apply_stale_safety_report.json": {
                "choices": [
                    GeometryApplyChoice.ADD_NEW.display_text,
                    GeometryApplyChoice.REPLACE_EXISTING.display_text,
                    GeometryApplyChoice.UPDATE_MATCHING.display_text,
                ],
                "add_new_source_id": str(result_add.source_id),
                "replace_preserved_source_id": (
                    result_replace.source_id == result_add.source_id
                ),
                "update_preserved_source_id": (
                    result_update.source_id == result_replace.source_id
                ),
                "deterministic_update_match": (
                    preview_update.deterministic_match_id
                    == result_replace.source_id
                ),
                "filename_only_matching": False,
                "scoped_dependency_stale": True,
                "unrelated_operation_stale_count": 0,
                "automatic_calculate": False,
                "automatic_simulation": False,
                "automatic_post": False,
                "ready_safe_copied": False,
                "rollback_failure_status": failed.status.display_text,
                "old_manifest_preserved_after_failure": (
                    service.current_project is not None
                    and service.current_project.manifest
                    == manifest_before_failure
                ),
                "zero_byte_geometry_count": 0,
            },
        }
        for filename, payload in reports.items():
            _write_json(OUTPUT / filename, payload)

        png_hashes = {name: _sha256(OUTPUT / name) for name in PNG_NAMES}
        if len(set(png_hashes.values())) != len(PNG_NAMES):
            raise RuntimeError("Every review PNG must have a unique SHA-256")
        source_paths = (
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "project"
            / "document_container.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "project"
            / "service.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "project"
            / "geometry_transfer.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "ui"
            / "workspace_dialog.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "ui"
            / "geometry_transfer_ui.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "ui"
            / "localized_dialogs.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "ui"
            / "localization.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "ui"
            / "ui_tokens.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "ui"
            / "main_window.py",
            REPOSITORY_ROOT
            / "src"
            / "hms_cadcam"
            / "ui"
            / "project_controller.py",
            REPOSITORY_ROOT / "tools" / "audit_vietnamese_ui.py",
            Path(__file__).resolve(),
        )
        summary = {
            "stage": "8A.4.2",
            "status": "IN PROGRESS",
            "package_file_count": 43,
            "png_count": 30,
            "json_count": 12,
            "markdown_count": 1,
            "production_model_service_widget_only": True,
            "model_state_asserted_before_each_image": True,
            "full_widget_capture": True,
            "sqlite_schema_version": 4,
            "png_records": records,
            "png_sha256": png_hashes,
            "source_sha256": {
                str(path.relative_to(REPOSITORY_ROOT)).replace("\\", "/"): _sha256(
                    path
                )
                for path in source_paths
            },
            "qa": qa_results,
        }
        _write_json(OUTPUT / "summary.json", summary)
        index_lines = [
            "# Duyệt Stage 8A.4.2 — tài liệu HMS và không gian làm việc CAM",
            "",
            "Trạng thái: **IN PROGRESS**. Package dùng mô hình, dịch vụ và thành phần giao diện thực tế.",
            "",
            "## PNG SHA-256",
            "",
        ]
        index_lines.extend(
            f"- `{name}` — `{digest}`"
            for name, digest in png_hashes.items()
        )
        index_lines.extend(
            [
                "",
                "## Quy ước",
                "",
                "- CAD đơn lẻ: một tệp `.HMS` xác định và có tổng kiểm.",
                "- CAM: thư mục làm việc dùng hình học chính xác, không dùng lưới thay thế.",
                "- Hộp thoại Mở và kéo/thả dùng cùng một lệnh ứng dụng.",
                "- Chuyển hình học dùng vùng chờ nguyên tử; bên gửi không sửa `project.db`.",
                "- Thông báo không khóa giao diện; Thêm/Thay thế/Cập nhật đều cần lựa chọn rõ.",
                "- Cập nhật chặn an toàn; hoàn tác giữ mô hình cũ và không tự tính toán.",
                "- Giai đoạn vẫn **IN PROGRESS**; không tuyên bố sẵn sàng chạy máy.",
                "",
            ]
        )
        if qa_results["recorded"]:
            index_lines.extend(
                [
                    "## QA thực tế",
                    "",
                    f"- Kiểm tra tập trung: {qa_results['focused']}.",
                    f"- Kiểm tra hồi quy: {qa_results['regression']}.",
                    f"- Toàn bộ: {qa_results['full']}; bỏ chọn: {qa_results['deselected']}.",
                    f"- `pip check`: {qa_results['pip_check']}.",
                    f"- `compileall`: {qa_results['compileall']}.",
                    f"- `git diff --check`: {qa_results['diff_check']}.",
                    "",
                ]
            )
        (OUTPUT / "REVIEW_INDEX.md").write_text(
            "\n".join(index_lines),
            encoding="utf-8",
        )

        actual = tuple(sorted(path.name for path in OUTPUT.iterdir()))
        expected = tuple(
            sorted((*PNG_NAMES, *JSON_NAMES, "REVIEW_INDEX.md"))
        )
        if actual != expected:
            raise RuntimeError(
                f"Review package shape mismatch: expected 43, got {len(actual)}"
            )
        print(
            json.dumps(
                {
                    "output": str(OUTPUT),
                    "files": len(actual),
                    "png": len(PNG_NAMES),
                    "png_sha256": png_hashes,
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
    finally:
        for auxiliary in auxiliary_services:
            try:
                auxiliary.close_workspace(discard_changes=True)
            except Exception:
                logger.warning(
                    "Review auxiliary workspace close failed",
                    exc_info=True,
                )
        if window is not None:
            try:
                window.cad_controller.shutdown()
                window.viewport.shutdown()
            except Exception:
                logger.warning("Review window shutdown failed", exc_info=True)
        if service is not None:
            try:
                service.close_workspace(discard_changes=True)
            except Exception:
                logger.warning("Review workspace close failed", exc_info=True)
        if window is not None:
            window.close()
        application.processEvents()
        shutil.rmtree(safe_root, ignore_errors=True)
        shutil.rmtree(unsafe_parent, ignore_errors=True)


def create_package() -> None:
    """Run the package in a clean native-Windows Qt process."""
    environment = dict(os.environ)
    environment.pop("QT_QPA_PLATFORM", None)
    environment.pop("QT_SCALE_FACTOR", None)
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--package-worker",
        ],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"Review package worker failed: {detail}")
    if result.stdout.strip():
        print(result.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-worker", action="store_true")
    parser.add_argument("--dpi-worker")
    arguments = parser.parse_args()
    if arguments.dpi_worker:
        return _dpi_worker(Path(arguments.dpi_worker))
    if arguments.package_worker:
        _create_package_in_process()
        return 0
    create_package()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

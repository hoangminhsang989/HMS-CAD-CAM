"""Final rendered-menu and property-label regressions for Stage 8A.4.2."""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QCoreApplication, QEvent, QRectF  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMainWindow,
    QTableWidget,
    QTableWidgetItem,
)

from hms_cadcam.cad.measurement import (  # noqa: E402
    BoundingDimensions,
    MeasurementResult,
)
from hms_cadcam.cad.models import CadDocumentId  # noqa: E402
from hms_cadcam.cad.unavailable import UnavailableCadKernel  # noqa: E402
from hms_cadcam.project.service import ProjectService  # noqa: E402
from hms_cadcam.ui.main_window import MainWindow, _measurement_rows  # noqa: E402
from hms_cadcam.ui.ui_tokens import (  # noqa: E402
    MAIN_MENU_CAPTURE_EXCLUDED_LEFT,
)
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore  # noqa: E402
from hms_cadcam.viewer.unavailable_backend import (  # noqa: E402
    UnavailableCadViewportBackend,
)
from tools.audit_vietnamese_ui import (  # noqa: E402
    collect_runtime_strings,
    menu_text_clipping_issues,
    unapproved_property_label_matches,
)


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _window(tmp_path: Path) -> MainWindow:
    return MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("stage8a42 menu test"),
        UnavailableCadViewportBackend("stage8a42 menu test"),
        layout_store=WorkspaceLayoutStore.for_config_directory(
            tmp_path / "layout"
        ),
    )


def _dispose(window: QMainWindow, application: QApplication) -> None:
    window.close()
    application.processEvents()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def _scaled(rect: QRectF, factor: float) -> QRectF:
    return QRectF(
        rect.x() * factor,
        rect.y() * factor,
        rect.width() * factor,
        rect.height() * factor,
    )


def test_production_main_menu_text_geometry_and_capture_are_complete(
    tmp_path: Path,
) -> None:
    application = _application()
    window = _window(tmp_path)
    window.resize(1440, 860)
    window.show()
    application.processEvents()

    menu_bar = window.menuBar()
    actions = menu_bar.actions()
    assert tuple(action.text() for action in actions) == (
        "Tệp",
        "Sửa",
        "Hiển thị",
        "CAD",
        "CAM",
        "Máy",
        "Đường chạy dao",
        "Thiết lập",
        "Trợ giúp",
    )
    assert not menu_text_clipping_issues(window, "production_main_window")
    assert (
        menu_bar.actionGeometry(actions[0]).left()
        >= MAIN_MENU_CAPTURE_EXCLUDED_LEFT
    )

    edit_action = actions[1]
    action_rect = menu_bar.actionGeometry(edit_action)
    metrics = menu_bar.fontMetrics()
    text_rect = QRectF(
        0.0,
        0.0,
        float(metrics.horizontalAdvance(edit_action.text())),
        float(metrics.height()),
    )
    text_rect.moveCenter(QRectF(action_rect).center())
    capture_rect = QRectF(menu_bar.rect())
    capture_rect.setLeft(float(MAIN_MENU_CAPTURE_EXCLUDED_LEFT))
    for factor in (1.0, 1.25, 1.5):
        assert _scaled(QRectF(action_rect), factor).contains(
            _scaled(text_rect, factor)
        )
        assert _scaled(capture_rect, factor).contains(
            _scaled(text_rect, factor)
        )

    menu_image = menu_bar.grab().toImage()
    window_image = window.grab().toImage()
    assert all(
        menu_image.pixelColor(x, y) == window_image.pixelColor(x, y)
        for x in range(action_rect.left(), action_rect.right() + 1)
        for y in range(action_rect.top(), action_rect.bottom() + 1)
    )
    _dispose(window, application)


def test_menu_geometry_audit_rejects_the_previous_capture_position(
    qtbot,
) -> None:
    window = QMainWindow()
    qtbot.addWidget(window)
    menu_bar = window.menuBar()
    menu_bar.setObjectName("MainMenuBar")
    menu_bar.addMenu("Sửa")
    window.resize(600, 320)
    window.show()
    _application().processEvents()

    issues = menu_text_clipping_issues(window, "old_capture_position")
    assert any(
        issue.text == "Sửa"
        and issue.reason == "text_outside_full_window_capture"
        for issue in issues
    )


def test_rendered_menu_audit_rejects_truncated_edit_title(qtbot) -> None:
    window = QMainWindow()
    qtbot.addWidget(window)
    window.menuBar().addMenu("ửa")
    window.show()
    _application().processEvents()

    entries = collect_runtime_strings(window, "truncated_edit_title")
    assert any(
        entry.text == "ửa"
        and entry.source in {"action_text", "title"}
        and entry.classification == "untranslated"
        for entry in entries
    )


def test_bounding_dimensions_keep_values_but_use_vietnamese_labels() -> None:
    result = MeasurementResult(
        CadDocumentId("document"),
        (),
        (BoundingDimensions(12.5, 8.25, 3.0),),
    )

    rows = _measurement_rows((result,))

    assert tuple(label for label, _value in rows) == (
        "Kích thước X",
        "Kích thước Y",
        "Kích thước Z",
    )
    assert tuple(value for _label, value in rows) == (
        "12.5 đơn vị mô hình",
        "8.25 đơn vị mô hình",
        "3 đơn vị mô hình",
    )


@pytest.mark.parametrize(
    "bad_label",
    ("Bounding X", "Bounding Y", "Bounding Z", "Bounding box"),
)
def test_properties_model_and_delegate_audit_reject_english_bounds(
    qtbot,
    bad_label: str,
) -> None:
    table = QTableWidget(1, 1)
    qtbot.addWidget(table)
    table.setHorizontalHeaderItem(0, QTableWidgetItem("Thuộc tính"))
    table.setVerticalHeaderItem(0, QTableWidgetItem(bad_label))
    table.setItem(0, 0, QTableWidgetItem(bad_label))
    table.show()
    _application().processEvents()

    entries = collect_runtime_strings(table, "property_table")
    bad_entries = [
        entry
        for entry in entries
        if entry.text == bad_label
        and entry.source.startswith(
            ("model_vertical_header", "model_display", "delegate_display")
        )
    ]
    assert bad_entries
    assert all(
        entry.classification == "untranslated" for entry in bad_entries
    )
    assert unapproved_property_label_matches(bad_label) == (bad_label,)

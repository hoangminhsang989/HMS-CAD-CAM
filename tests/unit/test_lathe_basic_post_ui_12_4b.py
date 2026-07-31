"""Focused UI/topology checks for Stage 12.4B."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from hms_cadcam.cam.lathe.lathe_post import LatheBasicNcService
from hms_cadcam.ui.basic_nc_preview import BasicNcExportAcknowledgementDialog, BasicNcPreviewPanel
from hms_cadcam.ui.lathe_program_preview_integration import install_lathe_basic_nc_preview


def test_acknowledgement_is_unchecked_and_export_is_disabled(qtbot) -> None:
    dialog = BasicNcExportAcknowledgementDialog()
    qtbot.addWidget(dialog)
    assert dialog.checkbox.isChecked() is False
    assert dialog.export_button.isEnabled() is False
    qtbot.mouseClick(dialog.checkbox, Qt.MouseButton.LeftButton)
    assert dialog.export_button.isEnabled() is True


def test_basic_panel_is_read_only_and_export_starts_disabled(qtbot) -> None:
    panel = BasicNcPreviewPanel(LatheBasicNcService())
    qtbot.addWidget(panel)
    assert panel.profile_field.isReadOnly() is True
    assert panel.listing.isReadOnly() is True
    assert panel.export_button.isEnabled() is False
    assert panel.badge.text() and "—" in panel.badge.text()


def test_feature_off_has_no_action_and_feature_on_has_one_singleton(qtbot) -> None:
    workspace = QWidget()
    qtbot.addWidget(workspace)
    service = LatheBasicNcService()
    assert install_lathe_basic_nc_preview(workspace, False, service) is None
    installed = install_lathe_basic_nc_preview(workspace, True, service)
    assert installed is not None
    controller, action = installed
    assert action.objectName() == "LatheBasicNcPreviewAction"
    first = controller.open(workspace)
    second = controller.open(workspace)
    assert first is second
    controller.close()
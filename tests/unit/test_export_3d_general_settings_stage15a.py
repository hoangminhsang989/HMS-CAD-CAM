"""Stage15A WP2 General Settings transaction and production UX tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication

from hms_cadcam.cad.export_models import ExportFormatId, StlEncoding
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.settings.export_defaults import ExportDefaultsSettingsService
from hms_cadcam.ui.settings.general_settings import GeneralSettingsDialog
from hms_cadcam.ui.settings.ui_scale import UiScaleManager


def _services(path: Path) -> tuple[QSettings, UiScaleManager, ExportDefaultsSettingsService]:
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    return settings, UiScaleManager(settings), ExportDefaultsSettingsService(settings)


def _export_row(dialog: GeneralSettingsDialog) -> int:
    for row in range(dialog.category_list.count()):
        if (
            dialog.category_list.item(row).data(Qt.ItemDataRole.UserRole)
            == "3D Export"
        ):
            return row
    raise AssertionError("3D Export category is missing")


def _select_format(dialog: GeneralSettingsDialog, format_id: ExportFormatId) -> None:
    page = dialog._export_3d_page
    page.format_combo.setCurrentIndex(page.format_combo.findData(format_id.value))


def test_export_page_is_real_and_reports_capabilities(qtbot, tmp_path: Path) -> None:
    _settings, manager, defaults = _services(tmp_path / "page.ini")
    dialog = GeneralSettingsDialog(manager, export_defaults_service=defaults)
    qtbot.addWidget(dialog)
    dialog.category_list.setCurrentRow(_export_row(dialog))
    page = dialog._export_3d_page
    assert dialog.page_stack.currentWidget() is page
    assert page.format_combo.count() == 8
    assert {
        ExportFormatId(str(page.format_combo.itemData(index)))
        for index in range(page.format_combo.count())
    } == set(ExportFormatId)

    for format_id in (
        ExportFormatId.STEP,
        ExportFormatId.IGES,
        ExportFormatId.STL,
        ExportFormatId.BREP,
    ):
        _select_format(dialog, format_id)
        assert "OCP" in page.reason_label.text()

    for format_id in (
        ExportFormatId.PARASOLID,
        ExportFormatId.ACIS,
        ExportFormatId.DWG,
        ExportFormatId.DXF,
    ):
        _select_format(dialog, format_id)
        assert page.reason_label.text()
        assert not page.standard_combo.isEnabled()


def test_cancel_discards_working_profile_without_writing(qtbot, tmp_path: Path) -> None:
    settings, manager, defaults = _services(tmp_path / "cancel.ini")
    dialog = GeneralSettingsDialog(manager, export_defaults_service=defaults)
    qtbot.addWidget(dialog)
    dialog.category_list.setCurrentRow(_export_row(dialog))
    _select_format(dialog, ExportFormatId.STEP)
    page = dialog._export_3d_page
    page.standard_combo.setCurrentIndex(page.standard_combo.findData("AP203"))
    assert page.dirty
    assert not settings.contains("export3d/defaults/step")
    dialog._cancel()
    QApplication.processEvents()
    assert defaults.load().profiles[ExportFormatId.STEP].standard == "AP242"
    assert not settings.contains("export3d/defaults/step")


def test_apply_and_restart_restore_exact_profile_controls(qtbot, tmp_path: Path) -> None:
    path = tmp_path / "apply-restart.ini"
    _settings, manager_a, defaults_a = _services(path)
    dialog_a = GeneralSettingsDialog(manager_a, export_defaults_service=defaults_a)
    qtbot.addWidget(dialog_a)
    dialog_a.category_list.setCurrentRow(_export_row(dialog_a))
    _select_format(dialog_a, ExportFormatId.STEP)
    page_a = dialog_a._export_3d_page
    page_a.standard_combo.setCurrentIndex(page_a.standard_combo.findData("AP203"))
    _select_format(dialog_a, ExportFormatId.BREP)
    page_a.standard_combo.setCurrentIndex(page_a.standard_combo.findData("2"))
    _select_format(dialog_a, ExportFormatId.STL)
    page_a.encoding_combo.setCurrentIndex(
        page_a.encoding_combo.findData(StlEncoding.ASCII.value)
    )
    page_a.linear_deflection.setValue(0.037)
    page_a.angular_deflection.setValue(0.23)
    page_a.relative_mesh.setChecked(True)
    dialog_a._apply()
    assert not page_a.dirty
    dialog_a.close()

    _settings_b, manager_b, defaults_b = _services(path)
    dialog_b = GeneralSettingsDialog(manager_b, export_defaults_service=defaults_b)
    qtbot.addWidget(dialog_b)
    restored = defaults_b.load().profiles
    assert restored[ExportFormatId.STEP].standard == "AP203"
    assert restored[ExportFormatId.BREP].standard == "2"
    assert restored[ExportFormatId.STL].stl_encoding is StlEncoding.ASCII
    assert restored[ExportFormatId.STL].mesh_options.linear_deflection == 0.037
    assert restored[ExportFormatId.STL].mesh_options.angular_deflection == 0.23
    assert restored[ExportFormatId.STL].mesh_options.relative is True
    _select_format(dialog_b, ExportFormatId.STL)
    assert dialog_b._export_3d_page.linear_deflection.value() == 0.037


def test_reset_current_and_reset_all_are_working_copy_operations(
    qtbot, tmp_path: Path
) -> None:
    settings, manager, defaults = _services(tmp_path / "reset.ini")
    profiles = dict(defaults.load().profiles)
    profiles[ExportFormatId.STEP] = profiles[ExportFormatId.STEP].__class__(
        ExportFormatId.STEP,
        standard="AP203",
    )
    defaults.apply(profiles)
    original_raw = settings.value("export3d/defaults/step")

    dialog = GeneralSettingsDialog(manager, export_defaults_service=defaults)
    qtbot.addWidget(dialog)
    dialog.category_list.setCurrentRow(_export_row(dialog))
    _select_format(dialog, ExportFormatId.STEP)
    dialog.reset_button.click()
    assert dialog._export_3d_page.profiles[ExportFormatId.STEP].standard == "AP242"
    assert settings.value("export3d/defaults/step") == original_raw
    dialog._export_3d_page.reset_all_button.click()
    assert settings.value("export3d/defaults/step") == original_raw
    dialog._apply()
    assert defaults.load().profiles[ExportFormatId.STEP].standard == "AP242"


@pytest.mark.parametrize("percent", (100, 125, 150, 200))
def test_dirty_export_controls_survive_runtime_language_and_scale_changes(
    qtbot, tmp_path: Path, percent: int
) -> None:
    service = translation_service()
    previous = service.language
    _settings, manager, defaults = _services(tmp_path / f"runtime-{percent}.ini")
    dialog = GeneralSettingsDialog(
        manager,
        service=service,
        export_defaults_service=defaults,
    )
    qtbot.addWidget(dialog)
    dialog.category_list.setCurrentRow(_export_row(dialog))
    _select_format(dialog, ExportFormatId.STL)
    page = dialog._export_3d_page
    page.linear_deflection.setValue(0.037)
    page.angular_deflection.setValue(0.23)
    page.relative_mesh.setChecked(True)
    page.encoding_combo.setCurrentIndex(
        page.encoding_combo.findData(StlEncoding.ASCII.value)
    )
    try:
        manager.set_preview_percent(percent)
        for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
            service.set_language(language)
            QApplication.processEvents()
            current = page.profiles[ExportFormatId.STL]
            assert current.stl_encoding is StlEncoding.ASCII
            assert current.mesh_options.linear_deflection == 0.037
            assert current.mesh_options.angular_deflection == 0.23
            assert current.mesh_options.relative is True
            assert page.dirty
        dialog.show()
        dialog._fit_to_screen()
        QApplication.processEvents()
        evidence = dialog.geometry_evidence()
        assert evidence.footer_accessible
        assert page.format_combo.isVisible()
        assert page.reset_all_button.isVisible()
    finally:
        service.set_language(previous)


def test_corrupt_profile_is_visible_but_not_rewritten_until_apply(
    qtbot, tmp_path: Path
) -> None:
    settings, manager, defaults = _services(tmp_path / "corrupt-ui.ini")
    settings.setValue("export3d/defaults/step", "not-json")
    settings.sync()
    dialog = GeneralSettingsDialog(manager, export_defaults_service=defaults)
    qtbot.addWidget(dialog)
    page = dialog._export_3d_page
    assert "STEP" in page.status_label.text()
    assert page.dirty and dialog.apply_button.isEnabled()
    assert settings.value("export3d/defaults/step") == "not-json"
    dialog.category_list.setCurrentRow(_export_row(dialog))
    _select_format(dialog, ExportFormatId.STEP)
    dialog.reset_button.click()
    assert settings.value("export3d/defaults/step") == "not-json"
    dialog._apply()
    assert defaults.load().issues == ()
    assert defaults.load().profiles[ExportFormatId.STEP].standard == "AP242"

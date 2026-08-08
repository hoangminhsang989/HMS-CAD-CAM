"""Stage15A compact UI, runtime i18n, and Save As routing tests."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialogButtonBox, QMainWindow

from hms_cadcam.cad.export_models import ExportFormatId, ExportProfile
from hms_cadcam.cad.export_service import CadExportService
from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cad_export import CadExportProfileDialog, CadExportUiController
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.project_controller import ProjectUiController


class _Backend:
    supported_formats = frozenset(
        {
            ExportFormatId.STEP,
            ExportFormatId.IGES,
            ExportFormatId.STL,
            ExportFormatId.BREP,
        }
    )
    unavailable_reason = None

    def write(self, request, temporary_path):  # pragma: no cover - UI does not write
        raise AssertionError("writer must not run in profile UI tests")


def _profiles(service: CadExportService) -> dict[ExportFormatId, ExportProfile]:
    return {
        item.format_id: ExportProfile.default_for(item.format_id)
        for item in service.capabilities()
    }


def test_unavailable_format_displays_reason_and_disables_export(qtbot) -> None:
    service = CadExportService(_Backend())
    dialog = CadExportProfileDialog(service.capabilities(), _profiles(service))
    qtbot.addWidget(dialog)
    index = dialog.format_combo.findData(ExportFormatId.PARASOLID.value)
    dialog.format_combo.setCurrentIndex(index)
    save = dialog.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert save is not None and not save.isEnabled()
    assert "Parasolid" in dialog.reason_label.text()
    assert not dialog.advanced_group.isVisible()


def test_runtime_language_switch_preserves_stl_profile_state(qtbot) -> None:
    service = CadExportService(_Backend())
    translator = translation_service()
    previous = translator.language
    dialog = CadExportProfileDialog(
        service.capabilities(),
        _profiles(service),
        initial_format=ExportFormatId.STL,
    )
    qtbot.addWidget(dialog)
    dialog.linear_deflection.setValue(0.025)
    dialog.angular_deflection.setValue(0.35)
    dialog.relative_mesh.setChecked(True)
    try:
        translator.set_language(UiLanguage.EN_US)
        assert dialog.windowTitle() == "3D Export Profile"
        translator.set_language(UiLanguage.KO_KR)
        profile = dialog.profile()
        assert profile.mesh_options.linear_deflection == 0.025
        assert profile.mesh_options.angular_deflection == 0.35
        assert profile.mesh_options.relative is True
    finally:
        translator.set_language(previous)


def test_profile_dialog_layout_expands_at_two_hundred_percent_font(qtbot) -> None:
    service = CadExportService(_Backend())
    dialog = CadExportProfileDialog(
        service.capabilities(),
        _profiles(service),
        initial_format=ExportFormatId.STL,
    )
    qtbot.addWidget(dialog)
    font = QFont(dialog.font())
    font.setPointSizeF(max(18.0, font.pointSizeF() * 2.0))
    dialog.setFont(font)
    dialog.adjustSize()
    dialog.show()
    qtbot.waitUntil(dialog.isVisible)
    assert dialog.size().width() >= dialog.minimumSizeHint().width()
    assert dialog.size().height() >= dialog.minimumSizeHint().height()
    assert dialog.buttons.geometry().bottom() <= dialog.contentsRect().bottom()


def test_save_as_hms_is_preserved_and_3d_extension_uses_export_router(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    service = ProjectService.create_default(tmp_path / "config")
    service.commit_document_open(service.prepare_document_open(source))
    window = QMainWindow()
    qtbot.addWidget(window)
    controller = ProjectUiController(window, service)
    saved: list[Path] = []
    routed: list[Path] = []
    operations = []
    monkeypatch.setattr(service, "save_document", lambda path=None: saved.append(path))
    monkeypatch.setattr(controller, "_start_operation", lambda operation: operations.append(operation()))
    controller.set_save_as_export_router(lambda path: routed.append(path) or True)

    hms_target = tmp_path / "preserved.HMS"
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(hms_target), "Tài liệu HMS (*.HMS)"),
    )
    controller.save_project_as()
    assert saved == [hms_target]
    assert routed == []

    step_target = tmp_path / "exported.step"
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(step_target), "STEP (*.step *.stp)"),
    )
    controller.save_project_as()
    assert saved == [hms_target]
    assert routed == [step_target]


def test_save_as_unknown_extension_fails_without_persistence_or_file(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    service = ProjectService.create_default(tmp_path / "config")
    service.commit_document_open(service.prepare_document_open(source))
    window = QMainWindow()
    qtbot.addWidget(window)
    controller = ProjectUiController(window, service)
    unknown = tmp_path / "part.xyz"
    operations = []
    warnings = []
    monkeypatch.setattr(controller, "_start_operation", operations.append)
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(unknown), "All files (*)"),
    )
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QMessageBox.critical",
        lambda *_args, **_kwargs: warnings.append(True),
    )
    controller.save_project_as()
    assert warnings == [True]
    assert operations == []
    assert not unknown.exists()


def test_interactive_export_never_silently_changes_mismatched_format(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    project_service = ProjectService.create_default(tmp_path / "config")
    project_service.commit_document_open(project_service.prepare_document_open(source))
    window = QMainWindow()
    qtbot.addWidget(window)
    controller = CadExportUiController(
        window,
        CadExportService(_Backend()),
        project_service,
        lambda: CadDocumentId("active-document"),
        lambda: (),
    )
    started = []
    warnings = []
    monkeypatch.setattr(
        controller,
        "_request_profile",
        lambda _format: ExportProfile.default_for(ExportFormatId.STEP),
    )
    monkeypatch.setattr(controller, "_start_export", lambda *args, **kwargs: started.append(args))
    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(tmp_path / "wrong.iges"), "STEP (*.step *.stp)"),
    )
    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QMessageBox.warning",
        lambda *_args, **_kwargs: warnings.append(True),
    )
    controller.export_document()
    assert started == []
    assert warnings == [True]
    assert not (tmp_path / "wrong.iges").exists()

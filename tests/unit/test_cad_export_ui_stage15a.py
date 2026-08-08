"""Stage15A compact UI, runtime i18n, and Save As routing tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QDialogButtonBox, QMainWindow

from hms_cadcam.cad.export_models import (
    ExportFormatId,
    ExportProfile,
    StlEncoding,
    StlMeshOptions,
)
from hms_cadcam.cad.export_service import BackendWriteMetadata, CadExportService
from hms_cadcam.cad.models import CadDocumentId, CadGeometryKind
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


class _WritingBackend(_Backend):
    def write(self, request, temporary_path):
        temporary_path.write_bytes(b"stage15a-ui-export")
        return BackendWriteMetadata("UI lifecycle writer", 1)


class _HoldingThreadPool:
    def __init__(self) -> None:
        self.tasks = []

    def start(self, task) -> None:
        self.tasks.append(task)


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


def test_existing_mesh_context_hides_tessellation_and_preserves_encoding_on_i18n(
    qtbot,
) -> None:
    service = CadExportService(_Backend())
    translator = translation_service()
    previous = translator.language
    dialog = CadExportProfileDialog(
        service.capabilities(),
        _profiles(service),
        initial_format=ExportFormatId.STL,
        stl_tessellation_applicable=False,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.encoding_combo.setCurrentIndex(1)
    try:
        assert not dialog._advanced_layout.isRowVisible(dialog.linear_deflection)
        assert not dialog._advanced_layout.isRowVisible(dialog.angular_deflection)
        assert not dialog._advanced_layout.isRowVisible(dialog.relative_mesh)
        assert dialog._advanced_layout.isRowVisible(dialog.mesh_applicability_label)
        translator.set_language(UiLanguage.EN_US)
        assert "not applicable" in dialog.mesh_applicability_label.text()
        translator.set_language(UiLanguage.KO_KR)
        profile = dialog.profile()
        assert profile.stl_encoding.value == "ascii"
        assert profile.mesh_options is None
        assert profile.tolerance is None
        assert dialog.profiles[ExportFormatId.STL].mesh_options is not None
    finally:
        translator.set_language(previous)


@pytest.mark.parametrize("encoding", tuple(StlEncoding))
def test_stl_brep_mesh_brep_round_trip_preserves_exact_tessellation_profile(
    qtbot, encoding: StlEncoding
) -> None:
    service = CadExportService(_Backend())
    options = StlMeshOptions(0.037, 0.23, True)
    profiles = _profiles(service)
    profiles[ExportFormatId.STL] = ExportProfile(
        ExportFormatId.STL,
        tolerance=options.linear_deflection,
        stl_encoding=encoding,
        mesh_options=options,
    )
    translator = translation_service()
    previous = translator.language
    try:
        for language in (UiLanguage.VI_VN, UiLanguage.EN_US, UiLanguage.KO_KR):
            mesh_dialog = CadExportProfileDialog(
                service.capabilities(),
                profiles,
                initial_format=ExportFormatId.STL,
                stl_tessellation_applicable=False,
            )
            qtbot.addWidget(mesh_dialog)
            translator.set_language(language)
            iges_index = mesh_dialog.format_combo.findData(ExportFormatId.IGES.value)
            stl_index = mesh_dialog.format_combo.findData(ExportFormatId.STL.value)
            mesh_dialog.format_combo.setCurrentIndex(iges_index)
            mesh_dialog.format_combo.setCurrentIndex(stl_index)
            effective = mesh_dialog.profile()
            mesh_dialog._accept_validated()
            assert effective.mesh_options is None
            assert effective.tolerance is None
            assert effective.stl_encoding is encoding
            stored = mesh_dialog.profiles[ExportFormatId.STL]
            assert stored.mesh_options == options
            assert stored.tolerance == 0.037
            assert stored.stl_encoding is encoding
            profiles = mesh_dialog.profiles

            brep_dialog = CadExportProfileDialog(
                service.capabilities(),
                profiles,
                initial_format=ExportFormatId.STL,
                stl_tessellation_applicable=True,
            )
            qtbot.addWidget(brep_dialog)
            restored = brep_dialog.profile()
            assert restored.mesh_options == options
            assert restored.tolerance == 0.037
            assert restored.stl_encoding is encoding
            profiles = brep_dialog.profiles
    finally:
        translator.set_language(previous)


@pytest.mark.parametrize("language", tuple(UiLanguage))
@pytest.mark.parametrize("scale", (1.0, 1.5, 2.0))
def test_mesh_not_applicable_context_has_no_clipping_at_runtime_locale_and_scale(
    qtbot, language: UiLanguage, scale: float
) -> None:
    service = CadExportService(_Backend())
    translator = translation_service()
    previous = translator.language
    dialog = CadExportProfileDialog(
        service.capabilities(),
        _profiles(service),
        initial_format=ExportFormatId.STL,
        stl_tessellation_applicable=False,
    )
    qtbot.addWidget(dialog)
    try:
        translator.set_language(language)
        font = QFont(dialog.font())
        point_size = font.pointSizeF() if font.pointSizeF() > 0 else 9.0
        font.setPointSizeF(point_size * scale)
        dialog.setFont(font)
        dialog.adjustSize()
        dialog.show()
        qtbot.waitUntil(dialog.isVisible)
        assert not dialog._advanced_layout.isRowVisible(dialog.linear_deflection)
        assert not dialog._advanced_layout.isRowVisible(dialog.angular_deflection)
        assert not dialog._advanced_layout.isRowVisible(dialog.relative_mesh)
        assert dialog._advanced_layout.isRowVisible(dialog.mesh_applicability_label)
        assert dialog.size().width() >= dialog.minimumSizeHint().width()
        assert dialog.size().height() >= dialog.minimumSizeHint().height()
        assert dialog.buttons.geometry().bottom() <= dialog.contentsRect().bottom()
    finally:
        translator.set_language(previous)


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
        lambda: CadGeometryKind.BREP,
        lambda: (),
        lambda: True,
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


def _lifecycle_controller(qtbot, tmp_path: Path):
    source = tmp_path / "part.step"
    source.write_bytes(b"source")
    project_service = ProjectService.create_default(tmp_path / "config")
    project_service.commit_document_open(project_service.prepare_document_open(source))
    window = QMainWindow()
    qtbot.addWidget(window)
    document = [CadDocumentId("active-document")]
    operation_available = [True]
    controller = CadExportUiController(
        window,
        CadExportService(_WritingBackend()),
        project_service,
        lambda: document[0],
        lambda: CadGeometryKind.BREP,
        lambda: (),
        lambda: operation_available[0],
    )
    pool = _HoldingThreadPool()
    controller._thread_pool = pool
    return controller, project_service, document, operation_available, pool


def test_export_admission_is_symmetric_and_repeated_start_cannot_overlap(
    qtbot, tmp_path: Path
) -> None:
    controller, _service, _document, available, pool = _lifecycle_controller(
        qtbot, tmp_path
    )
    target = tmp_path / "blocked.step"
    available[0] = False
    controller.refresh_action_states()
    assert not controller.actions["export_3d"].isEnabled()
    assert not controller._start_export(
        target,
        ExportProfile.default_for(ExportFormatId.STEP),
        selected=False,
    )
    available[0] = True
    assert controller._start_export(
        target,
        ExportProfile.default_for(ExportFormatId.STEP),
        selected=False,
    )
    assert not controller._start_export(
        tmp_path / "overlap.step",
        ExportProfile.default_for(ExportFormatId.STEP),
        selected=False,
    )
    assert len(pool.tasks) == 1


@pytest.mark.parametrize("worker_failure", (False, True))
def test_stale_export_completion_is_suppressed_and_lock_released_once(
    qtbot, tmp_path: Path, monkeypatch, worker_failure: bool
) -> None:
    controller, _service, document, _available, pool = _lifecycle_controller(
        qtbot, tmp_path
    )
    if worker_failure:
        monkeypatch.setattr(
            controller._service,
            "export",
            lambda _request: (_ for _ in ()).throw(RuntimeError("worker failed")),
        )
    messages: list[str] = []
    busy: list[bool] = []
    controller.message.connect(messages.append)
    controller.busy_changed.connect(busy.append)
    assert controller._start_export(
        tmp_path / "stale.step",
        ExportProfile.default_for(ExportFormatId.STEP),
        selected=False,
    )
    assert len(messages) == 1
    messages.clear()
    document[0] = CadDocumentId("replacement-document")
    pool.tasks[0].run()
    pool.tasks[0].signals.finished.emit()
    assert messages == []
    assert busy == [True, False]
    assert controller._active_task is None


def test_export_busy_blocks_project_lifecycle_until_owner_finishes(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    controller, service, _document, _available, pool = _lifecycle_controller(
        qtbot, tmp_path
    )
    window = QMainWindow()
    qtbot.addWidget(window)
    project_controller = ProjectUiController(window, service)
    controller.busy_changed.connect(project_controller.set_external_busy)
    notices: list[bool] = []
    monkeypatch.setattr(
        "hms_cadcam.ui.project_controller.QMessageBox.information",
        lambda *_args, **_kwargs: notices.append(True),
    )
    assert controller._start_export(
        tmp_path / "owned.step",
        ExportProfile.default_for(ExportFormatId.STEP),
        selected=False,
    )
    assert project_controller.is_busy
    assert not project_controller.request_open_path(tmp_path / "other.step")
    assert not project_controller.request_application_close()
    assert notices == [True]
    pool.tasks[0].run()
    assert not project_controller.is_busy

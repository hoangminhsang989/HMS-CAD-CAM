"""Stage15A WP2 controller, routing, overwrite, and mesh-profile integration."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QDialog, QMainWindow, QMessageBox

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
from hms_cadcam.ui.settings.export_defaults import (
    ExportDefaultsSettingsService,
    factory_export_profiles,
)


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

    def write(self, request, temporary_path):
        temporary_path.write_bytes(request.profile.to_json().encode("utf-8"))
        return BackendWriteMetadata("WP2 test writer", 1)


class _HoldingThreadPool:
    def __init__(self) -> None:
        self.tasks = []

    def start(self, task) -> None:
        self.tasks.append(task)


def _persisted_service(path: Path) -> ExportDefaultsSettingsService:
    settings = QSettings(str(path), QSettings.Format.IniFormat)
    service = ExportDefaultsSettingsService(settings)
    profiles = factory_export_profiles()
    profiles[ExportFormatId.STEP] = ExportProfile(
        ExportFormatId.STEP,
        standard="AP203",
    )
    profiles[ExportFormatId.BREP] = ExportProfile(
        ExportFormatId.BREP,
        standard="2",
    )
    mesh = StlMeshOptions(0.037, 0.23, True)
    profiles[ExportFormatId.STL] = ExportProfile(
        ExportFormatId.STL,
        tolerance=0.037,
        stl_encoding=StlEncoding.ASCII,
        mesh_options=mesh,
    )
    service.apply(profiles)
    return service


def _controller(
    qtbot,
    tmp_path: Path,
    defaults: ExportDefaultsSettingsService,
    *,
    geometry: list[CadGeometryKind] | None = None,
    selected: bool = False,
) -> CadExportUiController:
    window = QMainWindow()
    qtbot.addWidget(window)
    return CadExportUiController(
        window,
        CadExportService(_Backend()),
        ProjectService.create_default(tmp_path / "config"),
        lambda: CadDocumentId("persistent-defaults-document"),
        lambda: (geometry or [CadGeometryKind.BREP])[0],
        lambda: (object(),) if selected else (),
        lambda: True,
        defaults_service=defaults,
    )


def test_direct_selected_and_save_as_seed_one_persistent_store(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    defaults = _persisted_service(tmp_path / "routes.ini")
    controller = _controller(qtbot, tmp_path, defaults, selected=True)
    observed: list[tuple[Path, ExportProfile, bool]] = []
    monkeypatch.setattr(
        CadExportProfileDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        controller,
        "_start_export",
        lambda target, profile, *, selected: observed.append(
            (target, profile, selected)
        )
        or True,
    )
    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QFileDialog.getSaveFileName",
        lambda *_args, **_kwargs: (str(tmp_path / "direct.step"), "STEP"),
    )
    controller.export_document()
    controller.export_selected()
    assert controller.route_save_as(tmp_path / "save-as.brep")
    assert controller.route_save_as(tmp_path / "save-as.stl")
    assert controller.route_save_as(tmp_path / "save-as.iges")

    assert observed[0][1].standard == "AP203" and observed[0][2] is False
    assert observed[1][1].standard == "AP203" and observed[1][2] is True
    assert observed[2][1].standard == "2"
    assert observed[3][1].stl_encoding is StlEncoding.ASCII
    assert observed[3][1].mesh_options == StlMeshOptions(0.037, 0.23, True)
    assert observed[4][1].format_id is ExportFormatId.IGES


def test_cancelled_or_successful_export_never_changes_persistent_defaults(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "unchanged.ini"
    defaults = _persisted_service(path)
    before = {
        key: defaults.settings.value(key)
        for key in defaults.settings.allKeys()
    }
    controller = _controller(qtbot, tmp_path, defaults)
    monkeypatch.setattr(
        CadExportProfileDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Rejected,
    )
    assert not controller.route_save_as(tmp_path / "cancelled.step")
    assert {
        key: defaults.settings.value(key)
        for key in defaults.settings.allKeys()
    } == before

    pool = _HoldingThreadPool()
    controller._thread_pool = pool
    profile = defaults.load().profiles[ExportFormatId.STEP]
    assert controller._start_export(tmp_path / "success.step", profile, selected=False)
    pool.tasks[0].run()
    assert (tmp_path / "success.step").is_file()
    assert {
        key: defaults.settings.value(key)
        for key in defaults.settings.allKeys()
    } == before


def test_restart_mesh_effective_profile_preserves_stored_brep_tessellation(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    path = tmp_path / "mesh-restart.ini"
    _persisted_service(path)
    defaults = ExportDefaultsSettingsService(
        QSettings(str(path), QSettings.Format.IniFormat)
    )
    geometry = [CadGeometryKind.TRIANGLE_MESH]
    controller = _controller(qtbot, tmp_path, defaults, geometry=geometry)
    observed: list[ExportProfile] = []
    monkeypatch.setattr(
        CadExportProfileDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        controller,
        "_start_export",
        lambda _target, profile, *, selected: observed.append(profile) or True,
    )

    for kind in (CadGeometryKind.TRIANGLE_MESH, CadGeometryKind.BREP) * 3:
        geometry[0] = kind
        assert controller.route_save_as(tmp_path / f"transition-{len(observed)}.stl")
    for index, profile in enumerate(observed):
        assert profile.stl_encoding is StlEncoding.ASCII
        if index % 2 == 0:
            assert profile.mesh_options is None
            assert profile.tolerance is None
        else:
            assert profile.mesh_options == StlMeshOptions(0.037, 0.23, True)
            assert profile.tolerance == 0.037
    stored = defaults.load().profiles[ExportFormatId.STL]
    assert stored.stl_encoding is StlEncoding.ASCII
    assert stored.mesh_options == StlMeshOptions(0.037, 0.23, True)


def test_corrupt_profile_and_unavailable_format_cannot_reach_writer(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    settings = QSettings(str(tmp_path / "corrupt-route.ini"), QSettings.Format.IniFormat)
    settings.setValue("export3d/defaults/step", "broken-json")
    settings.sync()
    defaults = ExportDefaultsSettingsService(settings)
    controller = _controller(qtbot, tmp_path, defaults)
    starts: list[object] = []
    messages: list[str] = []
    controller.message.connect(messages.append)
    monkeypatch.setattr(
        CadExportProfileDialog,
        "exec",
        lambda _dialog: QDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok,
    )
    monkeypatch.setattr(controller, "_start_export", lambda *args, **kwargs: starts.append(args))
    assert not controller.route_save_as(tmp_path / "blocked.step")
    assert not controller.route_save_as(tmp_path / "blocked.x_t")
    assert starts == []
    assert any("STEP" in message for message in messages)


def test_save_as_profile_mismatch_fails_before_export_start(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    defaults = _persisted_service(tmp_path / "mismatch.ini")
    controller = _controller(qtbot, tmp_path, defaults)
    starts: list[object] = []

    def choose_brep(dialog: CadExportProfileDialog) -> QDialog.DialogCode:
        dialog.format_combo.setCurrentIndex(
            dialog.format_combo.findData(ExportFormatId.BREP.value)
        )
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(CadExportProfileDialog, "exec", choose_brep)
    monkeypatch.setattr(controller, "_start_export", lambda *args, **kwargs: starts.append(args))
    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QMessageBox.warning",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Ok,
    )
    assert not controller.route_save_as(tmp_path / "wrong.step")
    assert starts == []


def test_existing_destination_requires_bounded_confirmation(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    defaults = _persisted_service(tmp_path / "overwrite-confirm.ini")
    controller = _controller(qtbot, tmp_path, defaults)
    target = tmp_path / "exists.step"
    target.write_bytes(b"old")
    profile = defaults.load().profiles[ExportFormatId.STEP]
    pool = _HoldingThreadPool()
    controller._thread_pool = pool
    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )
    assert not controller._start_export(target, profile, selected=False)
    assert target.read_bytes() == b"old"
    assert pool.tasks == []

    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    assert controller._start_export(target, profile, selected=False)
    pool.tasks[0].run()
    assert target.read_bytes() != b"old"

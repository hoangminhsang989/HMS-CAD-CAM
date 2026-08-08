"""Stage15A WP3 deterministic long-running export UI lifecycle tests."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMainWindow, QMessageBox

from hms_cadcam.cad.export_models import ExportFormatId, ExportProfile
from hms_cadcam.cad.export_service import (
    BackendWriteMetadata,
    CadExportService,
    ExportCancellationState,
    ExportRequest,
)
from hms_cadcam.cad.models import CadDocumentId, CadGeometryKind
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cad_export import CadExportUiController
from hms_cadcam.ui.cad_export_status import (
    CadExportStatusSurface,
    ExportOperationState,
)


class _HeldBackend:
    supported_formats = frozenset({ExportFormatId.STEP})
    unavailable_reason = None

    def __init__(self, entered: Event, release: Event) -> None:
        self.entered = entered
        self.release = release

    def write(
        self, request: ExportRequest, temporary_path: Path
    ) -> BackendWriteMetadata:
        self.entered.set()
        assert self.release.wait(5), "test did not release held export"
        temporary_path.write_bytes(b"long-running-export")
        return BackendWriteMetadata("held responsiveness writer", 1)


def _controller(
    qtbot,
    tmp_path: Path,
    backend: object,
    *,
    selection: tuple[object, ...] = (),
) -> tuple[CadExportUiController, CadExportStatusSurface]:
    source = tmp_path / "source.step"
    source.write_bytes(b"source")
    project_service = ProjectService.create_default(tmp_path / "config")
    project_service.commit_document_open(project_service.prepare_document_open(source))
    window = QMainWindow()
    qtbot.addWidget(window)
    controller = CadExportUiController(
        window,
        CadExportService(backend),
        project_service,
        lambda: CadDocumentId("responsive-document"),
        lambda: CadGeometryKind.BREP,
        lambda: selection,
        lambda: True,
    )
    surface = CadExportStatusSurface(controller.cancel_active_export, window)
    qtbot.addWidget(surface)
    controller.operation_state_changed.connect(surface.handle_export_event)
    window.show()
    qtbot.waitUntil(window.isVisible)
    return controller, surface


def test_held_writer_keeps_qt_heartbeat_alive_and_cancel_is_immediate(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    entered = Event()
    release = Event()
    controller, surface = _controller(
        qtbot, tmp_path, _HeldBackend(entered, release)
    )
    heartbeat = [0]
    timer = QTimer()
    timer.setInterval(0)
    timer.timeout.connect(lambda: heartbeat.__setitem__(0, heartbeat[0] + 1))
    timer.start()
    busy: list[bool] = []
    warnings: list[str] = []
    controller.busy_changed.connect(busy.append)
    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QMessageBox.warning",
        lambda *_args, **_kwargs: warnings.append("warning"),
    )
    target = tmp_path / "cancelled.step"
    try:
        assert controller._start_export(
            target,
            ExportProfile.default_for(ExportFormatId.STEP),
            selected=False,
        )
        qtbot.waitUntil(entered.is_set, timeout=5000)
        initial_heartbeat = heartbeat[0]
        qtbot.waitUntil(lambda: heartbeat[0] >= initial_heartbeat + 5, timeout=2000)
        assert controller._active_task is not None
        assert surface.isVisible()
        assert surface.progress_bar.isVisible()

        QTest.mouseClick(surface.cancel_button, Qt.MouseButton.LeftButton)
        assert surface.last_event is not None
        assert surface.last_event.state is ExportOperationState.CANCELLING
        assert busy == [True]
        assert controller._active_request is not None
        assert (
            controller._active_request.cancellation.state
            is ExportCancellationState.CANCEL_REQUESTED
        )
        assert controller._active_task is not None

        release.set()
        qtbot.waitUntil(lambda: controller._active_task is None, timeout=5000)
        assert busy == [True, False]
        assert surface.last_event is not None
        assert surface.last_event.state is ExportOperationState.CANCELLED
        assert warnings == []
        assert not target.exists()
        assert not tuple(tmp_path.glob("*.hms-exporting"))
        assert heartbeat[0] > initial_heartbeat
    finally:
        release.set()
        timer.stop()


def test_cancel_losing_to_commit_reports_commit_then_real_success(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    entered = Event()
    release_writer = Event()
    publication_entered = Event()
    release_publication = Event()
    controller, surface = _controller(
        qtbot, tmp_path, _HeldBackend(entered, release_writer)
    )
    real_rename = os.rename

    def held_rename(source: Path, destination: Path) -> None:
        publication_entered.set()
        assert release_publication.wait(5)
        real_rename(source, destination)

    monkeypatch.setattr("hms_cadcam.cad.export_service.os.rename", held_rename)
    target = tmp_path / "commit-wins.step"
    try:
        assert controller._start_export(
            target,
            ExportProfile.default_for(ExportFormatId.STEP),
            selected=False,
        )
        qtbot.waitUntil(entered.is_set, timeout=5000)
        release_writer.set()
        qtbot.waitUntil(publication_entered.is_set, timeout=5000)
        assert controller._active_request is not None
        assert (
            controller._active_request.cancellation.state
            is ExportCancellationState.COMMITTING
        )
        QTest.mouseClick(surface.cancel_button, Qt.MouseButton.LeftButton)
        assert surface.last_event is not None
        assert surface.last_event.state is ExportOperationState.COMMITTING
        assert surface.cancel_button.isHidden()
        assert controller._active_task is not None

        release_publication.set()
        qtbot.waitUntil(lambda: controller._active_task is None, timeout=5000)
        assert surface.last_event is not None
        assert surface.last_event.state is ExportOperationState.SUCCEEDED
        assert target.read_bytes() == b"long-running-export"
        assert not tuple(tmp_path.glob("*.hms-exporting"))
    finally:
        release_writer.set()
        release_publication.set()


def test_confirmed_replace_cancel_preserves_old_bytes_and_safe_default(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    entered = Event()
    release = Event()
    controller, surface = _controller(
        qtbot, tmp_path, _HeldBackend(entered, release)
    )
    monkeypatch.setattr(
        "hms_cadcam.ui.cad_export.QMessageBox.question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )
    target = tmp_path / "confirmed-replace.step"
    target.write_bytes(b"original-before-confirmation")
    safe_profile = ExportProfile.default_for(ExportFormatId.STEP)
    try:
        assert controller._start_export(target, safe_profile, selected=False)
        qtbot.waitUntil(entered.is_set, timeout=5000)
        assert controller._active_request is not None
        assert controller._active_request.overwrite_policy.value == "replace_existing"
        assert safe_profile.overwrite_policy.value == "fail_if_exists"
        QTest.mouseClick(surface.cancel_button, Qt.MouseButton.LeftButton)
        release.set()
        qtbot.waitUntil(lambda: controller._active_task is None, timeout=5000)
        assert surface.last_event is not None
        assert surface.last_event.state is ExportOperationState.CANCELLED
        assert target.read_bytes() == b"original-before-confirmation"
        assert controller.profiles[ExportFormatId.STEP].overwrite_policy.value == (
            "fail_if_exists"
        )
        assert not tuple(tmp_path.glob("*.hms-exporting"))
    finally:
        release.set()


def test_all_three_entry_points_converge_on_one_start_export(
    qtbot, tmp_path: Path, monkeypatch
) -> None:
    entered = Event()
    release = Event()
    controller, _surface = _controller(
        qtbot,
        tmp_path,
        _HeldBackend(entered, release),
        selection=(object(),),
    )
    interactive: list[bool] = []
    started: list[tuple[Path, bool]] = []
    profile = ExportProfile.default_for(ExportFormatId.STEP)
    monkeypatch.setattr(
        controller,
        "_interactive_export",
        lambda *, selected: interactive.append(selected),
    )
    controller.export_document()
    controller.export_selected()
    assert interactive == [False, True]

    monkeypatch.setattr(controller, "_request_profile", lambda _format: profile)
    monkeypatch.setattr(
        controller,
        "_start_export",
        lambda target, _profile, *, selected: (
            started.append((target, selected)) or True
        ),
    )
    save_as_target = tmp_path / "save-as.step"
    assert controller.route_save_as(save_as_target)
    assert started == [(save_as_target, False)]

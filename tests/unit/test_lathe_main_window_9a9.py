"""Feature topology and project lifecycle integration tests for Stage 9A.9."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QSettings
from PySide6.QtWidgets import QDockWidget

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.lathe_session import LatheSessionController, LatheUiContext
from hms_cadcam.ui.lathe_presenter import LatheQtPresenter
from hms_cadcam.ui.lathe_workspace import LatheWorkspace
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from hms_cadcam.ui.workspace_shell import WorkspaceId
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend

from _lathe_fixtures import setup_id, stable_uuid
from _lathe_ui_fixtures import application, selection_context


def _flags(enabled: bool) -> UiFeatureFlags:
    return UiFeatureFlags({UiFeatureFlag.LATHE_9A9: enabled})


def _window(tmp_path: Path, *, lathe: bool) -> MainWindow:
    application()
    return MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("stage9a9 test"),
        UnavailableCadViewportBackend("stage9a9 test"),
        layout_store=WorkspaceLayoutStore(
            QSettings(
                str(tmp_path / "workspace.ini"),
                QSettings.Format.IniFormat,
            )
        ),
        ui_feature_flags=_flags(lathe),
    )


def _dispose(window: MainWindow) -> None:
    window.close()
    application().processEvents()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_feature_off_preserves_exact_legacy_topology(tmp_path: Path) -> None:
    window = _window(tmp_path, lathe=False)
    assert not hasattr(window, "lathe_workspace")
    assert not hasattr(window, "lathe_dock")
    assert not hasattr(window, "_lathe_session_controller")
    assert window.findChild(LatheWorkspace, "LatheWorkspace") is None
    assert window.findChild(QDockWidget, "LatheWorkspaceDock") is None
    action = window.workspace_bar.actions_by_workspace[WorkspaceId.LATHE]
    assert not action.isEnabled()
    assert window.workspace_bar.set_active_workspace(WorkspaceId.LATHE) is WorkspaceId.HOME
    _dispose(window)


def test_feature_on_creates_one_workspace_but_no_hidden_session_without_context(
    tmp_path: Path,
) -> None:
    window = _window(tmp_path, lathe=True)
    assert len(window.findChildren(LatheWorkspace, "LatheWorkspace")) == 1
    assert len(window.findChildren(QDockWidget, "LatheWorkspaceDock")) == 1
    assert window._lathe_session_controller.presenter is None
    assert window._lathe_session_controller.service is None
    assert not window.workspace_bar.actions_by_workspace[WorkspaceId.LATHE].isEnabled()
    assert window.lathe_dock.isHidden()
    assert not hasattr(window.workspace_bar, "lathe_presenter")
    assert not hasattr(window.workspace_bar, "lathe_widget")
    _dispose(window)


def test_offscreen_dock_callback_is_safe_after_main_window_deletion(
    tmp_path: Path,
) -> None:
    window = _window(tmp_path, lathe=False)
    window.show()
    application().processEvents()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application().processEvents()


def test_repeated_feature_on_window_construction_has_no_cross_window_leakage(
    tmp_path: Path,
) -> None:
    first = _window(tmp_path / "first", lathe=True)
    second = _window(tmp_path / "second", lathe=True)
    assert first.lathe_workspace is not second.lathe_workspace
    assert first._lathe_session_controller is not second._lathe_session_controller
    assert first.findChild(QDockWidget, "LatheWorkspaceDock") is first.lathe_dock
    assert second.findChild(QDockWidget, "LatheWorkspaceDock") is second.lathe_dock
    _dispose(first)
    _dispose(second)


def _context(
    index: int = 1,
    *,
    document: str = "lathe-document",
    source_index: int = 1,
    generation: int = 3,
    setup_index: int | None = 1,
    read_only: bool = False,
) -> LatheUiContext:
    return LatheUiContext(
        stable_uuid(f"ui-project/{index}"),
        CadDocumentId(document),
        stable_uuid(f"ui-source/{source_index}"),
        generation,
        None if setup_index is None else setup_id(setup_index),
        read_only,
    )


def test_session_controller_reuses_same_context_and_transitions_lifecycle() -> None:
    workspace = LatheWorkspace()
    controller = LatheSessionController(
        workspace,
        lambda: selection_context(),
    )
    initial = _context()
    controller.update_context(initial)
    presenter = controller.presenter
    service = controller.service
    assert presenter is not None and service is not None
    controller.update_context(initial)
    assert controller.presenter is presenter
    assert controller.service is service
    controller.update_context(_context(setup_index=2))
    assert controller.service is service
    assert service.session.setup_id == setup_id(2)
    controller.update_context(_context(setup_index=2, read_only=True))
    assert service.session.read_only
    controller.update_context(
        _context(source_index=2, generation=4, setup_index=2)
    )
    assert controller.service is service
    assert service.session.source_id == stable_uuid("ui-source/2")
    assert service.session.generation == 4
    controller.teardown()
    assert service.session.closed
    assert not presenter.is_alive
    assert controller.presenter is None
    assert workspace.presenter is None
    workspace.deleteLater()


def test_project_or_document_switch_closes_old_session_and_isolates_operations() -> None:
    workspace = LatheWorkspace()
    controller = LatheSessionController(workspace, lambda: selection_context())
    controller.update_context(_context())
    first_presenter = controller.presenter
    first_service = controller.service
    assert first_presenter is not None and first_service is not None
    first_presenter.create_operation(LatheStrategyId.FACE)
    assert len(first_presenter.snapshot.operations) == 1
    controller.update_context(_context(index=2, document="other-document"))
    assert first_service.session.closed
    assert not first_presenter.is_alive
    assert controller.presenter is not first_presenter
    assert controller.presenter is not None
    assert controller.presenter.snapshot.operations == ()
    controller.teardown()
    workspace.deleteLater()


def test_thirty_open_close_cycles_leave_no_live_presenter_or_service() -> None:
    workspace = LatheWorkspace()
    controller = LatheSessionController(workspace, lambda: selection_context())
    for index in range(30):
        controller.update_context(
            _context(index=index + 1, document=f"document-{index + 1}")
        )
        assert controller.presenter is not None
        assert controller.service is not None
        controller.teardown()
        assert controller.presenter is None
        assert controller.service is None
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert controller.findChildren(LatheQtPresenter) == []
    workspace.deleteLater()


def test_workspace_bar_configuration_is_idempotent_and_keeps_one_action() -> None:
    window = _window(Path(".pytest_tmp") / "stage9a9_bar", lathe=True)
    bar = window.workspace_bar
    before = tuple(bar.actions())
    bar.configure_lathe(enabled=True, explanation="Lathe ready")
    bar.configure_lathe(enabled=True, explanation="Lathe ready")
    assert tuple(bar.actions()) == before
    assert len(before) == 7
    assert bar.actions_by_workspace[WorkspaceId.LATHE].isEnabled()
    _dispose(window)

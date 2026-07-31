"""Qt bridge, explicit UI actions, lifecycle, feature and I18N acceptance."""

from __future__ import annotations

from pathlib import Path
from threading import Event, get_ident

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, Slot
from PySide6.QtWidgets import QPushButton
from shiboken6 import isValid

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.cam.domain import CylinderStock, Length, LengthUnit, WcsFrame
from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.toolpath import (
    AxialDrillToolpathGenerator,
    LatheCancelDecision,
    LatheToolpathCancelledError,
    LatheToolpathCoordinator,
    LatheToolpathJobState,
    LatheToolpathGeneratorRegistry,
    OdFinishToolpathGenerator,
    OdRoughToolpathGenerator,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.lathe_session import LatheSessionController, LatheUiContext
from hms_cadcam.ui.lathe_toolpath import (
    LatheToolpathQtBridge,
    LatheToolpathUiController,
    LatheToolpathUiStateCode,
)
from hms_cadcam.ui.lathe_workspace import LatheWorkspace
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.ui.workspace_layout import WorkspaceLayoutStore
from hms_cadcam.viewer.models import SelectionMode
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend
from tests.unit._lathe_fixtures import setup_id, stable_uuid
from tests.unit._lathe_toolpath_fixtures import ready_request, stock_snapshot
from tests.unit._lathe_ui_fixtures import (
    application,
    presenter_for,
    selection_context,
)


class _PreviewSink:
    def __init__(self, *, fail_publication: bool = False) -> None:
        self.fail_publication = fail_publication
        self.publications = []
        self.cleared = []
        self.publish_thread_ids: list[int] = []

    def publish(self, result) -> bool:
        self.publish_thread_ids.append(get_ident())
        if self.fail_publication:
            return False
        self.publications.append(result)
        return True

    def clear(self, ownership) -> bool:
        self.cleared.append(ownership)
        return True


def _ready_presenter(strategy_id: LatheStrategyId):
    mode = (
        SelectionMode.VERTEX
        if strategy_id is LatheStrategyId.AXIAL_DRILL
        else SelectionMode.EDGE
    )
    presenter, _catalog, reference = presenter_for(
        strategy_id,
        selection_provider=lambda: selection_context(mode),
    )
    presenter.create_operation(strategy_id)
    active = presenter.active_operation
    assert active is not None
    presenter.bind_tool(
        active.ownership.operation_id,
        reference,
        active.revision,
    )
    active = presenter.active_operation
    assert active is not None
    presenter.bind_current_geometry(
        active.ownership.operation_id,
        active.revision,
    )
    active = presenter.active_operation
    assert active is not None and active.readiness.value == "READY"
    return presenter, active


def _controller_for(strategy_id: LatheStrategyId, *, sink=None, coordinator=None):
    presenter, operation = _ready_presenter(strategy_id)
    selected_sink = sink or _PreviewSink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        selected_sink,
        coordinator=coordinator,
    )
    return presenter, operation, selected_sink, controller


def _dispose_controller(controller: LatheToolpathUiController) -> None:
    controller.shutdown(wait=True)
    controller.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_explicit_preview_only_and_cache_fresh_share_gui_publication_gate(qtbot) -> None:
    application()
    presenter, operation, sink, controller = _controller_for(
        LatheStrategyId.OD_ROUGH
    )
    gui_thread = get_ident()
    assert sink.publications == []
    assert controller.state.code is LatheToolpathUiStateCode.READY
    first = controller.preview(operation)
    assert first is not None and first.accepted
    qtbot.waitUntil(
        lambda: controller.state.code is LatheToolpathUiStateCode.PREVIEW_READY,
        timeout=5000,
    )
    assert len(sink.publications) == 1
    assert sink.publish_thread_ids == [gui_thread]

    second = controller.preview(presenter.active_operation)
    assert second is not None and second.accepted
    qtbot.waitUntil(
        lambda: controller.state.code is LatheToolpathUiStateCode.CACHE_HIT,
        timeout=5000,
    )
    assert len(sink.publications) == 2
    assert sink.publications[1].motions is sink.publications[0].motions
    assert sink.publish_thread_ids == [gui_thread, gui_thread]
    _dispose_controller(controller)
    presenter.teardown()


def test_incomplete_and_unsupported_operations_fail_closed_without_submission() -> None:
    application()
    presenter, _catalog, _reference = presenter_for(LatheStrategyId.OD_ROUGH)
    presenter.create_operation(LatheStrategyId.OD_ROUGH)
    incomplete = presenter.active_operation
    assert incomplete is not None
    sink = _PreviewSink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        sink,
    )
    assert controller.preview(incomplete) is None
    assert controller.state.code is LatheToolpathUiStateCode.INVALID_REQUEST
    assert sink.publications == []
    _dispose_controller(controller)
    presenter.teardown()

    unsupported_presenter, unsupported, sink, controller = _controller_for(
        LatheStrategyId.OD_THREAD
    )
    assert controller.preview(unsupported) is None
    assert controller.state.code is LatheToolpathUiStateCode.THREAD_UNSUPPORTED_V2
    assert controller.state.diagnostic is not None
    assert controller.state.diagnostic.code.value == "thread_toolpath_not_implemented_v2"
    assert sink.publications == []
    _dispose_controller(controller)
    unsupported_presenter.teardown()


def test_publication_failure_is_never_reported_as_preview_success(qtbot) -> None:
    application()
    sink = _PreviewSink(fail_publication=True)
    presenter, operation, _sink, controller = _controller_for(
        LatheStrategyId.OD_FINISH,
        sink=sink,
    )
    controller.preview(operation)
    qtbot.waitUntil(
        lambda: controller.state.code is LatheToolpathUiStateCode.PUBLICATION_FAILED,
        timeout=5000,
    )
    assert sink.publications == []
    assert controller.state.code is not LatheToolpathUiStateCode.PREVIEW_READY
    _dispose_controller(controller)
    presenter.teardown()


class _BlockingRoughGenerator:
    strategy_id = LatheStrategyId.OD_ROUGH

    def __init__(self) -> None:
        self.started = Event()
        self.cancelled = Event()

    def generate(self, request, cancellation):
        self.started.set()
        while not self.cancelled.wait(0.01):
            if cancellation():
                self.cancelled.set()
                raise LatheToolpathCancelledError("cancelled")
        raise LatheToolpathCancelledError("cancelled")


class _ReleasableRoughGenerator:
    strategy_id = LatheStrategyId.OD_ROUGH

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.delegate = OdRoughToolpathGenerator()

    def generate(self, request, cancellation):
        self.started.set()
        assert self.release.wait(5.0)
        return self.delegate.generate(request, cancellation)


class _BridgeReceiver(QObject):
    def __init__(self, deliveries: list[object]) -> None:
        super().__init__()
        self.deliveries = deliveries

    @Slot(object)
    def handle_lathe_toolpath_result(self, result: object) -> None:
        self.deliveries.append(result)


def test_bridge_repeated_binding_is_idempotent_and_deleted_receiver_is_safe(qtbot) -> None:
    application()
    request = ready_request()[2]
    generator = _ReleasableRoughGenerator()
    coordinator = LatheToolpathCoordinator(
        LatheToolpathGeneratorRegistry(
            (
                generator,
                OdFinishToolpathGenerator(),
                AxialDrillToolpathGenerator(),
            )
        )
    )
    bridge = LatheToolpathQtBridge(coordinator)
    deliveries: list[object] = []
    receiver = _BridgeReceiver(deliveries)
    bridge.set_receiver(receiver)
    bridge.set_receiver(receiver)
    receipt = bridge.submit(request)
    assert receipt.accepted and generator.started.wait(5.0)
    receiver.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(receiver)
    generator.release.set()

    def completed() -> bool:
        record = coordinator.job_record(request.job_id)
        return bool(
            record is not None
            and record.state is LatheToolpathJobState.COMPLETED
        )

    qtbot.waitUntil(completed, timeout=5000)
    QCoreApplication.processEvents()
    assert deliveries == []
    bridge.shutdown(wait=True)
    bridge.deleteLater()


def test_cancel_targets_exact_active_job_and_close_ignores_late_result(qtbot) -> None:
    application()
    blocker = _BlockingRoughGenerator()
    coordinator = LatheToolpathCoordinator(
        LatheToolpathGeneratorRegistry(
            (
                blocker,
                OdFinishToolpathGenerator(),
                AxialDrillToolpathGenerator(),
            )
        )
    )
    presenter, operation, sink, controller = _controller_for(
        LatheStrategyId.OD_ROUGH,
        coordinator=coordinator,
    )
    receipt = controller.preview(operation)
    assert receipt is not None and blocker.started.wait(5.0)
    active_job = controller.active_job_id
    assert active_job == receipt.job_id
    assert controller.cancel() is LatheCancelDecision.REQUESTED
    assert controller.state.code is LatheToolpathUiStateCode.CANCELLED
    assert controller.active_job_id is None
    assert blocker.cancelled.wait(5.0)
    QCoreApplication.processEvents()
    assert sink.publications == []
    _dispose_controller(controller)
    presenter.teardown()


def test_workspace_dynamic_actions_are_feature_bound_singletons_and_accessible() -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.OD_ROUGH)
    workspace = LatheWorkspace()
    workspace.bind_presenter(presenter)
    assert not hasattr(workspace, "preview_toolpath_button")
    sink = _PreviewSink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        sink,
    )
    workspace.bind_toolpath_controller(controller)
    workspace.bind_toolpath_controller(controller)
    assert workspace.toolpath_controller is controller
    assert len(
        workspace.findChildren(QPushButton, "LathePreviewToolpathButton")
    ) == 1
    assert len(
        workspace.findChildren(QPushButton, "LatheCancelCalculationButton")
    ) == 1
    assert workspace.preview_toolpath_button.isEnabled()
    assert not workspace.cancel_toolpath_button.isEnabled()
    assert workspace.preview_toolpath_button.accessibleName().strip()
    assert workspace.preview_toolpath_button.accessibleDescription().strip()
    assert workspace.cancel_toolpath_button.accessibleName().strip()
    assert operation == presenter.active_operation
    workspace.bind_toolpath_controller(None)
    assert not hasattr(workspace, "preview_toolpath_button")
    _dispose_controller(controller)
    presenter.teardown()
    workspace.deleteLater()


def test_workspace_edit_invalidates_preview_without_automatic_resubmit(qtbot) -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.OD_ROUGH)
    workspace = LatheWorkspace()
    workspace.bind_presenter(presenter)
    sink = _PreviewSink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        sink,
    )
    workspace.bind_toolpath_controller(controller)
    workspace.preview_toolpath_button.click()
    qtbot.waitUntil(lambda: len(sink.publications) == 1, timeout=5000)
    active = presenter.active_operation
    assert active is not None
    presenter.apply_parameter_changes(
        active.ownership.operation_id,
        (LatheParameterUpdate("feed_mm_per_rev", 0.37),),
        active.revision,
    )
    assert len(sink.publications) == 1
    assert sink.cleared == [operation.ownership]
    assert controller.state.code is LatheToolpathUiStateCode.READY
    workspace.bind_toolpath_controller(None)
    _dispose_controller(controller)
    presenter.teardown()
    workspace.deleteLater()


def test_runtime_retranslation_preserves_operation_revision_result_and_state(qtbot) -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.OD_FINISH)
    workspace = LatheWorkspace()
    workspace.bind_presenter(presenter)
    sink = _PreviewSink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        sink,
    )
    workspace.bind_toolpath_controller(controller)
    controller.preview(operation)
    qtbot.waitUntil(lambda: len(sink.publications) == 1, timeout=5000)
    accepted = sink.publications[0]
    before_revision = presenter.active_operation.revision
    before_state = controller.state
    service = translation_service()
    original_language = service.language
    labels = []
    try:
        for language in UiLanguage:
            service.set_language(language)
            workspace.retranslate_ui(language)
            labels.append(workspace.outcome_label.text())
            assert controller.state == before_state
            assert sink.publications[0] is accepted
            assert presenter.active_operation.revision == before_revision
        assert len(set(labels)) == 3
        assert all("preview_ready" not in label for label in labels)
    finally:
        service.set_language(original_language)
    workspace.bind_toolpath_controller(None)
    _dispose_controller(controller)
    presenter.teardown()
    workspace.deleteLater()


def _cylinder() -> CylinderStock:
    return CylinderStock(
        Length(100.0, LengthUnit.MM),
        Length(100.0, LengthUnit.MM),
        WcsFrame.identity(LengthUnit.MM),
    )


def _context(*, setup_index: int = 1, generation: int = 3) -> LatheUiContext:
    return LatheUiContext(
        stable_uuid("ui-project/12-1"),
        CadDocumentId("lathe-toolpath-document"),
        stable_uuid("source/1"),
        generation,
        setup_id(setup_index),
        False,
        stock=_cylinder(),
    )


def test_session_context_creates_one_controller_and_transition_tears_down_owned_state() -> None:
    application()
    workspace = LatheWorkspace()
    sink = _PreviewSink()
    session_controller = LatheSessionController(
        workspace,
        lambda: selection_context(),
        toolpath_sink=sink,
    )
    session_controller.update_context(_context())
    toolpath = session_controller.toolpath_controller
    assert toolpath is not None
    session_controller.update_context(_context())
    assert session_controller.toolpath_controller is toolpath
    session_controller.update_context(_context(setup_index=2))
    assert session_controller.toolpath_controller is toolpath
    assert toolpath.state.code is LatheToolpathUiStateCode.READY
    session_controller.teardown()
    assert session_controller.toolpath_controller is None
    assert workspace.toolpath_controller is None
    workspace.deleteLater()


def _flags(*, lathe: bool, toolpath: bool) -> UiFeatureFlags:
    return UiFeatureFlags(
        {
            UiFeatureFlag.LATHE_9A9: lathe,
            UiFeatureFlag.LATHE_TOOLPATH_12_1: toolpath,
        }
    )


def _window(tmp_path: Path, *, lathe: bool, toolpath: bool) -> MainWindow:
    application()
    return MainWindow(
        ProjectService.create_default(tmp_path / "config"),
        UnavailableCadKernel("stage12.1 test"),
        UnavailableCadViewportBackend("stage12.1 test"),
        layout_store=WorkspaceLayoutStore(
            QSettings(
                str(tmp_path / "workspace.ini"),
                QSettings.Format.IniFormat,
            )
        ),
        ui_feature_flags=_flags(lathe=lathe, toolpath=toolpath),
    )


def _dispose_window(window: MainWindow) -> None:
    window.close()
    application().processEvents()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_feature_off_has_lathe_9a9_but_no_toolpath_sink_worker_bridge_or_actions(
    tmp_path: Path,
) -> None:
    window = _window(tmp_path, lathe=True, toolpath=False)
    assert hasattr(window, "lathe_workspace")
    assert not hasattr(window, "_lathe_toolpath_sink")
    assert window._lathe_session_controller.toolpath_controller is None
    assert window.findChildren(LatheToolpathUiController) == []
    assert window.findChild(QPushButton, "LathePreviewToolpathButton") is None
    assert window.findChild(QPushButton, "LatheCancelCalculationButton") is None
    _dispose_window(window)


def test_feature_on_creates_singletons_only_after_live_context_and_is_idempotent(
    tmp_path: Path,
) -> None:
    window = _window(tmp_path, lathe=True, toolpath=True)
    assert hasattr(window, "_lathe_toolpath_sink")
    assert window._lathe_session_controller.toolpath_controller is None
    window._lathe_session_controller.update_context(_context())
    controller = window._lathe_session_controller.toolpath_controller
    assert controller is not None
    window._lathe_session_controller.update_context(_context())
    assert window._lathe_session_controller.toolpath_controller is controller
    assert len(window.findChildren(LatheToolpathUiController)) == 1
    assert len(
        window.findChildren(QPushButton, "LathePreviewToolpathButton")
    ) == 1
    assert len(
        window.findChildren(QPushButton, "LatheCancelCalculationButton")
    ) == 1
    _dispose_window(window)

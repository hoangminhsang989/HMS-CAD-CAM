"""Stage 12.3 explicit thread Preview/Cancel UI integration tests."""

from __future__ import annotations

import pytest

from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.toolpath import LatheToolpathDiagnosticCode
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.lathe_toolpath import (
    LatheToolpathUiController,
    LatheToolpathUiStateCode,
)
from hms_cadcam.ui.lathe_workspace import LatheWorkspace
from hms_cadcam.viewer.models import SelectionMode
from tests.unit._lathe_toolpath_fixtures import stock_snapshot
from tests.unit._lathe_ui_fixtures import (
    application,
    presenter_for,
    selection_context,
)


class _Sink:
    def __init__(self, *, publish_ok: bool = True) -> None:
        self.publish_ok = publish_ok
        self.publications = []
        self.cleared = []

    def publish(self, result) -> bool:
        self.publications.append(result)
        return self.publish_ok

    def clear(self, ownership) -> bool:
        self.cleared.append(ownership)
        return True


def _ready_presenter(strategy_id: LatheStrategyId):
    presenter, _catalog, reference = presenter_for(
        strategy_id,
        selection_provider=lambda: selection_context(SelectionMode.EDGE),
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


def _dispose(controller: LatheToolpathUiController, presenter) -> None:
    controller.shutdown(wait=True)
    controller.deleteLater()
    presenter.teardown()


@pytest.mark.parametrize("strategy_id", (LatheStrategyId.OD_THREAD, LatheStrategyId.ID_THREAD))
def test_ready_thread_submits_only_on_explicit_preview_and_exposes_limitations(
    qtbot,
    strategy_id: LatheStrategyId,
) -> None:
    application()
    presenter, operation = _ready_presenter(strategy_id)
    sink = _Sink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(
            inner_diameter_mm=10.0
            if strategy_id is LatheStrategyId.ID_THREAD
            else 0.0
        ),
        sink,
    )
    assert controller.state.code is LatheToolpathUiStateCode.READY
    assert sink.publications == []
    receipt = controller.preview(operation)
    assert receipt is not None and receipt.accepted
    qtbot.waitUntil(
        lambda: controller.state.code is LatheToolpathUiStateCode.PREVIEW_READY,
        timeout=5000,
    )
    assert len(sink.publications) == 1
    assert sink.publications[0].strategy_id is strategy_id
    assert controller.state.diagnostic is not None
    assert controller.state.diagnostic.code is (
        LatheToolpathDiagnosticCode.PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW
    )
    assert {item.code for item in controller.state.diagnostics} == {
        LatheToolpathDiagnosticCode.PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW,
        LatheToolpathDiagnosticCode.THREAD_FEED_DERIVED_FROM_PITCH,
        LatheToolpathDiagnosticCode.NOMINAL_INFEED_ANGLE_METADATA_ONLY,
        LatheToolpathDiagnosticCode.NOT_MACHINE_READY,
    }

    cached = controller.preview(operation)
    assert cached is not None and cached.accepted
    qtbot.waitUntil(
        lambda: controller.state.code is LatheToolpathUiStateCode.CACHE_HIT,
        timeout=5000,
    )
    assert len(sink.publications) == 2
    assert sink.publications[1].motions is sink.publications[0].motions
    _dispose(controller, presenter)


def test_internal_thread_missing_bore_never_submits_or_publishes() -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.ID_THREAD)
    sink = _Sink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(inner_diameter_mm=0.0),
        sink,
    )
    assert controller.preview(operation) is None
    assert controller.state.code is LatheToolpathUiStateCode.INVALID_REQUEST
    assert controller.state.diagnostic is not None
    assert controller.state.diagnostic.code is (
        LatheToolpathDiagnosticCode.MISSING_INTERNAL_BORE
    )
    assert sink.publications == []
    _dispose(controller, presenter)


def test_thread_publication_failure_is_not_preview_success(qtbot) -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.OD_THREAD)
    sink = _Sink(publish_ok=False)
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        sink,
    )
    receipt = controller.preview(operation)
    assert receipt is not None and receipt.accepted
    qtbot.waitUntil(
        lambda: controller.state.code is LatheToolpathUiStateCode.PUBLICATION_FAILED,
        timeout=5000,
    )
    assert controller.state.diagnostic is not None
    assert controller.state.diagnostic.code is (
        LatheToolpathDiagnosticCode.PUBLICATION_FAILED
    )
    _dispose(controller, presenter)


def test_workspace_displays_all_thread_limitations_and_retranslates(qtbot) -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.OD_THREAD)
    workspace = LatheWorkspace()
    workspace.bind_presenter(presenter)
    sink = _Sink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        sink,
    )
    workspace.bind_toolpath_controller(controller)
    controller.preview(operation)
    qtbot.waitUntil(
        lambda: controller.state.code is LatheToolpathUiStateCode.PREVIEW_READY,
        timeout=5000,
    )
    service = translation_service()
    original = service.language
    labels = []
    try:
        for language in UiLanguage:
            service.set_language(language)
            workspace.retranslate_ui(language)
            labels.append(workspace.outcome_label.text())
        assert len(set(labels)) == 3
        english = labels[tuple(UiLanguage).index(UiLanguage.EN_US)]
        assert "Phase-neutral" in english
        assert "thread pitch" in english
        assert "metadata only" in english
        assert "Not machine-ready" in english
        assert "phase_neutral_synchronized_centerline_preview" not in english
        assert sink.publications[0].strategy_id is LatheStrategyId.OD_THREAD
    finally:
        service.set_language(original)
    workspace.bind_toolpath_controller(None)
    workspace.deleteLater()
    _dispose(controller, presenter)


def test_workspace_localizes_missing_bore_in_invalid_request_status() -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.ID_THREAD)
    workspace = LatheWorkspace()
    workspace.bind_presenter(presenter)
    sink = _Sink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        sink,
    )
    workspace.bind_toolpath_controller(controller)
    service = translation_service()
    original = service.language
    try:
        service.set_language(UiLanguage.EN_US)
        assert controller.preview(operation) is None
        workspace.retranslate_ui(UiLanguage.EN_US)
        assert "explicit positive bore" in workspace.outcome_label.text()
        assert "missing_internal_bore" not in workspace.outcome_label.text()
    finally:
        service.set_language(original)
    workspace.bind_toolpath_controller(None)
    workspace.deleteLater()
    _dispose(controller, presenter)


def test_workspace_maps_invalid_pitch_field_to_thread_specific_status() -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.OD_THREAD)
    workspace = LatheWorkspace()
    workspace.bind_presenter(presenter)
    service = translation_service()
    original = service.language
    try:
        service.set_language(UiLanguage.EN_US)
        workspace.retranslate_ui(UiLanguage.EN_US)
        outcome = presenter.apply_parameter_changes(
            operation.ownership.operation_id,
            (LatheParameterUpdate("pitch_mm", 0.0),),
            operation.revision,
        )
        assert not outcome.accepted
        assert "Thread pitch must be a positive finite value" in (
            workspace.outcome_label.text()
        )
        assert "invalid_pitch" not in workspace.outcome_label.text()
    finally:
        service.set_language(original)
    presenter.teardown()
    workspace.deleteLater()

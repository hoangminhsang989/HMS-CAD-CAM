"""Stage 12.2 explicit UI submission and fail-closed threading tests."""

from __future__ import annotations

from hms_cadcam.cam.lathe.parameters import LatheParameterUpdate
from hms_cadcam.cam.lathe.toolpath import LatheToolpathDiagnosticCode
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.lathe_toolpath import (
    LatheToolpathUiController,
    LatheToolpathUiStateCode,
)
from hms_cadcam.viewer.models import SelectionMode
from tests.unit._lathe_toolpath_fixtures import stock_snapshot
from tests.unit._lathe_ui_fixtures import (
    application,
    presenter_for,
    selection_context,
)


NEW_STRATEGIES = (
    LatheStrategyId.FACE,
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
    LatheStrategyId.OD_GROOVE,
    LatheStrategyId.ID_GROOVE,
    LatheStrategyId.PART_OFF,
)


class _Sink:
    def __init__(self) -> None:
        self.publications = []
        self.cleared = []

    def publish(self, result) -> bool:
        self.publications.append(result)
        return True

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
    assert active is not None
    if strategy_id is LatheStrategyId.FACE:
        presenter.apply_parameter_changes(
            active.ownership.operation_id,
            (
                LatheParameterUpdate("face_z_mm", -2.0),
                LatheParameterUpdate("outer_diameter_mm", 80.0),
                LatheParameterUpdate("max_depth_of_cut_mm", 0.75),
                LatheParameterUpdate("finish_allowance_mm", 0.25),
            ),
            active.revision,
        )
        active = presenter.active_operation
        assert active is not None
    assert active.readiness.value == "READY"
    return presenter, active


def _stock(strategy_id: LatheStrategyId):
    return stock_snapshot(
        inner_diameter_mm=(
            10.0
            if strategy_id in {
                LatheStrategyId.ID_ROUGH,
                LatheStrategyId.ID_FINISH,
                LatheStrategyId.ID_GROOVE,
            }
            else 0.0
        )
    )


def _dispose(controller: LatheToolpathUiController, presenter) -> None:
    controller.shutdown(wait=True)
    controller.deleteLater()
    presenter.teardown()


def test_six_new_strategies_submit_only_on_explicit_preview_and_publish(qtbot) -> None:
    application()
    for strategy_id in NEW_STRATEGIES:
        presenter, operation = _ready_presenter(strategy_id)
        sink = _Sink()
        controller = LatheToolpathUiController(
            presenter.facade.service,
            _stock(strategy_id),
            sink,
        )
        assert controller.state.code is LatheToolpathUiStateCode.READY
        assert sink.publications == []
        receipt = controller.preview(operation)
        assert receipt is not None and receipt.accepted
        qtbot.waitUntil(
            lambda: controller.state.code
            is LatheToolpathUiStateCode.PREVIEW_READY,
            timeout=5000,
        )
        assert len(sink.publications) == 1
        assert sink.publications[0].strategy_id is strategy_id
        _dispose(controller, presenter)


def test_internal_ui_propagates_missing_bore_without_worker_submission() -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.ID_ROUGH)
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


def test_thread_ui_is_editable_ready_but_preview_fails_closed_with_v2_code() -> None:
    application()
    presenter, operation = _ready_presenter(LatheStrategyId.OD_THREAD)
    sink = _Sink()
    controller = LatheToolpathUiController(
        presenter.facade.service,
        stock_snapshot(),
        sink,
    )
    assert operation.readiness.value == "READY"
    assert controller.preview(operation) is None
    assert controller.state.code is LatheToolpathUiStateCode.THREAD_UNSUPPORTED_V2
    assert controller.state.diagnostic is not None
    assert controller.state.diagnostic.code is (
        LatheToolpathDiagnosticCode.THREAD_TOOLPATH_NOT_IMPLEMENTED_V2
    )
    assert sink.publications == []
    _dispose(controller, presenter)

"""Offscreen playback, language and feature-off tests for Stage 12.6A."""

from __future__ import annotations

from hms_cadcam.cam.lathe.simulation.models import SimulationSettings, ToolEnvelope
from hms_cadcam.cam.lathe.simulation.service import LatheSimulationService, SimulationRequest
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.i18n import UiLanguage, translation_service
from hms_cadcam.ui.lathe_simulation import LatheSimulationWindow, LatheSimulationWindowManager
from tests.unit._lathe_toolpath_fixtures import generate, ready_request


def _simulation():
    _service, _operation, request = ready_request(LatheStrategyId.OD_FINISH)
    return LatheSimulationService().run(
        SimulationRequest(
            (generate(request),), request.stock,
            ToolEnvelope(0.4, 4.0, 0.0, 4.0, holder_radial_offset_mm=100.0),
            SimulationSettings(sampling_resolution_mm=2.0, maximum_stock_stations=100),
        )
    )


def test_window_playback_controls_seek_speed_layers_and_layout(qtbot) -> None:
    window = LatheSimulationWindow()
    qtbot.addWidget(window)
    result = _simulation()
    window.set_result(result)
    window.show()
    assert window.minimumSize().width() <= window.width()
    assert window.minimumSize().height() <= window.height()
    assert set(window.control_buttons) == {"run", "pause", "resume", "stop", "reset", "first", "back", "forward", "last"}
    window.last()
    assert window.timeline.value() == len(result.frames) - 1
    window.step_back()
    assert window.timeline.value() == len(result.frames) - 2
    window.speed.setCurrentText("4×")
    window.layer_checks["holder"].setChecked(False)
    assert not window.canvas.layers["holder"]
    window.reset()
    assert window.timeline.value() == 0


def test_language_switch_does_not_change_result_or_frame(qtbot) -> None:
    window = LatheSimulationWindow()
    qtbot.addWidget(window)
    result = _simulation()
    window.set_result(result)
    window.timeline.setValue(min(2, len(result.frames) - 1))
    fingerprint = result.fingerprint
    frame = window.timeline.value()
    service = translation_service()
    original = service.language
    try:
        for language in UiLanguage:
            service.set_language(language)
            assert window.timeline.value() == frame
            assert result.fingerprint == fingerprint
            assert window.windowTitle()
    finally:
        service.set_language(original)


def test_manager_open_is_idempotent_and_feature_off_fails_closed(qtbot) -> None:
    parent = LatheSimulationWindow()
    qtbot.addWidget(parent)
    assert LatheSimulationWindowManager(parent, enabled=False).open(_simulation()) is None
    manager = LatheSimulationWindowManager(parent, enabled=True)
    first = manager.open(_simulation())
    second = manager.open()
    assert first is second
    flags = UiFeatureFlags({UiFeatureFlag.LATHE_SIMULATION_12_6A: True})
    assert not flags.is_enabled(UiFeatureFlag.LATHE_SIMULATION_12_6A)
    enabled = UiFeatureFlags({UiFeatureFlag.LATHE_SIMULATION_12_6A: True, UiFeatureFlag.LATHE_9A9: True, UiFeatureFlag.LATHE_TOOLPATH_12_1: True})
    assert enabled.is_enabled(UiFeatureFlag.LATHE_SIMULATION_12_6A)


def test_30_open_close_cycles_leave_no_active_timer(qtbot) -> None:
    for _index in range(30):
        window = LatheSimulationWindow()
        qtbot.addWidget(window)
        window.set_result(_simulation())
        window.show()
        window.play()
        window.close()
        assert not window._timer.isActive()

"""R241 lazy window, playback, worker, and repeated-session lifecycle tests."""

import gc

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from hms_cadcam.ui.machining_simulation_window import MachiningSimulationWindow
from tests.unit.test_simulation_service import _source


def _inputs():
    operation, artifact, setup, tool, holder, assembly, request, _scene = _source()
    from hms_cadcam.cam.simulation.runtime import SimulationInputSnapshot

    return SimulationInputSnapshot(
        operation, artifact, setup, tool, assembly, holder, None, request
    )


def test_window_appears_before_material_compute_and_runs_worker(qtbot) -> None:
    window = MachiningSimulationWindow(_inputs)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    assert not window.worker_active
    window.prepare_scene()
    qtbot.waitUntil(lambda: window._inputs is not None)
    assert not window.worker_active
    window.calculate()
    qtbot.waitUntil(lambda: not window.worker_active, timeout=10_000)
    assert "HEIGHTFIELD_3AXIS" in window.result_label.text()
    assert window.progress.value() == 100


def test_worker_cancellation_has_explicit_cancelled_state(qtbot) -> None:
    window = MachiningSimulationWindow(_inputs)
    qtbot.addWidget(window)
    window.prepare_scene()
    qtbot.waitUntil(lambda: window._inputs is not None)
    window.calculate()
    window.cancel()
    qtbot.waitUntil(lambda: not window.worker_active, timeout=10_000)
    assert window.cancel_button.isEnabled() is False


def test_repeated_open_prepare_close_releases_owned_windows(qtbot) -> None:
    pool = QThreadPool.globalInstance()
    baseline_active = pool.activeThreadCount()
    windows = []
    for _index in range(12):
        window = MachiningSimulationWindow(_inputs)
        qtbot.addWidget(window)
        window.show()
        window.prepare_scene()
        qtbot.waitUntil(lambda owned=window: owned._inputs is not None)
        window.close()
        windows.append(window)
    QApplication.sendPostedEvents()
    gc.collect()
    assert pool.activeThreadCount() <= baseline_active
    assert all(not window.worker_active for window in windows)

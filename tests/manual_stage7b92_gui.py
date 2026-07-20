"""Real Windows/PySide6/OCP smoke test for Boring Viewer Stage 7B.9.2.

The fixture is built directly from the Boring foundation; no Boring UI is used.
Run from the repository root with the project virtual environment.
"""

from __future__ import annotations

import logging
import sys
import tempfile
from dataclasses import fields, replace
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.toolpath import LinearMove, ToolpathArtifact
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend
from tests.unit.test_boring_strategy import _artifact, _inputs, _pattern, _strategy

logger = logging.getLogger(__name__)


def _generated(*, hole_count: int, dwell_seconds: float):
    pattern = _pattern(*(
        (float(index * 12), float(index * 4))
        for index in range(hole_count)
    ))
    generator, inputs, _resolved = _inputs(strategy=_strategy(
        pattern=pattern,
        dwell_seconds=dwell_seconds,
    ))
    artifact, _operation, _token = _artifact(generator, inputs)
    return artifact


def _for_operation(artifact, operation_id):
    return replace(
        artifact,
        source_operation_id=operation_id,
        events=tuple(
            replace(event, source_operation_id=operation_id)
            for event in artifact.events
        ),
        artifact_fingerprint=None,
    )


def _unchecked(artifact, events):
    candidate = object.__new__(ToolpathArtifact)
    for field in fields(artifact):
        object.__setattr__(
            candidate,
            field.name,
            tuple(events) if field.name == "events" else getattr(artifact, field.name),
        )
    return candidate


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage7b92_")
    root = Path(temporary.name)
    service = ProjectService.create_default(root / "config")
    service.new_project(root, "Boring Viewer")
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.resize(1180, 720)
    window.show()
    failures: list[BaseException] = []

    def finish(error: BaseException | None = None) -> None:
        if error is not None:
            failures.append(error)
        try:
            window.cad_controller.shutdown()
            window.viewport.shutdown()
            window.hide()
        except RuntimeError:
            pass
        application.quit()

    def verify() -> None:
        try:
            single = _generated(hole_count=1, dwell_seconds=0.0)
            multi = _for_operation(
                _generated(hole_count=3, dwell_seconds=0.25),
                single.source_operation_id,
            )
            assert window.viewport.display_toolpath(single)
            first_native = backend._toolpaths[single.source_operation_id]
            assert window.viewport.toolpath_presentations[0].pass_count == 1
            assert any(
                item.semantic == "controlled_retract"
                for item in window.viewport.toolpath_presentations[0].segments
            )
            assert window.viewport.set_toolpath_visibility(
                single.source_operation_id, False
            )
            assert window.viewport.set_toolpath_visibility(
                single.source_operation_id, True
            )
            assert window.viewport.display_toolpath(multi)
            presentation = window.viewport.toolpath_presentations[0]
            assert presentation.pass_count == 3
            assert sum(
                item.semantic == "dwell" for item in presentation.annotations
            ) == 3
            assert backend._toolpaths[single.source_operation_id] != first_native

            events = list(multi.events)
            descent = next(
                event for event in events
                if event.provenance == "bore.hole.0.descent"
            )
            final_index = next(
                index for index, event in enumerate(events)
                if event.provenance == "bore.hole.0.final_retract"
            )
            assert isinstance(descent, LinearMove)
            events[final_index] = replace(events[final_index], start=descent.end)
            old_native = backend._toolpaths[single.source_operation_id]
            old_metadata = window.viewport.toolpath_presentations
            assert not window.viewport.display_toolpath(_unchecked(multi, events))
            assert backend._toolpaths[single.source_operation_id] == old_native
            assert window.viewport.toolpath_presentations == old_metadata

            mixed_events = list(multi.events)
            rapid_index = next(
                index for index, event in enumerate(mixed_events)
                if event.provenance == "bore.hole.0.rapid"
            )
            mixed_events[rapid_index] = replace(
                mixed_events[rapid_index], provenance="tap.hole.0.rapid"
            )
            mixed = replace(
                multi,
                events=tuple(mixed_events),
                artifact_fingerprint=None,
            )
            assert not window.viewport.display_toolpath(mixed)
            assert backend._toolpaths[single.source_operation_id] == old_native
            assert window.viewport.toolpath_presentations == old_metadata

            window.viewport.clear_toolpaths()
            service.save_as(root, "Boring Viewer Switched")
            window.cam_workspace.bind_project(service.current_project)
            assert backend._toolpaths == {}
            assert window.viewport.toolpath_presentations == ()
            assert window.viewport.display_toolpath(single)
            window.resize(960, 640)
            window.viewport.fit_all()
            window.viewport.remove_toolpath(single.source_operation_id)
            assert backend._toolpaths == {}
            logger.info(
                "GUI verified: single/multi/dwell/controlled retract, visibility, "
                "replace/rollback, mixed provenance, project switch, resize/close"
            )
            finish()
        except BaseException as error:
            finish(error)

    QTimer.singleShot(150, verify)
    QTimer.singleShot(
        60_000,
        lambda: finish(TimeoutError("Stage 7B.9.2 GUI timeout"))
        if not failures and window.isVisible()
        else None,
    )
    application.exec()
    temporary.cleanup()
    if failures:
        raise failures[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

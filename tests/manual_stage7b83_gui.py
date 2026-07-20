"""Real Windows/PySide6/OCP smoke verification for Reaming UI Stage 7B.8.3."""

from __future__ import annotations

import logging
import math
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    LengthUnit,
    ReamingCoolantMode,
    ReamingStrategy,
    SpindleDirection,
)
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend
from hms_cadcam.viewer.widget import CadViewportWidget

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from tests.unit.test_reaming_ui import (  # noqa: E402
    _find_item,
    _hole,
    _pattern,
    _resolved,
)

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage7b83_")
    root = Path(temporary.name)
    source = root / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service = ProjectService.create_default(root / "config")
    session = service.create_project_from_source(
        root, "Reaming UI", source,
    )
    source_id = session.manifest.source_files[0].source_id
    selected = {"source": _pattern(
        _hole(
            source_id,
            hint="left",
            x=4,
            unit=LengthUnit.MM,
            occurrence_path="root/left",
        ),
        _hole(
            source_id,
            hint="right",
            x=16,
            unit=LengthUnit.MM,
            occurrence_path="root/right",
        ),
    )}
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    viewport = CadViewportWidget(kernel, backend)
    workspace = CamWorkspace(
        service,
        lambda: source_id,
        toolpath_display=viewport.display_toolpath,
        toolpath_clear=viewport.clear_toolpaths,
        toolpath_remove=viewport.remove_toolpath,
        drilling_pick_provider=lambda _axis: selected["source"],
        drilling_resolver=_resolved,
    )
    window = QWidget()
    window.setWindowTitle("HMS CAM Reaming UI 7B.8.3 Smoke")
    layout = QHBoxLayout(window)
    layout.addWidget(workspace)
    layout.addWidget(viewport, 1)
    window.resize(1280, 760)
    window.show()
    failures: list[BaseException] = []
    active_service = {"value": service}

    def finish(error: BaseException | None = None) -> None:
        if error is not None:
            failures.append(error)
        try:
            active_service["value"].close_project()
        except (RuntimeError, ValueError):
            pass
        try:
            viewport.shutdown()
            window.hide()
        except RuntimeError:
            pass
        application.quit()

    def select_reaming() -> None:
        item = _find_item(workspace.tree.topLevelItem(0), "Reaming")
        assert item is not None
        workspace.tree.setCurrentItem(item)

    def verify() -> None:
        try:
            workspace.create_job()
            workspace.create_setup()
            workspace.create_basic_resources()
            workspace.create_basic_reaming_resources()
            workspace.add_reaming_operation()
            select_reaming()
            operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
            assert operation.strategy_key == "reaming_v1"
            assert "HOLE PATTERN 2" in workspace.editor.status.text()
            assert "Stock/side: 0.1" in workspace.editor.reaming_derived.text()

            old_pre_hole = workspace.editor._reaming_fields["pre_hole"].text()
            workspace.editor._reaming_fields["pre_hole"].clear()
            workspace.editor._submit()
            assert "ream.prehole_missing" in workspace.editor.error.text()
            assert workspace.editor._reaming_fields["pre_hole"].text() == old_pre_hole

            machine_index = next(
                index for index in range(workspace.editor.machine.count())
                if "doa" in workspace.editor.machine.itemText(index).lower()
            )
            workspace.editor.machine.setCurrentIndex(machine_index)
            workspace.editor.reaming_spindle_direction.setCurrentText(
                SpindleDirection.COUNTERCLOCKWISE.value
            )
            workspace.editor.reaming_coolant.setCurrentText(
                ReamingCoolantMode.FLOOD.value
            )
            workspace.editor._reaming_fields["spindle"].setText("600")
            workspace.editor._reaming_fields["feed_per_revolution"].setText("0.12")
            workspace.editor._reaming_fields["dwell"].setText("0.2")
            workspace.editor._submit()
            strategy = ReamingStrategy.from_operation_parameters(
                service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].parameters
            )
            assert strategy.feed_per_minute.value == 72
            assert math.isclose(strategy.stock_per_side.value, 0.1)

            workspace.generate_selected()
            operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
            assert operation.artifact_state.status is ArtifactStatus.VALID
            assert viewport.toolpath_presentations
            assert any(
                segment.semantic == "controlled_retract"
                for segment in viewport.toolpath_presentations[0].segments
            )
            workspace.toggle_toolpath_visibility()
            workspace.toggle_toolpath_visibility()

            service.save()
            saved_root = service.current_project.root_path
            service.close_project()
            workspace.bind_project(None)
            reopened = service.open_project(saved_root)
            workspace.bind_project(reopened)
            select_reaming()
            assert viewport.toolpath_presentations

            copied = service.save_as(root, "Reaming UI Copy")
            workspace.bind_project(copied)
            select_reaming()
            workspace.editor._reaming_fields["dwell"].setText("0.4")
            workspace.editor._submit()
            assert service.autosave() is not None

            opener = ProjectService.create_default(root / "recovery-config")
            opener._session_locks._pid_checker = lambda _pid: False
            try:
                opener.open_project(copied.root_path)
            except RecoveryRequiredError as error:
                recovered = opener.recover_project(error.assessment)
            else:
                raise AssertionError("Autosave recovery was not requested")
            active_service["value"] = opener
            workspace._service = opener
            workspace.bind_project(recovered)
            select_reaming()
            recovered_strategy = ReamingStrategy.from_operation_parameters(
                opener.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0].parameters
            )
            assert recovered_strategy.dwell_seconds == 0.4

            switched = opener.new_project(root, "Reaming UI Switched")
            workspace.bind_project(switched)
            assert viewport.toolpath_presentations == ()
            window.resize(980, 640)
            viewport.fit_all()
            logger.info(
                "GUI verified: Reaming resources/create/multi-hole, derived preview, "
                "Apply/Generate/Show-Hide, validation rollback, Save/Open/Save As, "
                "Autosave/Recovery, project switch, resize/close"
            )
            finish()
        except BaseException as error:
            finish(error)

    QTimer.singleShot(250, verify)
    QTimer.singleShot(
        60_000,
        lambda: finish(TimeoutError("Stage 7B.8.3 GUI timeout"))
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

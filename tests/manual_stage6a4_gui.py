"""Manual Windows GUI verification for Stage 6A.4 XCAF persistence."""

from __future__ import annotations

import logging
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.models import ObjectAppearance, ObjectColor
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend
from spikes.xcaf_step.fixture import write_xcaf_step_fixture

logger = logging.getLogger(__name__)


def _window(service: ProjectService) -> tuple[MainWindow, OcpCadViewportBackend]:
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.show()
    return window, backend


def _dispose(window: MainWindow) -> None:
    window.cad_controller.shutdown()
    window.viewport.shutdown()
    window.hide()


def _repeated(window: MainWindow):
    tree = window.cad_controller.active_tree
    assert tree is not None
    nodes = sorted(
        (
            node
            for node in tree.presentation_nodes
            if node.product_name == "Repeated Product"
        ),
        key=lambda node: node.absolute_transform.translation[0],
    )
    assert len(nodes) == 2
    assert nodes[0].object_id != nodes[1].object_id
    assert nodes[0].absolute_transform != nodes[1].absolute_transform
    return tree, nodes


def _row_count(project_root: Path) -> int:
    with closing(sqlite3.connect(project_root / "project.db")) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM cad_xcaf_occurrence_appearance"
        ).fetchone()[0]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage6a4_")
    root = Path(temporary.name)
    screenshot = Path(tempfile.gettempdir()) / "hms_stage6a4_xcaf.png"
    source = root / "assembly.step"
    write_xcaf_step_fixture(source)
    service = ProjectService.create_default(root / "owner-config")
    original = service.create_project_from_source(root, "XCAF Original", source)
    original_root = original.root_path
    source_id = original.manifest.source_files[0].source_id
    window, backend = _window(service)
    windows = [window]
    failure: list[BaseException] = []
    state = {"step": "initial import"}
    override_color = ObjectColor(0.2, 0.55, 0.8)

    def finish() -> None:
        for candidate in windows:
            try:
                if candidate.isVisible():
                    _dispose(candidate)
            except RuntimeError:
                pass
        application.quit()

    def fail(error: BaseException) -> None:
        failure.append(error)
        logger.error("Stage 6A.4 GUI verification failed: %s", error)
        finish()

    def wait_ready(candidate: MainWindow, callback) -> None:
        if candidate.cad_controller.is_busy:
            QTimer.singleShot(25, lambda: wait_ready(candidate, callback))
            return
        try:
            callback()
        except BaseException as error:
            fail(error)

    def verify_initial() -> None:
        nonlocal window, backend, service
        state["step"] = "verify initial"
        tree, repeated = _repeated(window)
        assert len(tree.presentation_nodes) == 3
        registry = backend._lifecycle.registry
        assert registry is not None and len(registry.presentations) == 3
        first, second = repeated
        controller = window.cad_controller
        assert controller.set_object_color(
            tree.document_id, first.object_id, override_color
        )
        assert controller.set_object_transparency(
            tree.document_id, first.object_id, 0.35
        )
        assert controller.set_object_visibility(
            tree.document_id, second.object_id, False
        )
        assert controller.isolate_object(tree.document_id, first.object_id)
        service.save()
        assert controller.reset_isolate(tree.document_id)
        assert _row_count(original_root) == 2
        assert application.primaryScreen().grabWindow(0).save(str(screenshot))
        _dispose(window)
        service.close_project(discard_changes=True)

        service = ProjectService.create_default(root / "reopen-config")
        service.open_project(original_root)
        window, backend = _window(service)
        windows.append(window)
        state["step"] = "reopen import"
        QTimer.singleShot(25, lambda: wait_ready(window, verify_reopen))

    def verify_reopen() -> None:
        nonlocal window, backend
        state["step"] = "verify reopen"
        tree, repeated = _repeated(window)
        appearances = dict(window.cad_controller.appearances)
        assert appearances[repeated[0].object_id] == ObjectAppearance(
            True, override_color, 0.35
        )
        assert not appearances[repeated[1].object_id].visible
        assert not service.is_dirty and service.autosave() is None
        _dispose(window)
        copied = service.save_as(root, "XCAF Copy")
        assert copied.manifest.source_files[0].source_id == source_id
        window, backend = _window(service)
        windows.append(window)
        state["step"] = "copy import"
        QTimer.singleShot(25, lambda: wait_ready(window, verify_copy))

    def verify_copy() -> None:
        nonlocal window
        state["step"] = "verify copy and autosave"
        tree, repeated = _repeated(window)
        first, second = repeated
        assert dict(window.cad_controller.appearances)[first.object_id].color == (
            override_color
        )
        assert not dict(window.cad_controller.appearances)[second.object_id].visible
        assert window.cad_controller.reset_object_appearance(
            tree.document_id, first.object_id
        )
        source_appearance = window.cad_controller._base_appearances[first.object_id]
        assert dict(window.cad_controller.appearances)[first.object_id] == (
            source_appearance
        )
        service.save()
        copy_root = service.current_project.root_path
        assert _row_count(original_root) == 2
        assert _row_count(copy_root) == 1

        assert window.cad_controller.set_object_transparency(
            tree.document_id, first.object_id, 0.6
        )
        snapshot = service.autosave()
        assert snapshot is not None and service.is_dirty
        with closing(sqlite3.connect(snapshot.path / "project.db")) as connection:
            rows = connection.execute(
                "SELECT color_r, color_g, color_b, transparency "
                "FROM cad_xcaf_occurrence_appearance"
            ).fetchall()
        assert any(row[:3] == (None, None, None) and row[3] == 0.6 for row in rows)
        _dispose(window)

        opener = ProjectService.create_default(root / "recovery-config")
        opener._session_locks._pid_checker = lambda _pid: False
        try:
            opener.open_project(copy_root)
        except RecoveryRequiredError as error:
            opener.recover_project(error.assessment)
        else:
            raise AssertionError("Recovery was not requested")
        recovered, _recovered_backend = _window(opener)
        windows.append(recovered)
        state["step"] = "recovery import"
        QTimer.singleShot(
            25,
            lambda: wait_ready(
                recovered,
                lambda: verify_recovered(recovered, opener),
            ),
        )

    def verify_recovered(
        recovered: MainWindow,
        opener: ProjectService,
    ) -> None:
        state["step"] = "verify recovery"
        tree, repeated = _repeated(recovered)
        first, second = repeated
        source_appearance = recovered.cad_controller._base_appearances[first.object_id]
        effective = dict(recovered.cad_controller.appearances)
        assert effective[first.object_id].color == source_appearance.color
        assert effective[first.object_id].transparency == 0.6
        assert not effective[second.object_id].visible
        assert not opener.is_dirty
        assert recovered.cad_controller.reset_object_appearance(
            tree.document_id, first.object_id
        )
        assert recovered.cad_controller.set_object_visibility(
            tree.document_id, second.object_id, True
        )
        opener.save()
        assert _row_count(opener.current_project.root_path) == 0
        logger.info(
            "GUI verified: nested assembly, repeated instances, "
            "Save/Open/Save As/Autosave/Recovery/reset"
        )
        logger.info("Screenshot: %s", screenshot)
        recovered.close()
        opener.close_project()
        QTimer.singleShot(100, finish)

    QTimer.singleShot(100, lambda: wait_ready(window, verify_initial))
    QTimer.singleShot(
        90_000,
        lambda: fail(TimeoutError(f"timeout at {state['step']}"))
        if not failure
        else None,
    )
    application.exec()
    temporary.cleanup()
    if failure:
        raise failure[0]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

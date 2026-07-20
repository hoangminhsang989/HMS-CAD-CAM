"""Windows/PySide6/OCP smoke checklist for Simulation UI/cache 7C.3.

Run from the repository root with ``.venv\\Scripts\\python.exe`` on a real
Windows desktop.  The run itself uses the owner-thread OCP scene builder; the
viewer-only PASS/WARN/FAIL loop uses the 7C.2 deterministic fixture so every
result class and marker-focus path is exercised without inventing persistence.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import logging
import sys
import tempfile
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import CamJob, CamJobId, ContentFingerprint, OperationTree
from hms_cadcam.cam.persistence import CamProjectSnapshot, ToolpathArtifactStore
from hms_cadcam.cam.simulation import SimulationIssueCode
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend

logger = logging.getLogger(__name__)


def _load_viewer_fixture():
    path = Path(__file__).parent / "unit" / "test_simulation_viewer.py"
    spec = importlib.util.spec_from_file_location("hms_stage7c3_fixture", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load 7C.2 viewer fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._fixture


def _write_source(path: Path) -> None:
    if not BRepTools.Write_s(BRepPrimAPI_MakeBox(80, 60, 30).Shape(), str(path)):
        raise RuntimeError("Cannot write BREP fixture")


def _install_valid_operation(service: ProjectService, root: Path):
    from tests.unit.test_simulation_service import _source

    operation, artifact, setup, tool, holder, assembly, _request, scene = _source()
    artifact = dataclasses.replace(
        artifact,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(assembly.to_dict()),
        artifact_fingerprint=None,
    )
    operation = dataclasses.replace(
        operation,
        artifact_state=dataclasses.replace(
            operation.artifact_state,
            artifact_fingerprint=artifact.artifact_fingerprint,
        ),
    )
    empty_tree = OperationTree.empty(setup.setup_id)
    setup = dataclasses.replace(
        setup,
        operation_tree=empty_tree.add_operation(
            empty_tree.root_id, "Simulation operation", operation
        ),
    )
    job = CamJob(
        CamJobId.new(), "Simulation job", setups=(setup,), active_setup_id=setup.setup_id
    )
    metadata = ToolpathArtifactStore().publish(root, artifact)
    service.stage_cam_snapshot(
        CamProjectSnapshot(
            (job,), job.job_id, (tool,), (holder,), (assembly,), (), (metadata,)
        )
    )
    service.save()
    return operation, scene


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    temporary = tempfile.TemporaryDirectory(prefix="hms_stage7c3_")
    root = Path(temporary.name)
    source = root / "simulation-source.brep"
    _write_source(source)
    service = ProjectService.create_default(root / "config")
    session = service.create_project_from_source(root, "Simulation UI", source)
    operation, _scene = _install_valid_operation(service, session.root_path)
    kernel = OcpCadKernel()
    backend = OcpCadViewportBackend(kernel)
    window = MainWindow(service, kernel, backend)
    window.resize(1400, 820)
    window.show()
    fixture = _load_viewer_fixture()
    failures: list[BaseException] = []

    def finish(error: BaseException | None = None) -> None:
        if error is not None:
            failures.append(error)
            logger.error("Stage 7C.3 GUI failed: %s", error, exc_info=True)
        try:
            window.cad_controller.shutdown()
            window.viewport.shutdown()
            window.hide()
        finally:
            app.quit()

    def verify() -> None:
        try:
            workspace = window.cam_workspace
            # Select the real VALID operation in the CAM tree.
            for index in range(workspace.tree.topLevelItemCount()):
                top = workspace.tree.topLevelItem(index)
                stack = [top]
                while stack:
                    item = stack.pop()
                    if item.text(0) == "Simulation operation":
                        workspace.tree.setCurrentItem(item)
                        stack.clear()
                        break
                    stack.extend(item.child(i) for i in range(item.childCount()))
            assert workspace.simulation_panel.run_button.isEnabled()
            workspace.simulation_panel.run_button.click()
            assert workspace.simulation_panel.progress_bar.value() >= 0

            def wait_for_run() -> None:
                record = service.simulation_runs.record(operation.operation_id)
                if record is None or record.state.value in {"validating", "running", "cancelling"}:
                    QTimer.singleShot(50, lambda: _guarded(wait_for_run, finish))
                    return
                assert record.state.value == "completed"
                result = service.simulation_runs.result(operation.operation_id)
                assert result is not None
                logger.info(
                    "owner-thread run status=%s issues=%s",
                    result.status.value,
                    len(result.issues),
                )
                assert result.status.value == "pass"
                workspace.simulation_panel.visibility_button.click()
                workspace.simulation_panel.visibility_button.click()

                # Viewer-only deterministic result classes and marker metadata.
                project_id = session.manifest.project_id
                generation = service.cam_generation
                window.viewport.bind_simulation_project(project_id, generation)
                _, artifact, wcs, _, _, warning, context = fixture(
                    (SimulationIssueCode.RAPID_BELOW_SAFE,), project_id=project_id
                )
                context = dataclasses.replace(context, project_generation=generation)
                assert window.viewport.display_simulation(warning, artifact, wcs, context)
                warning_presentation = next(
                    item
                    for item in window.viewport.simulation_presentations
                    if item.key.operation_id == warning.operation_id
                )
                marker = warning_presentation.markers[0]
                assert window.viewport.focus_simulation_issue(
                    project_id=project_id,
                    operation_id=warning.operation_id,
                    result_id=warning.result_id,
                    marker_id=marker.marker_id,
                )
                assert window.viewport.set_simulation_visibility(warning.operation_id, False)
                assert window.viewport.set_simulation_visibility(warning.operation_id, True)
                _, fail_artifact, fail_wcs, _, _, failed, fail_context = fixture(
                    (SimulationIssueCode.TOOL_FIXTURE_COLLISION,),
                    operation_id=warning.operation_id,
                    project_id=project_id,
                )
                fail_context = dataclasses.replace(fail_context, project_generation=generation)
                assert window.viewport.display_simulation(failed, fail_artifact, fail_wcs, fail_context)
                failed_presentation = next(
                    item
                    for item in window.viewport.simulation_presentations
                    if item.key.operation_id == warning.operation_id
                )
                assert failed_presentation.status.value == "fail"
                window.viewport.clear_simulation_issue_focus()

                # Invalid policy is rejected atomically; a valid change marks the
                # current result stale and can be rerun, then Clear Result only
                # removes derived runtime/cache/overlay data.
                panel = workspace.simulation_panel
                old_policy = panel.sampling_policy
                panel.policy_fields["maximum_samples"].setText("999999999")
                assert not panel.apply_policy_draft()
                assert panel.sampling_policy == old_policy
                panel.reset_policy_defaults()
                panel.clear_button.click()
                assert panel.source_labels["current"].text().startswith("No current result")

                service.save()
                reopened = service.open_project(session.root_path)
                assert not reopened.is_dirty
                copied = service.save_as(root, "Simulation UI Copy")
                assert copied.manifest.project_id != session.manifest.project_id
                assert (copied.root_path / "cache" / "simulation").is_dir()
                service.apply_cam_mutation(lambda value: dataclasses.replace(value, active_job_id=None))
                autosave = service.autosave()
                assert autosave is not None
                assert (autosave.path / "cache" / "simulation").is_dir()
                service.save()

                # Exercise the actual Cancel button while sampling is busy.
                panel.policy_fields["max_linear_step"].setText("0.00002")
                assert panel.apply_policy_draft()
                panel.run_button.click()
                # The headless runtime tests cover cancellation during sampling
                # and collision; this GUI smoke also verifies the user action
                # itself without waiting for an unbounded native call.
                panel.cancel_button.click()

                def wait_for_cancel() -> None:
                    cancel_record = service.simulation_runs.record(operation.operation_id)
                    if cancel_record is not None and cancel_record.state.value in {
                        "validating", "running", "cancelling",
                    }:
                        QTimer.singleShot(25, lambda: _guarded(wait_for_cancel, finish))
                        return
                    assert cancel_record is not None
                    logger.info(
                        "cancel smoke terminal state=%s code=%s",
                        cancel_record.state.value,
                        cancel_record.diagnostic_code,
                    )
                    assert cancel_record.state.value == "idle"
                    panel.reset_policy_defaults()
                    service.save()

                    # Invalidate the generation from an event pumped during a
                    # second long run, then clear the CAD binding as a reimport
                    # boundary. No late callback may update the new project.
                    panel.policy_fields["max_linear_step"].setText("0.00002")
                    assert panel.apply_policy_draft()
                    panel.run_button.click()

                    def switch_project() -> None:
                        switched = service.new_project(root, "Simulation Switch")
                        workspace.bind_project(switched)
                        window.cad_controller.bind_project(None)
                        assert workspace.simulation_panel.inputs is None
                        assert service.simulation_runs.record(operation.operation_id) is None
                        assert window.viewport.simulation_presentations == ()
                        window.resize(1100, 700)
                        window.resize(1500, 900)
                        logger.info(
                            "7C.3 GUI verified: VALID run/PASS, progress, Cancel, "
                            "show/hide, WARN/FAIL marker focus, policy stale/clear, "
                            "Save/Open/Save As/Autosave, project switch/CAD clear, resize"
                        )
                        finish()

                    QTimer.singleShot(0, switch_project)

                QTimer.singleShot(25, lambda: _guarded(wait_for_cancel, finish))

            QTimer.singleShot(50, lambda: _guarded(wait_for_run, finish))
        except BaseException as error:
            finish(error)

    QTimer.singleShot(500, verify)
    QTimer.singleShot(120_000, lambda: finish(TimeoutError("Stage 7C.3 GUI timeout")) if not failures else None)
    app.exec()
    temporary.cleanup()
    if failures:
        raise failures[0]
    return 0


def _guarded(callback, fail) -> None:
    try:
        callback()
    except BaseException as error:
        fail(error)


if __name__ == "__main__":
    raise SystemExit(main())

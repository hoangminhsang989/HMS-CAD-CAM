"""CAM 7B.1 application facade and stale UI-state tests."""

from uuid import uuid4

import pytest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.application import (
    CamApplicationService,
    CamSelection,
    basic_mill_resources,
)
from hms_cadcam.cam.domain import CamJobId
from hms_cadcam.ui.cam_ui import _default_setup
from hms_cadcam.cam.domain import LengthUnit
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import CamWorkspace


def test_job_setup_mutations_are_ordered_and_snapshot_collections_immutable() -> None:
    service = CamApplicationService()
    first = service.create_job("Job A").active_job_id
    second = service.create_job("Job B").active_job_id
    assert first is not None and second is not None
    service.reorder_job(second, 0)
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.add_setup(first, setup)

    assert tuple(job.job_id for job in service.snapshot.jobs) == (second, first)
    assert service.snapshot.jobs[1].setups == (setup,)
    with pytest.raises(AttributeError):
        service.snapshot.jobs.append(object())  # type: ignore[attr-defined]


def test_failed_domain_mutation_rolls_back_complete_snapshot() -> None:
    service = CamApplicationService()
    job_id = service.create_job("Hợp lệ").active_job_id
    before = service.snapshot
    with pytest.raises(Exception):
        service.rename_job(job_id, "   ")  # type: ignore[arg-type]
    assert service.snapshot == before


def test_public_snapshot_cannot_mutate_service_job_aggregate() -> None:
    service = CamApplicationService()
    service.create_job("Tên gốc")

    exposed = service.snapshot
    exposed.jobs[0].rename("Tên ngoài service")

    assert service.snapshot.jobs[0].name == "Tên gốc"


def test_multistep_project_command_and_resource_bundle_roll_back_atomically(
    tmp_path,
) -> None:
    project = ProjectService.create_default(tmp_path / "config")
    session = project.new_project(tmp_path, "CAM Transaction")

    def failing_command(app: CamApplicationService):
        app.create_job("Không được giữ")
        raise ValueError("step two failed")

    with pytest.raises(ValueError, match="step two failed"):
        project.execute_cam_command(failing_command)
    assert project.cam_snapshot.is_empty
    assert not session.is_dirty

    resources = basic_mill_resources(LengthUnit.MM)
    project.execute_cam_command(lambda app: app.add_basic_resources(*resources))
    before = project.cam_snapshot
    with pytest.raises(Exception):
        project.execute_cam_command(lambda app: app.add_basic_resources(*resources))
    assert project.cam_snapshot == before


def test_project_command_rejects_stale_generation(tmp_path) -> None:
    project = ProjectService.create_default(tmp_path / "config")
    project.new_project(tmp_path, "Generation A")
    stale_generation = project.cam_generation
    project.new_project(tmp_path, "Generation B")

    with pytest.raises(RuntimeError, match="inactive project generation"):
        project.execute_cam_command(
            lambda app: app.create_job("Sai project"),
            expected_generation=stale_generation,
        )
    assert project.cam_snapshot.is_empty


def test_project_generation_rejects_stale_selection_callback() -> None:
    service = CamApplicationService()
    service.create_job("Job")
    generation = service.generation
    service.clear()
    stale = CamSelection(job_id=CamJobId.new())
    assert not service.select(stale, generation=generation)
    assert service.selection == CamSelection()


def test_cam_workspace_builds_job_setup_group_operation_tree(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    project = ProjectService.create_default(tmp_path / "config")
    session = project.create_project_from_source(tmp_path, "UI CAM", source)
    source_id = session.manifest.source_files[0].source_id
    workspace = CamWorkspace(project, lambda: source_id)
    workspace.create_job()
    workspace.create_setup()
    workspace.create_basic_resources()
    workspace.add_group()
    workspace.add_operation()

    job = project.cam_snapshot.jobs[0]
    setup = job.setups[0]
    assert len(setup.operation_tree.nodes) == 3
    assert len(setup.operation_tree.operations) == 1
    assert len(project.cam_snapshot.tool_assemblies) == 1
    assert len(project.cam_snapshot.machine_definitions) == 1
    assert project.is_dirty
    workspace.deleteLater()


def test_invalid_properties_edit_preserves_domain_and_displays_error(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    source = tmp_path / "invalid.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    project = ProjectService.create_default(tmp_path / "config")
    session = project.create_project_from_source(tmp_path, "Invalid CAM", source)
    source_id = session.manifest.source_files[0].source_id
    workspace = CamWorkspace(project, lambda: source_id)
    workspace.create_job()
    workspace.create_setup()
    before = project.cam_snapshot

    workspace.editor._fields["a"].setText("-1")
    workspace.editor._submit()

    assert project.cam_snapshot == before
    assert workspace.editor.error.text()
    workspace.deleteLater()


def test_project_switch_cancels_transient_geometry_pick(tmp_path) -> None:
    QApplication.instance() or QApplication([])
    project = ProjectService.create_default(tmp_path / "config")
    first = project.new_project(tmp_path, "Pick A")
    source_id = uuid4()
    reference = _default_setup(source_id, LengthUnit.MM, 1).model_reference
    workspace = CamWorkspace(project, lambda: source_id, lambda: reference)

    workspace.pick_geometry()
    assert workspace._picked_reference == reference
    second = project.new_project(tmp_path, "Pick B")
    workspace.bind_project(second)

    assert workspace._picked_reference is None
    assert workspace.editor.status.text() == "—"
    assert first.manifest.project_id != second.manifest.project_id
    workspace.deleteLater()


def test_repeated_project_binding_does_not_duplicate_action_signal(tmp_path) -> None:
    application = QApplication.instance() or QApplication([])
    project = ProjectService.create_default(tmp_path / "config")
    session = project.new_project(tmp_path, "Signal CAM")
    workspace = CamWorkspace(project, lambda: None)

    for _ in range(4):
        workspace.bind_project(session)
    workspace.actions["job"].trigger()
    application.processEvents()

    assert len(project.cam_snapshot.jobs) == 1
    workspace.deleteLater()

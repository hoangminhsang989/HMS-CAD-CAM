from dataclasses import replace

from hms_cadcam.cam.post import ExportOverwritePolicy, NCArtifactStatus
from hms_cadcam.project.service import ProjectService
from tests.unit._export_fixtures import production_export_fixture


def test_project_service_save_open_close_and_cad_only_lazy_layout(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "NC Lifecycle")
    assert not (session.root_path / "post").exists()
    assert not (session.root_path / "nc").exists()
    request, snapshot = production_export_fixture(
        session.root_path,
        project_id=session.manifest.project_id,
        project_generation=service.cam_generation,
        post_runtime=service.post_service,
    )
    execution = service.export_nc(request, snapshot)
    assert execution.accepted and execution.artifact is not None
    output = session.root_path / execution.artifact.output_relative_path
    expected = output.read_bytes()
    service.post_service.clear()
    stale = service.export_nc(
        replace(
            request,
            request_id=None,
            overwrite_policy=ExportOverwritePolicy.REPLACE_EXPLICIT,
        ),
        snapshot,
    )
    assert not stale.accepted
    assert output.read_bytes() == expected
    service.save()
    service.close_project()
    assert output.read_bytes() == expected
    opened = service.open_project(session.root_path)
    assert not opened.is_dirty
    assert service.nc_export_service.artifacts()[0].status is NCArtifactStatus.CURRENT


def test_save_as_and_autosave_copy_only_managed_nc_state(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    original = service.new_project(tmp_path, "NC Original")
    request, snapshot = production_export_fixture(
        original.root_path,
        project_id=original.manifest.project_id,
        project_generation=service.cam_generation,
        post_runtime=service.post_service,
    )
    execution = service.export_nc(request, snapshot)
    assert execution.accepted and execution.artifact is not None
    original_bytes = (
        original.root_path / execution.artifact.output_relative_path
    ).read_bytes()

    original.is_dirty = True
    autosave = service.autosave()
    assert autosave is not None
    autosave_manifest = service.nc_export_service.store.inspect(
        autosave.path, original.manifest.project_id
    )
    assert autosave_manifest.entries[0].status is NCArtifactStatus.CURRENT
    assert (autosave.path / autosave_manifest.entries[0].output_relative_path).read_bytes() == original_bytes

    copied = service.save_as(tmp_path, "NC Copy")
    copied_manifest = service.nc_export_service.store.inspect(
        copied.root_path, copied.manifest.project_id
    )
    assert copied_manifest.entries[0].status is NCArtifactStatus.STALE
    assert copied_manifest.entries[0].project_id == copied.manifest.project_id
    assert (copied.root_path / copied_manifest.entries[0].output_relative_path).read_bytes() == original_bytes
    assert service.nc_export_service.current(request.project_id, request.operation_id) is None


def test_operation_invalidation_keeps_file_but_marks_manifest_stale(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "NC Stale")
    request, snapshot = production_export_fixture(
        session.root_path,
        project_id=session.manifest.project_id,
        project_generation=service.cam_generation,
        post_runtime=service.post_service,
    )
    execution = service.export_nc(request, snapshot)
    assert execution.accepted and execution.artifact is not None
    output = session.root_path / execution.artifact.output_relative_path
    service.nc_export_service.mark_operation_stale(request.operation_id)
    manifest = service.nc_export_service.store.inspect(
        session.root_path, session.manifest.project_id
    )
    assert manifest.entries[0].status is NCArtifactStatus.STALE
    assert output.is_file()

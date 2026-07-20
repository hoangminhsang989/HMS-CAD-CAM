"""7C.3 project Save/Open/Save-As/autosave cache lifecycle tests."""

from __future__ import annotations

import dataclasses

import pytest

from hms_cadcam.cam.domain import CamJob, CamJobId, ContentFingerprint, OperationTree
from hms_cadcam.cam.persistence import CamProjectSnapshot, ToolpathArtifactStore
from hms_cadcam.cam.simulation import (
    InMemoryAabbBackend,
    SimulationCacheStatus,
    SimulationRuntimeService,
)
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.service import ProjectService
from tests.unit.test_simulation_service import _source


def _project(tmp_path):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Simulation cache project")
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    # The foundation fixture predates artifact-store codec validation and
    # carries the legacy dependency-kind assembly fingerprint.  Normalize it
    # to the current ToolpathArtifact content fingerprint for this lifecycle
    # test without changing the production model or codec version.
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
    tree = empty_tree.add_operation(
        empty_tree.root_id,
        "Simulation operation",
        operation,
    )
    setup = dataclasses.replace(setup, operation_tree=tree)
    job = CamJob(
        CamJobId.new(),
        "Simulation job",
        setups=(setup,),
        active_setup_id=setup.setup_id,
    )
    metadata = ToolpathArtifactStore().publish(session.root_path, artifact)
    snapshot = CamProjectSnapshot(
        (job,),
        job.job_id,
        (tool,),
        (holder,),
        (assembly,),
        (),
        (metadata,),
    )
    service.stage_cam_snapshot(snapshot)
    service.save()
    inputs = service.capture_simulation_inputs(operation.operation_id)
    return service, session, operation, artifact, inputs, scene


def _publish_cache(service, inputs, scene):
    execution = service.simulation_runs.execute(
        service.simulation_runs.start(inputs.request),
        snapshot=inputs,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )
    assert execution.accepted and execution.result is not None
    service.persist_simulation_result(execution.result)
    service.save()
    return execution.result


def test_save_open_save_as_and_clear_keep_cache_project_isolated(tmp_path) -> None:
    service, session, operation, artifact, inputs, scene = _project(tmp_path)
    result = _publish_cache(service, inputs, scene)
    root = session.root_path
    service.close_project()

    reopened = service.open_project(root)
    reopened_inputs = service.capture_simulation_inputs(operation.operation_id)
    loaded = service.load_cached_simulation(reopened_inputs)
    assert loaded.status is SimulationCacheStatus.VALID
    assert loaded.result == result
    assert not reopened.is_dirty

    copied = service.save_as(tmp_path, "Simulation cache copy")
    copied_inputs = service.capture_simulation_inputs(operation.operation_id)
    copied_loaded = service.load_cached_simulation(copied_inputs)
    assert copied.manifest.project_id != reopened.manifest.project_id
    assert copied_loaded.status is SimulationCacheStatus.VALID
    assert copied_loaded.metadata is not None
    assert copied_loaded.metadata.project_id == copied.manifest.project_id
    assert str(reopened.root_path) not in copied_loaded.metadata.payload_filename

    artifact_path = copied.root_path / copied.cam_snapshot.artifacts[0].relative_path
    assert artifact_path.is_file()  # Clear Result never deletes ToolpathArtifact.
    service.clear_simulation_result(operation.operation_id, delete_cache=True)
    assert service.load_cached_simulation(copied_inputs).status is SimulationCacheStatus.MISSING


def test_autosave_and_recovery_copy_cache_without_dirty_load(tmp_path) -> None:
    owner, session, operation, _artifact, inputs, scene = _project(tmp_path)
    _publish_cache(owner, inputs, scene)
    dirty_snapshot = dataclasses.replace(owner.cam_snapshot, active_job_id=None)
    owner.stage_cam_snapshot(dirty_snapshot)
    autosave = owner.autosave()
    assert autosave is not None
    assert list((autosave.path / "cache" / "simulation").rglob("*.metadata.json"))

    opener = ProjectService.create_default(tmp_path / "recovery-config")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(session.root_path)
    recovered = opener.recover_project(raised.value.assessment)
    recovered_inputs = opener.capture_simulation_inputs(operation.operation_id)
    recovered_cache = opener.load_cached_simulation(recovered_inputs)
    assert recovered_cache.status is SimulationCacheStatus.VALID
    assert not recovered.is_dirty

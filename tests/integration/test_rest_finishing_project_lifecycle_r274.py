"""Focused R274 project lifecycle coverage using real R272 authority bytes."""

from __future__ import annotations

from pathlib import Path
import hashlib
import json
import sqlite3
import sys
import shutil
from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import (
    CamJob, CamNodeId, ContentFingerprint, DirtyReason,
    GeometryResolutionStatus, OperationId,
    ResolvedContourProfile,
    SpindleDirection,
    ToolAssembly, ToolAssemblyId,
)
from hms_cadcam.cam.domain.dependency import DependencyEdge
from hms_cadcam.cam.domain.contour import ContourProfileSource
from hms_cadcam.cam.domain.rest_finishing import (
    RestFinishingParameters, RestFinishingProfileSelection,
)
from hms_cadcam.cam.domain.units import FeedRate, FeedUnit, Length, LengthUnit, SpindleSpeed
from hms_cadcam.cam.application.rest_finishing_application import (
    RestFinishingApplicationStatus,
    RestFinishingPreparationStatus,
)
from hms_cadcam.cam.domain.rest_finishing import RestFinishingDiagnosticCode
from hms_cadcam.cam.persistence import CamSqliteRepository, ToolpathArtifactStore
from hms_cadcam.cam.toolpath import (
    ArcMove,
    LinearMove,
    MotionClass,
    ToolpathArtifact,
    artifact_to_dict,
    compute_material_removal_fingerprint,
)
from hms_cadcam.project.service import ProjectService

_INTEGRATION = Path(__file__).parent
_UNIT = _INTEGRATION.parent / "unit"
for _path in (_INTEGRATION, _UNIT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
from test_rest_contour_project_lifecycle_r272 import (  # noqa: E402
    _profile_resolver,
    _project_with_published_upstream,
)
from test_rest_finishing_core_r273 import _inputs as _r273_inputs  # noqa: E402


def _parameters(*, nominal_target_z: float = 2.0, max_stepdown: float = 50.0) -> RestFinishingParameters:
    unit = LengthUnit.MM
    return RestFinishingParameters(
        unit, ContourProfileSource.PLANAR_FACE_OUTER,
        Length(nominal_target_z, unit), Length(0.0, unit), Length(0.01, unit),
        Length(0.5, unit), Length(max_stepdown, unit), Length(55.0, unit),
        Length(52.0, unit), FeedRate(300.0, FeedUnit.MM_PER_MINUTE),
        FeedRate(80.0, FeedUnit.MM_PER_MINUTE), SpindleSpeed(1000.0),
    )


def _resolver(descriptor):
    def resolve(reference):
        return ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED
            if reference == descriptor.reference
            else GeometryResolutionStatus.MISSING,
            descriptor if reference == descriptor.reference else None,
        )
    return resolve


@pytest.fixture(scope="module")
def r274_seed(tmp_path_factory):
    """One genuine persisted R272 project with an official R274 consumer.

    Hostile cases copy these bytes instead of repeatedly regenerating the
    upstream Rest Contour authority.  The finishing operation is intentionally
    uncomputed so no test receives an in-memory only authority as a fixture.
    """
    root = tmp_path_factory.mktemp("r274-seed")
    service = ProjectService.create_default(root / "config")
    session = service.new_project(root, "R274 seed")
    inputs, job, r272_id = _project_with_published_upstream(service, root)
    snapshot = service.current_project.cam_snapshot
    setup = snapshot.jobs[0].setups[0]
    r272_operation = setup.operation_tree.get_operation(r272_id)
    machine = replace(
        snapshot.machine_definitions[0],
        spindles=tuple(
            replace(spindle, directions=(SpindleDirection.CLOCKWISE,))
            for spindle in snapshot.machine_definitions[0].spindles
        ),
    )
    r272_operation = replace(
        r272_operation,
        machine_requirement=replace(
            r272_operation.machine_requirement,
            expected_fingerprint=machine.content_fingerprint,
        ),
    )
    setup = replace(setup, operation_tree=setup.operation_tree.replace_operation(r272_operation))
    changed_job = CamJob.from_dict(snapshot.jobs[0].to_dict())
    changed_job.replace_setup(setup)
    service.stage_cam_snapshot(replace(
        snapshot, jobs=(changed_job,), machine_definitions=(machine,),
    ))
    assert service.generate_rest_contour(
        r272_id, profile_resolver=_profile_resolver(inputs),
    ).status.value == "SUCCESS"
    snapshot = service.current_project.cam_snapshot
    setup = snapshot.jobs[0].setups[0]
    producer = setup.operation_tree.get_operation(r272_id)
    finishing = _r273_inputs()
    finishing_assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "R274 finishing tool", finishing.tool,
        finishing.assembly.stickout, finishing.assembly.gauge_length,
    )
    service.stage_cam_snapshot(replace(
        snapshot,
        tool_definitions=(*snapshot.tool_definitions, finishing.tool),
        tool_assemblies=(*snapshot.tool_assemblies, finishing_assembly),
    ))
    service.create_rest_finishing_operation(
        job.job_id, setup.setup_id, setup.operation_tree.root_id,
        operation_id=OperationId.new(), node_id=CamNodeId.new(),
        name="Rest Finishing", parameters=_parameters(max_stepdown=10.0),
        profile=RestFinishingProfileSelection(inputs.profile_descriptor),
        dependency_operation_id=r272_id,
        tool_assembly_id=finishing_assembly.assembly_id,
        machine_requirement=producer.machine_requirement,
    )
    operation_id = next(
        operation.operation_id
        for operation in service.current_project.cam_snapshot.jobs[0].setups[0].operation_tree.operations
        if operation.strategy_key == "rest_finishing_3axis"
    )
    service.save()
    service.close_project()
    return session.root_path, operation_id, inputs.profile_descriptor


def _copy_seed(r274_seed, tmp_path: Path) -> tuple[ProjectService, OperationId, object]:
    source, operation_id, descriptor = r274_seed
    target = tmp_path / source.name
    shutil.copytree(source, target)
    service = ProjectService.create_default(tmp_path / "config")
    service.open_project(target)
    return service, operation_id, descriptor


@pytest.fixture(scope="module")
def r274_computed(r274_seed, tmp_path_factory):
    """Generate one genuine R274 completion for copy-only hostile reopen tests."""
    root = tmp_path_factory.mktemp("r274-computed")
    service, operation_id, descriptor = _copy_seed(r274_seed, root)
    result = service.generate_rest_finishing(
        operation_id, profile_resolver=_resolver(descriptor), persist=True,
    )
    assert result.status is RestFinishingApplicationStatus.SUCCESS
    project_root = service.current_project.root_path
    service.close_project()
    return project_root, operation_id, descriptor


def _copy_computed(r274_computed, tmp_path: Path) -> tuple[ProjectService, OperationId, object]:
    source, operation_id, descriptor = r274_computed
    target = tmp_path / source.name
    shutil.copytree(source, target)
    service = ProjectService.create_default(tmp_path / "config")
    service.open_project(target)
    return service, operation_id, descriptor


def _stage_setup(service: ProjectService, changed_setup) -> None:
    """Replace one Setup in the test snapshot without touching durable bytes."""
    snapshot = service.current_project.cam_snapshot
    changed_job = CamJob.from_dict(snapshot.jobs[0].to_dict())
    changed_job.replace_setup(changed_setup)
    service.stage_cam_snapshot(replace(snapshot, jobs=(changed_job,)))


def test_r274_generates_and_reopens_from_fresh_r272_replay(tmp_path: Path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "R274 lifecycle")
    inputs, job, r272_id = _project_with_published_upstream(service, tmp_path)
    # R272 fixture authority intentionally permits an unspecified spindle
    # direction.  R273 correctly requires clockwise finishing, so make the
    # persisted R272 consumer machine explicit before generating its artifact.
    snapshot = service.current_project.cam_snapshot
    setup = snapshot.jobs[0].setups[0]
    r272_operation = setup.operation_tree.get_operation(r272_id)
    machine = replace(
        snapshot.machine_definitions[0],
        spindles=tuple(
            replace(spindle, directions=(SpindleDirection.CLOCKWISE,))
            for spindle in snapshot.machine_definitions[0].spindles
        ),
    )
    r272_operation = replace(
        r272_operation,
        machine_requirement=replace(
            r272_operation.machine_requirement,
            expected_fingerprint=machine.content_fingerprint,
        ),
    )
    setup = replace(
        setup, operation_tree=setup.operation_tree.replace_operation(r272_operation),
    )
    changed_job = CamJob.from_dict(snapshot.jobs[0].to_dict())
    changed_job.replace_setup(setup)
    service.stage_cam_snapshot(replace(
        snapshot,
        jobs=(changed_job,),
        machine_definitions=(machine,),
    ))
    r272 = service.generate_rest_contour(r272_id, profile_resolver=_profile_resolver(inputs))
    assert r272.status.value == "SUCCESS"
    snapshot = service.current_project.cam_snapshot
    setup = snapshot.jobs[0].setups[0]
    producer = setup.operation_tree.get_operation(r272_id)
    finishing = _r273_inputs()
    finishing_assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "R274 finishing tool", finishing.tool,
        finishing.assembly.stickout, finishing.assembly.gauge_length,
    )
    service.stage_cam_snapshot(replace(
        snapshot,
        tool_definitions=(*snapshot.tool_definitions, finishing.tool),
        tool_assemblies=(*snapshot.tool_assemblies, finishing_assembly),
    ))
    r272_profile = inputs.profile_descriptor

    def finishing_resolver(reference):
        return ResolvedContourProfile(
            GeometryResolutionStatus.RESOLVED
            if reference == r272_profile.reference
            else GeometryResolutionStatus.MISSING,
            r272_profile if reference == r272_profile.reference else None,
        )

    service.create_rest_finishing_operation(
        job.job_id, setup.setup_id, setup.operation_tree.root_id,
        operation_id=OperationId.new(),
        node_id=CamNodeId.new(), name="Rest Finishing", parameters=_parameters(max_stepdown=10.0),
        profile=RestFinishingProfileSelection(r272_profile),
        dependency_operation_id=r272_id,
        tool_assembly_id=finishing_assembly.assembly_id,
        machine_requirement=producer.machine_requirement,
    )
    operation_id = next(
        operation.operation_id for operation in service.current_project.cam_snapshot.jobs[0].setups[0].operation_tree.operations
        if operation.strategy_key == "rest_finishing_3axis"
    )
    result = service.generate_rest_finishing(
        operation_id, profile_resolver=finishing_resolver, persist=True,
    )
    assert result.status is RestFinishingApplicationStatus.SUCCESS
    service.close_project()
    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(session.root_path)
    replay = reopened.generate_rest_finishing(
        operation_id, profile_resolver=finishing_resolver, persist=False,
    )
    assert replay.status is RestFinishingApplicationStatus.SUCCESS


def test_r274_cancel_before_sqlite_leaves_seed_metadata_authoritative(
    r274_seed,
    tmp_path: Path,
) -> None:
    """A cancellation at the public application boundary writes no R274 row."""
    service, operation_id, descriptor = _copy_seed(r274_seed, tmp_path)
    before = service.current_project.cam_snapshot
    result = service.generate_rest_finishing(
        operation_id,
        profile_resolver=_resolver(descriptor),
        cancellation=lambda: True,
    )
    assert result.status is RestFinishingApplicationStatus.CANCELLED
    assert result.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    after = service.current_project.cam_snapshot
    assert after == before
    assert all(item.operation_id != operation_id for item in after.artifacts)
    dependency = next(
        item for item in after.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert dependency.successor_publication is None


def test_r274_missing_dependency_fails_before_fresh_r272_replay(
    r274_seed,
    tmp_path: Path,
) -> None:
    """The persisted input record is mandatory; no list-order fallback exists."""
    service, operation_id, descriptor = _copy_seed(r274_seed, tmp_path)
    snapshot = service.current_project.cam_snapshot
    service.stage_cam_snapshot(replace(
        snapshot,
        material_state_dependencies=tuple(
            item for item in snapshot.material_state_dependencies
            if item.consumer_operation_id != operation_id
        ),
    ))
    result = service.prepare_rest_finishing(
        operation_id, profile_resolver=_resolver(descriptor),
    )
    assert result.status is RestFinishingPreparationStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID


def test_r274_multiple_material_edges_are_typed_ambiguous_before_replay(
    r274_seed,
    tmp_path: Path,
) -> None:
    service, operation_id, descriptor = _copy_seed(r274_seed, tmp_path)
    snapshot = service.current_project.cam_snapshot
    setup = snapshot.jobs[0].setups[0]
    dependency = next(
        item for item in snapshot.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    # The exact duplicate edge is rejected by the domain.  A second real
    # ancestor is required to exercise the application ambiguity branch
    # without bypassing graph validation.
    ancestor = next(
        item.producer_operation_id
        for item in snapshot.material_state_dependencies
        if item.consumer_operation_id == dependency.producer_operation_id
    )
    changed_tree = setup.operation_tree.with_dependency_added(
        DependencyEdge.material_state(ancestor, operation_id)
    )
    _stage_setup(service, replace(setup, operation_tree=changed_tree))
    result = service.prepare_rest_finishing(
        operation_id, profile_resolver=_resolver(descriptor),
    )
    assert result.status is RestFinishingPreparationStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_AMBIGUOUS


def test_r274_foreign_dependency_row_is_invalid_before_replay(
    r274_seed,
    tmp_path: Path,
) -> None:
    service, operation_id, descriptor = _copy_seed(r274_seed, tmp_path)
    snapshot = service.current_project.cam_snapshot
    dependency = next(
        item for item in snapshot.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    ancestor = next(
        item.producer_operation_id
        for item in snapshot.material_state_dependencies
        if item.consumer_operation_id == dependency.producer_operation_id
    )
    service.stage_cam_snapshot(replace(
        snapshot,
        material_state_dependencies=tuple(
            replace(item, producer_operation_id=ancestor)
            if item.consumer_operation_id == operation_id else item
            for item in snapshot.material_state_dependencies
        ),
    ))
    result = service.prepare_rest_finishing(
        operation_id, profile_resolver=_resolver(descriptor),
    )
    assert result.status is RestFinishingPreparationStatus.FAILURE
    assert result.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID


def test_r274_reopen_treats_wrong_artifact_bytes_as_non_authoritative(
    r274_computed,
    tmp_path: Path,
) -> None:
    """A committed SQLite pointer cannot promote checksum-mismatched bytes."""
    service, operation_id, _descriptor = _copy_computed(r274_computed, tmp_path)
    metadata = next(
        item for item in service.current_project.cam_snapshot.artifacts
        if item.operation_id == operation_id
    )
    root = service.current_project.root_path
    path = ToolpathArtifactStore().resolve_metadata_path(root, metadata)
    payload = bytearray(path.read_bytes())
    payload[-1] ^= 1
    path.write_bytes(bytes(payload))
    service.close_project()
    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    snapshot = reopened.current_project.cam_snapshot
    assert all(item.operation_id != operation_id for item in snapshot.artifacts)


def test_r274_fresh_replay_rejects_coherently_resealed_persisted_output(
    r274_computed,
    tmp_path: Path,
) -> None:
    """Checksums and v2 seals cannot replace deterministic R273 replay."""
    service, operation_id, descriptor = _copy_computed(r274_computed, tmp_path)
    snapshot = service.current_project.cam_snapshot
    metadata = next(
        item for item in snapshot.artifacts if item.operation_id == operation_id
    )
    dependency = next(
        item for item in snapshot.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert dependency.successor_publication is not None
    root = service.current_project.root_path
    store = ToolpathArtifactStore()
    artifact = store.load(root, metadata)
    events = list(artifact.events)
    index = next(
        index for index, event in enumerate(events)
        if isinstance(event, (LinearMove, ArcMove))
        and event.motion_class in {MotionClass.CUTTING, MotionClass.LINK}
        and event.feed_rate is not None
    )
    event = events[index]
    events[index] = replace(
        event,
        feed_rate=replace(event.feed_rate, value=event.feed_rate.value + 7.0),
    )
    forged = ToolpathArtifact.create(
        artifact_id=artifact.artifact_id,
        source_operation_id=artifact.source_operation_id,
        operation_revision=artifact.operation_revision,
        computation_token=artifact.computation_token,
        input_fingerprint=artifact.input_fingerprint,
        coordinate_space=artifact.coordinate_space,
        unit=artifact.unit,
        setup_id=artifact.setup_id,
        setup_revision=artifact.setup_revision,
        wcs_fingerprint=artifact.wcs_fingerprint,
        tool_assembly_id=artifact.tool_assembly_id,
        tool_assembly_fingerprint=artifact.tool_assembly_fingerprint,
        machine_id=artifact.machine_id,
        machine_fingerprint=artifact.machine_fingerprint,
        initial_pose=artifact.initial_pose,
        events=tuple(events),
        diagnostics=artifact.diagnostics,
        completion_status=artifact.completion_status,
        created_at=artifact.created_at,
    )
    assert compute_material_removal_fingerprint(forged) == (
        dependency.successor_publication.semantic_material_removal_fingerprint
    )
    forged_payload = json.dumps(
        artifact_to_dict(forged),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    store.resolve_metadata_path(root, metadata).write_bytes(forged_payload)
    service.close_project()

    with sqlite3.connect(root / "project.db") as connection:
        operation_row = connection.execute(
            "SELECT payload_json FROM cam_operations WHERE operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert operation_row is not None
        operation_payload = json.loads(operation_row[0])
        operation_payload["artifact_state"]["artifact_fingerprint"] = (
            forged.artifact_fingerprint.to_dict()
        )
        connection.execute(
            "UPDATE cam_operations SET payload_json=? WHERE operation_id=?",
            (
                json.dumps(operation_payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")),
                str(operation_id),
            ),
        )
        connection.execute(
            "UPDATE toolpath_artifacts SET checksum_sha256=?, "
            "artifact_fingerprint_json=?, size_bytes=? WHERE operation_id=?",
            (
                hashlib.sha256(forged_payload).hexdigest(),
                json.dumps(forged.artifact_fingerprint.to_dict(), sort_keys=True,
                           separators=(",", ":")),
                len(forged_payload),
                str(operation_id),
            ),
        )
        row = connection.execute(
            "SELECT payload_json FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        publication = payload["successor_publication"]
        publication["artifact_fingerprint"] = forged.artifact_fingerprint.to_dict()
        fingerprint_payload = dict(publication)
        fingerprint_payload.pop("publication_fingerprint")
        publication["publication_fingerprint"] = ContentFingerprint.from_payload(
            fingerprint_payload
        ).to_dict()
        connection.execute(
            "UPDATE cam_material_state_dependencies SET payload_json=? "
            "WHERE consumer_operation_id=?",
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")),
                str(operation_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "coherent-reopen-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_finishing(
        operation_id,
        profile_resolver=_resolver(descriptor),
        persist=False,
    )
    assert replay.status is RestFinishingApplicationStatus.FAILURE
    assert replay.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert replay.message == (
        "Rest Finishing persisted artifact differs from fresh deterministic replay"
    )


def test_r274_fresh_replay_rejects_coherently_resealed_successor_heightfield(
    r274_computed,
    tmp_path: Path,
) -> None:
    """A checksum-valid forged successor grid is evidence, never authority."""
    service, operation_id, descriptor = _copy_computed(r274_computed, tmp_path)
    dependency = next(
        item for item in service.current_project.cam_snapshot.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert dependency.successor_publication is not None
    publication = dependency.successor_publication
    root = service.current_project.root_path
    state_path = (
        root / ".hms" / "cam" / "material_state"
        / f"{publication.successor_state_fingerprint.digest}.state.json"
    )
    document = json.loads(state_path.read_text(encoding="utf-8"))
    heights = list(document["top_heights"])
    index = next(
        index for index, value in enumerate(heights)
        if value + 0.25 <= service.current_project.cam_snapshot.jobs[0].setups[0].stock.size_z.value
    )
    heights[index] += 0.25
    document["top_heights"] = heights
    document["remaining_volume"] += (
        0.25 * document["cell_size_x"] * document["cell_size_y"]
    )
    content_payload = {
        "format": "HMS_CAM_MATERIAL_STATE_CONTENT_INTEGRITY",
        "format_version": 1,
        "schema_version": document["format_version"],
        "width": document["width"],
        "height": document["height"],
        "cell_size_x": document["cell_size_x"],
        "cell_size_y": document["cell_size_y"],
        "top_heights": document["top_heights"],
        "initial_volume": document["initial_volume"],
        "remaining_volume": document["remaining_volume"],
        "unit": document["unit"],
    }
    forged_content = ContentFingerprint.from_payload(content_payload)
    document["content_integrity_fingerprint"] = forged_content.to_dict()
    document["checksum_sha256"] = ""
    unsigned = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    document["checksum_sha256"] = hashlib.sha256(unsigned).hexdigest()
    state_path.write_bytes(json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))
    service.close_project()

    with sqlite3.connect(root / "project.db") as connection:
        row = connection.execute(
            "SELECT payload_json FROM cam_material_state_dependencies "
            "WHERE consumer_operation_id=?",
            (str(operation_id),),
        ).fetchone()
        assert row is not None
        payload = json.loads(row[0])
        successor = payload["successor_publication"]
        successor["successor_state_content_seal"] = forged_content.to_dict()
        fingerprint_payload = dict(successor)
        fingerprint_payload.pop("publication_fingerprint")
        successor["publication_fingerprint"] = ContentFingerprint.from_payload(
            fingerprint_payload
        ).to_dict()
        connection.execute(
            "UPDATE cam_material_state_dependencies SET payload_json=? "
            "WHERE consumer_operation_id=?",
            (
                json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")),
                str(operation_id),
            ),
        )

    reopened = ProjectService.create_default(tmp_path / "state-reseal-config")
    reopened.open_project(root)
    replay = reopened.generate_rest_finishing(
        operation_id,
        profile_resolver=_resolver(descriptor),
        persist=False,
    )
    assert replay.status is RestFinishingApplicationStatus.FAILURE
    assert replay.diagnostic_code is RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID
    assert replay.message == (
        "Rest Finishing persisted successor differs from fresh deterministic replay"
    )


def test_r274_parameter_invalidation_removes_completed_output_authority(
    r274_computed,
    tmp_path: Path,
) -> None:
    """A changed consumer parameter cannot retain its prior artifact pointer."""
    service, operation_id, _descriptor = _copy_computed(r274_computed, tmp_path)
    snapshot = service.current_project.cam_snapshot
    setup = snapshot.jobs[0].setups[0]
    operation = setup.operation_tree.get_operation(operation_id)
    values = tuple(
        (name, value + 0.1 if name == "stepover" else value)
        for name, value in operation.parameters.values
    )
    changed = replace(
        operation,
        parameters=replace(operation.parameters, values=values),
        revision=operation.revision.next(),
    )
    _stage_setup(
        service,
        replace(setup, operation_tree=setup.operation_tree.replace_operation(changed)),
    )
    after = service.current_project.cam_snapshot
    assert all(item.operation_id != operation_id for item in after.artifacts)
    dependency = next(
        item for item in after.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert dependency.successor_publication is None


def test_r274_precommit_save_failure_keeps_bytes_as_unreferenced_orphans(
    r274_seed,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Files-first bytes never become authority when SQLite save cannot start."""
    service, operation_id, descriptor = _copy_seed(r274_seed, tmp_path)
    root = service.current_project.root_path
    before = service.current_project.cam_snapshot
    before_files = frozenset((root / "toolpaths").glob("*.toolpath.json"))

    def fail_before_save(_session, *, before_transaction=None):
        del before_transaction
        raise OSError("R274 injected precommit save failure")

    monkeypatch.setattr(service._saver, "save", fail_before_save)
    with pytest.raises(OSError, match="injected precommit"):
        service.generate_rest_finishing(
            operation_id, profile_resolver=_resolver(descriptor), persist=True,
        )
    assert service.current_project.cam_snapshot == before
    durable = CamSqliteRepository().load(root / "project.db")
    assert durable == before
    new_files = frozenset((root / "toolpaths").glob("*.toolpath.json")) - before_files
    assert new_files
    assert all(item.operation_id != operation_id for item in durable.artifacts)
    service.close_project()
    reopened = ProjectService.create_default(tmp_path / "reopen-config")
    reopened.open_project(root)
    assert all(
        item.operation_id != operation_id
        for item in reopened.current_project.cam_snapshot.artifacts
    )


def test_r274_postcommit_mark_persisted_exception_reconciles_success(
    r274_seed,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A post-commit bookkeeping fault may not relabel durable SUCCESS."""
    service, operation_id, descriptor = _copy_seed(r274_seed, tmp_path)

    def fail_after_commit(_snapshot=None):
        raise RuntimeError("R274 injected postcommit mark_persisted failure")

    monkeypatch.setattr(service._cam_application, "mark_persisted", fail_after_commit)
    result = service.generate_rest_finishing(
        operation_id, profile_resolver=_resolver(descriptor), persist=True,
    )
    assert result.status is RestFinishingApplicationStatus.SUCCESS
    snapshot = service.current_project.cam_snapshot
    assert any(item.operation_id == operation_id for item in snapshot.artifacts)
    dependency = next(
        item for item in snapshot.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert dependency.successor_publication is not None
    assert service.current_project.is_dirty is False


def test_r274_postcommit_saver_exception_reconciles_canonical_row_order(
    r274_seed,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A committed save remains SUCCESS when SQLite canonicalizes row order."""
    service, operation_id, descriptor = _copy_seed(r274_seed, tmp_path)
    original_save = service._saver.save
    committed = False

    def commit_reordered_then_fail(session, *, before_transaction=None):
        nonlocal committed
        session.cam_snapshot = replace(
            session.cam_snapshot,
            artifacts=tuple(sorted(
                session.cam_snapshot.artifacts,
                key=lambda item: str(item.operation_id),
                reverse=True,
            )),
            material_state_dependencies=tuple(sorted(
                session.cam_snapshot.material_state_dependencies,
                key=lambda item: str(item.consumer_operation_id),
                reverse=True,
            )),
        )
        assert tuple(
            str(item.operation_id) for item in session.cam_snapshot.artifacts
        ) != tuple(sorted(
            str(item.operation_id) for item in session.cam_snapshot.artifacts
        ))
        assert tuple(
            str(item.consumer_operation_id)
            for item in session.cam_snapshot.material_state_dependencies
        ) != tuple(sorted(
            str(item.consumer_operation_id)
            for item in session.cam_snapshot.material_state_dependencies
        ))
        original_save(session, before_transaction=before_transaction)
        committed = True
        raise RuntimeError("R274 injected saver failure after SQLite commit")

    monkeypatch.setattr(service._saver, "save", commit_reordered_then_fail)
    result = service.generate_rest_finishing(
        operation_id, profile_resolver=_resolver(descriptor), persist=True,
    )
    assert committed
    assert result.status is RestFinishingApplicationStatus.SUCCESS
    durable = CamSqliteRepository().load(
        service.current_project.root_path / "project.db"
    )
    assert service.current_project.cam_snapshot == durable
    assert service.current_project.persisted_cam_snapshot == durable
    assert service.current_project.is_dirty is False
    assert any(item.operation_id == operation_id for item in durable.artifacts)
    dependency = next(
        item for item in durable.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert dependency.successor_publication is not None


def test_r274_final_pretransaction_cancellation_keeps_sqlite_unchanged(
    r274_seed,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Arm cancellation only at saver entry to exercise the final checkpoint."""
    service, operation_id, descriptor = _copy_seed(r274_seed, tmp_path)
    before = service.current_project.cam_snapshot
    armed = False
    original_save = service._saver.save

    def arm_then_save(session, *, before_transaction=None):
        nonlocal armed
        armed = True
        return original_save(session, before_transaction=before_transaction)

    monkeypatch.setattr(service._saver, "save", arm_then_save)
    result = service.generate_rest_finishing(
        operation_id,
        profile_resolver=_resolver(descriptor),
        cancellation=lambda: armed,
        persist=True,
    )
    assert armed
    assert result.status is RestFinishingApplicationStatus.CANCELLED
    assert result.diagnostic_code is RestFinishingDiagnosticCode.CANCELLED
    assert service.current_project.cam_snapshot == before
    assert all(item.operation_id != operation_id for item in before.artifacts)


def test_r274_feed_only_edit_preserves_semantic_output_authority(
    r274_computed,
    tmp_path: Path,
) -> None:
    """Feed-only DIRTY lifecycle preserves removal semantics through reopen."""
    service, operation_id, descriptor = _copy_computed(r274_computed, tmp_path)
    snapshot = service.current_project.cam_snapshot
    setup = snapshot.jobs[0].setups[0]
    operation = setup.operation_tree.get_operation(operation_id)
    before = next(
        item for item in snapshot.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert before.successor_publication is not None
    prior_artifact = next(
        item for item in snapshot.artifacts if item.operation_id == operation_id
    )
    values = tuple(
        (name, value + 1.0 if name == "cutting_feed_rate" else value)
        for name, value in operation.parameters.values
    )
    changed = replace(
        operation,
        parameters=replace(operation.parameters, values=values),
        revision=operation.revision.next(),
        artifact_state=operation.artifact_state.mark_dirty(
            DirtyReason.PARAMETERS_CHANGED
        ),
    )
    _stage_setup(
        service,
        replace(setup, operation_tree=setup.operation_tree.replace_operation(changed)),
    )
    after = service.current_project.cam_snapshot
    retained = next(
        item for item in after.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert retained.successor_publication is not None
    assert (
        retained.successor_publication.semantic_material_removal_fingerprint
        == before.successor_publication.semantic_material_removal_fingerprint
    )
    service.save()
    root = service.current_project.root_path
    service.close_project()
    reopened = ProjectService.create_default(tmp_path / "feed-reopen-config")
    reopened.open_project(root)
    persisted = next(
        item for item in reopened.current_project.cam_snapshot.material_state_dependencies
        if item.consumer_operation_id == operation_id
    )
    assert persisted.successor_publication is not None
    regenerated = reopened.generate_rest_finishing(
        operation_id,
        profile_resolver=_resolver(descriptor),
        persist=True,
    )
    assert regenerated.status is RestFinishingApplicationStatus.SUCCESS
    current = reopened.current_project.cam_snapshot
    artifact = next(
        item for item in current.artifacts if item.operation_id == operation_id
    )
    completion = next(
        item for item in current.material_state_dependencies
        if item.consumer_operation_id == operation_id
    ).successor_publication
    assert completion is not None
    assert artifact.artifact_fingerprint != prior_artifact.artifact_fingerprint
    assert (
        completion.semantic_material_removal_fingerprint
        == before.successor_publication.semantic_material_removal_fingerprint
    )
    assert (
        completion.successor_state_fingerprint
        == before.successor_publication.successor_state_fingerprint
    )

"""Stage 7A.6 CAM SQLite, artifact store and project lifecycle tests."""

from contextlib import closing
import dataclasses
import json
import math
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    AffineTransform, Angle, AngleUnit, ArtifactState, ArtifactStatus, BoxStock,
    BoringBarGeometry, BoringStrategy, CamJob, CamJobId, CamNodeId,
    ContentFingerprint, CylindricalGeometry,
    DependencyEdge, DependencyFingerprint, DiagnosticCode, DirtyReason, FeedRate, FeedUnit,
    GeometryFingerprint, GeometryInputId, GeometryInputRole, GeometryReference,
    GeometryReferenceId, GeometryReferenceKind, GeometryRepresentationKind,
    DrillDepthDefinition, DrillGeometryInput, HoleLocation, HolePattern,
    HolderDefinition, HolderDefinitionId, HolderSection, KinematicChain,
    KinematicMount, KinematicNode, KinematicSide, Length, LengthUnit,
    MachineAxis, MachineAxisType, MachineCapabilities, MachineDefinition,
    MachineDefinitionId, MachineKind, Operation, OperationCapability,
    OperationFamily, OperationGeometryInput, OperationId, OperationParameterSet,
    OperationTree, Point3, Revision, Setup, SetupId, SetupKind, ShankGeometry,
    SourceScope, SpindleCapability, SpindleDirection, SpindleSpeed,
    ToolAssembly, ToolAssemblyId,
    ToolAssemblyReference, ToolDefinition, ToolDefinitionId, ToolFamily,
    ToolHand, ToolpathArtifactId, Vector3, WcsFrame, WorkEnvelope, WorkOffset,
)
from hms_cadcam.cam.persistence import (
    CamPersistencePayloadError, CamProjectSnapshot, CamSqliteRepository,
    ToolpathArtifactMetadata, ToolpathArtifactStore, ToolpathArtifactStoreError,
)
from hms_cadcam.cam.toolpath import Pose, ToolpathBuilder
from hms_cadcam.project.constants import DATABASE_SCHEMA_VERSION
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.migrations import MIGRATIONS
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.exceptions import RecoveryRequiredError


def _fp(name):
    return ContentFingerprint.from_payload({"name": name})


def _reference(source_id, selector=None, occurrence="assembly:1/part:1"):
    selector = selector or f"hms_face_v1:{'a' * 64}:{'b' * 64}"
    return GeometryReference(GeometryReferenceId.new(), "hms_persistent_geometry", 1,
        source_id, GeometryReferenceKind.FACE, GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"selector": selector, "occurrence": occurrence}),
        Revision(2), occurrence_path=occurrence, subshape_selector=selector)


def _tooling():
    tool = ToolDefinition(ToolDefinitionId.new(), "End mill", ToolFamily.END_MILL, LengthUnit.MM,
        CylindricalGeometry(Length(10, LengthUnit.MM), Length(20, LengthUnit.MM)),
        Length(100, LengthUnit.MM), Length(30, LengthUnit.MM),
        ShankGeometry(Length(10, LengthUnit.MM), Length(70, LengthUnit.MM)), Revision(2))
    holder = HolderDefinition(HolderDefinitionId.new(), "Holder", LengthUnit.MM,
        (HolderSection(Length(0, LengthUnit.MM), Length(40, LengthUnit.MM),
                       Length(30, LengthUnit.MM), Length(40, LengthUnit.MM)),),
        Length(0, LengthUnit.MM), Revision(3), "generic_taper")
    assembly = ToolAssembly.create(ToolAssemblyId.new(), "Assembly", tool,
        Length(40, LengthUnit.MM), Length(80, LengthUnit.MM), holder)
    return tool, holder, assembly


def _machine():
    axis = MachineAxis("axis_x", "longitudinal_motion", MachineAxisType.LINEAR,
        Vector3(1, 0, 0), Length(-500, LengthUnit.MM), Length(500, LengthUnit.MM),
        Length(0, LengthUnit.MM))
    nodes = (KinematicNode("base", None, None, KinematicSide.FIXED, KinematicMount.NONE,
                          AffineTransform.identity(LengthUnit.MM)),
             KinematicNode("slide", "base", "axis_x", KinematicSide.TOOL, KinematicMount.TOOL,
                          AffineTransform.identity(LengthUnit.MM)))
    capabilities = MachineCapabilities(milling=True, turning=False, live_tooling=False,
        probing=True, tapping=True, threading=False, spindle_count=1,
        maximum_feed=FeedRate(5000, FeedUnit.MM_PER_MINUTE),
        maximum_rapid=FeedRate(10000, FeedUnit.MM_PER_MINUTE), tool_capacity=12,
        coolant=(), operations=(OperationCapability.MILLING,))
    return MachineDefinition(MachineDefinitionId.new(), "Mill", MachineKind.MILL,
        LengthUnit.MM, (axis,), (SpindleCapability("main", SpindleSpeed(100), SpindleSpeed(10000)),),
        capabilities, KinematicChain(nodes),
        WorkEnvelope(Length(1000, LengthUnit.MM), Length(500, LengthUnit.MM), Length(500, LengthUnit.MM)), Revision(4))


def _snapshot(*, computing=False, repeated=False):
    source_id = uuid4()
    frame = WcsFrame.identity(LengthUnit.MM)
    tool, holder, assembly = _tooling()
    setup_id = SetupId.new()
    reference = _reference(source_id)
    inputs = [OperationGeometryInput(GeometryInputId.new(), GeometryInputRole.DRIVE_GEOMETRY, reference)]
    if repeated:
        inputs.append(OperationGeometryInput(GeometryInputId.new(), GeometryInputRole.CHECK_GEOMETRY,
            _reference(source_id, occurrence="assembly:1/part:2")))
    state = ArtifactState()
    if computing:
        state, _ = state.begin(DependencyFingerprint.from_payload({"compute": 1}))
    operation = Operation(OperationId.new(), CamNodeId.new(), OperationFamily.MILLING, setup_id,
        ToolAssemblyReference.from_assembly(assembly), tuple(inputs),
        OperationParameterSet("mill.persistence", 1, (("depth", 2.0),)), artifact_state=state)
    tree = OperationTree.empty(setup_id).add_operation(
        OperationTree.empty(setup_id).root_id, "Contour", operation
    )
    tree = tree.with_dependency_added(DependencyEdge.operation_output(operation.operation_id, operation.operation_id)) if False else tree
    setup = Setup(setup_id, "Setup", SetupKind.MILL, frame, WorkOffset("PRIMARY", 1),
        BoxStock(Length(20, LengthUnit.MM), Length(15, LengthUnit.MM), Length(5, LengthUnit.MM), frame),
        reference, SourceScope(source_id), operation_tree=tree)
    job = CamJob(CamJobId.new(), "Job", revision=Revision(5), setups=(setup,), active_setup_id=setup_id)
    return CamProjectSnapshot((job,), job.job_id, (tool,), (holder,), (assembly,), (_machine(),)), operation


def _persist(database_path, snapshot):
    repository = CamSqliteRepository()
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        repository.replace_all(connection, snapshot)
    return repository


def _downgrade_current_database_to_v3_fixture(database_path: Path) -> None:
    """Construct an exact legacy V3 fixture from a freshly initialized database."""
    current_only_tables = (
        "lathe_derived_snapshots",
        "lathe_tool_bindings",
        "lathe_operations",
        "lathe_programs",
        "toolpath_artifacts",
        "cam_dependencies",
        "cam_operations",
        "cam_nodes",
        "cam_setups",
        "cam_jobs",
        "cam_project_state",
        "cam_tool_definitions",
        "cam_holder_definitions",
        "cam_tool_assemblies",
        "cam_machine_definitions",
    )
    with closing(sqlite3.connect(database_path)) as connection, connection:
        for table in current_only_tables:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version > ?", (3,))
        connection.execute("PRAGMA user_version = 3")
        ledger = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        )
        assert ledger == (1, 2, 3)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3


def _artifact(operation, fingerprint=None):
    fingerprint = fingerprint or DependencyFingerprint.from_payload({"artifact": 1})
    computing, token = operation.artifact_state.begin(fingerprint)
    operation = dataclasses.replace(operation, artifact_state=computing)
    builder = ToolpathBuilder(artifact_id=ToolpathArtifactId.new(), operation_id=operation.operation_id,
        operation_revision=operation.revision, computation_token=token, input_fingerprint=fingerprint,
        unit=LengthUnit.MM, setup_id=operation.setup_id, setup_revision=Revision(0),
        wcs_fingerprint=_fp("wcs"), tool_assembly_id=operation.tool_assembly.assembly_id,
        tool_assembly_fingerprint=operation.tool_assembly.expected_fingerprint)
    builder.set_initial_pose(Pose(Point3(0, 0, 0, LengthUnit.MM), Vector3(0, 0, 1)))
    builder.linear_to(Pose(Point3(10, 0, 0, LengthUnit.MM), Vector3(0, 0, 1)),
                      FeedRate(100, FeedUnit.MM_PER_MINUTE))
    return builder.finalize(), operation, token, fingerprint


def test_v3_to_v4_migration_is_safe_and_empty_cam(tmp_path):
    path = tmp_path / "project.db"
    database = ProjectDatabase()
    database.initialize(path)
    _downgrade_current_database_to_v3_fixture(path)
    database.open_and_migrate(path)
    assert database.current_schema_version(path) == DATABASE_SCHEMA_VERSION
    assert DATABASE_SCHEMA_VERSION == 5
    assert CamSqliteRepository().load(path).is_empty


def test_v4_migration_rolls_back_all_statements_on_error(tmp_path, monkeypatch):
    path = tmp_path / "rollback.db"
    database = ProjectDatabase()
    database.initialize(path)
    _downgrade_current_database_to_v3_fixture(path)
    monkeypatch.setitem(MIGRATIONS, 4, ("CREATE TABLE rollback_probe(value TEXT)", "INVALID SQL"))
    with pytest.raises(Exception):
        database.open_and_migrate(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='rollback_probe'").fetchone() is None


def test_editable_state_tooling_machine_tree_and_repeated_geometry_round_trip(tmp_path):
    path = tmp_path / "project.db"
    ProjectDatabase().initialize(path)
    snapshot, _ = _snapshot(repeated=True)
    repository = _persist(path, snapshot)
    restored = repository.load(path)
    assert restored == snapshot
    inputs = restored.jobs[0].setups[0].operation_tree.operations[0].geometry_inputs
    assert inputs[0].input_id != inputs[1].input_id
    assert inputs[0].reference.occurrence_path != inputs[1].reference.occurrence_path


def test_boring_strategy_and_tooling_round_trip_in_sqlite_v4(tmp_path):
    path = tmp_path / "project.db"
    ProjectDatabase().initialize(path)
    snapshot, operation = _snapshot()
    unit = LengthUnit.MM
    point = Point3(0, 0, 0, unit)
    pattern = HolePattern((HoleLocation(
        point, Vector3(0, 0, 1), point, None, unit,
    ),), unit)
    strategy = BoringStrategy(
        unit,
        DrillGeometryInput(pattern, unit),
        DrillDepthDefinition(unit, Length(0, unit), Length(-10, unit)),
        Length(20, unit),
        Length(18, unit),
        SpindleSpeed(600),
        FeedRate(0.1, FeedUnit.MM_PER_REVOLUTION),
        Length(8, unit),
        Length(3, unit),
        SpindleDirection.CLOCKWISE,
    )
    holder = snapshot.holder_definitions[0]
    tool = ToolDefinition(
        ToolDefinitionId.new(), "Boring bar", ToolFamily.BORING_BAR, unit,
        BoringBarGeometry(
            Length(15, unit), Length(25, unit), Length(20, unit),
            ToolHand.RIGHT,
        ),
        Length(80, unit), Length(30, unit),
        ShankGeometry(Length(12, unit), Length(50, unit)),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Boring assembly", tool,
        Length(25, unit), Length(60, unit), holder,
    )
    boring_operation = dataclasses.replace(
        operation,
        family=OperationFamily.DRILLING,
        tool_assembly=ToolAssemblyReference.from_assembly(assembly),
        geometry_inputs=(),
        parameters=strategy.to_operation_parameters(),
    )
    job = snapshot.jobs[0]
    setup = job.setups[0]
    tree = OperationTree(
        setup.setup_id,
        setup.operation_tree.root_id,
        setup.operation_tree.nodes,
        (boring_operation,),
        setup.operation_tree.dependency_graph,
        setup.operation_tree.revision,
    )
    changed_setup = dataclasses.replace(setup, operation_tree=tree)
    changed_job = CamJob(
        job.job_id,
        job.name,
        revision=job.revision,
        setups=(changed_setup,),
        active_setup_id=job.active_setup_id,
    )
    candidate = dataclasses.replace(
        snapshot,
        jobs=(changed_job,),
        tool_definitions=(tool,),
        tool_assemblies=(assembly,),
    )

    restored = _persist(path, candidate).load(path)

    restored_operation = restored.jobs[0].setups[0].operation_tree.operations[0]
    assert BoringStrategy.from_operation_parameters(
        restored_operation.parameters
    ) == strategy
    assert restored.tool_definitions == (tool,)
    assert restored.tool_assemblies == (assembly,)
    assert ProjectDatabase().current_schema_version(path) == DATABASE_SCHEMA_VERSION
    assert DATABASE_SCHEMA_VERSION == 5


def test_computing_state_is_persisted_as_dirty_without_runtime_token(tmp_path):
    path = tmp_path / "project.db"
    ProjectDatabase().initialize(path)
    snapshot, _ = _snapshot(computing=True)
    repository = _persist(path, snapshot)
    operation = repository.load(path).jobs[0].setups[0].operation_tree.operations[0]
    assert operation.artifact_state.status is ArtifactStatus.DIRTY
    assert operation.artifact_state.token is None


def test_malformed_editable_payload_fails_atomically(tmp_path):
    path = tmp_path / "project.db"
    ProjectDatabase().initialize(path)
    snapshot, _ = _snapshot()
    repository = _persist(path, snapshot)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("UPDATE cam_operations SET payload_json='{}'")
    with pytest.raises(CamPersistencePayloadError):
        repository.load(path)


@pytest.mark.parametrize("unsafe", (
    "../evil.json", "/absolute.json", "C:/evil.json", r"C:\evil.json",
    "//server/share.json", r"toolpaths\evil.json", "./{valid}",
    "toolpaths//{filename}", "toolpaths/./{filename}",
))
def test_artifact_store_rejects_traversal_absolute_drive_unc_and_separator(tmp_path, unsafe):
    root = tmp_path / "Project.HMS"
    (root / "toolpaths").mkdir(parents=True)
    snapshot, operation = _snapshot()
    artifact, *_ = _artifact(operation)
    valid = ToolpathArtifactStore().publish(root, artifact)
    forged_path = unsafe.format(valid=valid.relative_path, filename=Path(valid.relative_path).name)
    forged = dataclasses.replace(valid, relative_path=forged_path)
    with pytest.raises(ToolpathArtifactStoreError):
        ToolpathArtifactStore().load(root, forged)


@pytest.mark.parametrize(
    "changes",
    ({"schema_version": 2}, {"completion_status": "tampered"}),
)
def test_artifact_store_rejects_metadata_content_mismatch(tmp_path, changes):
    root = tmp_path / "Project.HMS"
    (root / "toolpaths").mkdir(parents=True)
    _, operation = _snapshot()
    artifact, *_ = _artifact(operation)
    store = ToolpathArtifactStore()
    metadata = store.publish(root, artifact)

    with pytest.raises(ToolpathArtifactStoreError):
        store.load(root, dataclasses.replace(metadata, **changes))


def test_artifact_atomic_publish_checksum_tamper_missing_and_cleanup_safety(tmp_path):
    root = tmp_path / "Project.HMS"
    (root / "toolpaths").mkdir(parents=True)
    _, operation = _snapshot()
    artifact, *_ = _artifact(operation)
    store = ToolpathArtifactStore()
    metadata = store.publish(root, artifact)
    assert store.load(root, metadata) == artifact
    path = root / Path(metadata.relative_path)
    path.write_bytes(path.read_bytes() + b"tamper")
    with pytest.raises(ToolpathArtifactStoreError):
        store.load(root, metadata)
    path.unlink()
    with pytest.raises(ToolpathArtifactStoreError):
        store.load(root, metadata)
    outside = root / "outside.toolpath.json"
    outside.write_text("safe", encoding="utf-8")
    orphan = root / "toolpaths" / ("f" * 32 + ".toolpath.json")
    orphan.write_text("orphan", encoding="utf-8")
    store.cleanup_orphans(root, ())
    assert outside.is_file() and not orphan.exists()


def test_project_service_save_open_save_as_preserves_cam_domain_ids(tmp_path):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "CAM Project")
    snapshot, _ = _snapshot(repeated=True)
    service.stage_cam_snapshot(snapshot)
    service.save()
    root = session.root_path
    service.close_project()
    reopened = service.open_project(root)
    assert service.cam_snapshot == snapshot
    copied = service.save_as(tmp_path, "CAM Copy")
    assert copied.manifest.project_id != reopened.manifest.project_id
    assert service.cam_snapshot.jobs[0].job_id == snapshot.jobs[0].job_id
    assert service.cam_snapshot.jobs[0].setups[0].setup_id == snapshot.jobs[0].setups[0].setup_id
    copy_root = copied.root_path
    service.close_project()
    service.open_project(copy_root)
    assert service.cam_snapshot == snapshot


def test_failed_save_keeps_pending_cam_dirty_and_database_unchanged(tmp_path, monkeypatch):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Pending CAM")
    snapshot, _ = _snapshot()
    service.stage_cam_snapshot(snapshot)
    monkeypatch.setattr(service._saver._manifest_store, "save",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("save failed")))
    with pytest.raises(OSError):
        service.save()
    assert session.is_dirty and service.cam_snapshot == snapshot
    assert CamSqliteRepository().load(session.root_path / "project.db").is_empty


def test_project_save_final_callback_runs_immediately_before_sqlite_transaction(
    tmp_path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Pre-transaction cancellation")
    snapshot, _ = _snapshot()
    service.stage_cam_snapshot(snapshot)
    observed: list[CamProjectSnapshot] = []

    def stop_before_transaction() -> None:
        observed.append(
            CamSqliteRepository().load(session.root_path / "project.db")
        )
        raise RuntimeError("cancel immediately before SQLite transaction")

    with pytest.raises(RuntimeError, match="immediately before SQLite"):
        service.save(before_transaction=stop_before_transaction)
    assert observed == [CamProjectSnapshot()]
    assert CamSqliteRepository().load(session.root_path / "project.db").is_empty
    assert session.is_dirty and service.cam_snapshot == snapshot


def test_failed_cam_open_keeps_current_project_and_cam_snapshot(tmp_path):
    service = ProjectService.create_default(tmp_path / "config")
    original = service.new_project(tmp_path, "Original CAM")
    snapshot, _ = _snapshot()
    service.stage_cam_snapshot(snapshot)
    service.save()
    other = ProjectService.create_default(tmp_path / "other-config")
    broken = other.new_project(tmp_path, "Broken CAM")
    other.close_project()
    with closing(sqlite3.connect(broken.root_path / "project.db")) as connection, connection:
        connection.execute("INSERT INTO cam_jobs VALUES('bad',0,'Bad','{}',NULL)")
    with pytest.raises(CamPersistencePayloadError):
        service.open_project(broken.root_path)
    assert service.current_project is original and service.cam_snapshot == snapshot


def test_autosave_contains_editable_cam_without_cleaning_main_session(tmp_path):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Autosave CAM")
    snapshot, _ = _snapshot()
    service.stage_cam_snapshot(snapshot)
    autosave = service.autosave()
    assert autosave is not None and session.is_dirty
    restored = CamSqliteRepository().load(autosave.path / "project.db")
    assert restored == snapshot


def test_missing_artifact_on_open_keeps_operation_and_marks_dirty(tmp_path):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Missing Artifact")
    snapshot, operation = _snapshot()
    artifact, operation, token, fingerprint = _artifact(operation)
    # Replace the snapshot operation with the COMPUTING state used by the candidate.
    setup = snapshot.jobs[0].setups[0]
    tree = OperationTree(setup.setup_id, setup.operation_tree.root_id, setup.operation_tree.nodes,
        (operation,), setup.operation_tree.dependency_graph, setup.operation_tree.revision)
    job = CamJob(snapshot.jobs[0].job_id, snapshot.jobs[0].name, revision=snapshot.jobs[0].revision,
        setups=(dataclasses.replace(setup, operation_tree=tree),), active_setup_id=setup.setup_id)
    service.stage_cam_snapshot(dataclasses.replace(snapshot, jobs=(job,)))
    result = service.register_toolpath_artifact(operation.operation_id, artifact, token, fingerprint)
    assert result.accepted
    service.save()
    metadata = service.cam_snapshot.artifacts[0]
    (session.root_path / Path(metadata.relative_path)).unlink()
    root = session.root_path
    service.close_project()
    service.open_project(root)
    restored_operation = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert restored_operation.operation_id == operation.operation_id
    assert restored_operation.artifact_state.status is ArtifactStatus.DIRTY
    assert any(item.code is DiagnosticCode.ARTIFACT_MISSING for item in restored_operation.diagnostics)


def test_save_as_copies_only_referenced_valid_artifact_and_original_is_unchanged(tmp_path):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Artifact Source")
    snapshot, operation = _snapshot()
    artifact, operation, token, fingerprint = _artifact(operation)
    setup = snapshot.jobs[0].setups[0]
    tree = OperationTree(setup.setup_id, setup.operation_tree.root_id, setup.operation_tree.nodes,
        (operation,), setup.operation_tree.dependency_graph, setup.operation_tree.revision)
    job = CamJob(snapshot.jobs[0].job_id, snapshot.jobs[0].name, revision=snapshot.jobs[0].revision,
        setups=(dataclasses.replace(setup, operation_tree=tree),), active_setup_id=setup.setup_id)
    service.stage_cam_snapshot(dataclasses.replace(snapshot, jobs=(job,)))
    assert service.register_toolpath_artifact(operation.operation_id, artifact, token, fingerprint).accepted
    service.save()
    source_metadata = service.cam_snapshot.artifacts[0]
    source_path = session.root_path / Path(source_metadata.relative_path)
    source_bytes = source_path.read_bytes()

    copied = service.save_as(tmp_path, "Artifact Copy")
    copied_metadata = service.cam_snapshot.artifacts[0]
    copied_path = copied.root_path / Path(copied_metadata.relative_path)
    assert copied_path.read_bytes() == source_bytes
    assert source_path.read_bytes() == source_bytes
    assert copied_metadata.artifact_id == source_metadata.artifact_id


def test_stale_candidate_is_not_written_and_old_artifact_survives_store_failure(tmp_path, monkeypatch):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Artifact Failure")
    snapshot, operation = _snapshot()
    artifact, operation, token, fingerprint = _artifact(operation)
    setup = snapshot.jobs[0].setups[0]
    tree = OperationTree(setup.setup_id, setup.operation_tree.root_id, setup.operation_tree.nodes,
        (operation,), setup.operation_tree.dependency_graph, setup.operation_tree.revision)
    job = CamJob(snapshot.jobs[0].job_id, snapshot.jobs[0].name, revision=snapshot.jobs[0].revision,
        setups=(dataclasses.replace(setup, operation_tree=tree),), active_setup_id=setup.setup_id)
    service.stage_cam_snapshot(dataclasses.replace(snapshot, jobs=(job,)))
    assert service.register_toolpath_artifact(operation.operation_id, artifact, token, fingerprint).accepted
    old_metadata = service.cam_snapshot.artifacts[0]
    old_path = session.root_path / Path(old_metadata.relative_path)
    old_bytes = old_path.read_bytes()
    service.save()

    current = service.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    dirty = dataclasses.replace(current, artifact_state=current.artifact_state.mark_dirty(DirtyReason.PARAMETERS_CHANGED))
    computing, new_token = dirty.artifact_state.begin(DependencyFingerprint.from_payload({"new": 1}))
    dirty = dataclasses.replace(dirty, artifact_state=computing)
    changed_tree = OperationTree(setup.setup_id, tree.root_id, tree.nodes, (dirty,), tree.dependency_graph, tree.revision)
    changed_job = CamJob(job.job_id, job.name, revision=job.revision,
        setups=(dataclasses.replace(setup, operation_tree=changed_tree),), active_setup_id=setup.setup_id)
    service.stage_cam_snapshot(dataclasses.replace(service.cam_snapshot, jobs=(changed_job,)))
    builder = ToolpathBuilder(artifact_id=ToolpathArtifactId.new(), operation_id=dirty.operation_id,
        operation_revision=dirty.revision, computation_token=new_token,
        input_fingerprint=DependencyFingerprint.from_payload({"new": 1}), unit=LengthUnit.MM,
        setup_id=dirty.setup_id, setup_revision=Revision(0), wcs_fingerprint=_fp("wcs-new"),
        tool_assembly_id=dirty.tool_assembly.assembly_id,
        tool_assembly_fingerprint=dirty.tool_assembly.expected_fingerprint)
    builder.set_initial_pose(Pose(Point3(0, 0, 0, LengthUnit.MM), Vector3(0, 0, 1)))
    candidate = builder.finalize()
    monkeypatch.setattr(service._cam_application._artifact_store, "publish",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(ToolpathArtifactStoreError("fail")))
    with pytest.raises(ToolpathArtifactStoreError):
        service.register_toolpath_artifact(dirty.operation_id, candidate, new_token,
            DependencyFingerprint.from_payload({"new": 1}))
    assert old_path.read_bytes() == old_bytes


def test_recovery_restores_cam_tree_and_discards_computation_token(tmp_path):
    owner = ProjectService.create_default(tmp_path / "owner-config")
    session = owner.new_project(tmp_path, "CAM Recovery")
    snapshot, _ = _snapshot(computing=True)
    owner.stage_cam_snapshot(snapshot)
    autosave = owner.autosave()
    assert autosave is not None and session.is_dirty
    opener = ProjectService.create_default(tmp_path / "opener-config")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(session.root_path)
    recovered = opener.recover_project(raised.value.assessment)
    operation = opener.cam_snapshot.jobs[0].setups[0].operation_tree.operations[0]
    assert not recovered.is_dirty
    assert operation.artifact_state.status is ArtifactStatus.DIRTY
    assert operation.artifact_state.token is None


def test_public_domain_remains_free_of_sqlite_and_native_types():
    snapshot, operation = _snapshot()
    values = (operation, snapshot.jobs[0], snapshot.jobs[0].setups[0])
    assert all(not type(item).__module__.startswith(("sqlite3", "OCP", "PySide6")) for item in values)

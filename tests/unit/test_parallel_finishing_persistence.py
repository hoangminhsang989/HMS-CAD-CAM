"""Operation/SQLite/artifact persistence tests for Parallel Finishing 8A.2.1."""

from __future__ import annotations

import json
import sqlite3

from hms_cadcam.cam.cam3d import Cam3DProjectConfig
from hms_cadcam.cam.cam3d.parallel import (
    ParallelFinishingParameters,
    calculate_and_publish_parallel_finishing,
)
from hms_cadcam.cam.domain import (
    BoxStock,
    CamJob,
    Length,
    LengthUnit,
    Operation,
    OperationTree,
    Revision,
    Setup,
    SetupKind,
    SourceScope,
    WorkOffset,
)
from hms_cadcam.cam.persistence import CamProjectSnapshot, CamSqliteRepository
from hms_cadcam.project.constants import DATABASE_SCHEMA_VERSION
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.service import ProjectService
from tests.unit._parallel_finishing_fixtures import planar_fixture


def _snapshot(fixture, operation, metadata=()):
    tree = OperationTree.empty(fixture.zone.setup_id)
    tree = tree.add_operation(tree.root_id, "Parallel Finishing", operation)
    reference = fixture.zone.part_surfaces.selection.surfaces[0].geometry
    frame = fixture.zone.wcs
    setup = Setup(
        fixture.zone.setup_id,
        "Parallel Setup",
        SetupKind.MILL,
        frame,
        WorkOffset("PRIMARY", 1),
        BoxStock(
            Length(20.0, LengthUnit.MM),
            Length(20.0, LengthUnit.MM),
            Length(10.0, LengthUnit.MM),
            frame,
        ),
        reference,
        SourceScope(reference.source_id),
        operation_tree=tree,
        revision=fixture.zone.setup_revision,
    )
    job = CamJob(
        fixture.zone.job_id,
        "Parallel Job",
        revision=Revision(1),
        setups=(setup,),
        active_setup_id=setup.setup_id,
    )
    return CamProjectSnapshot(
        (job,),
        job.job_id,
        (fixture.tool,),
        (),
        (fixture.assembly,),
        (),
        tuple(metadata),
    )


def _persist(path, snapshot):
    repository = CamSqliteRepository()
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        repository.replace_all(connection, snapshot)
    return repository


def test_parallel_operation_json_round_trip_contains_no_native_object() -> None:
    fixture = planar_fixture()
    payload = fixture.operation.to_dict()
    encoded = json.dumps(payload, allow_nan=False, sort_keys=True)
    restored = Operation.from_dict(json.loads(encoded))
    assert restored == fixture.operation
    assert ParallelFinishingParameters.from_operation_parameters(
        restored.parameters
    ).zone_id == fixture.zone.zone_id
    assert "TopoDS" not in encoded and "OCP." not in encoded


def test_parallel_operation_and_parameters_round_trip_in_sqlite_v4(tmp_path) -> None:
    fixture = planar_fixture(stepover=1.5)
    database_path = tmp_path / "project.db"
    database = ProjectDatabase()
    database.initialize(database_path)
    repository = _persist(database_path, _snapshot(fixture, fixture.operation))
    restored = repository.load(database_path)
    operation = restored.jobs[0].setups[0].operation_tree.operations[0]
    parameters = ParallelFinishingParameters.from_operation_parameters(
        operation.parameters
    )
    assert operation == fixture.operation
    assert parameters.stepover_mm == 1.5
    assert database.current_schema_version(database_path) == 4
    assert DATABASE_SCHEMA_VERSION == 4


def test_published_parallel_artifact_metadata_round_trips_in_sqlite_v4(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    project = tmp_path / "Parallel.HMS"
    project.mkdir()
    result = calculate_and_publish_parallel_finishing(
        project,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted and result.metadata is not None
    database_path = project / "project.db"
    database = ProjectDatabase()
    database.initialize(database_path)
    repository = _persist(
        database_path,
        _snapshot(fixture, result.operation, (result.metadata,)),
    )
    restored = repository.load(database_path)
    operation = restored.jobs[0].setups[0].operation_tree.operations[0]
    assert operation.artifact_state == result.operation.artifact_state
    assert restored.artifacts == (result.metadata,)
    assert database.current_schema_version(database_path) == 4


def test_parallel_operation_survives_real_project_save_close_open(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Parallel Lifecycle")
    fixture = planar_fixture(
        project_id=session.manifest.project_id,
        stepover=2.5,
    )
    result = calculate_and_publish_parallel_finishing(
        session.root_path,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted and result.metadata is not None
    service.stage_cam_snapshot(
        _snapshot(fixture, result.operation, (result.metadata,))
    )
    service.stage_cam3d_config(
        Cam3DProjectConfig(session.manifest.project_id, (fixture.zone,))
    )
    service.save()
    service.close_project()

    reopened = service.open_project(session.root_path)
    restored = reopened.cam_snapshot
    operation = restored.jobs[0].setups[0].operation_tree.operations[0]
    assert operation == result.operation
    assert restored.artifacts == (result.metadata,)
    assert reopened.cam3d_config == Cam3DProjectConfig(
        session.manifest.project_id,
        (fixture.zone,),
    )
    assert ProjectDatabase().current_schema_version(
        reopened.root_path / "project.db"
    ) == 4

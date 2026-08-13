"""Focused R260 shared material-state contract tests."""

from __future__ import annotations

import json
from dataclasses import replace
import gc
import shutil
import sqlite3
from uuid import uuid4

import pytest

from hms_cadcam.cam.domain import (
    ArtifactStatus, BallEndGeometry, BoxStock, CamNodeId, ContentFingerprint, ContourBounds,
    ContourCurveKind, ContourLoop, ContourOrientation, ContourProfileSource,
    ContourSegment, CylindricalGeometry, DependencyFingerprint,
    FeedRate, FeedUnit, GeometryFingerprint, GeometryReference, GeometryReferenceId,
    GeometryReferenceKind, GeometryRepresentationKind, Length, LengthUnit, Operation,
    GeometryInputId, GeometryInputRole, GeometryResolutionStatus, MachineRequirement,
    OccurrenceTransformProvenance, OperationCapability, OperationFamily,
    OperationGeometryInput, OperationId, OperationParameterSet, PocketBoundary, PocketCuttingDirection,
    PocketDepthDefinition, PocketEntryPolicy, PocketGeometryInput, PocketRegion,
    PocketStrategy, Point3, ProfileProvenance, ResolvedPocketGeometry, Revision,
    Setup, SetupId, SetupKind, SpindleSpeed,
    ShankGeometry, SourceScope, ToolAssembly, ToolAssemblyId, ToolAssemblyReference,
    ToolDefinition, ToolDefinitionId, ToolFamily, ToolpathArtifactId, Vector3, WcsFrame,
    WorkOffset, REST_POCKET_STRATEGY_KEY,
)
from hms_cadcam.cam.material_state import (
    MaterialStateLoadStatus, MaterialStatePrecisionPolicy, MaterialStateStore,
    calculate_material_state, material_state_setup_fingerprint,
)
from hms_cadcam.cam.application import PocketGenerator, basic_mill_resources
from hms_cadcam.cam.application.pocket import PocketInputs
from hms_cadcam.cam.application import service as cam_service_module
from hms_cadcam.cam.application.rest_pocket import (
    MaterialStateResolutionStatus, RestPocketGenerator, RestPocketInputs,
    material_state_status_vi, resolve_material_state,
)
from hms_cadcam.cam.application.rest_region import extract_rest_regions
from hms_cadcam.cam.persistence.models import MaterialStateDependency
from hms_cadcam.cam.persistence.repository import CamSqliteRepository
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.cam.material_state.core import MaterialState
from hms_cadcam.cam.toolpath import LinearMove, MotionClass, Pose, ToolpathBuilder
from hms_cadcam.cam.toolpath.fingerprint import (
    compute_material_removal_fingerprint, compute_toolpath_fingerprint,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam_ui import _default_setup


_IDENTITY = (1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0)


def _pocket_reference(source_id, label: str = "upstream") -> GeometryReference:
    selector = f"hms_profile_v1:{'a' * 64}:face:{ContentFingerprint.from_payload({'label': label}).digest}"
    return GeometryReference(
        GeometryReferenceId.new(), "hms_profile_v1", 1, source_id,
        GeometryReferenceKind.FACE, GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"selector": selector}), Revision(0),
        subshape_selector=selector,
    )


def _pocket_region(
    reference: GeometryReference,
    bounds_xy: tuple[float, float, float, float] = (10, 10, 50, 40),
) -> PocketRegion:
    unit = LengthUnit.MM
    min_x, min_y, max_x, max_y = bounds_xy
    points = tuple(Point3(x, y, 0, unit) for x, y in (
        (min_x, min_y), (max_x, min_y), (max_x, max_y), (min_x, max_y),
    ))
    loop = ContourLoop(tuple(
        ContourSegment(ContourCurveKind.LINE, points[index],
                       points[(index + 1) % len(points)])
        for index in range(len(points))
    ), ContourOrientation.COUNTERCLOCKWISE)
    bounds = ContourBounds(
        Point3(min_x, min_y, 0, unit), Point3(max_x, max_y, 0, unit)
    )
    provenance = ProfileProvenance(
        ContourProfileSource.PLANAR_FACE_OUTER,
        OccurrenceTransformProvenance(reference.occurrence_path, _IDENTITY),
    )
    return PocketRegion(
        reference, PocketBoundary(loop, unit), points[0], Vector3(1, 0, 0),
        Vector3(0, 1, 0), Vector3(0, 0, 1), bounds, unit,
        GeometryFingerprint.from_payload({"loop": loop.to_dict()}), provenance,
    )


def _pocket_strategy(reference: GeometryReference) -> PocketStrategy:
    unit = LengthUnit.MM
    return PocketStrategy(
        unit, PocketGeometryInput(reference, unit),
        PocketDepthDefinition(unit, Length(0, unit), Length(-3, unit), Length(0, unit)),
        Length(4, unit), Length(1, unit), Length(0, unit), Length(5, unit),
        Length(2, unit), FeedRate(500, FeedUnit.MM_PER_MINUTE),
        FeedRate(100, FeedUnit.MM_PER_MINUTE), SpindleSpeed(1000),
        PocketEntryPolicy.VERTICAL_PLUNGE, PocketCuttingDirection.CLIMB,
        Length(1.0e-7, unit),
    )


def test_r266_lead_in_codec_is_additive_and_legacy_zero_is_exact():
    reference = _pocket_reference(uuid4(), "lead-legacy")
    strategy = _pocket_strategy(reference)
    assert strategy.lead_in_length == Length(0.0, LengthUnit.MM)

    legacy_parameters = strategy.to_operation_parameters()
    legacy_parameters = replace(
        legacy_parameters,
        values=tuple(
            item for item in legacy_parameters.values if item[0] != "lead_in_length"
        ),
    )
    loaded_parameters = PocketStrategy.from_operation_parameters(
        legacy_parameters, reference
    )
    assert loaded_parameters.lead_in_length == Length(0.0, LengthUnit.MM)

    legacy_payload = strategy.to_dict()
    legacy_payload.pop("lead_in_length")
    loaded_payload = PocketStrategy.from_dict(legacy_payload)
    assert loaded_payload.lead_in_length == Length(0.0, LengthUnit.MM)

    with pytest.raises(Exception):
        replace(strategy, lead_in_length=Length(-0.1, LengthUnit.MM))


def _fixture():
    unit = LengthUnit.MM
    frame = WcsFrame.identity(unit)
    source_id = uuid4()
    reference = GeometryReference(GeometryReferenceId.new(), "part", 1, source_id,
        GeometryReferenceKind.FACE, GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"part": 1}), Revision(1), subshape_selector="face:1")
    setup = Setup(SetupId.new(), "Setup", SetupKind.MILL, frame, WorkOffset("PRIMARY", 1),
        BoxStock(Length(20, unit), Length(20, unit), Length(10, unit), frame), reference,
        SourceScope(source_id), revision=Revision(0))
    tool = ToolDefinition(ToolDefinitionId.new(), "End mill", ToolFamily.END_MILL, unit,
        CylindricalGeometry(Length(4, unit), Length(12, unit)), Length(80, unit), Length(20, unit),
        ShankGeometry(Length(4, unit), Length(60, unit)), Revision(1))
    assembly = ToolAssembly.create(ToolAssemblyId.new(), "Assembly", tool, Length(20, unit), Length(60, unit))
    operation = Operation(OperationId.new(), CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (),
        parameters=__import__("hms_cadcam.cam.domain", fromlist=["OperationParameterSet"]).OperationParameterSet("test", 1),
        revision=Revision(0))
    fingerprint = DependencyFingerprint.from_payload({"r260": 1})
    computing, token = operation.artifact_state.begin(fingerprint)
    operation = operation.__class__(**{**operation.__dict__, "artifact_state": computing}) if False else operation
    builder = ToolpathBuilder(artifact_id=ToolpathArtifactId.new(), operation_id=operation.operation_id,
        operation_revision=operation.revision, computation_token=token, input_fingerprint=fingerprint,
        unit=unit, setup_id=setup.setup_id, setup_revision=setup.revision,
        wcs_fingerprint=ContentFingerprint.from_payload(frame.to_dict()),
        tool_assembly_id=assembly.assembly_id, tool_assembly_fingerprint=assembly.content_fingerprint)
    builder.set_initial_pose(Pose(Point3(2, 2, 12, unit), Vector3(0, 0, 1)))
    builder.linear_to(Pose(Point3(18, 2, 2, unit), Vector3(0, 0, 1)), FeedRate(100, FeedUnit.MM_PER_MINUTE))
    return setup, tool, assembly, builder.finalize()


def test_material_state_is_monotonic_and_fingerprint_includes_toolpath():
    setup, tool, _, artifact = _fixture()
    setup_fp = ContentFingerprint.from_payload({"setup": setup.to_dict()})
    result = calculate_material_state(stock=setup.stock, artifact=artifact, tool=tool, setup_fingerprint=setup_fp)
    assert result.state.remaining_volume < result.state.initial_volume
    assert result.removed_volume > 0.0
    assert result.state.fingerprint.digest
    assert result.state.has_rest_material


def test_material_state_store_reopen_and_corruption_fail_closed(tmp_path):
    setup, tool, _, artifact = _fixture()
    state = calculate_material_state(stock=setup.stock, artifact=artifact, tool=tool,
        setup_fingerprint=ContentFingerprint.from_payload({"setup": 1})).state
    root = tmp_path / "material-state-store"
    root.mkdir()
    store = MaterialStateStore()
    path = store.write(root, state)
    loaded = store.load(root, state.fingerprint)
    assert loaded.status is MaterialStateLoadStatus.VALID
    path.write_text(json.dumps({"format": "HMS_CAM_MATERIAL_STATE", "format_version": 1}), encoding="utf-8")
    assert store.load(root, state.fingerprint).status is MaterialStateLoadStatus.CORRUPT


def test_material_state_rejects_unsupported_stock():
    setup, tool, _, artifact = _fixture()
    unsupported = object()
    with pytest.raises(Exception):
        calculate_material_state(stock=unsupported, artifact=artifact, tool=tool,
            setup_fingerprint=ContentFingerprint.from_payload({"setup": 1}))


def test_material_state_resolution_is_typed_and_not_position_based():
    setup, tool, _, artifact = _fixture()
    setup_fp = ContentFingerprint.from_payload({"setup": 1})
    state = calculate_material_state(stock=setup.stock, artifact=artifact, tool=tool,
        setup_fingerprint=setup_fp).state
    resolved = resolve_material_state((("op-1", state),), setup_fingerprint=setup_fp)
    assert resolved.status is MaterialStateResolutionStatus.RESOLVED
    ambiguous = resolve_material_state((("op-1", state), ("op-2", state)), setup_fingerprint=setup_fp)
    assert ambiguous.status is MaterialStateResolutionStatus.AMBIGUOUS
    missing = resolve_material_state((("op-1", state),), setup_fingerprint=ContentFingerprint.from_payload({"other": 2}))
    assert missing.status is MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE


def test_r266_material_state_status_mapping_is_vietnamese_first_and_complete():
    expected = {
        MaterialStateResolutionStatus.RESOLVED: "Sẵn sàng",
        MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE:
            "Không tìm thấy nguồn phần dư phù hợp",
        MaterialStateResolutionStatus.AMBIGUOUS: "Có nhiều nguồn phần dư phù hợp",
        MaterialStateResolutionStatus.STALE: "Nguồn phần dư cần tính lại",
        MaterialStateResolutionStatus.CORRUPT: "Dữ liệu phần dư không hợp lệ",
        MaterialStateResolutionStatus.UNSUPPORTED:
            "Cấu hình phần dư chưa được hỗ trợ",
        MaterialStateResolutionStatus.NO_REST_MATERIAL:
            "Không còn phần dư cần gia công",
    }
    assert {status: material_state_status_vi(status) for status in expected} == expected


def test_rest_region_extractor_returns_closed_deterministic_topology():
    setup, tool, _, artifact = _fixture()
    setup_fp = ContentFingerprint.from_payload({"setup": 1})
    state = calculate_material_state(stock=setup.stock, artifact=artifact, tool=tool,
        setup_fingerprint=setup_fp).state
    regions = extract_rest_regions(state)
    assert regions
    assert all(region.exterior.closed for region in regions)
    assert all(region.fingerprint is not None for region in regions)
    assert tuple(region.fingerprint.digest for region in regions) == tuple(
        region.fingerprint.digest for region in extract_rest_regions(state)
    )


def test_material_state_dependency_round_trip_is_strict_and_persistable():
    setup, tool, _, artifact = _fixture()
    setup_fp = ContentFingerprint.from_payload({"setup": 1})
    state = calculate_material_state(stock=setup.stock, artifact=artifact, tool=tool,
        setup_fingerprint=setup_fp).state
    dependency = MaterialStateDependency(
        artifact.source_operation_id, artifact.source_operation_id,
        state.fingerprint, state.toolpath_fingerprint, setup_fp,
        state.stock_fingerprint, state.engine_version, state.precision.to_dict(),
    )
    assert MaterialStateDependency.from_dict(dependency.to_dict()) == dependency
    with pytest.raises(Exception):
        MaterialStateDependency.from_dict({"format": "HMS_CAM_MATERIAL_STATE_DEPENDENCY", "format_version": 99})


def test_v5_project_without_additive_table_remains_loadable(tmp_path):
    database_path = tmp_path / "project.db"
    ProjectDatabase().initialize(database_path)
    repository = CamSqliteRepository()
    import sqlite3
    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP TABLE IF EXISTS cam_material_state_dependencies")
    assert repository.load(database_path).material_state_dependencies == ()


def test_additive_provenance_table_is_created_idempotently_on_save(tmp_path):
    database_path = tmp_path / "project.db"
    ProjectDatabase().initialize(database_path)
    repository = CamSqliteRepository()
    import sqlite3
    with sqlite3.connect(database_path) as connection:
        repository.replace_all(connection, __import__("hms_cadcam.cam.persistence.models", fromlist=["CamProjectSnapshot"]).CamProjectSnapshot())
        repository.replace_all(connection, __import__("hms_cadcam.cam.persistence.models", fromlist=["CamProjectSnapshot"]).CamProjectSnapshot())
        assert connection.execute("SELECT COUNT(*) FROM cam_material_state_dependencies").fetchone()[0] == 0


def test_material_state_store_discovers_persisted_state_in_fresh_instance(tmp_path):
    setup, tool, _, artifact = _fixture()
    state = calculate_material_state(stock=setup.stock, artifact=artifact, tool=tool,
        setup_fingerprint=ContentFingerprint.from_payload({"setup": 1})).state
    MaterialStateStore().write(tmp_path, state)
    del state
    discovered = MaterialStateStore().discover(tmp_path)
    assert len(discovered) == 1
    assert discovered[0].status is MaterialStateLoadStatus.VALID


def test_r266_true_project_lifecycle_fresh_reopen_uses_persisted_s1_without_replay(
    tmp_path, monkeypatch,
):
    service = ProjectService.create_default(tmp_path / "config-cold")
    session = service.new_project(tmp_path, "R266 Rest Lifecycle")
    project_root = session.root_path
    service.execute_cam_command(lambda app: app.create_job("Job R266"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(
        lambda app: app.add_basic_resources(tool, holder, assembly, machine)
    )
    upstream_reference = _pocket_reference(
        setup.source_scope.primary_source_id, "upstream"
    )
    rest_reference = _pocket_reference(
        setup.source_scope.primary_source_id, "rest"
    )
    upstream_geometry_input = OperationGeometryInput(
        GeometryInputId.new(), GeometryInputRole.BOUNDARY, upstream_reference, True,
        GeometryReferenceKind.FACE, 0,
    )
    rest_geometry_input = OperationGeometryInput(
        GeometryInputId.new(), GeometryInputRole.BOUNDARY, rest_reference, True,
        GeometryReferenceKind.FACE, 0,
    )
    upstream_strategy = _pocket_strategy(upstream_reference)
    rest_strategy = _pocket_strategy(rest_reference)
    requirement = MachineRequirement(
        machine.machine_id, machine.revision, machine.content_fingerprint,
        machine.unit, (OperationCapability.MILLING,),
    )
    upstream_id = OperationId.new()
    upstream = Operation(
        upstream_id, CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (upstream_geometry_input,),
        upstream_strategy.to_operation_parameters(), requirement,
    )
    rest_id = OperationId.new()
    base_parameters = rest_strategy.to_operation_parameters()
    rest_parameters = OperationParameterSet(
        REST_POCKET_STRATEGY_KEY, base_parameters.strategy_version,
        base_parameters.values, base_parameters.schema_version,
    )
    rest = Operation(
        rest_id, CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly), (rest_geometry_input,),
        rest_parameters, requirement,
    )
    service.execute_cam_command(lambda app: app.update_tree(
        job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Pocket upstream", upstream)
                         .add_operation(tree.root_id, "Pocket phần dư", rest),
    ))
    resolved = {
        upstream_reference.reference_id: ResolvedPocketGeometry(
            GeometryResolutionStatus.RESOLVED,
            _pocket_region(upstream_reference, (10, 10, 50, 40)),
        ),
        rest_reference.reference_id: ResolvedPocketGeometry(
            GeometryResolutionStatus.RESOLVED,
            _pocket_region(rest_reference, (0, 0, 90, 80)),
        ),
    }
    resolver = lambda reference: resolved[reference.reference_id]
    upstream_result = service.compute_pocket(upstream_id, geometry_resolver=resolver)
    assert upstream_result.accepted and upstream_result.artifact is not None
    def cancel_after_s1_is_persisted() -> bool:
        return any(
            item.status is MaterialStateLoadStatus.VALID
            for item in MaterialStateStore().discover(project_root)
        )

    cancelled = service.compute_pocket(
        rest_id,
        geometry_resolver=resolver,
        cancellation=cancel_after_s1_is_persisted,
    )
    assert not cancelled.accepted and cancelled.artifact is None
    assert not any(
        metadata.operation_id == rest_id
        for metadata in service.cam_snapshot.artifacts
    )
    cancelled_states = tuple(
        item.state for item in MaterialStateStore().discover(project_root)
        if item.state is not None
    )
    assert len(cancelled_states) == 1
    assert cancelled_states[0].parent_fingerprint is None
    assert len(service.cam_snapshot.material_state_dependencies) == 1
    cancelled_timing = service._cam_application.calculation_timing(rest_id)
    assert cancelled_timing is not None
    assert tuple(
        (phase.phase, phase.cache_status) for phase in cancelled_timing.phases
    ) == (
        ("material_state_load", "CACHE_MISS"),
        ("rest_region_extraction", "CANCELLED"),
    )
    rest_result = service.compute_pocket(rest_id, geometry_resolver=resolver)
    assert rest_result.accepted and rest_result.artifact is not None
    # The first MATERIAL_STATE edge advances Setup revision.  A published Rest
    # artifact must bind that new revision so production Simulation capture does
    # not reject the just-computed result as stale.
    simulation_inputs = service.capture_simulation_inputs(rest_id)
    assert simulation_inputs.artifact.setup_revision == simulation_inputs.setup.revision
    retry_timing = service._cam_application.calculation_timing(rest_id)
    assert retry_timing is not None
    assert tuple(
        (phase.phase, phase.cache_status) for phase in retry_timing.phases
    ) == (
        ("material_state_load", "CACHE_HIT"),
        ("rest_region_extraction", "CACHE_MISS"),
        ("cut_generation", "CACHE_MISS"),
        ("final_assembly", "CACHE_MISS"),
        ("material_state_update", "CACHE_MISS"),
    )

    # R266 lead-only production reuse: the physical lead is rebuilt, while the
    # validated S1, RestRegions and core offset-loop geometry remain reusable.
    original_core = tuple(
        (
            event.provenance, event.start, event.end,
            event.feed_rate, event.motion_class,
        ) for event in rest_result.artifact.events
        if isinstance(event, LinearMove)
        and event.motion_class is MotionClass.CUTTING
        and not event.provenance.endswith(".lead_in")
    )
    current_rest = next(
        item for item in service.cam_snapshot.jobs[0].setups[0].operation_tree.operations
        if item.operation_id == rest_id
    )
    lead_values = dict(current_rest.parameters.values)
    lead_values["lead_in_length"] = 0.1
    changed_rest = replace(
        current_rest,
        parameters=OperationParameterSet(
            current_rest.strategy_key, current_rest.strategy_version,
            tuple(lead_values.items()), current_rest.parameters.schema_version,
        ),
        revision=current_rest.revision.next(),
        artifact_state=current_rest.artifact_state.mark_dirty(
            cam_service_module.DirtyReason.PARAMETERS_CHANGED
        ),
    )
    service.execute_cam_command(lambda app: app.update_tree(
        job_id, setup.setup_id,
        lambda tree: tree.replace_operation(changed_rest),
    ))
    lead_counts = {
        "parent_build": 0,
        "successor_build": 0,
        "regions": 0,
        "core_generate": 0,
        "lead_reassemble": 0,
    }
    original_calculate_lead = cam_service_module.calculate_material_state
    original_generate_lead = PocketGenerator.generate
    original_reassemble_lead = PocketGenerator.regenerate_lead_only
    original_regions_lead = extract_rest_regions

    def counted_calculate_lead(*args, **kwargs):
        key = "parent_build" if kwargs.get("parent") is None else "successor_build"
        lead_counts[key] += 1
        return original_calculate_lead(*args, **kwargs)

    def counted_generate_lead(generator, inputs):
        lead_counts["core_generate"] += 1
        return original_generate_lead(generator, inputs)

    def counted_reassemble_lead(generator, inputs):
        lead_counts["lead_reassemble"] += 1
        return original_reassemble_lead(generator, inputs)

    def counted_regions_lead(state):
        lead_counts["regions"] += 1
        return original_regions_lead(state)

    with monkeypatch.context() as lead_patch:
        lead_patch.setattr(
            cam_service_module, "calculate_material_state", counted_calculate_lead
        )
        lead_patch.setattr(PocketGenerator, "generate", counted_generate_lead)
        lead_patch.setattr(
            PocketGenerator, "regenerate_lead_only", counted_reassemble_lead
        )
        lead_patch.setattr(
            "hms_cadcam.cam.application.rest_pocket.extract_rest_regions",
            counted_regions_lead,
        )
        lead_result = service.compute_pocket(rest_id, geometry_resolver=resolver)
    assert lead_result.accepted and lead_result.artifact is not None
    assert lead_counts == {
        "parent_build": 0,
        "successor_build": 1,
        "regions": 0,
        "core_generate": 0,
        "lead_reassemble": 1,
    }
    assert tuple(
        (
            event.provenance, event.start, event.end,
            event.feed_rate, event.motion_class,
        ) for event in lead_result.artifact.events
        if isinstance(event, LinearMove)
        and event.motion_class is MotionClass.CUTTING
        and not event.provenance.endswith(".lead_in")
    ) == original_core
    assert any(
        isinstance(event, LinearMove)
        and event.motion_class is MotionClass.CUTTING
        and event.provenance.endswith(".lead_in")
        for event in lead_result.artifact.events
    )
    lead_timing = service._cam_application.calculation_timing(rest_id)
    assert lead_timing is not None
    assert tuple(
        (phase.phase, phase.cache_status) for phase in lead_timing.phases
    ) == (
        ("material_state_load", "CACHE_HIT"),
        ("rest_region_extraction", "CACHE_HIT"),
        ("cut_generation", "CACHE_HIT"),
        ("leads", "CACHE_MISS"),
        ("final_assembly", "CACHE_MISS"),
        ("material_state_update", "CACHE_MISS"),
    )
    rest_result = lead_result

    pre_dependencies = service.cam_snapshot.material_state_dependencies
    assert len(pre_dependencies) == 1
    dependency = pre_dependencies[0]
    assert dependency.consumer_operation_id == rest_id
    assert dependency.producer_operation_id == upstream_id
    setup_fp = material_state_setup_fingerprint(
        service.cam_snapshot.jobs[0].setups[0]
    )
    assert dependency.setup_fingerprint == setup_fp
    discovered = MaterialStateStore().discover(project_root)
    states = tuple(item.state for item in discovered if item.state is not None)
    assert len(states) == 3
    s1 = next(state for state in states if state.parent_fingerprint is None)
    successors = tuple(
        state for state in states if state.parent_fingerprint == s1.fingerprint
    )
    assert len(successors) == 2
    s2 = next(
        state for state in successors
        if state.toolpath_fingerprint
        == compute_material_removal_fingerprint(rest_result.artifact)
    )
    assert dependency.parent_state_fingerprint == s1.fingerprint
    assert s1.toolpath_fingerprint == compute_material_removal_fingerprint(upstream_result.artifact)
    assert s2.toolpath_fingerprint == compute_material_removal_fingerprint(rest_result.artifact)
    region_fingerprints = tuple(
        region.fingerprint for region in extract_rest_regions(s1)
    )
    expected = {
        "dependency": dependency.to_dict(),
        "s1": s1.fingerprint,
        "s2": s2.fingerprint,
        "state_fingerprints": {state.fingerprint for state in states},
        "regions": region_fingerprints,
        "rest_semantic": compute_toolpath_fingerprint(rest_result.artifact),
    }
    service.save()
    service.close_project()
    del service, session, upstream_result, rest_result, dependency, discovered, states, s1, s2
    gc.collect()

    reopened = ProjectService.create_default(tmp_path / "config-reopened")
    reopened.open_project(project_root)
    restored = reopened.cam_snapshot
    assert len(restored.material_state_dependencies) == 1
    assert restored.material_state_dependencies[0].to_dict() == expected["dependency"]
    restored_setup = restored.jobs[0].setups[0]
    restored_operations = restored_setup.operation_tree.operations
    assert tuple(operation.operation_id for operation in restored_operations) == (
        upstream_id, rest_id,
    )
    assert sum(
        edge.kind.value == "material_state"
        and edge.source_operation_id == upstream_id
        and edge.target_operation_id == rest_id
        for edge in restored_setup.operation_tree.dependency_graph.edges
    ) == 1
    resolution = reopened._cam_application.resolve_persisted_material_state(
        project_root, rest_id
    )
    assert resolution.status is MaterialStateResolutionStatus.RESOLVED
    assert resolution.state is not None
    assert resolution.state.fingerprint == expected["s1"]
    reopened_states = tuple(
        item.state for item in MaterialStateStore().discover(project_root)
        if item.state is not None
    )
    assert {state.fingerprint for state in reopened_states} == expected[
        "state_fingerprints"
    ]
    reopened_s1 = next(
        state for state in reopened_states if state.fingerprint == expected["s1"]
    )
    assert tuple(region.fingerprint for region in extract_rest_regions(reopened_s1)) == expected["regions"]

    counts = {
        "parent_build": 0,
        "successor_build": 0,
        "upstream_generate": 0,
        "rest_generate": 0,
    }
    original_calculate = cam_service_module.calculate_material_state
    original_generate = PocketGenerator.generate

    def counted_calculate(*args, **kwargs):
        if kwargs.get("parent") is None:
            counts["parent_build"] += 1
        else:
            counts["successor_build"] += 1
        return original_calculate(*args, **kwargs)

    def counted_generate(generator, inputs):
        operation_id = inputs.operation.operation_id
        if operation_id == upstream_id:
            counts["upstream_generate"] += 1
        if operation_id == rest_id:
            counts["rest_generate"] += 1
        return original_generate(generator, inputs)

    monkeypatch.setattr(cam_service_module, "calculate_material_state", counted_calculate)
    monkeypatch.setattr(PocketGenerator, "generate", counted_generate)
    repeated = reopened.compute_pocket(rest_id, geometry_resolver=resolver)
    assert repeated.accepted and repeated.artifact is not None
    assert counts == {
        "parent_build": 0,
        "successor_build": 0,
        "upstream_generate": 0,
        "rest_generate": 0,
    }
    assert compute_toolpath_fingerprint(repeated.artifact) == expected["rest_semantic"]
    final_timing = reopened._cam_application.calculation_timing(rest_id)
    assert final_timing is not None
    assert tuple(
        (phase.phase, phase.cache_status) for phase in final_timing.phases
    ) == (
        ("material_state_load", "CACHE_HIT"),
        ("rest_region_extraction", "CACHE_HIT"),
        ("cut_generation", "CACHE_HIT"),
        ("final_assembly", "CACHE_HIT"),
        ("material_state_update", "CACHE_HIT"),
    )
    repeated_states = tuple(
        item.state for item in MaterialStateStore().discover(project_root)
        if item.state is not None
    )
    assert {state.fingerprint for state in repeated_states} == expected[
        "state_fingerprints"
    ]
    restored_upstream = next(
        item for item in reopened.cam_snapshot.jobs[0].setups[0].operation_tree.operations
        if item.operation_id == upstream_id
    )
    feed_values = dict(restored_upstream.parameters.values)
    feed_values["cutting_feed_rate"] = 425.0
    feed_changed_upstream = replace(
        restored_upstream,
        parameters=OperationParameterSet(
            restored_upstream.strategy_key,
            restored_upstream.strategy_version,
            tuple(feed_values.items()),
            restored_upstream.parameters.schema_version,
        ),
        revision=restored_upstream.revision.next(),
        artifact_state=restored_upstream.artifact_state.mark_dirty(
            cam_service_module.DirtyReason.PARAMETERS_CHANGED
        ),
    )
    reopened.execute_cam_command(lambda app: app.update_tree(
        reopened.cam_snapshot.active_job_id,
        restored_setup.setup_id,
        lambda tree: tree.replace_operation(feed_changed_upstream),
    ))
    feed_reused = reopened.compute_pocket(rest_id, geometry_resolver=resolver)
    assert feed_reused.accepted and feed_reused.artifact is not None
    assert counts == {
        "parent_build": 0,
        "successor_build": 0,
        "upstream_generate": 0,
        "rest_generate": 0,
    }
    assert compute_toolpath_fingerprint(feed_reused.artifact) == expected["rest_semantic"]
    assert reopened.resolve_persisted_material_state(
        rest_id
    ).status is MaterialStateResolutionStatus.RESOLVED
    assert len(reopened.cam_snapshot.material_state_dependencies) == 1
    assert sum(
        edge.kind.value == "material_state"
        for edge in reopened.cam_snapshot.jobs[0].setups[0].operation_tree.dependency_graph.edges
    ) == 1
    reopened.close_project(discard_changes=True)

    mismatch_root = tmp_path / "mismatch-copies"
    mismatch_root.mkdir()
    mismatch_fingerprint = ContentFingerprint.from_payload({"mismatch": "R266"})
    cases = {
        "producer_identity": (
            lambda payload: payload.update({"producer_operation_id": str(rest_id)}),
            MaterialStateResolutionStatus.STALE,
        ),
        "parent_fingerprint": (
            lambda payload: payload.update({
                "parent_state_fingerprint": mismatch_fingerprint.to_dict()
            }),
            MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE,
        ),
        "producer_semantic": (
            lambda payload: payload.update({
                "producer_toolpath_fingerprint": mismatch_fingerprint.to_dict()
            }),
            MaterialStateResolutionStatus.STALE,
        ),
        "setup": (
            lambda payload: payload.update({
                "setup_fingerprint": mismatch_fingerprint.to_dict()
            }),
            MaterialStateResolutionStatus.STALE,
        ),
        "stock": (
            lambda payload: payload.update({
                "stock_fingerprint": mismatch_fingerprint.to_dict()
            }),
            MaterialStateResolutionStatus.STALE,
        ),
        "precision": (
            lambda payload: payload["precision"].update({"grid_target": 96}),
            MaterialStateResolutionStatus.STALE,
        ),
        "engine": (
            lambda payload: payload.update({"engine_version": "unsupported-r266"}),
            MaterialStateResolutionStatus.UNSUPPORTED,
        ),
    }
    for index, (name, (mutate, expected_status)) in enumerate(cases.items()):
        copied = mismatch_root / f"{index:02d}-{name}.HMS"
        shutil.copytree(project_root, copied)
        with sqlite3.connect(copied / "project.db") as connection:
            row = connection.execute(
                "SELECT payload_json FROM cam_material_state_dependencies"
            ).fetchone()
            payload = json.loads(row[0])
            mutate(payload)
            connection.execute(
                "UPDATE cam_material_state_dependencies SET payload_json = ?",
                (json.dumps(payload, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")),),
            )
        opener = ProjectService.create_default(tmp_path / f"config-{name}")
        opener.open_project(copied)
        outcome = opener._cam_application.resolve_persisted_material_state(
            copied, rest_id
        )
        assert outcome.status is expected_status
        assert outcome.status is not MaterialStateResolutionStatus.RESOLVED
        opener.close_project(discard_changes=True)

    missing_row = mismatch_root / "07-missing-row.HMS"
    shutil.copytree(project_root, missing_row)
    with sqlite3.connect(missing_row / "project.db") as connection:
        connection.execute("DELETE FROM cam_material_state_dependencies")
    missing_opener = ProjectService.create_default(tmp_path / "config-missing-row")
    missing_opener.open_project(missing_row)
    assert missing_opener._cam_application.resolve_persisted_material_state(
        missing_row, rest_id
    ).status is MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE
    missing_opener.close_project(discard_changes=True)

    missing_state = mismatch_root / "08-missing-state.HMS"
    shutil.copytree(project_root, missing_state)
    (missing_state / ".hms" / "cam" / "material_state"
     / f"{expected['s1'].digest}.state.json").unlink()
    missing_state_opener = ProjectService.create_default(
        tmp_path / "config-missing-state"
    )
    missing_state_opener.open_project(missing_state)
    assert missing_state_opener._cam_application.resolve_persisted_material_state(
        missing_state, rest_id
    ).status is MaterialStateResolutionStatus.NO_COMPATIBLE_MATERIAL_STATE
    missing_state_opener.close_project(discard_changes=True)

    corrupt_state = mismatch_root / "09-corrupt-state.HMS"
    shutil.copytree(project_root, corrupt_state)
    (corrupt_state / ".hms" / "cam" / "material_state"
     / f"{expected['s1'].digest}.state.json").write_text(
         "{}", encoding="utf-8"
     )
    corrupt_state_opener = ProjectService.create_default(
        tmp_path / "config-corrupt-state"
    )
    corrupt_state_opener.open_project(corrupt_state)
    assert corrupt_state_opener._cam_application.resolve_persisted_material_state(
        corrupt_state, rest_id
    ).status is MaterialStateResolutionStatus.CORRUPT
    corrupt_state_opener.close_project(discard_changes=True)

    malformed = mismatch_root / "10-malformed-row.HMS"
    shutil.copytree(project_root, malformed)
    with sqlite3.connect(malformed / "project.db") as connection:
        connection.execute(
            "UPDATE cam_material_state_dependencies SET payload_json = '{}'"
        )
    malformed_opener = ProjectService.create_default(tmp_path / "config-malformed")
    with pytest.raises(Exception):
        malformed_opener.open_project(malformed)


def test_material_state_setup_fingerprint_ignores_operation_tree_but_binds_wcs():
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    original = material_state_setup_fingerprint(setup)
    changed_tree = setup.with_operation_tree(
        replace(setup.operation_tree, revision=setup.operation_tree.revision.next())
    )
    assert material_state_setup_fingerprint(changed_tree) == original
    moved_wcs = replace(
        setup.wcs,
        origin=Point3(1, 0, 0, LengthUnit.MM),
    )
    assert material_state_setup_fingerprint(setup.with_wcs(moved_wcs)) != original


def _synthetic_state(mask: tuple[str, ...]) -> MaterialState:
    height, width = len(mask), len(mask[0])
    values = tuple(1.0 if cell == "#" else 0.0 for row in mask for cell in row)
    fingerprint = ContentFingerprint.from_payload({"mask": mask})
    provenance = ContentFingerprint.from_payload({"synthetic": "R266"})
    return MaterialState(
        1, fingerprint, None, provenance, provenance, provenance,
        MaterialStatePrecisionPolicy(grid_target=max(width, height)),
        "heightfield-3axis-v1", width, height, 1.0, 1.0, values,
        float(width * height), float(sum(values)), LengthUnit.MM,
    )


def _signed_area(loop: ContourLoop) -> float:
    return 0.5 * sum(
        segment.start.x * segment.end.y - segment.end.x * segment.start.y
        for segment in loop.segments
    )


def _strict_intersection(first: ContourSegment, second: ContourSegment) -> bool:
    def cross(a: Point3, b: Point3, c: Point3) -> float:
        return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
    return (
        cross(first.start, first.end, second.start)
        * cross(first.start, first.end, second.end) < 0
        and cross(second.start, second.end, first.start)
        * cross(second.start, second.end, first.end) < 0
    )


def _point_inside(point: Point3, loop: ContourLoop) -> bool:
    inside = False
    for segment in loop.segments:
        first, second = segment.start, segment.end
        if ((first.y > point.y) != (second.y > point.y)) and point.x < (
            (second.x - first.x) * (point.y - first.y)
            / (second.y - first.y) + first.x
        ):
            inside = not inside
    return inside


def test_r266_rest_region_topology_is_independently_validated_and_deterministic():
    state = _synthetic_state((
        "..............",
        ".#####...###..",
        ".#####...###..",
        ".##.##...###..",
        ".#####........",
        ".#####........",
        "..............",
    ))
    regions = extract_rest_regions(state)
    replay = extract_rest_regions(state)
    assert len(regions) == 2
    assert tuple(item.fingerprint for item in regions) == tuple(
        item.fingerprint for item in replay
    )
    assert sum(len(item.holes) for item in regions) == 1
    for region in regions:
        assert region.exterior.closed
        assert len(region.exterior.segments) >= 4
        assert _signed_area(region.exterior) > 0
        loops = (region.exterior, *region.holes)
        for loop in loops:
            assert all(segment.start != segment.end for segment in loop.segments)
            for index, segment in enumerate(loop.segments):
                for other_index, other in enumerate(loop.segments):
                    if other_index <= index or other_index in {
                        (index - 1) % len(loop.segments),
                        (index + 1) % len(loop.segments),
                    }:
                        continue
                    assert not _strict_intersection(segment, other)
        for hole in region.holes:
            assert hole.closed and len(hole.segments) >= 4
            assert _signed_area(hole) < 0
            assert _point_inside(hole.segments[0].start, region.exterior)
            assert not any(
                _strict_intersection(outer, inner)
                for outer in region.exterior.segments
                for inner in hole.segments
            )


def test_r266_end_mill_and_ball_end_produce_materially_different_state():
    setup, end_mill, _, artifact = _fixture()
    unit = LengthUnit.MM
    ball = replace(
        end_mill,
        name="Ball end R266",
        family=ToolFamily.BALL_END_MILL,
        cutting_geometry=BallEndGeometry(Length(4, unit), Length(12, unit)),
    )
    setup_fp = material_state_setup_fingerprint(setup)
    end_state = calculate_material_state(
        stock=setup.stock, artifact=artifact, tool=end_mill,
        setup_fingerprint=setup_fp,
    ).state
    ball_state = calculate_material_state(
        stock=setup.stock, artifact=artifact, tool=ball,
        setup_fingerprint=setup_fp,
    ).state
    assert end_state.fingerprint != ball_state.fingerprint
    assert end_state.top_heights != ball_state.top_heights
    assert end_state.remaining_volume != pytest.approx(ball_state.remaining_volume)
    assert 0 <= end_state.remaining_volume <= end_state.initial_volume
    assert 0 <= ball_state.remaining_volume <= ball_state.initial_volume


def test_r266_feed_only_change_preserves_material_removal_identity_but_geometry_does_not():
    setup, tool, _, artifact = _fixture()
    setup_fp = material_state_setup_fingerprint(setup)
    cutting = next(event for event in artifact.events if hasattr(event, "feed_rate"))
    feed_changed_event = replace(
        cutting, feed_rate=FeedRate(250, FeedUnit.MM_PER_MINUTE)
    )
    feed_events = tuple(
        feed_changed_event if event is cutting else event for event in artifact.events
    )
    feed_changed = replace(
        artifact,
        events=feed_events,
        statistics=type(artifact.statistics).calculate(feed_events, artifact.unit),
        artifact_fingerprint=None,
    )
    assert compute_toolpath_fingerprint(feed_changed) != compute_toolpath_fingerprint(artifact)
    assert compute_material_removal_fingerprint(feed_changed) == (
        compute_material_removal_fingerprint(artifact)
    )
    original_state = calculate_material_state(
        stock=setup.stock, artifact=artifact, tool=tool,
        setup_fingerprint=setup_fp,
    ).state
    feed_state = calculate_material_state(
        stock=setup.stock, artifact=feed_changed, tool=tool,
        setup_fingerprint=setup_fp,
    ).state
    assert feed_state.fingerprint == original_state.fingerprint
    assert feed_state.top_heights == original_state.top_heights

    geometry_event = replace(
        cutting,
        end=replace(cutting.end, position=Point3(18, 3, 2, LengthUnit.MM)),
    )
    geometry_events = tuple(
        geometry_event if event is cutting else event for event in artifact.events
    )
    geometry_changed = replace(
        artifact,
        events=geometry_events,
        bounds=type(artifact.bounds).from_points((
            artifact.initial_pose.position,
            geometry_event.start.position,
            geometry_event.end.position,
        )),
        statistics=type(artifact.statistics).calculate(geometry_events, artifact.unit),
        artifact_fingerprint=None,
    )
    assert compute_material_removal_fingerprint(geometry_changed) != (
        compute_material_removal_fingerprint(artifact)
    )
    geometry_state = calculate_material_state(
        stock=setup.stock, artifact=geometry_changed, tool=tool,
        setup_fingerprint=setup_fp,
    ).state
    assert geometry_state.fingerprint != original_state.fingerprint
    assert geometry_state.top_heights != original_state.top_heights


def test_r266_no_rest_is_valid_complete_zero_motion_production_result(tmp_path):
    service = ProjectService.create_default(tmp_path / "config-no-rest")
    session = service.new_project(tmp_path, "R266 No Rest")
    project_root = session.root_path
    service.execute_cam_command(lambda app: app.create_job("Job NO_REST"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(
        lambda app: app.add_basic_resources(tool, holder, assembly, machine)
    )
    upstream_reference = _pocket_reference(
        setup.source_scope.primary_source_id, "no-rest-upstream"
    )
    rest_reference = _pocket_reference(
        setup.source_scope.primary_source_id, "no-rest-consumer"
    )
    requirement = MachineRequirement(
        machine.machine_id, machine.revision, machine.content_fingerprint,
        machine.unit, (OperationCapability.MILLING,),
    )

    def operation_for(reference: GeometryReference, *, rest: bool) -> Operation:
        strategy = _pocket_strategy(reference)
        parameters = strategy.to_operation_parameters()
        if rest:
            parameters = OperationParameterSet(
                REST_POCKET_STRATEGY_KEY, parameters.strategy_version,
                parameters.values, parameters.schema_version,
            )
        return Operation(
            OperationId.new(), CamNodeId.new(), OperationFamily.MILLING,
            setup.setup_id, ToolAssemblyReference.from_assembly(assembly),
            (OperationGeometryInput(
                GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference,
                True, GeometryReferenceKind.FACE, 0,
            ),),
            parameters, requirement,
        )

    upstream = operation_for(upstream_reference, rest=False)
    rest = operation_for(rest_reference, rest=True)
    service.execute_cam_command(lambda app: app.update_tree(
        job_id, setup.setup_id,
        lambda tree: tree.add_operation(tree.root_id, "Pocket full clear", upstream)
                         .add_operation(tree.root_id, "Pocket NO_REST", rest),
    ))
    resolved = {
        upstream_reference.reference_id: ResolvedPocketGeometry(
            GeometryResolutionStatus.RESOLVED,
            _pocket_region(upstream_reference, (-10, -10, 110, 110)),
        ),
        rest_reference.reference_id: ResolvedPocketGeometry(
            GeometryResolutionStatus.RESOLVED,
            _pocket_region(rest_reference, (0, 0, 100, 100)),
        ),
    }
    resolver = lambda reference: resolved[reference.reference_id]
    upstream_result = service.compute_pocket(
        upstream.operation_id, geometry_resolver=resolver
    )
    assert upstream_result.accepted and upstream_result.artifact is not None
    s1 = next(
        item.state for item in MaterialStateStore().discover(project_root)
        if item.state is not None
    ) if MaterialStateStore().discover(project_root) else None
    assert s1 is None  # S1 is staged only when the Rest consumer calculates.

    result = service.compute_pocket(rest.operation_id, geometry_resolver=resolver)
    assert result.accepted and result.no_rest_material
    assert result.artifact is not None
    assert result.operation.artifact_state.status is ArtifactStatus.VALID
    assert result.artifact.events == ()
    assert result.artifact.statistics.total_cutting_length == 0.0
    assert result.artifact.completion_status.value == "complete"
    states = tuple(
        item.state for item in MaterialStateStore().discover(project_root)
        if item.state is not None
    )
    assert len(states) == 2
    parent = next(state for state in states if state.parent_fingerprint is None)
    successor = next(state for state in states if state.parent_fingerprint is not None)
    assert not parent.has_rest_material
    assert successor.top_heights == parent.top_heights
    assert successor.remaining_volume == pytest.approx(parent.remaining_volume)
    assert 0 <= successor.remaining_volume <= parent.remaining_volume <= parent.initial_volume
    resolution = service.resolve_persisted_material_state(rest.operation_id)
    assert resolution.status is MaterialStateResolutionStatus.NO_REST_MATERIAL
    service.save()
    service.close_project()

    reopened = ProjectService.create_default(tmp_path / "config-no-rest-reopened")
    reopened.open_project(project_root)
    repeated = reopened.compute_pocket(rest.operation_id, geometry_resolver=resolver)
    assert repeated.accepted and repeated.no_rest_material
    assert repeated.artifact is not None and repeated.artifact.events == ()
    assert reopened.resolve_persisted_material_state(
        rest.operation_id
    ).status is MaterialStateResolutionStatus.NO_REST_MATERIAL
    reopened.close_project(discard_changes=True)


def test_r266_production_rest_multi_region_linking_is_retracted_and_deterministic():
    state = _synthetic_state((
        "........................................",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "..############......############........",
        "........................................",
    ))
    unit = LengthUnit.MM
    setup = _default_setup(uuid4(), unit, 1)
    tool, _holder, assembly, machine = basic_mill_resources(unit)
    tool = replace(
        tool,
        name="Dao Rest D2",
        cutting_geometry=CylindricalGeometry(Length(2, unit), Length(20, unit)),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Rest D2 assembly", tool,
        Length(20, unit), Length(60, unit),
    )
    reference = _pocket_reference(setup.source_scope.primary_source_id, "multi")
    strategy = replace(
        _pocket_strategy(reference),
        stepover=Length(1, unit),
        stepdown=Length(1, unit),
    )
    parameters = strategy.to_operation_parameters()
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.MILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly), (),
        OperationParameterSet(
            REST_POCKET_STRATEGY_KEY, parameters.strategy_version,
            parameters.values, parameters.schema_version,
        ),
    )
    region = _pocket_region(reference, (0, 0, 40, 14))
    base = PocketInputs(
        operation, setup, strategy, region, (), (-1.0,), assembly, tool,
        machine, 2.0, DependencyFingerprint.from_payload({"multi": "R266"}),
    )
    regions = extract_rest_regions(state)
    generator = RestPocketGenerator()
    loops = generator._region_offset_loops(base, regions, state)
    assert len(regions) == 2 and loops
    inputs = RestPocketInputs(replace(base, offset_loops=loops), state, regions)
    computing, _token = generator.begin(inputs)
    first = generator.generate(computing)
    computing_again, _token_again = generator.begin(inputs)
    second = generator.generate(computing_again)
    assert compute_toolpath_fingerprint(first) == compute_toolpath_fingerprint(second)
    cutting = tuple(
        event for event in first.events
        if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING
    )
    assert cutting
    assert all(
        not (event.start.position.x < 20 < event.end.position.x)
        and not (event.end.position.x < 20 < event.start.position.x)
        for event in cutting
    )
    cutting_indices = tuple(first.events.index(event) for event in cutting)
    for previous, current in zip(cutting_indices, cutting_indices[1:]):
        if first.events[previous].end != first.events[current].start:
            between = first.events[previous + 1:current]
            assert any(
                isinstance(event, LinearMove)
                and event.motion_class is MotionClass.RETRACT
                for event in between
            )


def test_r266_rest_lead_in_is_real_cutting_motion_and_unsafe_length_fails_closed():
    state = _synthetic_state((
        "....................",
        ".##################.",
        ".##################.",
        ".##################.",
        ".##################.",
        ".##################.",
        ".##################.",
        ".##################.",
        ".##################.",
        ".##################.",
        "....................",
    ))
    unit = LengthUnit.MM
    setup = _default_setup(uuid4(), unit, 1)
    tool, _holder, assembly, machine = basic_mill_resources(unit)
    tool = replace(
        tool,
        cutting_geometry=CylindricalGeometry(Length(2, unit), Length(20, unit)),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Lead Rest assembly", tool,
        Length(20, unit), Length(60, unit),
    )
    reference = _pocket_reference(setup.source_scope.primary_source_id, "lead-real")
    strategy = replace(
        _pocket_strategy(reference),
        stepover=Length(1, unit),
        stepdown=Length(1, unit),
        lead_in_length=Length(0.1, unit),
    )
    parameters = strategy.to_operation_parameters()
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.MILLING,
        setup.setup_id, ToolAssemblyReference.from_assembly(assembly), (),
        OperationParameterSet(
            REST_POCKET_STRATEGY_KEY, parameters.strategy_version,
            parameters.values, parameters.schema_version,
        ),
    )
    region = _pocket_region(reference, (0, 0, 20, 11))
    base = PocketInputs(
        operation, setup, strategy, region, (), (-1.0,), assembly, tool,
        machine, 2.0, DependencyFingerprint.from_payload({"lead": "R266"}),
    )
    generator = RestPocketGenerator()
    regions = extract_rest_regions(state)
    loops = generator._region_offset_loops(base, regions, state)
    assert loops
    inputs = RestPocketInputs(replace(base, offset_loops=loops), state, regions)
    computing, _token = generator.begin(inputs)
    artifact = generator.generate(computing)
    leads = tuple(
        event for event in artifact.events
        if isinstance(event, LinearMove)
        and event.motion_class is MotionClass.CUTTING
        and event.provenance.endswith(".lead_in")
    )
    assert len(leads) == len(loops)
    for event, loop in zip(leads, loops):
        assert event.end.position == replace(loop.segments[0].start, z=-1.0)
        assert event.start.position != event.end.position
        assert event.length == pytest.approx(0.1)

    zero_inputs = replace(
        inputs,
        pocket=replace(inputs.pocket, strategy=replace(strategy, lead_in_length=Length(0, unit))),
    )
    zero_computing, _zero_token = generator.begin(zero_inputs)
    zero_artifact = generator.generate(zero_computing)
    assert not any(
        isinstance(event, LinearMove) and event.provenance.endswith(".lead_in")
        for event in zero_artifact.events
    )

    unsafe_inputs = replace(
        inputs,
        pocket=replace(inputs.pocket, strategy=replace(strategy, lead_in_length=Length(100, unit))),
    )
    unsafe_computing, _unsafe_token = generator.begin(unsafe_inputs)
    with pytest.raises(Exception):
        generator.generate(unsafe_computing)


def test_r266_material_state_dag_invalidates_only_the_real_upstream_chain():
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, _holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    requirement = MachineRequirement(
        machine.machine_id, machine.revision, machine.content_fingerprint,
        machine.unit, (OperationCapability.MILLING,),
    )

    def pocket(label: str, *, rest: bool = False) -> Operation:
        reference = _pocket_reference(setup.source_scope.primary_source_id, label)
        strategy = _pocket_strategy(reference)
        parameters = strategy.to_operation_parameters()
        if rest:
            parameters = OperationParameterSet(
                REST_POCKET_STRATEGY_KEY, parameters.strategy_version,
                parameters.values, parameters.schema_version,
            )
        operation = Operation(
            OperationId.new(), CamNodeId.new(), OperationFamily.MILLING,
            setup.setup_id, ToolAssemblyReference.from_assembly(assembly),
            (OperationGeometryInput(
                GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference,
                True, GeometryReferenceKind.FACE, 0,
            ),),
            parameters, requirement,
        )
        computing, token = operation.artifact_state.begin(
            DependencyFingerprint.from_payload({"valid": label})
        )
        published, accepted = computing.publish(
            token,
            DependencyFingerprint.from_payload({"valid": label}),
            ContentFingerprint.from_payload({"artifact": label}),
        )
        assert accepted
        return replace(operation, artifact_state=published)

    upstream = pocket("op01")
    unrelated = pocket("op02")
    rest = pocket("op03", rest=True)
    tree = setup.operation_tree.add_operation(
        setup.operation_tree.root_id, "OP01", upstream
    ).add_operation(setup.operation_tree.root_id, "OP02", unrelated
    ).add_operation(setup.operation_tree.root_id, "OP03", rest)
    tree = tree.with_dependency_added(
        cam_service_module.DependencyEdge.material_state(
            upstream.operation_id, rest.operation_id
        )
    )
    app = cam_service_module.CamApplicationService()
    app.create_job("DAG R266")
    job_id = app.snapshot.active_job_id
    app.add_setup(job_id, setup.with_operation_tree(tree))

    changed_upstream = replace(
        upstream,
        geometry_inputs=(replace(
            upstream.geometry_inputs[0],
            reference=_pocket_reference(
                setup.source_scope.primary_source_id, "op01-changed"
            ),
        ),),
        revision=upstream.revision.next(),
        artifact_state=upstream.artifact_state.mark_dirty(
            cam_service_module.DirtyReason.GEOMETRY_CHANGED
        ),
    )
    app.update_tree(
        job_id, setup.setup_id,
        lambda current: current.replace_operation(changed_upstream),
    )
    operations = {
        item.operation_id: item for item in app.snapshot.jobs[0].setups[0].operation_tree.operations
    }
    assert operations[upstream.operation_id].artifact_state.status is ArtifactStatus.DIRTY
    assert operations[rest.operation_id].artifact_state.status is ArtifactStatus.DIRTY
    assert operations[unrelated.operation_id].artifact_state.status is ArtifactStatus.VALID

    # A separate independent operation edit cannot invalidate the material-state chain.
    fresh_app = cam_service_module.CamApplicationService()
    fresh_app.create_job("DAG unrelated R266")
    fresh_job_id = fresh_app.snapshot.active_job_id
    fresh_app.add_setup(fresh_job_id, setup.with_operation_tree(tree))
    changed_values = dict(unrelated.parameters.values)
    changed_values["cutting_feed_rate"] = 450.0
    changed_unrelated = replace(
        unrelated,
        parameters=OperationParameterSet(
            unrelated.strategy_key, unrelated.strategy_version,
            tuple(changed_values.items()), unrelated.parameters.schema_version,
        ),
        revision=unrelated.revision.next(),
        artifact_state=unrelated.artifact_state.mark_dirty(
            cam_service_module.DirtyReason.PARAMETERS_CHANGED
        ),
    )
    fresh_app.update_tree(
        fresh_job_id, setup.setup_id,
        lambda current: current.replace_operation(changed_unrelated),
    )
    operations = {
        item.operation_id: item
        for item in fresh_app.snapshot.jobs[0].setups[0].operation_tree.operations
    }
    assert operations[unrelated.operation_id].artifact_state.status is ArtifactStatus.DIRTY
    assert operations[upstream.operation_id].artifact_state.status is ArtifactStatus.VALID
    assert operations[rest.operation_id].artifact_state.status is ArtifactStatus.VALID


def test_r266_feed_only_upstream_edit_preserves_rest_chain_authority():
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    tool, _holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    requirement = MachineRequirement(
        machine.machine_id, machine.revision, machine.content_fingerprint,
        machine.unit, (OperationCapability.MILLING,),
    )
    upstream_reference = _pocket_reference(
        setup.source_scope.primary_source_id, "feed-upstream"
    )
    rest_reference = _pocket_reference(
        setup.source_scope.primary_source_id, "feed-rest"
    )

    def operation_for(reference: GeometryReference, *, rest: bool) -> Operation:
        parameters = _pocket_strategy(reference).to_operation_parameters()
        if rest:
            parameters = OperationParameterSet(
                REST_POCKET_STRATEGY_KEY, parameters.strategy_version,
                parameters.values, parameters.schema_version,
            )
        operation = Operation(
            OperationId.new(), CamNodeId.new(), OperationFamily.MILLING,
            setup.setup_id, ToolAssemblyReference.from_assembly(assembly),
            (OperationGeometryInput(
                GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference,
                True, GeometryReferenceKind.FACE, 0,
            ),),
            parameters, requirement,
        )
        computing, token = operation.artifact_state.begin(
            DependencyFingerprint.from_payload({"feed": str(operation.operation_id)})
        )
        valid, accepted = computing.publish(
            token,
            DependencyFingerprint.from_payload({"feed": str(operation.operation_id)}),
            ContentFingerprint.from_payload({"artifact": str(operation.operation_id)}),
        )
        assert accepted
        return replace(operation, artifact_state=valid)

    upstream = operation_for(upstream_reference, rest=False)
    rest = operation_for(rest_reference, rest=True)
    tree = setup.operation_tree.add_operation(
        setup.operation_tree.root_id, "OP01", upstream
    ).add_operation(setup.operation_tree.root_id, "OP03", rest)
    tree = tree.with_dependency_added(
        cam_service_module.DependencyEdge.material_state(
            upstream.operation_id, rest.operation_id
        )
    )
    app = cam_service_module.CamApplicationService()
    app.create_job("Feed-only R266")
    job_id = app.snapshot.active_job_id
    app.add_setup(job_id, setup.with_operation_tree(tree))
    values = dict(upstream.parameters.values)
    values["cutting_feed_rate"] = 425.0
    feed_changed = replace(
        upstream,
        parameters=OperationParameterSet(
            upstream.strategy_key, upstream.strategy_version,
            tuple(values.items()), upstream.parameters.schema_version,
        ),
        revision=upstream.revision.next(),
        artifact_state=upstream.artifact_state.mark_dirty(
            cam_service_module.DirtyReason.PARAMETERS_CHANGED
        ),
    )
    app.update_tree(
        job_id, setup.setup_id,
        lambda current: current.replace_operation(feed_changed),
    )
    operations = {
        item.operation_id: item for item in app.snapshot.jobs[0].setups[0].operation_tree.operations
    }
    assert operations[upstream.operation_id].artifact_state.status is ArtifactStatus.DIRTY
    assert operations[rest.operation_id].artifact_state.status is ArtifactStatus.VALID


def test_r266_analytic_area_and_volume_reports_are_independently_checkable():
    masks = {
        "A_rectangle": (
            "..........",
            ".######...",
            ".######...",
            ".######...",
            "..........",
        ),
        "C_corner": (
            "........",
            ".##.....",
            ".##.....",
            "........",
        ),
        "D_island": (
            ".........",
            ".#######.",
            ".#######.",
            ".##...##.",
            ".##...##.",
            ".#######.",
            ".#######.",
            ".........",
        ),
    }
    for name, mask in masks.items():
        state = _synthetic_state(mask)
        expected_cells = sum(row.count("#") for row in mask)
        expected_area = float(expected_cells)
        represented_area = sum(
            state.cell_size_x * state.cell_size_y
            for value in state.top_heights
            if value > state.precision.residual_threshold
        )
        absolute_error = abs(represented_area - expected_area)
        relative_error = absolute_error / expected_area
        allowed_error = (
            state.precision.tolerance
            + state.precision.residual_threshold
            * state.cell_size_x * state.cell_size_y
        )
        assert represented_area == pytest.approx(expected_area), name
        assert absolute_error <= allowed_error, name
        assert relative_error <= allowed_error / expected_area, name
        assert state.initial_volume >= state.remaining_volume >= 0.0, name

    setup, tool, assembly, upstream = _fixture()
    setup_fp = material_state_setup_fingerprint(setup)
    s1 = calculate_material_state(
        stock=setup.stock, artifact=upstream, tool=tool,
        setup_fingerprint=setup_fp,
    ).state
    fingerprint = DependencyFingerprint.from_payload({"analytic": "s2"})
    operation = Operation(
        OperationId.new(), CamNodeId.new(), OperationFamily.MILLING,
        setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        (),
        OperationParameterSet("test", 1),
    )
    computing, token = operation.artifact_state.begin(fingerprint)
    builder = ToolpathBuilder(
        artifact_id=ToolpathArtifactId.new(), operation_id=operation.operation_id,
        operation_revision=operation.revision, computation_token=token,
        input_fingerprint=fingerprint, unit=LengthUnit.MM,
        setup_id=setup.setup_id, setup_revision=setup.revision,
        wcs_fingerprint=ContentFingerprint.from_payload(setup.wcs.to_dict()),
        tool_assembly_id=upstream.tool_assembly_id,
        tool_assembly_fingerprint=upstream.tool_assembly_fingerprint,
    )
    builder.set_initial_pose(Pose(Point3(18, 2, 12, LengthUnit.MM), Vector3(0, 0, 1)))
    builder.linear_to(
        Pose(Point3(18, 18, 2, LengthUnit.MM), Vector3(0, 0, 1)),
        FeedRate(100, FeedUnit.MM_PER_MINUTE),
    )
    s2 = calculate_material_state(
        stock=setup.stock, artifact=builder.finalize(), tool=tool,
        parent=s1, setup_fingerprint=setup_fp,
    ).state
    v0, v1, v2 = s1.initial_volume, s1.remaining_volume, s2.remaining_volume
    assert v0 >= v1 >= v2 >= 0.0


def test_r266_view_only_controls_never_call_material_or_rest_generation(monkeypatch):
    from hms_cadcam.simulation.playback import PlaybackController, Timeline
    from hms_cadcam.ui.i18n import UiLanguage, translation_service
    from hms_cadcam.viewer.models import ObjectColor, ViewDirection
    from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend

    counts = {"material": 0, "regions": 0, "rest": 0}

    def forbidden_material(*args, **kwargs):
        counts["material"] += 1
        raise AssertionError("View-only change invoked MaterialState build")

    def forbidden_regions(*args, **kwargs):
        counts["regions"] += 1
        raise AssertionError("View-only change invoked RestRegion extraction")

    def forbidden_rest(*args, **kwargs):
        counts["rest"] += 1
        raise AssertionError("View-only change invoked Rest core generation")

    monkeypatch.setattr(cam_service_module, "calculate_material_state", forbidden_material)
    monkeypatch.setattr(
        "hms_cadcam.cam.application.rest_pocket.extract_rest_regions",
        forbidden_regions,
    )
    monkeypatch.setattr(RestPocketGenerator, "generate", forbidden_rest)

    backend = UnavailableCadViewportBackend("R266 view-only probe")
    backend.set_view_direction(ViewDirection.ISOMETRIC)
    backend.set_background_color(ObjectColor(0.95, 0.96, 0.98))
    backend.set_toolpath_visibility(OperationId.new(), False)
    playback = PlaybackController(Timeline(()))
    playback.set_speed(2.0)
    translator = translation_service()
    previous = translator.language
    try:
        translator.set_language(UiLanguage.EN_US)
        translator.set_language(UiLanguage.KO_KR)
    finally:
        translator.set_language(previous)
    assert counts == {"material": 0, "regions": 0, "rest": 0}

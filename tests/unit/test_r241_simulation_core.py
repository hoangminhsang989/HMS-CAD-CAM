"""R241 deterministic 3-axis material-removal and invalidation acceptance."""

from dataclasses import replace

import pytest

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.simulation import (
    GougeStatus,
    HeightField3AxisEngine,
    IncrementalJobSimulator,
    MaterialRemovalError,
    PlaybackController,
    PlaybackState,
    QualityMode,
    SimulationDependencyGraph,
    Timeline,
    build_evidence,
    compare_target_surface,
    session_from_input,
)
from hms_cadcam.simulation.contracts import OperationCoverage, ResultState, StageTiming
from hms_cadcam.simulation.cache import BoundedSimulationCache, CacheKey
from tests.unit.test_cam_facing import _artifact


def test_flat_face_material_removal_is_real_bounded_and_deterministic() -> None:
    _generator, inputs, _computing, _token, artifact = _artifact()
    engine = HeightField3AxisEngine()
    first = engine.simulate(
        stock=inputs.setup.stock,
        artifact=artifact,
        tool=inputs.tool,
        quality=QualityMode.FAST,
    )
    second = engine.simulate(
        stock=inputs.setup.stock,
        artifact=artifact,
        tool=inputs.tool,
        quality=QualityMode.FAST,
    )
    assert first.remaining_stock == second.remaining_stock
    assert first.remaining_stock.removed_volume > 0.0
    assert first.remaining_stock.remaining_volume < first.remaining_stock.initial_volume
    assert first.remaining_stock.minimum_height == pytest.approx(48.0, abs=0.1)
    assert first.processed_cutting_samples > 0
    assert first.ignored_non_cutting_samples > 0


def test_material_removal_cancellation_never_returns_partial_full_result() -> None:
    _generator, inputs, _computing, _token, artifact = _artifact()
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks > 2

    with pytest.raises(MaterialRemovalError, match="cancelled"):
        HeightField3AxisEngine().simulate(
            stock=inputs.setup.stock,
            artifact=artifact,
            tool=inputs.tool,
            quality=QualityMode.FAST,
            cancellation=cancelled,
        )


def test_presentation_changes_never_invalidate_material_state() -> None:
    operations = tuple(
        (name, ContentFingerprint.from_payload({"operation": name}))
        for name in ("rough", "finish", "contour")
    )
    graph = SimulationDependencyGraph(operations)
    for reason in ("playback speed", "camera", "visibility", "timeline cursor"):
        plan = graph.presentation_change(reason)
        assert not plan.material_recomputation_required
        assert not plan.invalidated_operations
        assert not plan.invalidated_artifacts


def test_single_operation_edit_retains_safe_prefix_and_invalidates_downstream() -> None:
    operations = tuple(
        (name, ContentFingerprint.from_payload({"operation": name}))
        for name in ("rough", "finish", "contour")
    )
    plan = SimulationDependencyGraph(operations).operation_change(
        "finish", ContentFingerprint.from_payload({"operation": "finish-v2"})
    )
    assert plan.retained_operations == ("rough",)
    assert plan.invalidated_operations == ("finish", "contour")
    assert plan.material_recomputation_required


def test_timeline_playback_speed_and_cursor_do_not_call_engine() -> None:
    _generator, _inputs, _computing, _token, artifact = _artifact()
    timeline = Timeline.from_artifacts((artifact,))
    controller = PlaybackController(timeline)
    controller.set_speed(10.0)
    controller.play()
    assert controller.state is PlaybackState.PLAYING
    assert controller.next_event() is not None
    controller.pause()
    controller.previous_event()
    controller.stop()
    assert controller.cursor == 0


def test_bounded_cache_reuses_unchanged_and_evicts_oldest() -> None:
    cache: BoundedSimulationCache[str] = BoundedSimulationCache(
        maximum_entries=2, maximum_bytes=10
    )
    keys = tuple(
        CacheKey("stock", ContentFingerprint.from_payload({"key": value}))
        for value in range(3)
    )
    assert cache.put(keys[0], "first", byte_count=4)
    assert cache.put(keys[1], "second", byte_count=4)
    assert cache.get(keys[0]) == "first"
    assert cache.put(keys[2], "third", byte_count=4)
    assert cache.get(keys[1]) is None
    assert cache.get(keys[0]) == "first"
    assert cache.get(keys[2]) == "third"


def test_gouge_and_remaining_stock_comparison_is_analytic_and_fail_closed() -> None:
    _generator, inputs, _computing, _token, artifact = _artifact()
    material = HeightField3AxisEngine().simulate(
        stock=inputs.setup.stock,
        artifact=artifact,
        tool=inputs.tool,
        quality=QualityMode.FAST,
    )
    unavailable = compare_target_surface(material, None, tolerance=0.01)
    assert unavailable.status is GougeStatus.GEOMETRY_REFERENCE_UNAVAILABLE
    target = tuple(value + 0.5 for value in material.remaining_stock.top_heights)
    gouged = compare_target_surface(material, target, tolerance=0.01)
    assert gouged.status is GougeStatus.GOUGE_DETECTED
    assert gouged.maximum_gouge_depth == pytest.approx(0.5)


def test_partial_operation_evidence_never_claims_complete_job_pass() -> None:
    from hms_cadcam.cam.simulation.runtime import SimulationInputSnapshot
    from tests.unit.test_simulation_service import _source

    operation, artifact, setup, tool, holder, assembly, request, _scene = _source()
    inputs = SimulationInputSnapshot(
        operation, artifact, setup, tool, assembly, holder, None, request
    )
    material = HeightField3AxisEngine().simulate(
        stock=setup.stock,
        artifact=artifact,
        tool=tool,
        quality=QualityMode.FAST,
    )
    session = session_from_input(
        inputs,
        QualityMode.FAST,
        project_fingerprint=ContentFingerprint.from_payload({"project": "fixture"}),
        coverage=OperationCoverage.SINGLE_OPERATION,
    )
    evidence = build_evidence(
        session=session,
        material=material,
        comparison=compare_target_surface(material, None, tolerance=0.01),
        timings=(StageTiming("material_removal", 0.1),),
    )
    assert evidence.state is ResultState.PARTIAL
    assert evidence.remaining_stock_available
    assert evidence.result_fingerprint == build_evidence(
        session=session,
        material=material,
        comparison=compare_target_surface(material, None, tolerance=0.01),
        timings=(StageTiming("material_removal", 9.9),),
    ).result_fingerprint


def test_unchanged_reopen_hits_cache_and_single_edit_recomputes_downstream_only() -> None:
    from dataclasses import replace
    from hms_cadcam.cam.domain import Revision
    from hms_cadcam.cam.simulation.runtime import SimulationInputSnapshot
    from tests.unit.test_simulation_service import _source

    operation, artifact, setup, tool, holder, assembly, request, _scene = _source()
    first = SimulationInputSnapshot(operation, artifact, setup, tool, assembly, holder, None, request)
    second_operation = replace(operation, operation_id=type(operation.operation_id).new())
    second_artifact = replace(
        artifact,
        artifact_id=type(artifact.artifact_id).new(),
        source_operation_id=second_operation.operation_id,
        events=tuple(
            replace(event, source_operation_id=second_operation.operation_id)
            for event in artifact.events
        ),
        artifact_fingerprint=None,
    )
    second_request = replace(
        request,
        request_id=type(request.request_id).new(),
        operation_id=second_operation.operation_id,
        artifact_id=second_artifact.artifact_id,
        artifact_fingerprint=second_artifact.artifact_fingerprint,
    )
    second = SimulationInputSnapshot(
        second_operation, second_artifact, setup, tool, assembly, holder, None, second_request
    )
    runner = IncrementalJobSimulator()
    initial = runner.run(
        (first, second), quality=QualityMode.FAST,
        coverage=OperationCoverage.COMPLETE_JOB,
    )
    assert runner.material_computations == 2
    reopened = runner.run(
        (first, second), quality=QualityMode.FAST,
        coverage=OperationCoverage.COMPLETE_JOB,
    )
    assert runner.material_computations == 2
    assert all(timing.cache_hit for timing in reopened.timings)
    edited_artifact = replace(
        second_artifact,
        operation_revision=Revision(second_artifact.operation_revision.value + 1),
        artifact_fingerprint=None,
    )
    edited_request = replace(
        second_request,
        operation_revision=edited_artifact.operation_revision,
        artifact_fingerprint=edited_artifact.artifact_fingerprint,
    )
    edited = replace(second, artifact=edited_artifact, request=edited_request)
    incremental = runner.run(
        (first, edited), quality=QualityMode.FAST,
        coverage=OperationCoverage.COMPLETE_JOB,
    )
    assert runner.material_computations == 3
    assert incremental.timings[0].cache_hit
    assert not incremental.timings[1].cache_hit
    assert initial.remaining_stock == incremental.remaining_stock

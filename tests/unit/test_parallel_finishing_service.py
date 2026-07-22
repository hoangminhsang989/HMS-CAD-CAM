"""Tool support, linking, IR, progress and atomicity tests for Stage 8A.2.1."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from hms_cadcam.cam.cam3d.parallel import (
    ParallelFinishingError,
    ParallelFinishingGenerator,
    ParallelProgressPhase,
    calculate_and_publish_parallel_finishing,
)
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ContentFingerprint,
    Length,
    LengthUnit,
    SetupId,
    ToolAssembly,
    ToolAssemblyId,
    ToolAssemblyReference,
)
from hms_cadcam.cam.persistence.artifact_store import ToolpathArtifactStore
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import ToolpathArtifactMetadata
from hms_cadcam.cam.simulation import SimulationSamplingPolicy, sample_toolpath
from hms_cadcam.cam.toolpath import (
    LinearMove,
    MarkerEvent,
    MotionClass,
    RapidMove,
    ToolpathArtifact,
)
from tests.unit._cam3d_fixtures import tool
from tests.unit._parallel_finishing_fixtures import (
    disconnected_fixture,
    planar_fixture,
)


def _candidate(fixture):
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    return generator.generate(computing)


class _FailingArtifactStore(ToolpathArtifactStore):
    def publish(
        self,
        project_root: Path,
        artifact: ToolpathArtifact,
    ) -> ToolpathArtifactMetadata:
        raise ToolpathArtifactStoreError("injected review failure")


def test_generator_builds_complete_ball_center_toolpath_and_preview() -> None:
    fixture = planar_fixture(stepover=5.0, maximum_segment_length=2.0)
    candidate = _candidate(fixture)
    artifact = candidate.artifact
    assert artifact.completion_status.value == "complete"
    assert artifact.artifact_fingerprint is not None
    assert candidate.preview.fingerprint.digest
    assert candidate.preview.statistics.planned_pass_count == 3
    assert candidate.preview.statistics.toolpath_event_count == len(artifact.events)
    assert any(isinstance(event, MarkerEvent) for event in artifact.events)
    assert any(
        isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING
        for event in artifact.events
    )


def test_toolpath_ir_has_conservative_retract_and_no_direct_rapid_between_regions() -> None:
    fixture = disconnected_fixture(stepover=5.0)
    artifact = _candidate(fixture).artifact
    cutting_groups = [
        event
        for event in artifact.events
        if isinstance(event, LinearMove) and event.motion_class is MotionClass.CUTTING
    ]
    assert cutting_groups
    retracts = [
        event
        for event in artifact.events
        if isinstance(event, (LinearMove, RapidMove))
        and event.motion_class is MotionClass.RETRACT
    ]
    assert len(retracts) >= 2
    assert all(
        event.start.position.z >= 40.0 and event.end.position.z >= 40.0
        for event in artifact.events
        if isinstance(event, RapidMove)
        and abs(event.start.position.x - event.end.position.x) > 0.001
    )


def test_same_input_produces_same_preview_ir_and_hash() -> None:
    fixture = planar_fixture(stepover=2.0)
    first = _candidate(fixture)
    second = _candidate(fixture)
    assert first.preview.to_dict() == second.preview.to_dict()
    assert first.preview.fingerprint == second.preview.fingerprint
    assert first.artifact.events == second.artifact.events
    assert first.artifact.artifact_fingerprint == second.artifact.artifact_fingerprint


def test_progress_reports_all_required_phases_in_order() -> None:
    fixture = planar_fixture(stepover=5.0)
    reports = []
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    generator.generate(computing, progress=reports.append)
    phases = tuple(dict.fromkeys(report.phase for report in reports))
    assert phases == (
        ParallelProgressPhase.VALIDATION,
        ParallelProgressPhase.FRAME_BOUNDS,
        ParallelProgressPhase.PASS_GENERATION,
        ParallelProgressPhase.INTERSECTION,
        ParallelProgressPhase.DISCRETIZATION,
        ParallelProgressPhase.ORDERING_LINKING,
        ParallelProgressPhase.IR_BUILD,
        ParallelProgressPhase.FINALIZATION,
    )
    assert all(report.processed <= report.total for report in reports)


def test_cancellation_returns_failed_state_and_publishes_no_artifact(tmp_path) -> None:
    fixture = planar_fixture(stepover=1.0)
    project = tmp_path / "Cancel.HMS"
    project.mkdir()
    result = calculate_and_publish_parallel_finishing(
        project,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        cancellation=lambda: True,
    )
    assert not result.accepted
    assert result.artifact is None and result.preview is None and result.metadata is None
    assert result.operation.artifact_state.status is ArtifactStatus.FAILED
    assert result.diagnostics[0].code.value == "parallel.cancelled"
    assert not (project / "toolpaths").exists()


@pytest.mark.parametrize(
    "target_phase",
    (
        ParallelProgressPhase.INTERSECTION,
        ParallelProgressPhase.DISCRETIZATION,
    ),
)
def test_cancellation_inside_geometry_phase_publishes_no_artifact(
    tmp_path,
    target_phase: ParallelProgressPhase,
) -> None:
    fixture = planar_fixture(stepover=1.0)
    project = tmp_path / f"Cancel-{target_phase.value}.HMS"
    project.mkdir()
    state = {"cancel": False}

    def progress(report) -> None:
        if report.phase is target_phase and (
            target_phase is ParallelProgressPhase.DISCRETIZATION
            or report.processed >= 1
        ):
            state["cancel"] = True

    result = calculate_and_publish_parallel_finishing(
        project,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        cancellation=lambda: state["cancel"],
        progress=progress,
    )
    assert not result.accepted
    assert result.diagnostics[0].code.value == "parallel.cancelled"
    assert not (project / "toolpaths").exists()


def test_stale_current_operation_is_rejected_before_file_publish(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    project = tmp_path / "Stale.HMS"
    project.mkdir()
    result = calculate_and_publish_parallel_finishing(
        project,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        current_operation=lambda: fixture.operation,
    )
    assert not result.accepted
    assert result.diagnostics[0].code.value == "parallel.stale_result"
    assert not (project / "toolpaths").exists()


def test_atomic_artifact_publish_and_simulation_sampling_compatibility(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    project = tmp_path / "Published.HMS"
    project.mkdir()
    result = calculate_and_publish_parallel_finishing(
        project,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    assert result.accepted and result.artifact is not None and result.metadata is not None
    assert result.diagnostics[0].code.value == "parallel.foundation_limitation"
    assert {
        item.code.value for item in result.diagnostics
    } >= {"parallel.mesh_normal_approximation"}
    assert result.operation.artifact_state.status is ArtifactStatus.VALID
    target = project / result.metadata.relative_path
    assert target.is_file()
    assert not tuple(target.parent.glob(".staging-*.tmp"))
    sampled = sample_toolpath(
        artifact=result.artifact,
        wcs=fixture.zone.wcs,
        policy=SimulationSamplingPolicy(max_linear_step=2.0),
    )
    assert sampled.samples
    heights = [item.setup_pose.position.z for item in sampled.samples]
    assert min(heights) <= max(heights)


def test_unsupported_flat_end_tool_fails_with_stable_code() -> None:
    fixture = planar_fixture()
    flat = tool(ball=False)
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Flat assembly",
        flat,
        Length(30.0, LengthUnit.MM),
        Length(40.0, LengthUnit.MM),
    )
    operation = dataclasses.replace(
        fixture.operation,
        tool_assembly=ToolAssemblyReference.from_assembly(assembly),
    )
    context = dataclasses.replace(
        fixture.context,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(assembly.to_dict()),
        tool_definition_fingerprint=flat.content_fingerprint,
    )
    with pytest.raises(ParallelFinishingError) as captured:
        ParallelFinishingGenerator().resolve_inputs(
            operation, context, assembly=assembly, tool=flat
        )
    assert captured.value.code.value == "parallel.unsupported_tool_geometry"
    assert "UNSUPPORTED_TOOL_GEOMETRY" in str(captured.value)


def test_clearance_below_ball_center_is_rejected_fail_closed() -> None:
    fixture = planar_fixture()
    unsafe = dataclasses.replace(
        fixture.context.safe_motion_policy,
        clearance_z=4.0,
        retract_z=3.0,
    )
    context = dataclasses.replace(fixture.context, safe_motion_policy=unsafe)
    generator = ParallelFinishingGenerator()
    inputs = generator.resolve_inputs(
        fixture.operation,
        context,
        assembly=fixture.assembly,
        tool=fixture.tool,
    )
    computing, _token = generator.begin(inputs)
    with pytest.raises(ParallelFinishingError) as captured:
        generator.generate(computing)
    assert captured.value.code.value == "parallel.invalid_clearance"


def test_check_surfaces_are_rejected_until_collision_validation_exists() -> None:
    fixture = planar_fixture(with_check=True)
    with pytest.raises(ParallelFinishingError) as captured:
        ParallelFinishingGenerator().resolve_inputs(
            fixture.operation,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
        )
    assert captured.value.code.value == "parallel.unsupported_protective_geometry"


def test_unsupported_allowance_components_are_rejected() -> None:
    fixture = planar_fixture()
    allowance = dataclasses.replace(fixture.zone.allowance, axial=0.5)
    zone = dataclasses.replace(fixture.zone, allowance=allowance)
    snapshot = dataclasses.replace(fixture.context.geometry_snapshot, zone=zone)
    context = dataclasses.replace(
        fixture.context,
        geometry_snapshot=snapshot,
        machining_zone=zone,
        stock_allowance=allowance,
    )
    with pytest.raises(ParallelFinishingError) as captured:
        ParallelFinishingGenerator().resolve_inputs(
            fixture.operation,
            context,
            assembly=fixture.assembly,
            tool=fixture.tool,
        )
    assert captured.value.code.value == "parallel.unsupported_allowance"


def test_missing_context_and_mismatched_setup_return_structured_errors() -> None:
    fixture = planar_fixture()
    generator = ParallelFinishingGenerator()
    with pytest.raises(ParallelFinishingError) as no_geometry:
        generator.resolve_inputs(
            fixture.operation,
            None,  # type: ignore[arg-type]
            assembly=fixture.assembly,
            tool=fixture.tool,
        )
    assert no_geometry.value.code.value == "parallel.no_geometry"

    mismatched = dataclasses.replace(fixture.operation, setup_id=SetupId.new())
    with pytest.raises(ParallelFinishingError) as workplane:
        generator.resolve_inputs(
            mismatched,
            fixture.context,
            assembly=fixture.assembly,
            tool=fixture.tool,
        )
    assert workplane.value.code.value == "parallel.invalid_workplane"


def test_artifact_store_failure_returns_no_ready_artifact(tmp_path) -> None:
    fixture = planar_fixture(stepover=5.0)
    project = tmp_path / "StoreFailure.HMS"
    project.mkdir()
    result = calculate_and_publish_parallel_finishing(
        project,
        fixture.operation,
        fixture.context,
        assembly=fixture.assembly,
        tool=fixture.tool,
        artifact_store=_FailingArtifactStore(),
    )
    assert not result.accepted
    assert result.artifact is None and result.metadata is None
    assert result.operation.artifact_state.status is ArtifactStatus.FAILED
    assert result.diagnostics[0].code.value == "parallel.artifact_generation_failure"

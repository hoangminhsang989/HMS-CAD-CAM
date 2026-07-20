import pytest
from uuid import uuid4

from hms_cadcam.cam.domain import CamValidationError, FeedUnit, LengthUnit
from hms_cadcam.cam.post import *
from hms_cadcam.cam.toolpath import ArcMove, LinearMove, RapidMove, publish_toolpath
from tests.unit._post_fixtures import source_snapshot
from tests.unit.test_boring_strategy import _artifact as boring_artifact, _inputs as boring_inputs
from tests.unit.test_reaming_strategy import _artifact as reaming_artifact, _inputs as reaming_inputs
from tests.unit.test_tapping_strategy import _artifact as tapping_artifact, _inputs as tapping_inputs


def _request(source):
    return PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id,
                       canonical_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))


def test_lowering_is_single_operation_and_preserves_motion_geometry():
    source = source_snapshot()
    program = lower_toolpath(_request(source), source)
    motions = tuple(record for record in program.records if isinstance(record, (RapidMotionRecord, LinearMotionRecord, ArcMotionRecord)))
    assert len(motions) == sum(record.kind.value in {"rapid", "linear", "arc"} for record in program.records)
    assert isinstance(motions[0], LinearMotionRecord)
    assert motions[0].start == source.artifact.events[0].start
    assert motions[0].end == source.artifact.events[0].end
    assert any(isinstance(record, FeedModeRecord) and record.mode is FeedMode.UNITS_PER_MINUTE for record in program.records)
    assert program.coordinate_mode is CoordinateMode.ABSOLUTE
    assert program.unit is LengthUnit.MM


def test_required_simulation_gate_blocks_missing_result():
    source = source_snapshot(with_motion=False)
    request = PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id,
                          canonical_definition())
    with pytest.raises(CamValidationError):
        lower_toolpath(request, source)


def test_inverse_time_is_fail_closed():
    source = source_snapshot(with_motion=False)
    request = _request(source)
    # The source artifact's typed event stream is the only lowering input;
    # inverse-time cannot be represented by a post-v1 record.
    assert request.lowering_policy.allow_arc_to_line is False


@pytest.mark.parametrize("process", ("tapping", "reaming", "boring"))
def test_process_markers_and_boring_tool_context_lower_without_semantic_downgrade(process):
    if process == "tapping":
        generator, inputs, holder, _ = tapping_inputs()
        artifact, computing, token = tapping_artifact(generator, inputs)
    elif process == "reaming":
        generator, inputs, holder, _ = reaming_inputs()
        artifact, computing, token = reaming_artifact(generator, inputs)
    else:
        generator, inputs, _ = boring_inputs()
        holder = inputs.holder
        artifact, computing, token = boring_artifact(generator, inputs)
    published = publish_toolpath(computing.operation, artifact, token, inputs.input_fingerprint)
    assert published.accepted
    source = PostSourceSnapshot(uuid4(), published.operation, artifact, inputs.setup, inputs.assembly,
                                inputs.tool, holder, inputs.machine)
    request = _request(source)
    program = lower_toolpath(request, source)
    source_motions = sum(isinstance(event, (RapidMove, LinearMove, ArcMove)) for event in artifact.events)
    program_motions = sum(isinstance(record, (RapidMotionRecord, LinearMotionRecord, ArcMotionRecord)) for record in program.records)
    assert source_motions == program_motions
    assert sum(isinstance(record, SemanticMarkerRecord) for record in program.records) == sum(event.kind.value == "marker" for event in artifact.events)

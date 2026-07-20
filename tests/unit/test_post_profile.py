from dataclasses import replace
import json

import pytest

from hms_cadcam.cam.domain import CamInvariantError, CamValidationError, Length, LengthUnit
from hms_cadcam.cam.post import (
    ControllerToolBinding, PostRequest, ProductionProgramContext,
    ProductionControllerProfile, SimulationGateMode, SimulationGatePolicy,
    robodrill_21i_definition, robodrill_21i_profile,
)
from hms_cadcam.cam.post.codec import dumps, loads
from tests.unit._post_fixtures import source_snapshot
from tests.unit._fanuc_fixtures import fixture_context


def test_robodrill_profile_is_versioned_and_display_name_does_not_fingerprint():
    profile = robodrill_21i_profile()
    assert profile.profile_key == "robodrill_fanuc_21i_worknc_expanded_v1"
    assert profile.fingerprint == replace(profile, display_name="review label").fingerprint
    restored = loads(dumps(profile))
    assert isinstance(restored, ProductionControllerProfile)
    assert restored.fingerprint == profile.fingerprint


def test_binding_and_context_round_trip_is_strict_and_deterministic():
    source = source_snapshot(with_motion=False)
    context = fixture_context(source)
    assert loads(dumps(context)).fingerprint == context.fingerprint
    payload = json.loads(dumps(context))
    payload["unexpected"] = True
    with pytest.raises(CamValidationError):
        loads(json.dumps(payload))


def test_profile_rejects_duplicate_axes_and_unsafe_comment_fragments():
    with pytest.raises(CamInvariantError):
        replace(robodrill_21i_profile(), axes=("X", "X", "Y"))
    source = source_snapshot(with_motion=False)
    binding = ControllerToolBinding(source.assembly.content_fingerprint, 1, 1, 1, "bad\ncomment")
    assert binding.tool_comment == "bad comment"


def test_production_context_requires_fn_name_and_explicit_safe_z():
    source = source_snapshot(with_motion=False)
    context = fixture_context(source)
    with pytest.raises(CamValidationError):
        replace(context, file_name="program.nc")
    assert context.safe_z == Length(10.0, LengthUnit.MM)


def test_production_request_codec_preserves_embedded_profile_and_context_fingerprints():
    source = source_snapshot(with_motion=False)
    context = fixture_context(source, file_name="codec_round_trip.fn")
    request = PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        robodrill_21i_definition(),
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
        program_context=context,
    )

    restored = loads(dumps(request))

    assert restored.post_definition.production_profile.fingerprint == request.post_definition.production_profile.fingerprint
    assert restored.program_context.fingerprint == context.fingerprint

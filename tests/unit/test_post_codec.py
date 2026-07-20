import json

import pytest

from hms_cadcam.cam.domain import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.post import *
from hms_cadcam.cam.post.codec import dumps, loads
from tests.unit._post_fixtures import source_snapshot


def test_definition_and_request_round_trip_is_canonical():
    source = source_snapshot(with_motion=False)
    request = PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id,
                          canonical_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))
    encoded = dumps(request)
    assert encoded == dumps(loads(encoded))


def test_program_round_trip_preserves_fingerprint_and_typed_records():
    source = source_snapshot()
    request = PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id,
                          canonical_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))
    program = lower_toolpath(request, source)
    restored = loads(dumps(program))
    assert isinstance(restored, NCProgramIR)
    assert restored.program_fingerprint == program.program_fingerprint
    assert tuple(type(item) for item in restored.records) == tuple(type(item) for item in program.records)


def test_codecs_reject_future_versions_unknown_fields_and_nonfinite_values():
    source = source_snapshot(with_motion=False)
    payload = json.loads(dumps(PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id, canonical_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))))
    payload["format_version"] = 99
    with pytest.raises(UnsupportedCamSchemaError):
        loads(json.dumps(payload))
    payload["format_version"] = 1
    payload["unexpected"] = True
    with pytest.raises(CamValidationError):
        loads(json.dumps(payload))
    with pytest.raises(CamValidationError):
        loads("{\"format\":\"HMS_CAM_POST\",\"format_version\":1,\"request_id\":null,\"project_id\":\"bad\"}")

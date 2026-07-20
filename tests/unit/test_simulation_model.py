"""Phase 7C.1 immutable simulation model and codec tests."""

import dataclasses

import pytest

from hms_cadcam.cam.domain import (
    ContentFingerprint, DependencyFingerprint, FixtureInstanceId, HolderDefinitionId,
    LengthUnit, OperationId, Point3, Revision, SetupId, SimulationRequestId,
    SimulationResultId, ToolAssemblyId, ToolDefinitionId, ToolpathArtifactId,
    UnsupportedCamSchemaError,
)
from hms_cadcam.cam.domain.operation import DiagnosticSeverity
from hms_cadcam.cam.simulation import (
    SimulationIssue, SimulationIssueCategory, SimulationIssueCode, SimulationRequest,
    SimulationResult, SimulationSamplingPolicy, SimulationStatistics, SimulationStatus,
    dumps, loads_request, loads_result,
)
from hms_cadcam.cam.toolpath.geometry import Bounds3


def _fp(name):
    return ContentFingerprint.from_payload({"name": name})


def _request():
    return SimulationRequest(
        SimulationRequestId.new(), OperationId.new(), Revision(2), ToolpathArtifactId.new(),
        _fp("artifact"), DependencyFingerprint.from_payload({"all": "inputs"}), SetupId.new(),
        Revision(3), _fp("wcs"), _fp("stock"), ((FixtureInstanceId.new(), _fp("fixture")),),
        ToolAssemblyId.new(), _fp("assembly"), ToolDefinitionId.new(), _fp("tool"),
        HolderDefinitionId.new(), _fp("holder"), None, None, LengthUnit.MM,
        SimulationSamplingPolicy(maximum_samples=10_000), 12.0,
    )


def _statistics():
    bounds = Bounds3(Point3(0, 0, 0, LengthUnit.MM), Point3(10, 5, 2, LengthUnit.MM))
    return SimulationStatistics(11, 1, 0, 0, 0, bounds)


def test_request_is_immutable_versioned_and_uuid_is_not_input_identity():
    request = _request()
    replacement = dataclasses.replace(request, request_id=SimulationRequestId.new())
    assert replacement.identity_payload() == request.identity_payload()
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.safe_height = 4.0
    assert loads_request(dumps(request)) == request


def test_sampling_policy_defaults_and_hard_bounds():
    policy = SimulationSamplingPolicy()
    assert policy.maximum_samples == 250_000
    assert policy.chunk_size == 2_048
    assert policy.cancellation_check_interval == 256
    assert policy.memory_budget_bytes == 256 * 1024 * 1024
    for kwargs in ({"maximum_samples": 1_000_001}, {"maximum_issues": 10_001}, {"cancellation_check_interval": 257}, {"max_linear_step": float("nan")}):
        with pytest.raises(Exception):
            SimulationSamplingPolicy(**kwargs)


def test_result_sorts_issues_and_verifies_fingerprint_round_trip():
    request = _request()
    issue = SimulationIssue(DiagnosticSeverity.WARNING, SimulationIssueCategory.CLEARANCE_WARNING,
        SimulationIssueCode.RAPID_BELOW_SAFE, "rapid.below_safe", request.operation_id,
        request.artifact_id, event_index=2, sample_index=3, evidence=(("z", "1.0"),))
    stats = dataclasses.replace(_statistics(), warning_count=1)
    result = SimulationResult.create(result_id=SimulationResultId.new(), request=request,
        status=SimulationStatus.WARN, issues=(issue,), statistics=stats)
    assert loads_result(dumps(result)) == result
    payload = result.to_dict()
    payload["status"] = "fail"
    with pytest.raises(Exception):
        from hms_cadcam.cam.simulation.codec import result_from_dict
        result_from_dict(payload)


def test_codec_rejects_future_version_unknown_field_and_nonfinite_value():
    request = _request()
    payload = request.to_dict()
    payload["format_version"] = 2
    with pytest.raises(UnsupportedCamSchemaError):
        from hms_cadcam.cam.simulation.codec import request_from_dict
        request_from_dict(payload)
    payload = request.to_dict(); payload["unknown"] = 1
    with pytest.raises(Exception):
        from hms_cadcam.cam.simulation.codec import request_from_dict
        request_from_dict(payload)
    with pytest.raises(Exception):
        SimulationSamplingPolicy(chord_tolerance=float("inf"))

from dataclasses import replace

from hms_cadcam.cam.domain import ContentFingerprint, DependencyFingerprint, DiagnosticSeverity, SimulationRequestId, SimulationResultId
from hms_cadcam.cam.post import *
from hms_cadcam.cam.simulation.model import (
    SimulationIssue, SimulationIssueCategory, SimulationIssueCode, SimulationRequest,
    SimulationResult, SimulationSamplingPolicy, SimulationStatistics, SimulationStatus,
)
from hms_cadcam.cam.application.service import CamApplicationService
from tests.unit._post_fixtures import source_snapshot


def _request(source):
    return PostRequest(source.project_id, source.operation.operation_id, source.artifact.artifact_id,
                       canonical_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL))


def _simulation(source, status):
    input_fingerprint = DependencyFingerprint.from_payload({"simulation": "current"})
    request = SimulationRequest(SimulationRequestId.new(), source.operation.operation_id,
        source.operation.revision, source.artifact.artifact_id, source.artifact.artifact_fingerprint,
        input_fingerprint, source.setup.setup_id, source.setup.revision,
        ContentFingerprint.from_payload(source.setup.wcs.to_dict()),
        ContentFingerprint.from_payload(source.setup.stock.to_dict()), (), source.assembly.assembly_id,
        ContentFingerprint.from_payload(source.assembly.to_dict()), source.tool.tool_id,
        source.tool.content_fingerprint, None, None, None, None, source.artifact.unit,
        SimulationSamplingPolicy(), 10.0)
    issues = ()
    warnings = errors = 0
    if status is SimulationStatus.WARN:
        issues = (SimulationIssue(DiagnosticSeverity.WARNING, SimulationIssueCategory.CLEARANCE_WARNING,
                                  SimulationIssueCode.RAPID_BELOW_SAFE, "sim.warning",
                                  source.operation.operation_id, source.artifact.artifact_id),)
        warnings = 1
    elif status is SimulationStatus.FAIL:
        issues = (SimulationIssue(DiagnosticSeverity.ERROR, SimulationIssueCategory.INVALID_ARTIFACT,
                                  SimulationIssueCode.INVALID_MOTION, "sim.failure",
                                  source.operation.operation_id, source.artifact.artifact_id),)
        errors = 1
    statistics = SimulationStatistics(1, 0, 0, warnings, errors, source.artifact.bounds)
    return SimulationResult.create(result_id=SimulationResultId.new(), request=request,
                                   status=status, issues=issues, statistics=statistics), input_fingerprint


def test_runtime_publishes_one_current_result_and_cleans_operation():
    source = source_snapshot()
    request = _request(source)
    runtime = PostRuntimeService()
    execution = runtime.post(request, source)
    assert execution.accepted and execution.result is not None
    assert runtime.current(request) == execution.result
    runtime.mark_stale(source.operation.operation_id)
    assert runtime.current(request) is None


def test_runtime_preserves_previous_result_when_new_input_fails():
    source = source_snapshot()
    request = _request(source)
    runtime = PostRuntimeService()
    first = runtime.post(request, source)
    assert first.result is not None
    class FailingAdapter(CanonicalDummyAdapter):
        def format_program(self, program, definition):
            raise ValueError("formatter failed")

    failed = runtime.post(request, source, FailingAdapter())
    assert not failed.accepted
    assert runtime.current(request) == first.result


def test_input_fingerprint_excludes_request_uuid_and_changes_with_semantic_source():
    source = source_snapshot(with_motion=False)
    first = _request(source)
    second = _request(source)
    assert first.request_id != second.request_id
    assert build_post_input_fingerprint(first, source) == build_post_input_fingerprint(second, source)
    changed = replace(source, operation=replace(source.operation, enabled=False))
    assert build_post_input_fingerprint(first, changed) != build_post_input_fingerprint(first, source)


def test_older_token_cannot_publish_after_a_newer_begin():
    source = source_snapshot(with_motion=False)
    request = _request(source)
    runtime = PostRuntimeService()
    older = runtime.begin(request, source)
    runtime.begin(request, source)
    published = runtime.post(request, source)
    assert published.result is not None
    assert runtime.publish(request, older, published.result) is False


def test_simulation_gate_matrix_is_fail_closed():
    base = source_snapshot(with_motion=False)
    passed, current_input = _simulation(base, SimulationStatus.PASS)
    pass_source = replace(base, simulation_result=passed, expected_simulation_input_fingerprint=current_input)
    require = PostRequest(base.project_id, base.operation.operation_id, base.artifact.artifact_id,
                          canonical_definition(), simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.REQUIRE_PASS))
    assert PostRuntimeService().post(require, pass_source).accepted
    warned, current_input = _simulation(base, SimulationStatus.WARN)
    warn_source = replace(base, simulation_result=warned, expected_simulation_input_fingerprint=current_input)
    allow_warn = replace(require, simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.ALLOW_WARN))
    assert not PostRuntimeService().post(require, warn_source).accepted
    assert PostRuntimeService().post(allow_warn, warn_source).accepted
    failed, current_input = _simulation(base, SimulationStatus.FAIL)
    fail_source = replace(base, simulation_result=failed, expected_simulation_input_fingerprint=current_input)
    assert not PostRuntimeService().post(_request(base), fail_source).accepted


def test_cam_application_clear_cleans_runtime_post_registry():
    source = source_snapshot(with_motion=False)
    request = _request(source)
    application = CamApplicationService()
    assert application.post_service is application.post_runtime
    assert application.post_runtime.post(request, source).accepted
    application.clear()
    assert application.post_runtime.current(request) is None

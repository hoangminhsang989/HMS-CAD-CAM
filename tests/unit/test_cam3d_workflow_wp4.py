from dataclasses import replace

from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DPreviewCompletionState,
    Cam3DPreviewMesh,
    Cam3DPreviewResult,
    Cam3DPreviewSource,
    Cam3DCancelDecision,
    Cam3DSubmissionDecision,
    Cam3DSubmissionReceipt,
)
from hms_cadcam.cam.application.cam3d_request import Cam3DResultIdentity
from hms_cadcam.cam.application.cam3d_workflow import (
    Cam3DPreviewWorkflow,
    Cam3DWorkflowDiagnosticCode,
    Cam3DWorkflowInput,
    Cam3DWorkflowStatus,
)
from tests.unit.test_cam3d_request_wp3 import _ready_fixture


class _Gateway:
    def __init__(self) -> None:
        self.submitted = []
        self.cancelled = []
        self.closed = []
        self.switched = []
        self.shutdown_calls = 0

    def submit(self, request):
        self.submitted.append(request)
        return Cam3DSubmissionReceipt(
            request.job_id,
            True,
            Cam3DSubmissionDecision.ACCEPTED,
            True,
        )

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        return Cam3DCancelDecision.REQUESTED

    def close_ownership(self, ownership):
        self.closed.append(ownership)

    def switch_ownership(self, previous, current, project_generation):
        self.switched.append((previous, current, project_generation))

    def shutdown(self, *, wait=False):
        self.shutdown_calls += 1


class _Sink:
    def __init__(self, accepted=True) -> None:
        self.accepted = accepted
        self.published = []
        self.cleared = []

    def publish(self, result):
        self.published.append(result)
        return self.accepted

    def clear(self, ownership):
        self.cleared.append(ownership)


def _workflow(fixture, *, sink=None):
    gateway = _Gateway()
    sink = sink or _Sink()
    workflow = Cam3DPreviewWorkflow(gateway, sink)
    workflow.bind_inputs(
        Cam3DWorkflowInput(
            fixture.editor,
            fixture.context,
            fixture.selection,
            fixture.setup,
            True,
        )
    )
    return workflow, gateway, sink


def _mesh() -> Cam3DPreviewMesh:
    return Cam3DPreviewMesh(
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((0, 1, 2),),
        ((0.0, 0.0, 1.0),),
        (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
    )


def test_explicit_submit_latest_wins_and_duplicate_result_is_dropped():
    fixture = _ready_fixture()
    workflow, gateway, sink = _workflow(fixture)
    assert workflow.state.status is Cam3DWorkflowStatus.READY
    assert not gateway.submitted
    workflow.submit_preview()
    first = gateway.submitted[-1]
    first_result = Cam3DPreviewResult.success(
        first, _mesh(), source=Cam3DPreviewSource.WORKER
    )
    assert workflow.accept_result(first_result)
    assert workflow.state.status is Cam3DWorkflowStatus.CURRENT
    assert not workflow.accept_result(first_result)

    workflow.submit_preview()
    second = gateway.submitted[-1]
    assert second.job_id != first.job_id
    assert not workflow.accept_result(first_result)
    second_result = Cam3DPreviewResult.success(
        second, _mesh(), source=Cam3DPreviewSource.CACHE
    )
    assert workflow.accept_result(second_result)
    assert workflow.state.preview_source is Cam3DPreviewSource.CACHE
    assert len(sink.published) == 2


def test_invalid_and_read_only_inputs_never_submit():
    fixture = _ready_fixture(read_only=True)
    gateway = _Gateway()
    workflow = Cam3DPreviewWorkflow(gateway, _Sink())
    state = workflow.bind_inputs(
        Cam3DWorkflowInput(
            fixture.editor,
            fixture.context,
            fixture.selection,
            fixture.setup,
            False,
        )
    )
    assert state.status is Cam3DWorkflowStatus.BLOCKED
    workflow.submit_preview()
    assert gateway.submitted == []


def test_cancel_edit_close_and_switch_are_owned_and_idempotent():
    fixture = _ready_fixture()
    workflow, gateway, sink = _workflow(fixture)
    workflow.submit_preview()
    job_id = workflow.state.active_job_id
    assert job_id is not None
    assert workflow.cancel_preview() is Cam3DCancelDecision.REQUESTED
    assert workflow.cancel_preview() is Cam3DCancelDecision.NOT_FOUND
    assert gateway.cancelled == [job_id]

    changed = replace(fixture.editor, tool_profile=None)
    workflow.bind_inputs(
        Cam3DWorkflowInput(
            changed,
            fixture.context,
            fixture.selection,
            fixture.setup,
            False,
        )
    )
    assert sink.cleared == [fixture.setup.ownership]
    workflow.close()
    assert gateway.closed[-1] == fixture.setup.ownership
    assert workflow.state.status is Cam3DWorkflowStatus.CLOSED
    workflow.shutdown()
    assert gateway.shutdown_calls == 1


def test_publication_failure_is_fail_closed_without_fake_success():
    fixture = _ready_fixture()
    sink = _Sink(accepted=False)
    workflow, _gateway, _sink = _workflow(fixture, sink=sink)
    workflow.submit_preview()
    request = _gateway.submitted[-1]
    result = Cam3DPreviewResult.success(
        request, _mesh(), source=Cam3DPreviewSource.WORKER
    )
    assert not workflow.accept_result(result)
    assert workflow.state.status is Cam3DWorkflowStatus.ERROR
    assert (
        workflow.state.diagnostic
        is Cam3DWorkflowDiagnosticCode.PUBLICATION_UNAVAILABLE
    )
"""Qt sink and end-to-end CAM 3D viewport publication tests."""

from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import inspect

from PySide6.QtCore import QObject, Slot

from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DPreviewCoordinator,
    Cam3DPreviewResult,
    Cam3DPreviewSource,
)
from hms_cadcam.cam.application.cam3d_workflow import (
    Cam3DPreviewWorkflow,
    Cam3DWorkflowInput,
    Cam3DWorkflowStatus,
)
from hms_cadcam.ui.cam3d_preview_worker import Cam3DQtWorkerBridge
from hms_cadcam.ui.cam3d_viewport import (
    Cam3DViewportPreviewSink,
    cam3d_preview_publication_from_result,
)
from hms_cadcam.viewer.cam3d import (
    Cam3DPreviewActorIdentity,
    Cam3DPreviewPublication,
    Cam3DPreviewPublicationCode,
    Cam3DPreviewPublicationResult,
)
from hms_cadcam.viewer.models import ViewportStatus
from hms_cadcam.viewer.widget import CadViewportWidget
from tests.unit.test_cam3d_preview_worker_wp3b import (
    _ImmediateTessellator,
    _mesh,
    _request,
)
from tests.unit.test_cam3d_request_wp3 import _ready_fixture


class _ViewportBackend:
    def __init__(self) -> None:
        self.initialized = False
        self.closed = False
        self.identity: Cam3DPreviewActorIdentity | None = None
        self.publications: list[Cam3DPreviewPublication] = []
        self.clear_count = 0
        self.fail_publication = False
        self.selection_callback = lambda _items: None

    def get_status(self) -> ViewportStatus:
        return ViewportStatus(True, self.initialized and not self.closed, "test")

    def set_selection_callback(self, callback) -> None:
        self.selection_callback = callback

    def initialize(self, native_window_id: int) -> None:
        assert native_window_id > 0
        self.initialized = True

    def resize(self, width: int, height: int) -> None:
        assert width >= 0 and height >= 0

    def publish_cam3d_preview(
        self,
        publication: Cam3DPreviewPublication,
    ) -> Cam3DPreviewPublicationResult:
        if self.fail_publication:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.BACKEND_FAILURE,
                self.identity,
            )
        if self.identity is not None and (
            self.identity.ownership != publication.identity.ownership
            or self.identity.project_generation
            != publication.identity.project_generation
        ):
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.OWNERSHIP_MISMATCH,
                self.identity,
            )
        code = (
            Cam3DPreviewPublicationCode.PUBLISHED
            if self.identity is None
            else Cam3DPreviewPublicationCode.REPLACED
        )
        self.identity = publication.identity
        self.publications.append(publication)
        return Cam3DPreviewPublicationResult(code, self.identity)

    def clear_cam3d_preview(self, ownership) -> Cam3DPreviewPublicationResult:
        if self.identity is None:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.ALREADY_CLEAR
            )
        if self.identity.ownership != ownership:
            return Cam3DPreviewPublicationResult(
                Cam3DPreviewPublicationCode.OWNERSHIP_MISMATCH,
                self.identity,
            )
        previous = self.identity
        self.identity = None
        self.clear_count += 1
        return Cam3DPreviewPublicationResult(
            Cam3DPreviewPublicationCode.CLEARED,
            previous,
        )

    def get_cam3d_preview_identity(self) -> Cam3DPreviewActorIdentity | None:
        return self.identity

    def close(self) -> None:
        self.identity = None
        self.closed = True
        self.initialized = False


class _WorkflowReceiver(QObject):
    def __init__(self, workflow: Cam3DPreviewWorkflow) -> None:
        super().__init__()
        self.workflow = workflow
        self.deliveries = 0

    @Slot(object)
    def handle_cam3d_preview(self, result: object) -> None:
        self.deliveries += 1
        self.workflow.accept_result(result)


def _result(request, *, offset: float = 0.0) -> Cam3DPreviewResult:
    return Cam3DPreviewResult.success(
        request,
        _mesh(offset),
        source=Cam3DPreviewSource.WORKER,
    )


def test_widget_rejects_worker_thread_and_preserves_current_actor(qtbot) -> None:
    backend = _ViewportBackend()
    widget = CadViewportWidget(OcpCadKernel(), backend)
    qtbot.addWidget(widget)
    request = _request()
    first = widget.publish_cam3d_preview(cam3d_preview_publication_from_result(_result(request)))
    assert first.code is Cam3DPreviewPublicationCode.PUBLISHED
    before = tuple(backend.publications)

    with ThreadPoolExecutor(max_workers=1) as executor:
        rejected = executor.submit(
            widget.publish_cam3d_preview,
            cam3d_preview_publication_from_result(_result(
                _request(ownership=request.ownership, semantic=2),
                offset=2.0,
            )),
        ).result(timeout=5.0)

    assert rejected.code is Cam3DPreviewPublicationCode.WRONG_THREAD
    assert tuple(backend.publications) == before
    assert widget.cam3d_preview_identity == first.identity
    widget.shutdown()


def test_real_sink_never_reflects_or_reports_fake_success(qtbot) -> None:
    backend = _ViewportBackend()
    widget = CadViewportWidget(OcpCadKernel(), backend)
    qtbot.addWidget(widget)
    sink = Cam3DViewportPreviewSink(widget)
    request = _request()
    backend.fail_publication = True

    assert not sink.publish(_result(request))
    assert backend.identity is None
    assert "getattr" not in inspect.getsource(Cam3DViewportPreviewSink.publish)
    widget.shutdown()


def test_explicit_preview_worker_cache_gui_publication_and_invalidation(qtbot) -> None:
    fixture = _ready_fixture()
    backend = _ViewportBackend()
    widget = CadViewportWidget(OcpCadKernel(), backend)
    qtbot.addWidget(widget)
    coordinator = Cam3DPreviewCoordinator(_ImmediateTessellator())
    bridge = Cam3DQtWorkerBridge(coordinator)
    sink = Cam3DViewportPreviewSink(widget)
    workflow = Cam3DPreviewWorkflow(bridge, sink)
    receiver = _WorkflowReceiver(workflow)
    bridge.set_receiver(receiver)
    inputs = Cam3DWorkflowInput(
        fixture.editor,
        fixture.context,
        fixture.selection,
        fixture.setup,
        True,
    )
    workflow.bind_inputs(inputs)
    assert workflow.state.status is Cam3DWorkflowStatus.READY
    assert backend.publications == []

    try:
        workflow.submit_preview()
        qtbot.waitUntil(
            lambda: workflow.state.status is Cam3DWorkflowStatus.CURRENT,
            timeout=5000,
        )
        assert len(backend.publications) == 1
        assert receiver.deliveries == 1

        workflow.submit_preview()
        qtbot.waitUntil(lambda: len(backend.publications) == 2, timeout=5000)
        assert workflow.state.status is Cam3DWorkflowStatus.CURRENT
        assert workflow.state.preview_source is Cam3DPreviewSource.CACHE
        assert receiver.deliveries == 2
        assert backend.identity is not None
        assert backend.identity.job_id == str(workflow.state.accepted_identity.job_id)

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
        assert backend.identity is None
        assert backend.clear_count == 1
    finally:
        bridge.set_receiver(None)
        workflow.shutdown(wait=True)
        widget.shutdown()

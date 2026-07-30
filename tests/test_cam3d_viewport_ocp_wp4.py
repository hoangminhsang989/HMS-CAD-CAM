"""OCP actor lifecycle tests for Stage 9A.8 WP4 viewport publication."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from OCP.AIS import AIS_Triangulation

from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DPreviewResult,
    Cam3DPreviewSource,
)
from hms_cadcam.ui.cam3d_viewport import (
    cam3d_preview_ownership,
    cam3d_preview_publication_from_result,
)
from hms_cadcam.viewer.cam3d import (
    Cam3DPreviewPublication,
    Cam3DPreviewPublicationCode,
)
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend
from hms_cadcam.viewer.ocp.lifecycle import OcpViewportLifecycle
from tests.unit.test_cam3d_preview_worker_wp3b import _mesh, _request


class _ActorContext:
    def __init__(self) -> None:
        self.source_actor = object()
        self.displayed: list[object] = [self.source_actor]
        self.removed: list[object] = []
        self.deactivated: list[object] = []
        self.colors: list[object] = []
        self.transparencies: list[float] = []
        self.update_count = 0
        self.fail_display_once = False
        self.fail_remove_target: object | None = None

    def Display(self, actor: object, update: bool) -> None:  # noqa: N802
        assert not update
        if self.fail_display_once:
            self.fail_display_once = False
            raise RuntimeError("simulated CAM3D display failure")
        if not any(item is actor for item in self.displayed):
            self.displayed.append(actor)

    def SetColor(self, actor: object, color: object, update: bool) -> None:  # noqa: N802
        assert any(item is actor for item in self.displayed)
        assert not update
        self.colors.append(color)

    def SetTransparency(  # noqa: N802
        self,
        actor: object,
        value: float,
        update: bool,
    ) -> None:
        assert any(item is actor for item in self.displayed)
        assert not update
        self.transparencies.append(value)

    def Deactivate(self, actor: object) -> None:  # noqa: N802
        self.deactivated.append(actor)

    def Remove(self, actor: object, update: bool) -> None:  # noqa: N802
        assert not update
        if self.fail_remove_target is actor:
            self.fail_remove_target = None
            raise RuntimeError("simulated CAM3D remove failure")
        self.displayed = [item for item in self.displayed if item is not actor]
        self.removed.append(actor)

    def UpdateCurrentViewer(self) -> None:  # noqa: N802
        self.update_count += 1

    @property
    def preview_actors(self) -> tuple[AIS_Triangulation, ...]:
        return tuple(
            item for item in self.displayed if isinstance(item, AIS_Triangulation)
        )


class _ActorLifecycle(OcpViewportLifecycle):
    def __init__(self, context: _ActorContext) -> None:
        super().__init__()
        self.actor_context = context

    @property
    def initialized(self) -> bool:
        return True

    @property
    def context(self) -> _ActorContext:
        return self.actor_context


def _backend() -> tuple[OcpCadViewportBackend, _ActorContext]:
    context = _ActorContext()
    backend = OcpCadViewportBackend(
        OcpCadKernel(),
        lifecycle=_ActorLifecycle(context),
    )
    return backend, context


def _publication(request, *, offset: float = 0.0) -> Cam3DPreviewPublication:
    result = Cam3DPreviewResult.success(
        request,
        _mesh(offset),
        source=Cam3DPreviewSource.WORKER,
    )
    return cam3d_preview_publication_from_result(result)


def test_real_ocp_actor_first_publish_replace_and_source_isolation() -> None:
    backend, context = _backend()
    first_request = _request()
    second_request = _request(ownership=first_request.ownership, semantic=1)

    first = backend.publish_cam3d_preview(_publication(first_request))
    old_actor = context.preview_actors[0]
    second = backend.publish_cam3d_preview(
        _publication(second_request, offset=2.0)
    )

    assert first.code is Cam3DPreviewPublicationCode.PUBLISHED
    assert second.code is Cam3DPreviewPublicationCode.REPLACED
    assert len(context.preview_actors) == 1
    assert context.preview_actors[0] is not old_actor
    assert old_actor in context.removed
    assert context.source_actor in context.displayed
    assert context.source_actor not in context.removed
    assert all(actor in context.deactivated for actor in context.preview_actors)
    triangulation = context.preview_actors[0].GetTriangulation()
    assert triangulation.NbNodes() == 3
    assert triangulation.NbTriangles() == 1
    assert triangulation.HasNormals()
    assert backend.get_cam3d_preview_identity() == second.identity


def test_candidate_display_failure_preserves_previous_actor() -> None:
    backend, context = _backend()
    first_request = _request()
    first = backend.publish_cam3d_preview(_publication(first_request))
    old_actor = context.preview_actors[0]
    context.fail_display_once = True

    failed = backend.publish_cam3d_preview(
        _publication(
            _request(ownership=first_request.ownership, semantic=2),
            offset=4.0,
        )
    )

    assert failed.code is Cam3DPreviewPublicationCode.BACKEND_FAILURE
    assert failed.identity == first.identity
    assert context.preview_actors == (old_actor,)
    assert backend.get_cam3d_preview_identity() == first.identity
    assert context.source_actor in context.displayed


def test_remove_failure_rolls_back_candidate_without_double_actor() -> None:
    backend, context = _backend()
    first_request = _request()
    first = backend.publish_cam3d_preview(_publication(first_request))
    old_actor = context.preview_actors[0]
    context.fail_remove_target = old_actor

    failed = backend.publish_cam3d_preview(
        _publication(
            _request(ownership=first_request.ownership, semantic=3),
            offset=6.0,
        )
    )

    assert failed.code is Cam3DPreviewPublicationCode.BACKEND_FAILURE
    assert failed.identity == first.identity
    assert context.preview_actors == (old_actor,)
    assert backend.get_cam3d_preview_identity() == first.identity
    assert context.source_actor in context.displayed


def test_foreign_owner_generation_and_repeated_clear_are_fail_closed() -> None:
    backend, context = _backend()
    request = _request()
    published = backend.publish_cam3d_preview(_publication(request))
    actor = context.preview_actors[0]
    foreign = _request()

    wrong_clear = backend.clear_cam3d_preview(cam3d_preview_ownership(foreign.ownership))
    wrong_generation = backend.publish_cam3d_preview(
        _publication(
            _request(ownership=request.ownership, generation=5),
            offset=3.0,
        )
    )
    cleared = backend.clear_cam3d_preview(cam3d_preview_ownership(request.ownership))
    repeated = backend.clear_cam3d_preview(cam3d_preview_ownership(request.ownership))

    assert published.succeeded
    assert wrong_clear.code is Cam3DPreviewPublicationCode.OWNERSHIP_MISMATCH
    assert wrong_generation.code is Cam3DPreviewPublicationCode.OWNERSHIP_MISMATCH
    assert actor in context.removed
    assert cleared.code is Cam3DPreviewPublicationCode.CLEARED
    assert repeated.code is Cam3DPreviewPublicationCode.ALREADY_CLEAR
    assert context.preview_actors == ()
    assert context.source_actor in context.displayed


def test_worker_thread_direct_publication_is_rejected_without_scene_mutation() -> None:
    backend, context = _backend()
    request = _request()
    assert backend.publish_cam3d_preview(_publication(request)).succeeded
    before = tuple(context.displayed)
    next_publication = _publication(
        _request(ownership=request.ownership, semantic=4),
        offset=8.0,
    )

    with ThreadPoolExecutor(max_workers=1) as executor:
        outcome = executor.submit(
            backend.publish_cam3d_preview,
            next_publication,
        ).result(timeout=5.0)

    assert outcome.code is Cam3DPreviewPublicationCode.WRONG_THREAD
    assert tuple(context.displayed) == before


def test_replacement_and_clear_rebind_cycles_do_not_grow_actor_count() -> None:
    backend, context = _backend()
    owner_request = _request()
    for semantic in range(30):
        outcome = backend.publish_cam3d_preview(
            _publication(
                _request(ownership=owner_request.ownership, semantic=semantic),
                offset=float(semantic),
            )
        )
        assert outcome.succeeded
        assert len(context.preview_actors) == 1
        assert context.source_actor in context.displayed

    for semantic in range(50, 100):
        cleared = backend.clear_cam3d_preview(cam3d_preview_ownership(owner_request.ownership))
        assert cleared.succeeded
        assert context.preview_actors == ()
        published = backend.publish_cam3d_preview(
            _publication(
                _request(ownership=owner_request.ownership, semantic=semantic),
                offset=float(semantic),
            )
        )
        assert published.succeeded
        assert len(context.preview_actors) == 1
    backend.clear_cam3d_preview(cam3d_preview_ownership(owner_request.ownership))
    assert context.preview_actors == ()
    assert context.source_actor in context.displayed

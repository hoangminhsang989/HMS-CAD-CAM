"""Native-free mapping and viewport publication tests for Stage 12.1."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import inspect
from types import SimpleNamespace

import pytest
import hms_cadcam.viewer.ocp.backend as ocp_backend_module

from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cam.lathe.toolpath import LatheMotionClass
from hms_cadcam.cam.lathe.toolpath import LatheToolpathGeneratorRegistry
from hms_cadcam.ui.lathe_toolpath import (
    LatheViewportPreviewSink,
    lathe_preview_ownership,
    lathe_preview_publication_from_result,
)
from hms_cadcam.viewer.lathe import (
    LathePreviewActorIdentity,
    LathePreviewPublication,
    LathePreviewPublicationCode,
    LathePreviewPublicationResult,
    LathePreviewSegmentData,
)
from hms_cadcam.viewer.models import ViewportStatus
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend
from hms_cadcam.viewer.widget import CadViewportWidget
from tests.unit._lathe_toolpath_fixtures import generate, ready_request, segments


class _LatheViewportBackend:
    def __init__(self) -> None:
        self.initialized = False
        self.closed = False
        self.identity: LathePreviewActorIdentity | None = None
        self.publications: list[LathePreviewPublication] = []
        self.removed: list[LathePreviewActorIdentity] = []
        self.fail_publication = False
        self.fail_clear = False
        self.source_actor = object()
        self.selection = ("cad-face-1",)
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

    def publish_lathe_preview(
        self, publication: LathePreviewPublication
    ) -> LathePreviewPublicationResult:
        if self.fail_publication:
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.BACKEND_FAILURE,
                self.identity,
            )
        previous = self.identity
        self.identity = publication.identity
        self.publications.append(publication)
        if previous is not None:
            self.removed.append(previous)
        return LathePreviewPublicationResult(
            (
                LathePreviewPublicationCode.PUBLISHED
                if previous is None
                else LathePreviewPublicationCode.REPLACED
            ),
            self.identity,
        )

    def clear_lathe_preview(self, ownership) -> LathePreviewPublicationResult:
        if self.identity is None:
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.ALREADY_CLEAR
            )
        if self.identity.ownership != ownership:
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.OWNERSHIP_MISMATCH,
                self.identity,
            )
        if self.fail_clear:
            return LathePreviewPublicationResult(
                LathePreviewPublicationCode.BACKEND_FAILURE,
                self.identity,
            )
        previous = self.identity
        self.identity = None
        self.removed.append(previous)
        return LathePreviewPublicationResult(
            LathePreviewPublicationCode.CLEARED,
            previous,
        )

    def get_lathe_preview_identity(self) -> LathePreviewActorIdentity | None:
        return self.identity

    def close(self) -> None:
        self.identity = None
        self.closed = True
        self.initialized = False


class _OcpLatheContext:
    def __init__(self) -> None:
        self.displayed: set[object] = set()
        self.removed: list[object] = []
        self.deactivated: set[object] = set()
        self.fail_remove: set[object] = set()

    def Display(self, native: object, *_args: object) -> None:
        self.displayed.add(native)

    def SetColor(self, _native: object, _color: object, *_args: object) -> None:
        return None

    def Deactivate(self, native: object) -> None:
        self.deactivated.add(native)

    def Remove(self, native: object, *_args: object) -> None:
        if native in self.fail_remove:
            self.fail_remove.remove(native)
            raise RuntimeError("injected Lathe actor removal failure")
        self.displayed.discard(native)
        self.removed.append(native)

    def UpdateCurrentViewer(self) -> None:
        return None


class _OcpLatheBuilder:
    def MakeCompound(self, _compound: object) -> None:
        return None

    def Add(self, _compound: object, _shape: object) -> None:
        return None


def _ocp_lathe_backend(monkeypatch: pytest.MonkeyPatch):
    context = _OcpLatheContext()
    backend = OcpCadViewportBackend(OcpCadKernel())
    backend._lifecycle = SimpleNamespace(initialized=True, context=context)
    monkeypatch.setattr(ocp_backend_module, "TopoDS_Compound", object)
    monkeypatch.setattr(ocp_backend_module, "BRep_Builder", _OcpLatheBuilder)
    monkeypatch.setattr(
        ocp_backend_module,
        "BRepBuilderAPI_MakeEdge",
        lambda *_args: SimpleNamespace(Edge=lambda: object()),
    )
    monkeypatch.setattr(ocp_backend_module, "AIS_Shape", lambda _shape: object())
    monkeypatch.setattr(
        ocp_backend_module,
        "Quantity_Color",
        lambda *_args: object(),
    )
    return backend, context


def _widget(qtbot):
    backend = _LatheViewportBackend()
    widget = CadViewportWidget(OcpCadKernel(), backend)
    qtbot.addWidget(widget)
    return widget, backend


def test_result_maps_diameter_x_to_radius_on_fixed_xz_plane() -> None:
    result = generate(ready_request()[2])
    publication = lathe_preview_publication_from_result(result)
    domain_segments = segments(result)
    assert len(publication.segments) == len(domain_segments)
    for domain, display in zip(domain_segments, publication.segments, strict=True):
        assert display.start == (
            domain.start.x_diameter_mm / 2.0,
            0.0,
            domain.start.z_mm,
        )
        assert display.end == (
            domain.end.x_diameter_mm / 2.0,
            0.0,
            domain.end.z_mm,
        )
        assert display.motion_class is domain.motion_class
    assert publication.identity.ownership == lathe_preview_ownership(
        result.identity.ownership
    )
    assert publication.identity.request_fingerprint == result.identity.fingerprint.digest
    assert publication.identity.cache_key == result.cache_key.digest


def test_only_successful_typed_result_can_map_to_publication() -> None:
    request = ready_request()[2]
    result = generate(request)
    with pytest.raises(TypeError, match="result"):
        lathe_preview_publication_from_result(object())  # type: ignore[arg-type]
    cancelled = LatheToolpathGeneratorRegistry().generate(request, lambda: True)
    with pytest.raises(ValueError, match="successful"):
        lathe_preview_publication_from_result(cancelled)


@pytest.mark.parametrize(
    "point",
    (
        (0, 0.0, 0.0),
        (0.0, True, 0.0),
        (0.0, 0.0, float("nan")),
        (0.0, 0.0, float("inf")),
    ),
)
def test_publication_segment_rejects_untyped_or_non_finite_points(point) -> None:
    with pytest.raises((TypeError, ValueError)):
        LathePreviewSegmentData(
            0,
            LatheMotionClass.CUTTING,
            point,
            (1.0, 0.0, 0.0),
            "cut",
        )


def test_publication_contract_requires_unique_sorted_nonzero_segments() -> None:
    result = generate(ready_request()[2])
    publication = lathe_preview_publication_from_result(result)
    first = publication.segments[0]
    with pytest.raises(ValueError, match="zero-length"):
        replace(first, end=first.start)
    with pytest.raises(ValueError, match="deterministic"):
        replace(publication, segments=(publication.segments[1], first))


def test_typed_publication_outcomes_never_turn_failure_into_success() -> None:
    success = {
        LathePreviewPublicationCode.PUBLISHED,
        LathePreviewPublicationCode.REPLACED,
        LathePreviewPublicationCode.CLEARED,
        LathePreviewPublicationCode.ALREADY_CLEAR,
    }
    for code in LathePreviewPublicationCode:
        outcome = LathePreviewPublicationResult(code)
        assert outcome.succeeded is (code in success)
        assert bool(outcome) is outcome.succeeded


def test_widget_first_publication_atomic_replacement_and_exact_owner_clear(qtbot) -> None:
    widget, backend = _widget(qtbot)
    source_actor = backend.source_actor
    selection = backend.selection
    first_result = generate(ready_request(operation_index=1)[2])
    second_result = generate(
        ready_request(operation_index=2, parameters={"feed_mm_per_rev": 0.31})[2]
    )
    first = widget.publish_lathe_preview(
        lathe_preview_publication_from_result(first_result)
    )
    second = widget.publish_lathe_preview(
        lathe_preview_publication_from_result(second_result)
    )
    assert first.code is LathePreviewPublicationCode.PUBLISHED
    assert second.code is LathePreviewPublicationCode.REPLACED
    assert backend.identity == second.identity
    assert backend.removed == [first.identity]
    assert backend.source_actor is source_actor
    assert backend.selection == selection

    old_owner = lathe_preview_ownership(first_result.identity.ownership)
    rejected = widget.clear_lathe_preview(old_owner)
    assert rejected.code is LathePreviewPublicationCode.OWNERSHIP_MISMATCH
    assert backend.identity == second.identity
    current_owner = lathe_preview_ownership(second_result.identity.ownership)
    cleared = widget.clear_lathe_preview(current_owner)
    assert cleared.code is LathePreviewPublicationCode.CLEARED
    assert backend.identity is None
    assert widget.clear_lathe_preview(current_owner).code is (
        LathePreviewPublicationCode.ALREADY_CLEAR
    )
    widget.shutdown()


def test_failed_replacement_and_clear_preserve_old_actor_source_and_selection(qtbot) -> None:
    widget, backend = _widget(qtbot)
    first = generate(ready_request()[2])
    publication = lathe_preview_publication_from_result(first)
    assert widget.publish_lathe_preview(publication).succeeded
    source_actor = backend.source_actor
    selection = backend.selection
    backend.fail_publication = True
    replacement = lathe_preview_publication_from_result(
        generate(ready_request(parameters={"feed_mm_per_rev": 0.31})[2])
    )
    failed = widget.publish_lathe_preview(replacement)
    assert failed.code is LathePreviewPublicationCode.BACKEND_FAILURE
    assert backend.identity == publication.identity
    backend.fail_clear = True
    failed_clear = widget.clear_lathe_preview(publication.identity.ownership)
    assert failed_clear.code is LathePreviewPublicationCode.BACKEND_FAILURE
    assert backend.identity == publication.identity
    assert backend.source_actor is source_actor and backend.selection == selection
    widget.shutdown()


def test_widget_rejects_worker_thread_without_touching_current_preview(qtbot) -> None:
    widget, backend = _widget(qtbot)
    publication = lathe_preview_publication_from_result(
        generate(ready_request()[2])
    )
    assert widget.publish_lathe_preview(publication).succeeded
    before = tuple(backend.publications)
    with ThreadPoolExecutor(max_workers=1) as executor:
        outcome = executor.submit(widget.publish_lathe_preview, publication).result(
            timeout=5.0
        )
    assert outcome.code is LathePreviewPublicationCode.WRONG_THREAD
    assert tuple(backend.publications) == before
    widget.shutdown()


def test_real_sink_uses_typed_outcome_and_never_reports_fake_success(qtbot) -> None:
    widget, backend = _widget(qtbot)
    sink = LatheViewportPreviewSink(widget)
    result = generate(ready_request()[2])
    backend.fail_publication = True
    assert not sink.publish(result)
    assert backend.identity is None
    assert "getattr" not in inspect.getsource(LatheViewportPreviewSink.publish)
    widget.shutdown()


def test_unavailable_backend_fails_closed_and_clear_is_honestly_already_clear() -> None:
    backend = UnavailableCadViewportBackend("stage12.1 test")
    publication = lathe_preview_publication_from_result(
        generate(ready_request()[2])
    )
    assert backend.publish_lathe_preview(publication).code is (
        LathePreviewPublicationCode.UNAVAILABLE
    )
    assert backend.clear_lathe_preview(publication.identity.ownership).code is (
        LathePreviewPublicationCode.ALREADY_CLEAR
    )
    assert backend.get_lathe_preview_identity() is None


def test_ocp_missing_optional_lathe_state_is_idempotently_empty() -> None:
    backend = object.__new__(OcpCadViewportBackend)
    source_actor = object()
    selection_actor = object()
    backend._source_actor = source_actor
    backend._selection_actor = selection_actor

    for _index in range(100):
        backend._clear_lathe_preview_unconditionally()
    publication = lathe_preview_publication_from_result(
        generate(ready_request()[2])
    )
    assert backend.clear_lathe_preview(publication.identity.ownership).code is (
        LathePreviewPublicationCode.ALREADY_CLEAR
    )
    assert backend.get_lathe_preview_identity() is None
    assert not hasattr(backend, "_lathe_preview_actor")
    assert backend._source_actor is source_actor
    assert backend._selection_actor is selection_actor


def test_ocp_partial_fixture_clear_and_teardown_remain_safe() -> None:
    context = _OcpLatheContext()
    backend = object.__new__(OcpCadViewportBackend)
    backend._lifecycle = SimpleNamespace(
        initialized=True,
        context=context,
        clear=lambda: None,
        close=lambda: None,
    )
    backend._toolpaths = {}
    backend._toolpath_metadata = {}
    backend._closed = False
    backend._selection = None
    backend._input = None
    backend._selection_callback = lambda _items: None
    backend._document_id = None
    backend._tree = None
    backend._selected_object_ids = ()

    for _index in range(30):
        backend._clear_lathe_preview_unconditionally()
    backend.clear()
    backend.close()
    backend.close()
    assert backend._closed
    assert context.removed == []
    assert not hasattr(backend, "_lathe_preview_actor")


def test_ocp_normal_lathe_replace_and_exact_owner_clear_preserve_cad_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_lathe_backend(monkeypatch)
    source_actor = object()
    selection_actor = object()
    backend._source_actor = source_actor
    backend._selection_actor = selection_actor
    first = lathe_preview_publication_from_result(
        generate(ready_request(operation_index=31)[2])
    )
    second = lathe_preview_publication_from_result(
        generate(
            ready_request(
                operation_index=32,
                parameters={"feed_mm_per_rev": 0.31},
            )[2]
        )
    )

    first_result = backend.publish_lathe_preview(first)
    first_actor = backend._lathe_preview_actor
    assert first_actor is not None
    first_natives = first_actor.natives
    second_result = backend.publish_lathe_preview(second)
    second_actor = backend._lathe_preview_actor
    assert second_actor is not None
    second_natives = second_actor.natives
    assert first_result.code is LathePreviewPublicationCode.PUBLISHED
    assert second_result.code is LathePreviewPublicationCode.REPLACED
    assert all(native in context.removed for native in first_natives)
    assert all(native in context.displayed for native in second_natives)
    removed_before_mismatch = tuple(context.removed)
    assert backend.clear_lathe_preview(first.identity.ownership).code is (
        LathePreviewPublicationCode.OWNERSHIP_MISMATCH
    )
    assert tuple(context.removed) == removed_before_mismatch
    assert backend.clear_lathe_preview(second.identity.ownership).code is (
        LathePreviewPublicationCode.CLEARED
    )
    assert backend.clear_lathe_preview(second.identity.ownership).code is (
        LathePreviewPublicationCode.ALREADY_CLEAR
    )
    assert backend._source_actor is source_actor
    assert backend._selection_actor is selection_actor


def test_ocp_lathe_removal_failure_is_typed_and_unconditional_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend, context = _ocp_lathe_backend(monkeypatch)
    publication = lathe_preview_publication_from_result(
        generate(ready_request(operation_index=33)[2])
    )
    assert backend.publish_lathe_preview(publication).succeeded
    actor = backend._lathe_preview_actor
    assert actor is not None
    context.fail_remove.add(actor.natives[0])
    outcome = backend.clear_lathe_preview(publication.identity.ownership)
    assert outcome.code is LathePreviewPublicationCode.BACKEND_FAILURE
    assert backend.get_lathe_preview_identity() == publication.identity

    context.fail_remove.add(actor.natives[0])
    with pytest.raises(RuntimeError, match="injected Lathe actor removal failure"):
        backend._clear_lathe_preview_unconditionally()
    assert backend.get_lathe_preview_identity() == publication.identity


def test_ocp_partial_backend_publication_fails_closed_without_fake_success() -> None:
    publication = lathe_preview_publication_from_result(
        generate(ready_request(operation_index=34)[2])
    )
    missing_lifecycle = object.__new__(OcpCadViewportBackend)
    missing_context = object.__new__(OcpCadViewportBackend)
    missing_context._lifecycle = SimpleNamespace(initialized=True)

    for backend in (missing_lifecycle, missing_context):
        outcome = backend.publish_lathe_preview(publication)
        assert outcome.code is LathePreviewPublicationCode.NOT_INITIALIZED
        assert not outcome.succeeded
        assert backend.get_lathe_preview_identity() is None
        assert not hasattr(backend, "_lathe_preview_actor")

def test_ocp_publication_source_has_grouped_colors_swap_rollback_and_no_scene_clear() -> None:
    source = inspect.getsource(OcpCadViewportBackend)
    module_source = inspect.getsource(ocp_backend_module)
    assert "BRepBuilderAPI_MakeEdge" in source
    assert "AIS_Shape(compound)" in source
    assert "LatheMotionClass.RAPID: (1.0, 0.0, 0.0)" in module_source
    assert "LatheMotionClass.CUTTING: (1.0, 1.0, 0.0)" in module_source
    assert "LatheMotionClass.LEAD_IN: (1.0, 1.0, 1.0)" in module_source
    assert "LatheMotionClass.LEAD_OUT: (0.0, 1.0, 0.0)" in module_source
    publish = inspect.getsource(OcpCadViewportBackend.publish_lathe_preview)
    clear = inspect.getsource(OcpCadViewportBackend.clear_lathe_preview)
    assert publish.index("_build_lathe_preview_actor") < publish.index(
        "self._lathe_preview_actor = candidate"
    ) < publish.index("_remove_lathe_preview_actor(previous)")
    assert "_rollback_lathe_preview_swap" in publish
    assert "_lathe_preview_actor_or_none" in publish + clear
    assert "actor.identity.ownership != ownership" in clear
    assert "_restore_lathe_preview_actor" in clear
    forbidden = ("self.clear()", "fit_all", "clear_selection", "display_document")
    assert not any(token in publish + clear for token in forbidden)

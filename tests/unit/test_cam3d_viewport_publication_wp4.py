"""Pure publication-contract tests for Stage 9A.8 WP4."""

from __future__ import annotations

from dataclasses import fields, is_dataclass

import pytest

from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DPreviewDiagnostic,
    Cam3DPreviewDiagnosticCode,
    Cam3DPreviewResult,
    Cam3DPreviewSource,
)
from hms_cadcam.ui.cam3d_viewport import (
    cam3d_preview_ownership,
    cam3d_preview_publication_from_result,
)
from hms_cadcam.viewer.cam3d import (
    Cam3DPreviewMeshData,
    Cam3DPreviewPublication,
    Cam3DPreviewPublicationCode,
    Cam3DPreviewPublicationResult,
)
from tests.unit.test_cam3d_preview_worker_wp3b import _mesh, _request


def _assert_native_free(value: object) -> None:
    module = type(value).__module__
    assert not module.startswith(("OCP", "PySide6", "shiboken6"))
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _assert_native_free(getattr(value, field.name))
    elif isinstance(value, (tuple, list, set, frozenset, dict)):
        values = value.values() if isinstance(value, dict) else value
        for item in values:
            _assert_native_free(item)


def test_success_result_maps_to_immutable_native_free_publication() -> None:
    request = _request()
    result = Cam3DPreviewResult.success(
        request,
        _mesh(),
        source=Cam3DPreviewSource.WORKER,
    )

    publication = cam3d_preview_publication_from_result(result)

    assert publication.mesh.vertices is result.mesh.vertices
    assert publication.identity.job_id == str(result.identity.job_id)
    assert publication.identity.request_fingerprint == request.fingerprint.digest
    assert publication.identity.cache_key == request.cache_key.digest
    assert publication.identity.ownership == cam3d_preview_ownership(request.ownership)
    assert publication.identity.project_generation == request.project_generation
    _assert_native_free(publication)


def test_failure_cancelled_raw_and_untyped_payloads_cannot_publish() -> None:
    request = _request()
    failed = Cam3DPreviewResult.failure(
        request,
        Cam3DPreviewDiagnostic(Cam3DPreviewDiagnosticCode.MESH_INVALID),
    )
    cancelled = Cam3DPreviewResult.cancelled(request)

    with pytest.raises(ValueError, match="successful"):
        cam3d_preview_publication_from_result(failed)
    with pytest.raises(ValueError, match="successful"):
        cam3d_preview_publication_from_result(cancelled)
    with pytest.raises(TypeError, match="result"):
        cam3d_preview_publication_from_result(object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="mesh"):
        Cam3DPreviewPublication(
            cam3d_preview_publication_from_result(
                Cam3DPreviewResult.success(
                    request,
                    _mesh(),
                    source=Cam3DPreviewSource.CACHE,
                )
            ).identity,
            object(),  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "factory",
    (
        lambda: Cam3DPreviewMeshData(
            (),
            (),
            (),
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
        ),
        lambda: Cam3DPreviewMeshData(
            ((0.0, 0.0, float("nan")), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0, 1, 2),),
            ((0.0, 0.0, 1.0),),
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
        ),
        lambda: Cam3DPreviewMeshData(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0, 1, 2),),
            ((float("inf"), 0.0, 1.0),),
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
        ),
        lambda: Cam3DPreviewMeshData(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((-1, 1, 2),),
            ((0.0, 0.0, 1.0),),
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
        ),
        lambda: Cam3DPreviewMeshData(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0, 1, 3),),
            ((0.0, 0.0, 1.0),),
            (0.0, 0.0, 0.0, 1.0, 1.0, 0.0),
        ),
        lambda: Cam3DPreviewMeshData(
            ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            ((0, 1, 2),),
            ((0.0, 0.0, 1.0),),
            (0.0, 0.0, 0.0, 2.0, 1.0, 0.0),
        ),
    ),
)
def test_invalid_mesh_fails_before_backend_publication(factory) -> None:
    with pytest.raises(ValueError):
        factory()


def test_typed_outcomes_never_turn_failure_into_success() -> None:
    successful = {
        Cam3DPreviewPublicationCode.PUBLISHED,
        Cam3DPreviewPublicationCode.REPLACED,
        Cam3DPreviewPublicationCode.CLEARED,
        Cam3DPreviewPublicationCode.ALREADY_CLEAR,
    }
    for code in Cam3DPreviewPublicationCode:
        outcome = Cam3DPreviewPublicationResult(code)
        assert outcome.succeeded is (code in successful)
        assert bool(outcome) is outcome.succeeded

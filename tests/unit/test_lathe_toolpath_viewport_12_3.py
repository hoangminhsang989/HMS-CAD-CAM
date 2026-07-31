"""Stage 12.3 OD/ID thread viewport publication mapping tests."""

from __future__ import annotations

import pytest

from hms_cadcam.cam.lathe.toolpath import LatheMotionClass
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.lathe_toolpath import lathe_preview_publication_from_result
from hms_cadcam.viewer.lathe import LathePreviewPublicationSource
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    ready_request,
    stock_snapshot,
)


@pytest.mark.parametrize(
    ("strategy_id", "stock"),
    (
        (LatheStrategyId.OD_THREAD, stock_snapshot()),
        (LatheStrategyId.ID_THREAD, stock_snapshot(inner_diameter_mm=10.0)),
    ),
)
def test_thread_publication_maps_diameter_to_radius_on_existing_xz_plane(
    strategy_id: LatheStrategyId,
    stock,
) -> None:
    request = ready_request(
        strategy_id,
        parameters={"pass_count": 2, "spring_passes": 1},
        stock=stock,
    )[2]
    result = generate(request)
    publication = lathe_preview_publication_from_result(result)
    assert publication.identity.ownership.operation_id == request.ownership.operation_id
    assert publication.identity.source is LathePreviewPublicationSource.WORKER
    assert len(publication.segments) == len(result.motions)
    for rendered, semantic in zip(publication.segments, result.motions, strict=True):
        assert rendered.start == (
            semantic.start.x_diameter_mm / 2.0,
            0.0,
            semantic.start.z_mm,
        )
        assert rendered.end == (
            semantic.end.x_diameter_mm / 2.0,
            0.0,
            semantic.end.z_mm,
        )
        assert rendered.motion_class is semantic.motion_class
    assert {item.motion_class for item in publication.segments} == {
        LatheMotionClass.RAPID,
        LatheMotionClass.LEAD_IN,
        LatheMotionClass.CUTTING,
        LatheMotionClass.LEAD_OUT,
    }


def test_opposite_hands_publish_the_same_phase_neutral_xz_geometry() -> None:
    right = ready_request(
        LatheStrategyId.OD_THREAD,
        stock=stock_snapshot(),
    )[2]
    from hms_cadcam.cam.lathe.types import LatheThreadHand

    left = ready_request(
        LatheStrategyId.OD_THREAD,
        parameters={"thread_hand": LatheThreadHand.LEFT},
        stock=stock_snapshot(),
    )[2]
    right_publication = lathe_preview_publication_from_result(generate(right))
    left_publication = lathe_preview_publication_from_result(generate(left))
    assert right_publication.segments == left_publication.segments
    assert (
        right_publication.identity.request_fingerprint
        != left_publication.identity.request_fingerprint
    )

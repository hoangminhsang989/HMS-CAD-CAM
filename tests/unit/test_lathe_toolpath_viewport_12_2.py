"""Stage 12.2 viewport publication coverage for all six new strategies."""

from __future__ import annotations

import inspect

import pytest

import hms_cadcam.viewer.ocp.backend as ocp_backend_module
from hms_cadcam.cam.lathe.toolpath import LatheMotionClass
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.lathe_toolpath import lathe_preview_publication_from_result
from hms_cadcam.viewer.ocp.backend import OcpCadViewportBackend
from tests.unit._lathe_toolpath_fixtures import (
    generate,
    ready_request,
    segments,
    stock_snapshot,
)


NEW_STRATEGIES = (
    LatheStrategyId.FACE,
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
    LatheStrategyId.OD_GROOVE,
    LatheStrategyId.ID_GROOVE,
    LatheStrategyId.PART_OFF,
)


def _request(strategy_id: LatheStrategyId):
    parameters = {}
    stock = stock_snapshot()
    if strategy_id is LatheStrategyId.FACE:
        parameters = {
            "face_z_mm": -2.0,
            "outer_diameter_mm": 80.0,
            "max_depth_of_cut_mm": 0.75,
            "finish_allowance_mm": 0.25,
        }
    elif strategy_id in {
        LatheStrategyId.ID_ROUGH,
        LatheStrategyId.ID_FINISH,
        LatheStrategyId.ID_GROOVE,
    }:
        stock = stock_snapshot(inner_diameter_mm=10.0)
    elif strategy_id is LatheStrategyId.PART_OFF:
        parameters = {"max_step_mm": 10.0}
    return ready_request(strategy_id, parameters=parameters, stock=stock)[2]


@pytest.mark.parametrize("strategy_id", NEW_STRATEGIES)
def test_six_new_results_publish_with_diameter_to_radius_mapping(
    strategy_id: LatheStrategyId,
) -> None:
    result = generate(_request(strategy_id))
    publication = lathe_preview_publication_from_result(result)
    domain = segments(result)
    assert len(publication.segments) == len(domain)
    for segment, mapped in zip(domain, publication.segments, strict=True):
        assert mapped.start == (
            segment.start.x_diameter_mm / 2.0,
            0.0,
            segment.start.z_mm,
        )
        assert mapped.end == (
            segment.end.x_diameter_mm / 2.0,
            0.0,
            segment.end.z_mm,
        )
        assert mapped.motion_class is segment.motion_class
    assert {
        item.motion_class for item in publication.segments
    } == {
        LatheMotionClass.RAPID,
        LatheMotionClass.CUTTING,
        LatheMotionClass.LEAD_IN,
        LatheMotionClass.LEAD_OUT,
    }


def test_part_off_centerline_endpoint_publishes_at_display_radius_zero() -> None:
    result = generate(_request(LatheStrategyId.PART_OFF))
    publication = lathe_preview_publication_from_result(result)
    assert any(
        item.motion_class is LatheMotionClass.CUTTING
        and item.end[0] == 0.0
        for item in publication.segments
    )
    assert all(
        coordinate >= 0.0
        for item in publication.segments
        for coordinate in (item.start[0], item.end[0])
    )


def test_stage12_2_reuses_one_grouped_ocp_actor_and_exact_four_colors() -> None:
    source = inspect.getsource(OcpCadViewportBackend.publish_lathe_preview)
    module_source = inspect.getsource(ocp_backend_module)
    assert source.count("_build_lathe_preview_actor") == 1
    assert "_rollback_lathe_preview_swap" in source
    assert "LatheMotionClass.RAPID: (1.0, 0.0, 0.0)" in module_source
    assert "LatheMotionClass.CUTTING: (1.0, 1.0, 0.0)" in module_source
    assert "LatheMotionClass.LEAD_IN: (1.0, 1.0, 1.0)" in module_source
    assert "LatheMotionClass.LEAD_OUT: (0.0, 1.0, 0.0)" in module_source
    assert "self.clear()" not in source

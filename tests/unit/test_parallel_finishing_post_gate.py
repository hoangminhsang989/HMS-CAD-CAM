"""Post remains fail-closed for the non-production Parallel foundation."""

from __future__ import annotations

from dataclasses import replace

import pytest

from hms_cadcam.cam.cam3d.parallel import ParallelFinishingParameters
from hms_cadcam.cam.domain import CamValidationError
from hms_cadcam.cam.post import (
    PostRequest,
    SimulationGateMode,
    SimulationGatePolicy,
    canonical_definition,
    lower_toolpath,
)
from tests.unit._parallel_finishing_fixtures import planar_fixture
from tests.unit._post_fixtures import source_snapshot


def test_parallel_foundation_is_rejected_by_existing_post_capability_gate() -> None:
    source = source_snapshot()
    parameters = ParallelFinishingParameters(
        planar_fixture().zone.zone_id,
        2.0,
    ).to_operation_parameters()
    source = replace(
        source,
        operation=replace(source.operation, parameters=parameters),
    )
    request = PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        canonical_definition(),
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
    )
    with pytest.raises(
        CamValidationError,
        match="Operation strategy is unsupported by post definition",
    ):
        lower_toolpath(request, source)

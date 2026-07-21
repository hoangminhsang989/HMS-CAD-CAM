"""Explicit, threshold-free benchmarks for stable HMS code paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from hms_cadcam.cam.domain import CamJobId
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.operation_manager_projection import OperationManagerProjectionBuilder

pytestmark = pytest.mark.benchmark


def test_cam_identity_codec_benchmark(benchmark) -> None:
    encoded = str(CamJobId.new())

    decoded = benchmark(CamJobId.parse, encoded)

    assert str(decoded) == encoded


def test_operation_manager_projection_benchmark(
    benchmark,
    tmp_path: Path,
) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Operation Manager Benchmark")
    builder = OperationManagerProjectionBuilder()

    projection = benchmark(builder.build, service, session)

    assert projection.project_id == session.manifest.project_id
    service.close_project()

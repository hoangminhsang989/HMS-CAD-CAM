"""Measure normal HMS paths without importing the optional R241 package."""

from __future__ import annotations

import argparse
import gc
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _measure(callable_, count: int) -> dict[str, object]:
    callable_()
    values: list[float] = []
    for _index in range(count):
        gc.collect()
        started = time.perf_counter_ns()
        callable_()
        values.append((time.perf_counter_ns() - started) / 1_000_000)
    return {
        "count": count,
        "samples_ms": values,
        "minimum_ms": min(values),
        "median_ms": statistics.median(values),
        "mean_ms": statistics.mean(values),
        "stdev_ms": statistics.stdev(values) if count > 1 else 0.0,
    }


def _startup_samples(count: int, environment: dict[str, str]) -> dict[str, object]:
    code = (
        "import json,time,sys; s=time.perf_counter_ns(); "
        "import hms_cadcam.application; "
        "print(json.dumps({'ms':(time.perf_counter_ns()-s)/1e6,"
        "'r241_loaded':any(n=='hms_cadcam.simulation' or "
        "n.startswith('hms_cadcam.simulation.') for n in sys.modules)}))"
    )
    values: list[float] = []
    loaded: list[bool] = []
    for _index in range(count):
        completed = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        result = json.loads(completed.stdout)
        values.append(result["ms"])
        loaded.append(result["r241_loaded"])
    return {
        "count": count,
        "samples_ms": values,
        "minimum_ms": min(values),
        "median_ms": statistics.median(values),
        "mean_ms": statistics.mean(values),
        "stdev_ms": statistics.stdev(values) if count > 1 else 0.0,
        "r241_package_loaded": loaded,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--revision", required=True)
    arguments = parser.parse_args()
    from hms_cadcam.project.service import ProjectService
    from hms_cadcam.cam.post import (
        NCExportService,
        PostRequest,
        PostRuntimeService,
        SimulationGateMode,
        SimulationGatePolicy,
        canonical_definition,
    )
    from hms_cadcam.ui.operation_manager_projection import OperationManagerProjectionBuilder
    from tests.unit._export_fixtures import production_export_fixture
    from tests.unit._post_fixtures import source_snapshot
    from tests.unit.test_cam_facing import _inputs

    root = Path(tempfile.mkdtemp(prefix="r241-normal-", dir="E:/R241_EVIDENCE"))
    service = ProjectService.create_default(root / "config")
    session = service.new_project(root, "Normal Path Benchmark")
    service.save()
    project_root = session.root_path
    service.close_project()
    project_open = _measure(
        lambda: (service.open_project(project_root), service.close_project()), 7
    )
    service.open_project(project_root)
    projection = _measure(
        lambda: OperationManagerProjectionBuilder().build(service.cam_snapshot, None), 9
    )

    def calculate() -> None:
        generator, inputs = _inputs()
        computing, _token = generator.begin(inputs)
        generator.generate(computing)

    calculate_result = _measure(calculate, 9)
    source = source_snapshot()
    post_request = PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        canonical_definition(),
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
    )
    post = _measure(lambda: PostRuntimeService().post(post_request, source), 9)
    export_index = 0

    def export() -> None:
        nonlocal export_index
        export_index += 1
        export_root = root / f"export-{export_index}.HMS"
        request, snapshot = production_export_fixture(export_root)
        result = NCExportService().export(export_root, request, snapshot)
        if not result.accepted:
            raise RuntimeError("NC export benchmark was rejected")

    nc_export = _measure(export, 7)
    environment = dict(os.environ)
    report = {
        "format": "HMS_R241_NORMAL_PATH_PERFORMANCE",
        "format_version": 1,
        "revision": arguments.revision,
        "startup_import": _startup_samples(7, environment),
        "project_open_close": project_open,
        "operation_projection": projection,
        "representative_calculate": calculate_result,
        "post": post,
        "nc_export": nc_export,
        "normal_path_r241_invocation_count": 0,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(arguments.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

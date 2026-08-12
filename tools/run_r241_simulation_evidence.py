"""Generate deterministic external R241 simulation evidence.

This harness uses existing analytic HMS fixtures.  It never reads or writes a
production Post, NC file, or CNC endpoint.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from dataclasses import replace
from pathlib import Path

from hms_cadcam.cam.domain import ContentFingerprint, FeedRate, FeedUnit, Point3, Vector3
from hms_cadcam.cam.simulation.runtime import SimulationInputSnapshot
from hms_cadcam.cam.toolpath import Pose, ToolpathBuilder
from hms_cadcam.simulation import (
    HeightField3AxisEngine,
    IncrementalJobSimulator,
    OperationCoverage,
    QualityMode,
)
from tests.unit.test_simulation_service import _source


def _timed(callable_, *, count: int = 5) -> dict[str, object]:
    values: list[float] = []
    result = None
    for _index in range(count):
        started = time.perf_counter()
        result = callable_()
        values.append(time.perf_counter() - started)
    return {
        "count": count,
        "samples_seconds": values,
        "minimum_seconds": min(values),
        "median_seconds": statistics.median(values),
        "mean_seconds": statistics.mean(values),
        "stdev_seconds": statistics.stdev(values) if count > 1 else 0.0,
        "result": result,
    }


def _snapshot() -> SimulationInputSnapshot:
    operation, source_artifact, setup, tool, holder, assembly, request, _scene = _source()
    builder = ToolpathBuilder(
        artifact_id=type(source_artifact.artifact_id).new(),
        operation_id=operation.operation_id,
        operation_revision=operation.revision,
        computation_token=source_artifact.computation_token,
        input_fingerprint=source_artifact.input_fingerprint,
        unit=source_artifact.unit,
        setup_id=source_artifact.setup_id,
        setup_revision=source_artifact.setup_revision,
        wcs_fingerprint=source_artifact.wcs_fingerprint,
        tool_assembly_id=source_artifact.tool_assembly_id,
        tool_assembly_fingerprint=source_artifact.tool_assembly_fingerprint,
    )
    axis = Vector3(0, 0, 1)
    builder.set_initial_pose(Pose(Point3(2, 2, 15, source_artifact.unit), axis))
    builder.rapid_to(
        Pose(Point3(2, 2, 5, source_artifact.unit), axis),
        rapid_rate=FeedRate(1000, FeedUnit.MM_PER_MINUTE),
    )
    builder.linear_to(
        Pose(Point3(18, 2, 5, source_artifact.unit), axis),
        feed_rate=FeedRate(300, FeedUnit.MM_PER_MINUTE),
    )
    artifact = builder.finalize()
    request = replace(
        request,
        request_id=type(request.request_id).new(),
        artifact_id=artifact.artifact_id,
        artifact_fingerprint=artifact.artifact_fingerprint,
    )
    return SimulationInputSnapshot(
        operation,
        artifact,
        setup,
        tool,
        assembly,
        holder,
        None,
        request,
    )


def _second(inputs: SimulationInputSnapshot) -> SimulationInputSnapshot:
    operation = replace(inputs.operation, operation_id=type(inputs.operation.operation_id).new())
    artifact = replace(
        inputs.artifact,
        artifact_id=type(inputs.artifact.artifact_id).new(),
        source_operation_id=operation.operation_id,
        events=tuple(
            replace(event, source_operation_id=operation.operation_id)
            for event in inputs.artifact.events
        ),
        artifact_fingerprint=None,
    )
    request = replace(
        inputs.request,
        request_id=type(inputs.request.request_id).new(),
        operation_id=operation.operation_id,
        artifact_id=artifact.artifact_id,
        artifact_fingerprint=artifact.artifact_fingerprint,
    )
    return replace(inputs, operation=operation, artifact=artifact, request=request)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    inputs = _snapshot()
    engine = HeightField3AxisEngine()
    first = _timed(lambda: engine.simulate(
        stock=inputs.setup.stock,
        artifact=inputs.artifact,
        tool=inputs.tool,
        quality=QualityMode.STANDARD,
    ))
    material = first.pop("result")
    runner = IncrementalJobSimulator()
    second = _second(inputs)
    initial_started = time.perf_counter()
    initial = runner.run(
        (inputs, second),
        quality=QualityMode.FAST,
        coverage=OperationCoverage.COMPLETE_JOB,
    )
    initial_duration = time.perf_counter() - initial_started
    cache_started = time.perf_counter()
    cached = runner.run(
        (inputs, second),
        quality=QualityMode.FAST,
        coverage=OperationCoverage.COMPLETE_JOB,
    )
    cache_duration = time.perf_counter() - cache_started
    edited_artifact = replace(
        second.artifact,
        artifact_fingerprint=None,
        events=(
            replace(
                second.artifact.events[0],
                metadata=(*second.artifact.events[0].metadata, ("r241_edit", "true")),
            ),
            *second.artifact.events[1:],
        ),
    )
    edited_request = replace(
        second.request,
        artifact_fingerprint=edited_artifact.artifact_fingerprint,
    )
    incremental_started = time.perf_counter()
    incremental = runner.run(
        (inputs, replace(second, artifact=edited_artifact, request=edited_request)),
        quality=QualityMode.FAST,
        coverage=OperationCoverage.COMPLETE_JOB,
    )
    incremental_duration = time.perf_counter() - incremental_started
    report = {
        "format": "HMS_R241_SIMULATION_EVIDENCE",
        "format_version": 1,
        "engine": "HEIGHTFIELD_3AXIS",
        "engine_version": "r241.1",
        "quality": "STANDARD",
        "accuracy": "bounded fixed-axis top-down height-field approximation",
        "scene": "analytic HMS multi-pass box-stock facing fixture",
        "material_removal": first,
        "sampled_points": len(material.sampled.samples),
        "removed_volume": material.remaining_stock.removed_volume,
        "remaining_volume": material.remaining_stock.remaining_volume,
        "first_two_operation_seconds": initial_duration,
        "cache_hit_reopen_seconds": cache_duration,
        "cache_hit_flags": [value.cache_hit for value in cached.timings],
        "incremental_recalculation_seconds": incremental_duration,
        "incremental_cache_hit_flags": [value.cache_hit for value in incremental.timings],
        "material_computations": runner.material_computations,
        "normal_path_invocation_count": 0,
        "physical_qualification": "NOT_PERFORMED",
        "cnc_action": "NONE",
        "result_fingerprint": ContentFingerprint.from_payload({
            "remaining": list(initial.remaining_stock.top_heights),
            "removed": initial.remaining_stock.removed_volume,
        }).to_dict(),
    }
    report_path = output / "simulation_evidence.json"
    encoded = json.dumps(report, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    report_path.write_bytes(encoded)
    manifest = {
        "format": "HMS_R241_EVIDENCE_MANIFEST",
        "format_version": 1,
        "files": [{
            "path": report_path.name,
            "size": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        }],
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

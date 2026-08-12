"""R251 exact-baseline production CAM benchmark and semantic evidence harness."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import sys
import threading
import time
from time import perf_counter_ns
from uuid import UUID, uuid5


NAMESPACE = UUID("4c07e970-c8ab-5c66-b01e-f732e04e75f9")


def _install_source(source_root: Path) -> None:
    sys.path.insert(0, str(source_root))
    sys.path.insert(0, str(source_root.parent))


def _deterministic_ids(label: str) -> None:
    from hms_cadcam.cam.domain import ids

    counter = 0

    def next_uuid() -> UUID:
        nonlocal counter
        counter += 1
        return uuid5(NAMESPACE, f"{label}:{counter}")

    ids.uuid4 = next_uuid


def _semantic_payload(artifact) -> dict[str, object]:
    from hms_cadcam.cam.toolpath.codec import diagnostic_to_dict, event_to_dict

    events = []
    for item in artifact.events:
        event = event_to_dict(item)
        event.pop("event_id")
        events.append(event)
    return {
        "format": "HMS_R250_SEMANTIC_TOOLPATH",
        "format_version": 1,
        "source_operation_id": str(artifact.source_operation_id),
        "operation_revision": artifact.operation_revision.to_dict(),
        "coordinate_space": artifact.coordinate_space.value,
        "unit": artifact.unit.value,
        "setup_id": str(artifact.setup_id),
        "wcs_fingerprint": artifact.wcs_fingerprint.to_dict(),
        "tool_assembly_id": str(artifact.tool_assembly_id),
        "tool_assembly_fingerprint": artifact.tool_assembly_fingerprint.to_dict(),
        "machine_id": str(artifact.machine_id) if artifact.machine_id else None,
        "machine_fingerprint": (
            artifact.machine_fingerprint.to_dict() if artifact.machine_fingerprint else None
        ),
        "initial_pose": artifact.initial_pose.to_dict(),
        "events": events,
        "diagnostics": [diagnostic_to_dict(item) for item in artifact.diagnostics],
        "completion_status": artifact.completion_status.value,
    }


def _semantic_fingerprint(artifact) -> str:
    encoded = json.dumps(
        _semantic_payload(artifact), ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _percentile95(values: list[int]) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * 0.95 + 0.999999)))
    return float(ordered[index])


def _summary(values: list[int]) -> dict[str, object]:
    return {
        "samples_ns": values,
        "median_ns": float(statistics.median(values)),
        "p95_ns": _percentile95(values),
    }


class Workload:
    def __init__(self, service, root: Path, operations: dict[str, object], resolvers: dict[str, object]):
        self.service = service
        self.root = root
        self.operations = operations
        self.resolvers = resolvers

    def calculate(self, name: str):
        operation_id = self.operations[name]
        if name == "facing":
            return self.service.compute_facing(operation_id)
        if name == "contour":
            return self.service.compute_contour(
                operation_id, profile_resolver=self.resolvers["contour"]
            )
        if name.startswith("pocket"):
            return self.service.compute_pocket(
                operation_id, geometry_resolver=self.resolvers[name]
            )
        if name == "drilling":
            return self.service.compute_drilling(
                operation_id, geometry_resolver=self.resolvers["drilling"]
            )
        raise ValueError(f"Unknown workload operation: {name}")


def _build_workload(parent: Path, label: str, kind: str) -> Workload:
    _deterministic_ids(label)
    from hms_cadcam.cam.application import basic_mill_resources
    from hms_cadcam.cam.domain import (
        CamNodeId, GeometryInputId, GeometryInputRole, GeometryReferenceKind,
        Length, LengthUnit, MachineRequirement, Operation, OperationCapability,
        OperationFamily, OperationGeometryInput, OperationId, Revision,
        ToolAssemblyReference,
    )
    from hms_cadcam.project.service import ProjectService
    from hms_cadcam.ui.cam_ui import _default_setup
    from tests.unit.test_cam_facing import _parameters as facing_parameters
    from tests.unit.test_cam_contour import (
        _descriptor as contour_descriptor,
        _parameters as contour_parameters,
        _rectangle_loop,
    )
    from tests.unit.test_pocket_strategy import (
        _reference as pocket_reference,
        _region as pocket_region,
        _rectangle as pocket_rectangle,
        _strategy as pocket_strategy,
    )
    from hms_cadcam.cam.domain import (
        GeometryResolutionStatus, ResolvedContourProfile, ResolvedPocketGeometry,
    )

    service = ProjectService.create_default(parent / "config")
    session = service.new_project(parent, f"R251 {label}")
    service.execute_cam_command(lambda app: app.create_job("R251 Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid5(NAMESPACE, f"{label}:source"), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(
        lambda app: app.add_basic_resources(tool, holder, assembly, machine)
    )
    requirement = MachineRequirement(
        machine.machine_id, machine.revision, machine.content_fingerprint,
        machine.unit, (OperationCapability.MILLING,),
    )
    operations: dict[str, object] = {}
    resolvers: dict[str, object] = {}

    def add(name: str, operation: Operation) -> None:
        service.execute_cam_command(lambda app: app.update_tree(
            job_id, setup.setup_id,
            lambda tree: tree.add_operation(tree.root_id, name, operation),
        ))

    if kind in {"facing", "mixed"}:
        operation_id = OperationId.new()
        operation = Operation(
            operation_id, CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
            ToolAssemblyReference.from_assembly(assembly), (),
            facing_parameters(target=45.0, stepdown=0.5, stepover=1.0).to_operation_parameters(),
            requirement,
        )
        add("Facing", operation)
        operations["facing"] = operation_id

    if kind in {"contour", "mixed"}:
        size = 120.0 if kind == "contour" else 40.0
        descriptor, geometry_input = contour_descriptor(
            _rectangle_loop(size), setup.source_scope.primary_source_id
        )
        operation_id = OperationId.new()
        operation = Operation(
            operation_id, CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
            ToolAssemblyReference.from_assembly(assembly), (geometry_input,),
            contour_parameters(
                final_depth=Length(40.0, LengthUnit.MM),
                stepdown=Length(0.5, LengthUnit.MM),
            ).to_operation_parameters(),
            requirement,
        )
        add("Contour", operation)
        operations["contour"] = operation_id
        resolved = ResolvedContourProfile(
            status=GeometryResolutionStatus.RESOLVED, profile=descriptor
        )
        resolvers["contour"] = lambda _reference, value=resolved: value

    if kind in {
        "pocket", "pocket_feed", "pocket_multi", "larger", "larger_feed", "mixed"
    }:
        dimensions = {
            "pocket": (100.0, 80.0, 2.0),
            "pocket_feed": (100.0, 80.0, 2.0),
            "pocket_multi": (160.0, 100.0, 1.0),
            "larger": (240.0, 180.0, 0.5),
            "larger_feed": (240.0, 180.0, 0.5),
            "mixed": (50.0, 40.0, 2.0),
        }[kind]
        reference = pocket_reference(setup.source_scope.primary_source_id)
        region = pocket_region(
            pocket_rectangle(dimensions[0], dimensions[1]), reference
        )
        strategy = pocket_strategy(
            reference,
            stepover=Length(dimensions[2], LengthUnit.MM),
            stepdown=Length(0.5, LengthUnit.MM),
        )
        if kind in {"pocket_feed", "larger_feed"}:
            from hms_cadcam.cam.domain import FeedRate
            strategy = replace(
                strategy,
                cutting_feed_rate=FeedRate(
                    strategy.cutting_feed_rate.value * 0.9,
                    strategy.cutting_feed_rate.unit,
                ),
            )
        geometry_input = OperationGeometryInput(
            GeometryInputId.new(), GeometryInputRole.BOUNDARY, reference,
            True, GeometryReferenceKind.FACE, 0,
        )
        operation_id = OperationId.new()
        operation = Operation(
            operation_id, CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
            ToolAssemblyReference.from_assembly(assembly), (geometry_input,),
            strategy.to_operation_parameters(), requirement,
            revision=(
                Revision(1)
                if kind in {"pocket_feed", "larger_feed"}
                else Revision(0)
            ),
        )
        add("Pocket", operation)
        operations["pocket"] = operation_id
        resolved = ResolvedPocketGeometry(GeometryResolutionStatus.RESOLVED, region)
        resolvers["pocket"] = lambda _reference, value=resolved: value
        if kind == "pocket_multi":
            second_reference = pocket_reference(setup.source_scope.primary_source_id)
            second_region = pocket_region(
                pocket_rectangle(70.0, 55.0), second_reference
            )
            second_strategy = pocket_strategy(
                second_reference,
                stepover=Length(1.0, LengthUnit.MM),
                stepdown=Length(0.5, LengthUnit.MM),
            )
            second_input = OperationGeometryInput(
                GeometryInputId.new(), GeometryInputRole.BOUNDARY, second_reference,
                True, GeometryReferenceKind.FACE, 0,
            )
            second_id = OperationId.new()
            second_operation = Operation(
                second_id, CamNodeId.new(), OperationFamily.MILLING, setup.setup_id,
                ToolAssemblyReference.from_assembly(assembly), (second_input,),
                second_strategy.to_operation_parameters(), requirement,
            )
            add("Pocket B", second_operation)
            operations["pocket_b"] = second_id
            second_resolved = ResolvedPocketGeometry(
                GeometryResolutionStatus.RESOLVED, second_region
            )
            resolvers["pocket_b"] = lambda _reference, value=second_resolved: value

    if kind == "many_hole":
        from tests.unit.test_drilling_strategy import _pattern, _resources, _strategy
        from hms_cadcam.cam.domain import (
            DrillingRegion, GeometryFingerprint, ResolvedDrillingGeometry,
        )
        points = tuple((float(x * 8), float(y * 8)) for y in range(8) for x in range(10))
        pattern = _pattern(*points)
        strategy = _strategy(pattern=pattern)
        drill, drill_holder, drill_assembly, drill_machine = _resources(strategy.cycle)
        service.execute_cam_command(lambda app: app.add_basic_resources(
            drill, drill_holder, drill_assembly, drill_machine
        ))
        operation_id = OperationId.new()
        operation = Operation(
            operation_id, CamNodeId.new(), OperationFamily.DRILLING, setup.setup_id,
            ToolAssemblyReference.from_assembly(drill_assembly), (),
            strategy.to_operation_parameters(),
            MachineRequirement(
                drill_machine.machine_id, drill_machine.revision,
                drill_machine.content_fingerprint, drill_machine.unit,
                (OperationCapability.DRILLING,),
            ),
        )
        add("Drilling", operation)
        operations["drilling"] = operation_id
        region = DrillingRegion(
            strategy.geometry, strategy.geometry.source, strategy.depth, strategy.unit,
            GeometryFingerprint.from_payload({"pattern": strategy.geometry.to_dict()}),
        )
        resolved = ResolvedDrillingGeometry(GeometryResolutionStatus.RESOLVED, region)
        resolvers["drilling"] = lambda _geometry, _depth, value=resolved: value

    return Workload(service, session.root_path, operations, resolvers)


def _calculate_all(workload: Workload) -> tuple[object, ...]:
    artifacts = []
    for name in workload.operations:
        result = workload.calculate(name)
        if not result.accepted or result.artifact is None:
            raise RuntimeError(f"Production calculation failed: {name}")
        artifacts.append(result.artifact)
    return tuple(artifacts)


def _mark_dirty(workload: Workload, *, revise: bool = False, feed_change: bool = False) -> None:
    from hms_cadcam.cam.domain import DirtyReason, FeedRate
    snapshot = workload.service.cam_snapshot
    for job in snapshot.jobs:
        for setup in job.setups:
            for operation in setup.operation_tree.operations:
                changed = operation
                if feed_change and operation.strategy_key == "pocket_2_5d":
                    from hms_cadcam.cam.domain import PocketStrategy
                    reference = operation.geometry_inputs[0].reference
                    strategy = PocketStrategy.from_operation_parameters(
                        operation.parameters, reference
                    )
                    strategy = replace(
                        strategy,
                        cutting_feed_rate=FeedRate(
                            strategy.cutting_feed_rate.value * 0.9,
                            strategy.cutting_feed_rate.unit,
                        ),
                    )
                    changed = replace(changed, parameters=strategy.to_operation_parameters())
                changed = replace(
                    changed,
                    revision=changed.revision.next() if revise else changed.revision,
                    artifact_state=changed.artifact_state.mark_dirty(
                        DirtyReason.PARAMETERS_CHANGED
                    ),
                )
                workload.service.execute_cam_command(lambda app, value=changed: app.update_tree(
                    job.job_id, setup.setup_id,
                    lambda tree: tree.replace_operation(value),
                ))


def _cold_sample(parent: Path, label: str, kind: str) -> tuple[int, dict[str, str]]:
    workload = _build_workload(parent, label, kind)
    started = perf_counter_ns()
    artifacts = _calculate_all(workload)
    elapsed = perf_counter_ns() - started
    fingerprints = {
        name: _semantic_fingerprint(artifact)
        for name, artifact in zip(workload.operations, artifacts, strict=True)
    }
    workload.service.close_project(discard_changes=True)
    return elapsed, fingerprints


def _candidate_modes(parent: Path, samples: int) -> dict[str, object]:
    modes: dict[str, object] = {}
    for mode in (
        "final_cache_hit", "facing_final_cache_hit", "phase_cache_hit",
        "incremental_feed_change", "project_reopen_reuse",
        "facing_project_reopen_reuse",
    ):
        values: list[int] = []
        semantics: list[str] = []
        invocation_counts: list[int | None] = []
        phase_statuses: list[list[str]] = []
        for index in range(samples):
            sample_root = parent / mode / str(index)
            sample_root.mkdir(parents=True)
            if mode.startswith("facing_"):
                workload_kind = "facing"
            elif mode == "final_cache_hit":
                workload_kind = "pocket"
            else:
                workload_kind = "larger"
            fixture_label = (
                "pocket-fixture"
                if workload_kind == "pocket"
                else (
                    "facing-fixture"
                    if workload_kind == "facing"
                    else "larger-fixture"
                )
            )
            workload = _build_workload(sample_root, fixture_label, workload_kind)
            cold = _calculate_all(workload)[0]
            if mode in {"project_reopen_reuse", "facing_project_reopen_reuse"}:
                workload.service.save()
                root = workload.root
                workload.service.close_project()
                from hms_cadcam.project.service import ProjectService
                reopened = ProjectService.create_default(sample_root / "config")
                reopened.open_project(root)
                workload.service = reopened
            elif mode == "phase_cache_hit":
                final_root = (
                    workload.root / ".hms" / "cam" / "operations"
                    / workload.operations["pocket"].value.hex / "final_assembly"
                )
                if final_root.exists():
                    shutil.rmtree(final_root)
                _mark_dirty(workload)
                workload.service._cam_application._pocket_incremental_templates.clear()
            elif mode == "incremental_feed_change":
                _mark_dirty(workload, revise=True, feed_change=True)
            started = perf_counter_ns()
            operation_name = "facing" if workload_kind == "facing" else "pocket"
            result = workload.calculate(operation_name)
            values.append(perf_counter_ns() - started)
            if not result.accepted or result.artifact is None:
                raise RuntimeError(f"Candidate mode failed: {mode}")
            semantics.append(_semantic_fingerprint(result.artifact))
            timing = workload.service._cam_application.calculation_timing(
                workload.operations[operation_name]
            )
            if timing is None:
                raise RuntimeError(f"Missing phase timing in {mode}")
            phase_statuses.append([phase.cache_status for phase in timing.phases])
            invocation_counts.append(
                None
                if workload_kind == "facing"
                else (
                    0
                    if timing.phases[0].cache_status in {
                        "CACHE_HIT", "CHECKPOINT_HIT"
                    }
                    else 1
                )
            )
            if mode in {"final_cache_hit", "facing_final_cache_hit"}:
                if timing.phases[-1].cache_status != "CACHE_HIT":
                    raise RuntimeError(f"Final cache did not hit in {mode}")
            if mode == "incremental_feed_change":
                if timing.phases[-1].cache_status != "INCREMENTAL_HIT":
                    raise RuntimeError(f"Incremental template did not hit in {mode}")
            if mode == "project_reopen_reuse":
                if timing.phases[0].cache_status != "CACHE_HIT":
                    raise RuntimeError(f"Reopen phase cache did not hit in {mode}")
            if mode == "facing_project_reopen_reuse":
                if timing.phases[-1].cache_status != "CACHE_HIT":
                    raise RuntimeError(f"Reopen final cache did not hit in {mode}")
            if mode in {
                "final_cache_hit", "facing_final_cache_hit", "phase_cache_hit",
                "project_reopen_reuse", "facing_project_reopen_reuse",
            }:
                if semantics[-1] != _semantic_fingerprint(cold):
                    raise RuntimeError(f"Semantic mismatch in {mode}")
            workload.service.close_project(discard_changes=True)
        modes[mode] = {
            **_summary(values),
            "reference_workload": workload_kind,
            "semantic_fingerprints": semantics,
            "geometry_prepare_invocations": invocation_counts,
            "phase_cache_statuses": phase_statuses,
        }
    return modes


def _simulation_contention(parent: Path, samples: int) -> dict[str, object]:
    """Measure production Pocket while an actual Simulation worker yields."""
    from types import SimpleNamespace
    from hms_cadcam.simulation.background import (
        BackgroundSimulationCoordinator,
        PrecomputeState,
    )
    from tests.unit.test_cam_facing import _artifact, _parameters

    off_values: list[int] = []
    active_values: list[int] = []
    paused_observed: list[bool] = []
    for index in range(samples):
        off_root = parent / "simulation_off" / str(index)
        off_root.mkdir(parents=True)
        off = _build_workload(off_root, "contention-foreground", "larger")
        started = perf_counter_ns()
        _calculate_all(off)
        off_values.append(perf_counter_ns() - started)
        off.service.close_project(discard_changes=True)

        active_root = parent / "simulation_active" / str(index)
        active_root.mkdir(parents=True)
        active = _build_workload(active_root, "contention-foreground", "larger")
        _generator, values, _computing, _token, artifact = _artifact(
            _parameters(target=45.0, stepdown=0.5, stepover=0.1)
        )
        background_inputs = SimpleNamespace(
            operation=values.operation,
            artifact=artifact,
            setup=values.setup,
            tool=values.tool,
            holder=None,
            request=SimpleNamespace(
                stock_fingerprint=__import__(
                    "hms_cadcam.cam.domain", fromlist=["ContentFingerprint"]
                ).ContentFingerprint.from_payload(values.setup.stock.to_dict())
            ),
        )
        coordinator = BackgroundSimulationCoordinator(
            chunk_samples=1, pressure_poll_seconds=0.005
        )
        active.service._background_simulation_enabled = True
        active.service._background_simulation = coordinator
        coordinator.schedule(
            project_root=active.root,
            project_id=active.service.current_project.manifest.project_id,
            project_generation=active.service.cam_generation,
            operation_id=values.operation.operation_id,
            load_inputs=lambda value=background_inputs: value,
        )
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            status = coordinator.status(values.operation.operation_id)
            if status is not None and status.state not in {PrecomputeState.QUEUED}:
                break
            time.sleep(0.002)
        seen_pause = threading.Event()
        stop_monitor = threading.Event()

        def monitor() -> None:
            while not stop_monitor.is_set():
                status = coordinator.status(values.operation.operation_id)
                if status is not None and status.state is PrecomputeState.PAUSED_FOREGROUND:
                    seen_pause.set()
                    return
                time.sleep(0.001)

        thread = threading.Thread(target=monitor, name="R251-contention-monitor")
        thread.start()
        try:
            started = perf_counter_ns()
            _calculate_all(active)
            active_values.append(perf_counter_ns() - started)
        finally:
            stop_monitor.set()
            thread.join(timeout=2.0)
            paused_observed.append(seen_pause.is_set())
            active.service.close_project(discard_changes=True)
            coordinator.shutdown()
    off = _summary(off_values)
    active = _summary(active_values)
    return {
        "simulation_off": off,
        "simulation_active": active,
        "paused_foreground_observed": paused_observed,
        "median_slowdown_percent": (
            (float(active["median_ns"]) / float(off["median_ns"]) - 1.0) * 100.0
        ),
    }


def run(args: argparse.Namespace) -> dict[str, object]:
    source_root = args.source_root.resolve()
    _install_source(source_root)
    output_root = args.output_root.resolve()
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    workloads = (
        "facing", "contour", "pocket", "pocket_feed", "pocket_multi",
        "many_hole", "mixed", "larger", "larger_feed",
    )
    cold: dict[str, object] = {}
    for kind in workloads:
        values: list[int] = []
        fingerprints: list[dict[str, str]] = []
        for index in range(args.samples):
            parent = output_root / "cold" / kind / str(index)
            parent.mkdir(parents=True)
            fixture_label = (
                "pocket-fixture"
                if kind in {"pocket", "pocket_feed"}
                else (
                    "larger-fixture"
                    if kind in {"larger", "larger_feed"}
                    else f"{kind}-fixture"
                )
            )
            elapsed, semantic = _cold_sample(parent, fixture_label, kind)
            values.append(elapsed)
            fingerprints.append(semantic)
        cold[kind] = {**_summary(values), "semantic_fingerprints": fingerprints}
    result: dict[str, object] = {
        "format": "HMS_R251_CAM_OPTIMIZATION_BENCHMARK",
        "format_version": 1,
        "role": args.role,
        "source_root": str(source_root),
        "samples_per_mode": args.samples,
        "cold": cold,
    }
    if args.role == "candidate":
        result["candidate_modes"] = _candidate_modes(output_root, args.samples)
        result["simulation_contention"] = _simulation_contention(
            output_root, args.samples
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--role", choices=("baseline", "candidate"), required=True)
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if args.samples < 3:
        raise ValueError("R251 benchmark requires at least three samples")
    result = run(args)
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"role": args.role, "output": str(args.output_json)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

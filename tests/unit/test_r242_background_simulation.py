"""R242 deterministic background-precompute and foreground-priority gates."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hms_cadcam.project.service import ProjectService
from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.application import basic_mill_resources
from hms_cadcam.cam.domain import (
    CamNodeId,
    LengthUnit,
    MachineRequirement,
    Operation,
    OperationCapability,
    OperationFamily,
    OperationId,
    ToolAssemblyReference,
)
from hms_cadcam.ui.cam_ui import _default_setup
from hms_cadcam.simulation.background import (
    BackgroundSimulationCoordinator,
    PrecomputeCheckpointStore,
    PrecomputeState,
    ResourceDecision,
    ResourceGovernor,
    ResourcePressure,
)
from hms_cadcam.simulation.contracts import QualityMode
from tests.unit.test_r241_simulation_ui import _inputs
from tests.unit.test_cam_facing import _artifact, _parameters


def _cutting_inputs():
    _generator, values, _computing, _token, artifact = _artifact()
    return SimpleNamespace(
        operation=values.operation,
        artifact=artifact,
        setup=values.setup,
        tool=values.tool,
        holder=None,
        request=SimpleNamespace(
            stock_fingerprint=ContentFingerprint.from_payload(
                values.setup.stock.to_dict()
            )
        ),
    )


def _project_root(tmp_path: Path) -> Path:
    root = tmp_path / "part.HMS"
    (root / "cache" / "simulation").mkdir(parents=True)
    return root


def _wait_state(
    coordinator: BackgroundSimulationCoordinator,
    operation_id: object,
    states: set[PrecomputeState],
    timeout: float = 5.0,
):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        status = coordinator.status(operation_id)
        if status is not None and status.state in states:
            return status
        sleep(0.005)
    raise AssertionError(f"precompute did not reach {states}: {coordinator.status(operation_id)}")


class _PressureProbe:
    def __init__(self) -> None:
        self.pressure = ResourcePressure()

    def __call__(self) -> ResourcePressure:
        return self.pressure


def test_resource_pressure_reduces_or_suspends_background_concurrency() -> None:
    probe = _PressureProbe()
    governor = ResourceGovernor(probe)
    assert governor.decide(foreground_active=False) is ResourceDecision.RUN
    probe.pressure = ResourcePressure(cpu_percent=75.0)
    assert governor.decide(foreground_active=False) is ResourceDecision.THROTTLE
    probe.pressure = ResourcePressure(memory_percent=90.0)
    assert governor.decide(foreground_active=False) is ResourceDecision.SUSPEND
    assert governor.decide(foreground_active=True) is ResourceDecision.SUSPEND


def test_foreground_pauses_then_resumes_from_completed_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = _cutting_inputs()
    operation_id = inputs.operation.operation_id
    root = _project_root(tmp_path)
    allow_second_chunk = Event()
    first_chunk_published = Event()
    original_publish = PrecomputeCheckpointStore.publish

    def observed_publish(self, *args, **kwargs):
        checkpoint = original_publish(self, *args, **kwargs)
        if checkpoint.completed_chunks == 1:
            first_chunk_published.set()
            assert allow_second_chunk.wait(5.0)
        return checkpoint

    monkeypatch.setattr(PrecomputeCheckpointStore, "publish", observed_publish)
    coordinator = BackgroundSimulationCoordinator(chunk_samples=1024)
    try:
        coordinator.schedule(
            project_root=root,
            project_id=uuid4(),
            project_generation=1,
            operation_id=operation_id,
            load_inputs=lambda: inputs,
            quality=QualityMode.FAST,
        )
        assert first_chunk_published.wait(5.0)
        with coordinator.foreground("calculate"):
            allow_second_chunk.set()
            paused = _wait_state(
                coordinator, operation_id, {PrecomputeState.PAUSED_FOREGROUND}
            )
            assert paused.completed_chunks == 1
            assert paused.next_sample == 1024
        complete = _wait_state(
            coordinator, operation_id, {PrecomputeState.COMPLETE}, timeout=15.0
        )
        assert complete.resumed_from_sample == 1024
        assert complete.completed_chunks > 1
    finally:
        coordinator.shutdown()


def test_complete_checkpoint_reuse_does_not_restart_chunks(tmp_path: Path) -> None:
    inputs = _cutting_inputs()
    operation_id = inputs.operation.operation_id
    root = _project_root(tmp_path)
    project_id = uuid4()
    first = BackgroundSimulationCoordinator(chunk_samples=4096)
    try:
        first.schedule(
            project_root=root,
            project_id=project_id,
            project_generation=1,
            operation_id=operation_id,
            load_inputs=lambda: inputs,
            quality=QualityMode.FAST,
        )
        initial = _wait_state(first, operation_id, {PrecomputeState.COMPLETE}, 15.0)
    finally:
        first.shutdown()
    second = BackgroundSimulationCoordinator(chunk_samples=1)
    try:
        loaded = second.load_completed(
            project_root=root,
            project_id=project_id,
            project_generation=2,
            operation_id=operation_id,
            inputs=inputs,
            quality=QualityMode.FAST,
        )
        assert loaded is not None
        assert loaded.completed_chunks == initial.completed_chunks
        second.schedule(
            project_root=root,
            project_id=project_id,
            project_generation=2,
            operation_id=operation_id,
            load_inputs=lambda: inputs,
            quality=QualityMode.FAST,
        )
        reused = _wait_state(second, operation_id, {PrecomputeState.COMPLETE})
        assert reused.completed_chunks == initial.completed_chunks
        assert reused.resumed_from_sample == initial.next_sample
    finally:
        second.shutdown()


def test_cancel_and_cleanup_remove_only_inactive_owned_scratch(tmp_path: Path) -> None:
    root = _project_root(tmp_path)
    store = PrecomputeCheckpointStore(maximum_runs=1, maximum_bytes=4096)
    old_run = root / "cache" / "simulation" / "precompute" / "old" / "run"
    old_run.mkdir(parents=True)
    scratch = old_run / "orphan.writing"
    scratch.write_bytes(b"scratch")
    audit = root / "cache" / "simulation" / "audit-evidence.json"
    audit.write_text("audit", encoding="utf-8")
    removed = store.cleanup_scratch(root)
    assert scratch in removed
    assert audit.read_text(encoding="utf-8") == "audit"


def test_quota_cleanup_never_deletes_active_run_or_audit_artifact(
    tmp_path: Path,
) -> None:
    root = _project_root(tmp_path)
    store = PrecomputeCheckpointStore(maximum_runs=1, maximum_bytes=1)
    active = root / "cache" / "simulation" / "precompute" / "active" / "run"
    inactive = root / "cache" / "simulation" / "precompute" / "old" / "run"
    active.mkdir(parents=True)
    inactive.mkdir(parents=True)
    (active / "stock.bin").write_bytes(b"active")
    (inactive / "stock.bin").write_bytes(b"inactive")
    audit = root / "cache" / "simulation" / "audit-evidence.json"
    audit.write_text("immutable audit", encoding="utf-8")
    with store.active(active):
        store.cleanup(root)
        assert (active / "stock.bin").read_bytes() == b"active"
    assert not inactive.exists()
    assert audit.read_text(encoding="utf-8") == "immutable audit"


def test_project_cancel_stops_worker_and_cleans_owned_scratch(tmp_path: Path) -> None:
    inputs = _cutting_inputs()
    root = _project_root(tmp_path)
    probe = _PressureProbe()
    probe.pressure = ResourcePressure(memory_percent=95.0)
    coordinator = BackgroundSimulationCoordinator(
        governor=ResourceGovernor(probe), chunk_samples=1024
    )
    try:
        coordinator.schedule(
            project_root=root,
            project_id=uuid4(),
            project_generation=7,
            operation_id=inputs.operation.operation_id,
            load_inputs=lambda: inputs,
            quality=QualityMode.FAST,
        )
        _wait_state(
            coordinator,
            inputs.operation.operation_id,
            {PrecomputeState.PAUSED_PRESSURE},
        )
        coordinator.cancel_project(root, 7)
        cancelled = _wait_state(
            coordinator,
            inputs.operation.operation_id,
            {PrecomputeState.CANCELLED},
        )
        assert cancelled.state is PrecomputeState.CANCELLED
        assert not tuple(root.glob("cache/simulation/precompute/*/*/*.writing"))
    finally:
        coordinator.shutdown()


def test_startup_job_cleanup_removes_crash_scratch_before_compute(
    tmp_path: Path,
) -> None:
    inputs = _cutting_inputs()
    root = _project_root(tmp_path)
    orphan = (
        root
        / "cache"
        / "simulation"
        / "precompute"
        / "orphan"
        / "run"
        / "crash.writing"
    )
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"crash scratch")
    coordinator = BackgroundSimulationCoordinator(chunk_samples=4096)
    try:
        coordinator.schedule(
            project_root=root,
            project_id=uuid4(),
            project_generation=1,
            operation_id=inputs.operation.operation_id,
            load_inputs=lambda: inputs,
            quality=QualityMode.FAST,
        )
        _wait_state(
            coordinator,
            inputs.operation.operation_id,
            {PrecomputeState.COMPLETE},
            15.0,
        )
        assert not orphan.exists()
    finally:
        coordinator.shutdown()


class _FakeCoordinator:
    def __init__(self) -> None:
        self.depth = 0
        self.scheduled = 0

    @contextmanager
    def foreground(self, _name: str):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    def schedule(self, **_kwargs) -> None:
        self.scheduled += 1

    def cancel_project(self, *_args) -> None:
        return None


def _install_facing(service: ProjectService):
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    setup = _default_setup(uuid4(), LengthUnit.MM, 1)
    service.execute_cam_command(lambda app: app.add_setup(job_id, setup))
    tool, holder, assembly, machine = basic_mill_resources(LengthUnit.MM)
    service.execute_cam_command(
        lambda app: app.add_basic_resources(tool, holder, assembly, machine)
    )
    node_id, operation_id = CamNodeId.new(), OperationId.new()
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (OperationCapability.MILLING,),
    )
    operation = Operation(
        operation_id,
        node_id,
        OperationFamily.MILLING,
        setup.setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        (),
        _parameters(target=49.0).to_operation_parameters(),
        requirement,
    )
    service.execute_cam_command(
        lambda app: app.update_tree(
            job_id,
            setup.setup_id,
            lambda tree: tree.add_operation(tree.root_id, "Facing", operation),
        )
    )
    return operation_id


def test_successful_project_calculate_enqueues_only_when_enabled(tmp_path: Path) -> None:
    disabled_coordinator = _FakeCoordinator()
    disabled = ProjectService.create_default(tmp_path / "off-config")
    disabled._background_simulation = disabled_coordinator
    disabled.new_project(tmp_path, "Off")
    assert disabled.compute_facing(_install_facing(disabled)).accepted
    assert disabled_coordinator.scheduled == 0
    disabled.close_project(discard_changes=True)

    enabled_coordinator = _FakeCoordinator()
    enabled = ProjectService.create_default(
        tmp_path / "on-config", background_simulation_precompute=True
    )
    enabled._background_simulation = enabled_coordinator
    enabled.new_project(tmp_path, "On")
    assert enabled.compute_facing(_install_facing(enabled)).accepted
    assert enabled_coordinator.scheduled == 1
    enabled.close_project(discard_changes=True)


def test_disabled_or_unavailable_precompute_keeps_normal_cam_lazy() -> None:
    service = ProjectService.__new__(ProjectService)
    service._background_simulation_enabled = False
    service._background_simulation = None
    with service._background_foreground("post"):
        pass
    assert service._background_simulation is None


def test_post_and_export_foreground_gateway_never_waits_for_background() -> None:
    coordinator = _FakeCoordinator()
    service = ProjectService.__new__(ProjectService)
    service._background_simulation_enabled = True
    service._background_simulation = coordinator
    started = monotonic()
    with service._background_foreground("post"):
        assert coordinator.depth == 1
    with service._background_foreground("nc_export"):
        assert coordinator.depth == 1
    assert monotonic() - started < 0.05


def test_checkpoint_manifest_marks_partial_output_explicitly(tmp_path: Path) -> None:
    inputs = _cutting_inputs()
    root = _project_root(tmp_path)
    store = PrecomputeCheckpointStore()
    stock = __import__(
        "hms_cadcam.simulation.heightfield", fromlist=["HeightField3AxisEngine"]
    ).HeightField3AxisEngine().simulate_chunk(
        stock=inputs.setup.stock,
        artifact=inputs.artifact,
        tool=inputs.tool,
        quality=QualityMode.FAST,
        maximum_cutting_samples=1,
    ).result.remaining_stock
    fingerprint = "a" * 64
    run_root = store.run_root(root, inputs.operation.operation_id, fingerprint)
    store.publish(
        run_root,
        run_fingerprint=fingerprint,
        stock=stock,
        next_sample=1,
        total_samples=2,
        completed_chunks=1,
        complete=False,
    )
    manifest = json.loads((run_root / "manifest.json").read_text(encoding="ascii"))
    assert manifest["state"] == "partial"
    assert manifest["next_sample"] == 1

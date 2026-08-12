"""Focused R246 correctness tests for cache, identity and scheduling primitives."""

from __future__ import annotations

from pathlib import Path

from hms_cadcam.cam.domain import ContentFingerprint
from hms_cadcam.cam.optimization import (
    CalculationArtifactStore,
    CalculationFingerprint,
    CalculationFingerprintInput,
    CacheLookupStatus,
    CheckpointState,
    CheckpointStore,
    InvalidationMatrix,
    InvalidationScope,
    ResourceGovernor,
    ResourcePressure,
    deterministic_parallel_map,
)


def _fingerprint() -> CalculationFingerprint:
    return CalculationFingerprint.from_input(CalculationFingerprintInput(
        operation_id="op-01", operation_type="milling", strategy={"key": "pocket.v1"},
        geometry={"source": "face-a", "topology": "topo-a"}, setup={"wcs": "wcs-a"},
        stock={"kind": "box", "z": 10.0}, tool={"type": "end_mill", "diameter": 6.0},
        holder={"id": "holder-a"}, boundary={"id": "boundary-a"},
        parameters={"tolerance": 0.001, "stepover": 2.0}, dependencies=(),
        engine_id="hms-cam", engine_version="r246-v1", precision_policy={"tol": 1e-6},
        algorithm_version="pocket.v1",
    ))


def test_calculation_fingerprint_is_deterministic_and_includes_engine() -> None:
    first = _fingerprint()
    second = _fingerprint()
    assert first.value == second.value
    assert first.payload["engine_version"] == "r246-v1"
    changed = CalculationFingerprint.from_input(CalculationFingerprintInput(
        **{**first.payload, "dependencies": tuple(), "engine_version": "r246-v2"}
    ))
    assert changed.value != first.value


def test_project_local_cache_fail_closed_on_corruption(tmp_path: Path) -> None:
    store = CalculationArtifactStore()
    fp = _fingerprint().value.digest
    manifest = store.publish(tmp_path, operation_id="op-01", phase="geometry", fingerprint=fp,
                             payload=b"geometry", dependency_fingerprints=())
    assert manifest.state == "COMPLETE"
    hit = store.lookup(tmp_path, operation_id="op-01", phase="geometry", fingerprint=fp)
    assert hit.status is CacheLookupStatus.HIT and hit.payload == b"geometry"
    payload_path = tmp_path / manifest.artifact_path
    payload_path.write_bytes(b"tampered")
    corrupted = store.lookup(tmp_path, operation_id="op-01", phase="geometry", fingerprint=fp)
    assert corrupted.status is CacheLookupStatus.CORRUPT


def test_checkpoint_only_loads_complete_matching_payload(tmp_path: Path) -> None:
    store = CheckpointStore()
    record = store.publish(tmp_path, "op-01", "regions", "fp-a", b"{\"regions\":[]}")
    assert record.state is CheckpointState.COMPLETE
    assert store.load(tmp_path, "op-01", "regions", "fp-a")[1] == b"{\"regions\":[]}"
    assert store.load(tmp_path, "op-01", "regions", "fp-b") is None


def test_invalidation_matrix_is_phase_aware_and_fail_closed() -> None:
    assert InvalidationMatrix.decide("lead").scope is InvalidationScope.LINKING
    assert InvalidationMatrix.decide("camera").scope is InvalidationScope.NONE
    assert InvalidationMatrix.decide("unknown_future_parameter").scope is InvalidationScope.ALL


def test_parallel_map_has_stable_order_and_governor_yields() -> None:
    assert deterministic_parallel_map((3, 1, 2), lambda value: value * 2, max_workers=3) == (6, 2, 4)
    pressure = ResourcePressure(cpu_percent=10.0, memory_percent=10.0)
    governor = ResourceGovernor(lambda: pressure)
    assert governor.decide(foreground_active=True).value == "suspend"

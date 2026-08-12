"""Focused R246 correctness tests for cache, identity and scheduling primitives."""

from __future__ import annotations

import json
import os
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


def test_cleanup_preserves_leased_shared_artifact_until_release(tmp_path: Path) -> None:
    store = CalculationArtifactStore()
    fingerprint = "a" * 64
    manifest = store.publish_shared(
        tmp_path, phase="geometry", fingerprint=fingerprint, payload=b"shared-geometry"
    )
    payload = tmp_path / manifest.artifact_path
    with store.lease(payload):
        assert store.cleanup(tmp_path, max_bytes=0) == 0
        assert payload.is_file()
    assert store.cleanup(tmp_path, max_bytes=0) == 1
    assert not payload.exists()


def test_r251_production_cache_corruption_matrix_never_silently_hits(tmp_path: Path) -> None:
    """Every malformed/stale production representation must fail closed."""
    fingerprint = "b" * 64

    def published_root(name: str) -> tuple[CalculationArtifactStore, Path, Path, Path]:
        root = tmp_path / name
        root.mkdir()
        store = CalculationArtifactStore()
        manifest = store.publish(
            root,
            operation_id="op-01",
            phase="geometry",
            fingerprint=fingerprint,
            payload=b"geometry-payload",
            dependency_fingerprints=("dep-a",),
            engine_version="engine-a",
            algorithm_version="algorithm-a",
        )
        payload = root / manifest.artifact_path
        metadata = payload.with_suffix(".manifest.json")
        return store, root, payload, metadata

    mutations = {
        "truncated": lambda payload, _metadata: payload.write_bytes(b"geo"),
        "checksum": lambda payload, _metadata: payload.write_bytes(b"geometry-tampered"),
        "wrong_fingerprint": lambda _payload, metadata: _rewrite_json(
            metadata, fingerprint="c" * 64
        ),
        "wrong_dependency": lambda _payload, metadata: _rewrite_json(
            metadata, dependency_fingerprints=["dep-b"]
        ),
        "wrong_schema": lambda _payload, metadata: _rewrite_json(
            metadata, format_version=999
        ),
        "wrong_engine": lambda _payload, metadata: _rewrite_json(
            metadata, engine_version="engine-b"
        ),
        "wrong_algorithm": lambda _payload, metadata: _rewrite_json(
            metadata, algorithm_version="algorithm-b"
        ),
        "building": lambda _payload, metadata: _rewrite_json(metadata, state="BUILDING"),
        "malformed": lambda _payload, metadata: metadata.write_text("{", encoding="utf-8"),
        "missing_payload": lambda payload, _metadata: payload.unlink(),
        "missing_manifest": lambda _payload, metadata: metadata.unlink(),
    }
    for name, mutate in mutations.items():
        store, root, payload, metadata = published_root(name)
        mutate(payload, metadata)
        lookup = store.lookup(
            root,
            operation_id="op-01",
            phase="geometry",
            fingerprint=fingerprint,
            dependency_fingerprints=("dep-a",),
            engine_version="engine-a",
            algorithm_version="algorithm-a",
        )
        assert lookup.status is not CacheLookupStatus.HIT, name
        assert lookup.payload is None, name


def _rewrite_json(path: Path, **updates: object) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.update(updates)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_r251_shared_reference_and_age_lru_cleanup_are_dependency_safe(
    tmp_path: Path,
) -> None:
    store = CalculationArtifactStore()
    fingerprint = "d" * 64
    manifest = store.publish_shared(
        tmp_path,
        phase="geometry",
        fingerprint=fingerprint,
        payload=b"shared-geometry",
        algorithm_version="facing.geometry.v1",
        operation_references=("op-01", "op-02"),
    )
    payload = tmp_path / manifest.artifact_path
    assert store.release_operation_references(tmp_path, "op-01") == 1
    assert store.cleanup(
        tmp_path, max_bytes=0, live_operation_ids=frozenset({"op-02"})
    ) == 0
    assert payload.is_file()
    assert store.release_operation_references(tmp_path, "op-02") == 1
    assert store.cleanup(
        tmp_path, max_bytes=0, live_operation_ids=frozenset()
    ) == 1
    assert not payload.exists()

    first = store.publish(
        tmp_path, operation_id="op-a", phase="geometry", fingerprint="e" * 64,
        payload=b"old", algorithm_version="geometry.v1",
    )
    second = store.publish(
        tmp_path, operation_id="op-b", phase="geometry", fingerprint="f" * 64,
        payload=b"newer", algorithm_version="geometry.v1",
    )
    first_path = tmp_path / first.artifact_path
    second_path = tmp_path / second.artifact_path
    old_ns = 1_000_000_000
    os.utime(first_path, ns=(old_ns, old_ns))
    os.utime(first_path.with_suffix(".manifest.json"), ns=(old_ns, old_ns))
    assert store.cleanup(tmp_path, max_bytes=5) == 1
    assert not first_path.exists() and second_path.exists()


def test_r251_abandoned_atomic_scratch_is_removed_without_touching_complete(
    tmp_path: Path,
) -> None:
    store = CalculationArtifactStore()
    manifest = store.publish(
        tmp_path, operation_id="op-01", phase="geometry", fingerprint="a" * 64,
        payload=b"complete",
    )
    complete = tmp_path / manifest.artifact_path
    scratch = complete.parent / ".orphan.bin.deadbeef.tmp"
    scratch.write_bytes(b"partial")
    assert store.recover_abandoned_scratch(tmp_path) == 1
    assert not scratch.exists()
    assert complete.is_file()

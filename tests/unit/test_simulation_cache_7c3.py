"""Simulation 7C.3 external-cache integrity and isolation tests."""

from __future__ import annotations

import json
from uuid import uuid4

from hms_cadcam.cam.simulation import (
    InMemoryAabbBackend,
    SimulationCacheStatus,
    SimulationCacheStore,
    SimulationRuntimeService,
)
from tests.unit.test_simulation_service import _source


def _result():
    operation, artifact, setup, tool, holder, assembly, request, scene = _source()
    execution = SimulationRuntimeService().run(
        request=request,
        artifact=artifact,
        setup=setup,
        tool=tool,
        assembly=assembly,
        holder=holder,
        scene=scene,
        backend=InMemoryAabbBackend(),
    )
    assert execution.accepted
    return execution.result


def test_cache_round_trip_is_deterministic_checksummed_and_path_relative(tmp_path) -> None:
    root = tmp_path / "cache-project.HMS"
    root.mkdir()
    project_id = uuid4()
    result = _result()
    store = SimulationCacheStore()
    metadata = store.write(root, project_id, result)
    operation_root = next((root / "cache" / "simulation").iterdir())
    metadata_path = operation_root / f"{metadata.cache_key}.metadata.json"
    first_bytes = metadata_path.read_bytes()
    second = store.write(root, project_id, result)

    assert second == metadata
    assert metadata_path.read_bytes() == first_bytes
    assert not metadata.payload_filename.startswith(("/", "\\"))
    loaded = store.load_current(
        root,
        project_id,
        result.operation_id,
        result.artifact_fingerprint,
        result.input_fingerprint,
    )
    assert loaded.status is SimulationCacheStatus.VALID
    assert loaded.result == result


def test_tampered_missing_and_future_cache_fail_closed(tmp_path) -> None:
    result = _result()
    project_id = uuid4()

    tampered_root = tmp_path / "tampered.HMS"
    tampered_root.mkdir()
    store = SimulationCacheStore()
    metadata = store.write(tampered_root, project_id, result)
    operation_root = next((tampered_root / "cache" / "simulation").iterdir())
    payload_path = operation_root / metadata.payload_filename
    payload_path.write_bytes(payload_path.read_bytes() + b"tampered")
    tampered = store.load_current(
        tampered_root,
        project_id,
        result.operation_id,
        result.artifact_fingerprint,
        result.input_fingerprint,
    )
    assert tampered.status is SimulationCacheStatus.CHECKSUM_MISMATCH

    payload_path.unlink()
    missing = store.load_current(
        tampered_root,
        project_id,
        result.operation_id,
        result.artifact_fingerprint,
        result.input_fingerprint,
    )
    assert missing.status is SimulationCacheStatus.MISSING

    future_root = tmp_path / "future.HMS"
    future_root.mkdir()
    metadata = store.write(future_root, project_id, result)
    operation_root = next((future_root / "cache" / "simulation").iterdir())
    metadata_path = operation_root / f"{metadata.cache_key}.metadata.json"
    value = json.loads(metadata_path.read_text(encoding="utf-8"))
    value["format_version"] = 999
    metadata_path.write_text(json.dumps(value), encoding="utf-8")
    future = store.load_current(
        future_root,
        project_id,
        result.operation_id,
        result.artifact_fingerprint,
        result.input_fingerprint,
    )
    assert future.status is SimulationCacheStatus.FUTURE_VERSION


def test_cache_copy_rekeys_project_identity_and_cleanup_removes_temp(tmp_path) -> None:
    source = tmp_path / "source.HMS"
    target = tmp_path / "target.HMS"
    source.mkdir()
    target.mkdir()
    source_id, target_id = uuid4(), uuid4()
    result = _result()
    store = SimulationCacheStore()
    source_metadata = store.write(source, source_id, result)
    operation_root = next((source / "cache" / "simulation").iterdir())
    incomplete = operation_root / ".interrupted.result.json.dead.writing"
    incomplete.write_bytes(b"partial")
    orphan = operation_root / ("a" * 64 + ".result.json")
    orphan.write_bytes(b"orphan")
    store.cleanup(source)
    assert not incomplete.exists() and not orphan.exists()

    copied = store.copy_valid_entries(source, target, source_id, target_id)
    assert len(copied) == 1
    assert copied[0].project_id == target_id
    assert copied[0].cache_key != source_metadata.cache_key
    loaded = store.load_current(
        target,
        target_id,
        result.operation_id,
        result.artifact_fingerprint,
        result.input_fingerprint,
    )
    assert loaded.status is SimulationCacheStatus.VALID


def test_cache_stale_source_is_not_current(tmp_path) -> None:
    root = tmp_path / "stale.HMS"
    root.mkdir()
    project_id = uuid4()
    result = _result()
    store = SimulationCacheStore()
    store.write(root, project_id, result)
    stale = store.load_current(
        root,
        project_id,
        result.operation_id,
        type(result.artifact_fingerprint).from_payload({"changed": True}),
        result.input_fingerprint,
    )
    assert stale.status is SimulationCacheStatus.STALE

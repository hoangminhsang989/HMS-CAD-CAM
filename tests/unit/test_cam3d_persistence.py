"""CAM 3D config/cache persistence and lifecycle isolation tests."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from hms_cadcam.cam.cam3d import (
    CAM3D_CONFIG_FILENAME,
    Cam3DCalculationMeshCache,
    Cam3DPersistenceError,
    Cam3DProjectConfig,
    Cam3DProjectStore,
    build_calculation_mesh,
)
from hms_cadcam.cam.domain import UnsupportedCamSchemaError
from tests.unit._cam3d_fixtures import fragments, zone


def _project(tmp_path: Path, name: str) -> Path:
    root = tmp_path / f"{name}.HMS"
    root.mkdir()
    return root


def test_config_save_open_round_trip_and_load_does_not_write(tmp_path: Path) -> None:
    root = _project(tmp_path, "Source")
    project_id = uuid4()
    value = Cam3DProjectConfig(project_id, (zone(project_id=project_id),))
    store = Cam3DProjectStore()
    path = store.save(root, value)
    before = path.stat().st_mtime_ns
    assert store.load(root, project_id) == value
    assert path.stat().st_mtime_ns == before
    assert path.name == CAM3D_CONFIG_FILENAME


def test_cad_only_load_creates_no_config_or_cache(tmp_path: Path) -> None:
    root = _project(tmp_path, "CadOnly")
    project_id = uuid4()
    loaded = Cam3DProjectStore().load(root, project_id)
    assert loaded.is_empty
    assert list(root.iterdir()) == []


def test_save_as_rebinds_project_and_does_not_copy_cache(tmp_path: Path) -> None:
    source_root = _project(tmp_path, "Source")
    target_root = _project(tmp_path, "Target")
    source_id, target_id = uuid4(), uuid4()
    value = zone(project_id=source_id)
    store = Cam3DProjectStore()
    store.save(source_root, Cam3DProjectConfig(source_id, (value,)))
    mesh = build_calculation_mesh(
        fragments(value), value.tolerance, value.geometry_fingerprint
    )
    Cam3DCalculationMeshCache().publish(source_root, source_id, mesh)
    copied = store.copy_for_save_as(
        source_root, target_root, source_id, target_id
    )
    assert copied.project_id == target_id
    assert copied.zones[0].project_id == target_id
    assert all(
        item.project_id == target_id for item in copied.zones[0].all_surfaces()
    )
    assert store.load(target_root, target_id) == copied
    assert not (target_root / "cache" / "cam3d").exists()


def test_autosave_workspace_copy_preserves_project_identity(tmp_path: Path) -> None:
    source_root = _project(tmp_path, "Source")
    autosave_root = _project(tmp_path, "AutosaveWorkspace")
    project_id = uuid4()
    value = Cam3DProjectConfig(project_id, (zone(project_id=project_id),))
    store = Cam3DProjectStore()
    store.save(source_root, value)
    copied = store.copy_for_workspace(source_root, autosave_root, project_id)
    assert copied == value
    assert store.load(autosave_root, project_id) == value


def test_config_rejects_future_version_and_project_mismatch(tmp_path: Path) -> None:
    root = _project(tmp_path, "Project")
    project_id = uuid4()
    store = Cam3DProjectStore()
    path = store.save(root, Cam3DProjectConfig(project_id))
    data = json.loads(path.read_text(encoding="utf-8"))
    data["format_version"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Cam3DPersistenceError) as captured:
        store.load(root, project_id)
    assert isinstance(captured.value.__cause__, UnsupportedCamSchemaError)
    store.save(root, Cam3DProjectConfig(project_id))
    with pytest.raises(Cam3DPersistenceError, match="identity"):
        store.load(root, uuid4())


def test_mesh_cache_round_trip_and_project_isolation(tmp_path: Path) -> None:
    root = _project(tmp_path, "Project")
    project_id = uuid4()
    value = zone(project_id=project_id)
    mesh = build_calculation_mesh(
        fragments(value), value.tolerance, value.geometry_fingerprint
    )
    cache = Cam3DCalculationMeshCache()
    path = cache.publish(root, project_id, mesh)
    assert project_id.hex in path.parts
    assert cache.load(root, project_id, mesh.mesh_fingerprint.digest) == mesh
    with pytest.raises(Cam3DPersistenceError):
        cache.load(root, uuid4(), mesh.mesh_fingerprint.digest)


def test_mesh_cache_corruption_fails_closed(tmp_path: Path) -> None:
    root = _project(tmp_path, "Project")
    project_id = uuid4()
    value = zone(project_id=project_id)
    mesh = build_calculation_mesh(
        fragments(value), value.tolerance, value.geometry_fingerprint
    )
    cache = Cam3DCalculationMeshCache()
    path = cache.publish(root, project_id, mesh)
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(Cam3DPersistenceError):
        cache.load(root, project_id, mesh.mesh_fingerprint.digest)


def test_mesh_cache_cleanup_removes_only_orphans(tmp_path: Path) -> None:
    root = _project(tmp_path, "Project")
    project_id = uuid4()
    first_zone = zone(project_id=project_id, chordal=0.01)
    second_zone = zone(project_id=project_id, chordal=0.02)
    first = build_calculation_mesh(
        fragments(first_zone), first_zone.tolerance, first_zone.geometry_fingerprint
    )
    second = build_calculation_mesh(
        fragments(second_zone), second_zone.tolerance, second_zone.geometry_fingerprint
    )
    cache = Cam3DCalculationMeshCache()
    first_path = cache.publish(root, project_id, first)
    second_path = cache.publish(root, project_id, second)
    unrelated = first_path.parent / "keep.txt"
    unrelated.write_text("not a mesh", encoding="utf-8")
    removed = cache.cleanup_orphans(
        root, project_id, (second.mesh_fingerprint.digest,)
    )
    assert removed == (first_path,)
    assert second_path.exists()
    assert unrelated.exists()

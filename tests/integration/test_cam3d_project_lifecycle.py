"""Real HMS project lifecycle integration for CAM 3D editable configuration."""

from __future__ import annotations

from uuid import uuid4

import pytest

from hms_cadcam.cam.cam3d import (
    Cam3DCalculationMeshCache,
    Cam3DProjectConfig,
    build_calculation_mesh,
)
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.service import ProjectService
from tests.unit._cam3d_fixtures import fragments, zone


def test_project_save_open_save_as_and_zone_deletion(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "CAM3D Lifecycle")
    project_id = session.manifest.project_id
    value = zone(project_id=project_id)
    service.stage_cam3d_config(Cam3DProjectConfig(project_id, (value,)))
    assert service.is_dirty
    service.save()
    assert not service.is_dirty
    service.close_project()

    reopened = service.open_project(session.root_path)
    assert reopened.cam3d_config == Cam3DProjectConfig(project_id, (value,))
    assert not reopened.is_dirty

    mesh = build_calculation_mesh(
        fragments(value), value.tolerance, value.geometry_fingerprint
    )
    Cam3DCalculationMeshCache().publish(reopened.root_path, project_id, mesh)
    copied = service.save_as(tmp_path, "CAM3D Copy")
    assert copied.cam3d_config is not None
    assert copied.cam3d_config.project_id == copied.manifest.project_id
    assert copied.cam3d_config.project_id != project_id
    assert not (copied.root_path / "cache" / "cam3d").exists()

    service.stage_cam3d_config(Cam3DProjectConfig(copied.manifest.project_id))
    service.save()
    service.close_project()
    empty = service.open_project(copied.root_path)
    assert empty.cam3d_config is not None and empty.cam3d_config.is_empty
    assert not empty.is_dirty


def test_autosave_recovery_restores_unsaved_cam3d_config(tmp_path) -> None:
    owner = ProjectService.create_default(tmp_path / "owner-config")
    session = owner.new_project(tmp_path, "CAM3D Recovery")
    project_id = session.manifest.project_id
    value = zone(project_id=project_id)
    owner.stage_cam3d_config(Cam3DProjectConfig(project_id, (value,)))
    snapshot = owner.autosave()
    assert snapshot is not None
    assert (snapshot.path / "cam" / "cam3d_foundation.hms.json").is_file()
    assert not (snapshot.path / "cache" / "cam3d").exists()

    opener = ProjectService.create_default(tmp_path / "opener-config")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(session.root_path)
    recovered = opener.recover_project(raised.value.assessment)
    assert recovered.cam3d_config == Cam3DProjectConfig(project_id, (value,))
    assert not recovered.is_dirty

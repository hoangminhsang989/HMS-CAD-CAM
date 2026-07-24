"""Stage 8A.4.1 project Save/Open and autosave profile integration."""

from __future__ import annotations

import pytest

from hms_cadcam.cam.application import basic_parallel_resources
from hms_cadcam.cam.domain import (
    DEFAULT_TOOL_PROFILE_REGISTRY,
    LengthUnit,
    Revision,
    preview_tool_profile_capture,
)
from hms_cadcam.cam.persistence import CamSqliteRepository
from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.service import ProjectService


def _configure_project_tool(service: ProjectService):
    tool, holder, assembly, machine = basic_parallel_resources(LengthUnit.MM)
    service.execute_cam_command(
        lambda app: app.add_basic_resources(tool, holder, assembly, machine)
    )
    preview = preview_tool_profile_capture(
        tool,
        "parallel_finishing_3d",
        "Gia công tinh song song",
        {"stepover_mm": 0.65, "direction_angle_degrees": 30.0},
        overridden_field_ids=frozenset(
            {"stepover_mm", "direction_angle_degrees"}
        ),
        registry=DEFAULT_TOOL_PROFILE_REGISTRY,
    )
    service.execute_cam_command(
        lambda app: app.save_tool_program_profile(
            preview,
            expected_configuration_revision=Revision(0),
            holder_fingerprint=holder.content_fingerprint,
        )
    )
    return tool


def test_project_save_open_round_trip_preserves_optional_profiles(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Tool Profiles")
    tool = _configure_project_tool(service)

    service.save()
    service.close_project()
    reopened = service.open_project(session.root_path)
    restored = next(
        item for item in reopened.cam_snapshot.tool_definitions
        if item.tool_id == tool.tool_id
    )

    assert restored.configuration_revision == Revision(1)
    assert restored.program_profiles[0].sparse_mapping == {
        "direction_angle_degrees": 30.0,
        "stepover_mm": 0.65,
    }
    assert ProjectDatabase().current_schema_version(
        reopened.root_path / "project.db"
    ) == 4
    service.close_project()


def test_autosave_snapshot_contains_same_typed_tool_profile(tmp_path) -> None:
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Tool Profile Autosave")
    tool = _configure_project_tool(service)

    autosave = service.autosave(expected_project_id=session.manifest.project_id)

    assert autosave is not None
    restored = CamSqliteRepository().load(autosave.path / "project.db")
    saved_tool = next(
        item for item in restored.tool_definitions if item.tool_id == tool.tool_id
    )
    assert saved_tool.program_profiles[0].strategy_id == (
        "parallel_finishing_3d"
    )
    assert saved_tool.program_profiles[0].sparse_mapping["stepover_mm"] == 0.65
    assert service.is_dirty
    service.save()
    service.close_project()


def test_recovery_restores_unsaved_tool_profile(tmp_path) -> None:
    owner = ProjectService.create_default(tmp_path / "owner-config")
    session = owner.new_project(tmp_path, "Tool Profile Recovery")
    tool = _configure_project_tool(owner)
    snapshot = owner.autosave()
    assert snapshot is not None

    opener = ProjectService.create_default(tmp_path / "opener-config")
    opener._session_locks._pid_checker = lambda _pid: False
    with pytest.raises(RecoveryRequiredError) as raised:
        opener.open_project(session.root_path)

    recovered = opener.recover_project(raised.value.assessment)
    restored = next(
        item
        for item in recovered.cam_snapshot.tool_definitions
        if item.tool_id == tool.tool_id
    )
    assert restored.configuration_revision == Revision(1)
    assert restored.program_profiles[0].sparse_mapping == {
        "direction_angle_degrees": 30.0,
        "stepover_mm": 0.65,
    }
    assert not recovered.is_dirty
    opener.close_project()

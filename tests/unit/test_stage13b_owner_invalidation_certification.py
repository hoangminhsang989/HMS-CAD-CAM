"""R26 tests invoke actual owner transition methods, not only invalidation helpers."""
from __future__ import annotations

from pathlib import Path

from hms_cadcam.ai_assist.advisor_coordinator import CuttingAdvisorCoordinator
from hms_cadcam.ai_assist.lifecycle import AiAssistBroker
from hms_cadcam.ai_assist.supervisor import NoOpWorkerSupervisor
from hms_cadcam.project.service import ProjectService
from test_stage13b_camworkspace_dispatch_certification import _facing_workspace


def test_project_close_actual_service_transition_invalidates_owner(tmp_path: Path):
    reasons: list[str] = []
    service = ProjectService.create_default(tmp_path / "config", lifecycle_hook=reasons.append)
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service.create_project_from_source(tmp_path, "owner-close", source)
    service.close_project(discard_changes=True)
    assert reasons == ["PROJECT_CLOSED"]
    assert service.current_project is None


def test_document_unload_actual_service_transition_invalidates_owner(tmp_path: Path):
    reasons: list[str] = []
    service = ProjectService.create_default(tmp_path / "config", lifecycle_hook=reasons.append)
    source = tmp_path / "part.step"
    source.write_text("ISO-10303-21;END-ISO-10303-21;", encoding="utf-8")
    service.create_project_from_source(tmp_path, "owner-document", source)
    # close_workspace uses the production document owner when one is active;
    # project mode remains a valid close boundary otherwise.
    service.close_workspace(discard_changes=True)
    assert reasons == ["PROJECT_CLOSED"]


def test_application_owner_shutdown_is_idempotent_and_releases_broker():
    coordinator = CuttingAdvisorCoordinator(
        AiAssistBroker(capability_enabled=False, master_enabled=False),
        NoOpWorkerSupervisor(), capability_enabled=False, preference_enabled=False,
    )
    assert coordinator.shutdown("APPLICATION_SHUTDOWN").reason == "APPLICATION_SHUTDOWN"
    assert coordinator.shutdown("APPLICATION_SHUTDOWN").status == "OFF"

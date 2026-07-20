"""Stage 7B.8.2 Reaming compute/publish-to-viewer integration tests."""

from __future__ import annotations

import pytest

import hms_cadcam.cam.application.service as cam_application_service
from hms_cadcam.cam.application import ReamingGenerationError, ReamingGenerator
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    DiagnosticCode,
    DiagnosticSeverity,
    GeometryResolutionStatus,
    ResolvedDrillingGeometry,
    ValidationDiagnostic,
)
from hms_cadcam.cam.persistence import ToolpathArtifactStoreError
from hms_cadcam.cam.toolpath import ToolpathPublishResult
from hms_cadcam.project.service import ProjectService
from hms_cadcam.viewer.toolpath import ToolpathPresentationRegistry
from tests.unit.test_reaming_strategy import _inputs


def _service_with_reaming(tmp_path):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Reaming Viewer")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    _generator, inputs, holder, resolved = _inputs()
    service.execute_cam_command(lambda app: app.add_setup(job_id, inputs.setup))
    service.execute_cam_command(lambda app: app.add_basic_resources(
        inputs.tool,
        holder,
        inputs.assembly,
        inputs.machine,
    ))
    service.execute_cam_command(lambda app: app.update_tree(
        job_id,
        inputs.setup.setup_id,
        lambda tree: tree.add_operation(
            tree.root_id,
            "Reaming",
            inputs.operation,
        ),
    ))
    return service, session, inputs, resolved


def test_successful_recompute_replaces_only_current_reaming_presentation(
    tmp_path,
) -> None:
    service, _session, inputs, resolved = _service_with_reaming(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)

    first = service.compute_reaming(
        inputs.operation.operation_id,
        expected_generation=service.cam_generation,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert first.accepted and first.artifact is not None
    first_request = registry.request_display(
        inputs.operation.operation_id,
        generation=service.cam_generation,
    )
    assert first_request is not None
    assert registry.display(
        first.artifact,
        generation=service.cam_generation,
        request=first_request,
        expected_strategy_key="reaming_v1",
        expected_artifact_fingerprint=first.artifact.artifact_fingerprint,
    )
    old_presentation = registry.presentations[0]
    assert old_presentation.nominal_diameter == inputs.strategy.nominal_diameter
    assert old_presentation.pre_hole_diameter == inputs.strategy.pre_hole_diameter
    assert old_presentation.stock_per_side == inputs.strategy.stock_per_side
    assert old_presentation.feed_per_minute == inputs.strategy.feed_per_minute

    obsolete_request = registry.request_display(
        inputs.operation.operation_id,
        generation=service.cam_generation,
    )
    second = service.compute_reaming(
        inputs.operation.operation_id,
        expected_generation=service.cam_generation,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert second.accepted and second.artifact is not None
    current_request = registry.request_display(
        inputs.operation.operation_id,
        generation=service.cam_generation,
    )
    assert obsolete_request is not None and current_request is not None
    assert not registry.display(
        second.artifact,
        generation=service.cam_generation,
        request=obsolete_request,
    )
    assert registry.presentations == (old_presentation,)
    assert registry.display(
        second.artifact,
        generation=service.cam_generation,
        request=current_request,
        expected_strategy_key="reaming_v1",
        expected_artifact_fingerprint=second.artifact.artifact_fingerprint,
    )
    assert registry.presentations[0].artifact_id == second.artifact.artifact_id


@pytest.mark.parametrize("failure_kind", ("stale", "validation"))
def test_failed_recompute_keeps_valid_artifact_and_presentation(
    tmp_path,
    failure_kind: str,
) -> None:
    service, _session, inputs, resolved = _service_with_reaming(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)
    success = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted and success.artifact is not None
    assert registry.display(success.artifact, generation=service.cam_generation)
    old_presentation = registry.presentations
    old_artifact = service.load_toolpath_artifact(inputs.operation.operation_id)
    invalid_resolution = (
        ResolvedDrillingGeometry(
            GeometryResolutionStatus.STALE,
            diagnostics=(ValidationDiagnostic(
                DiagnosticSeverity.ERROR,
                DiagnosticCode.REAM_GEOMETRY_STALE,
                "stale",
            ),),
        )
        if failure_kind == "stale"
        else object()
    )

    failure = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: invalid_resolution,
    )

    assert not failure.accepted and failure.artifact is None
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact
    assert registry.presentations == old_presentation


def test_artifact_publish_failure_keeps_valid_artifact_and_presentation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _session, inputs, resolved = _service_with_reaming(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)
    success = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted and success.artifact is not None
    assert registry.display(success.artifact, generation=service.cam_generation)
    old_presentation = registry.presentations
    old_artifact = service.load_toolpath_artifact(inputs.operation.operation_id)

    def fail_publish(*_args, **_kwargs):
        raise ToolpathArtifactStoreError("injected store failure")

    monkeypatch.setattr(
        service._cam_application._artifact_store,
        "publish",
        fail_publish,
    )
    failure = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )

    assert not failure.accepted and failure.artifact is None
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact
    assert registry.presentations == old_presentation


def test_generation_failure_keeps_valid_artifact_and_presentation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _session, inputs, resolved = _service_with_reaming(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)
    success = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted and success.artifact is not None
    assert registry.display(success.artifact, generation=service.cam_generation)
    old_presentation = registry.presentations
    old_artifact = service.load_toolpath_artifact(inputs.operation.operation_id)

    def fail_generate(_generator, _computing):
        raise ReamingGenerationError(
            DiagnosticCode.REAM_GENERATION_FAILED,
            "injected generation failure",
        )

    monkeypatch.setattr(ReamingGenerator, "generate", fail_generate)
    failure = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )

    assert not failure.accepted and failure.artifact is None
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact
    assert registry.presentations == old_presentation


def test_stale_computation_token_keeps_valid_artifact_and_presentation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _session, inputs, resolved = _service_with_reaming(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)
    success = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted and success.artifact is not None
    assert registry.display(success.artifact, generation=service.cam_generation)
    old_presentation = registry.presentations
    old_artifact = service.load_toolpath_artifact(inputs.operation.operation_id)
    old_operation = success.operation

    def reject_stale_token(*_args, **_kwargs):
        return ToolpathPublishResult(
            old_operation,
            None,
            False,
            "stale_token",
        )

    monkeypatch.setattr(
        cam_application_service,
        "publish_toolpath",
        reject_stale_token,
    )
    failure = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )

    assert not failure.accepted and failure.artifact is None
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact
    assert registry.presentations == old_presentation


def test_project_switch_rejects_old_reaming_callback(tmp_path) -> None:
    service, session, inputs, resolved = _service_with_reaming(tmp_path)
    registry = ToolpathPresentationRegistry()
    old_generation = service.cam_generation
    registry.bind_project(old_generation)
    success = service.compute_reaming(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert success.accepted and success.artifact is not None
    pending = registry.request_display(
        inputs.operation.operation_id,
        generation=old_generation,
    )
    assert pending is not None

    service.save()
    service.close_project()
    service.open_project(session.root_path)
    registry.bind_project(service.cam_generation)

    assert registry.presentations == ()
    assert not registry.display(
        success.artifact,
        generation=old_generation,
        request=pending,
    )

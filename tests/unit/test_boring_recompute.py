"""Stage 7B.9.2 Boring compute/publish-to-viewer integration tests."""

from __future__ import annotations

from uuid import uuid4

import pytest

import hms_cadcam.cam.application.service as cam_application_service
from hms_cadcam.cam.application import BoringGenerationError, BoringGenerator
from hms_cadcam.cam.domain import (
    ArtifactStatus,
    ComputationToken,
    ContentFingerprint,
    DependencyFingerprint,
    DiagnosticCode,
    DiagnosticSeverity,
    GeometryResolutionStatus,
    OperationFamily,
    ResolvedDrillingGeometry,
    Revision,
    ValidationDiagnostic,
)
from hms_cadcam.cam.persistence import ToolpathArtifactStoreError
from hms_cadcam.cam.toolpath import ToolpathPublishResult
from hms_cadcam.project.exceptions import ProjectError
from hms_cadcam.project.service import ProjectService
from hms_cadcam.viewer.toolpath import ToolpathPresentationRegistry
from tests.unit.test_boring_strategy import _inputs


def _service_with_boring(tmp_path):
    service = ProjectService.create_default(tmp_path / "config")
    session = service.new_project(tmp_path, "Boring Viewer")
    service.execute_cam_command(lambda app: app.create_job("Job"))
    job_id = service.cam_snapshot.active_job_id
    _generator, inputs, resolved = _inputs()
    service.execute_cam_command(lambda app: app.add_setup(job_id, inputs.setup))
    service.execute_cam_command(lambda app: app.add_basic_resources(
        inputs.tool,
        inputs.holder,
        inputs.assembly,
        inputs.machine,
    ))
    service.execute_cam_command(lambda app: app.update_tree(
        job_id,
        inputs.setup.setup_id,
        lambda tree: tree.add_operation(
            tree.root_id,
            "Boring",
            inputs.operation,
        ),
    ))
    return service, session, inputs, resolved


def _display_current(registry, service, result, *, request=None) -> bool:
    assert result.accepted and result.artifact is not None
    artifact = result.artifact
    return registry.display(
        artifact,
        generation=service.cam_generation,
        request=request,
        expected_strategy_key="boring_v1",
        expected_strategy_version=1,
        expected_operation_family=OperationFamily.DRILLING,
        expected_artifact_fingerprint=artifact.artifact_fingerprint,
        expected_input_fingerprint=artifact.input_fingerprint,
        expected_computation_token=artifact.computation_token,
        expected_operation_revision=artifact.operation_revision,
    )


def test_successful_recompute_replaces_only_current_boring_presentation(
    tmp_path,
) -> None:
    service, _session, inputs, resolved = _service_with_boring(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)

    first = service.compute_boring(
        inputs.operation.operation_id,
        expected_generation=service.cam_generation,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    first_request = registry.request_display(
        inputs.operation.operation_id,
        generation=service.cam_generation,
    )
    assert first_request is not None
    assert _display_current(registry, service, first, request=first_request)
    old_presentation = registry.presentations[0]
    assert old_presentation.finished_bore_diameter == (
        inputs.strategy.finished_bore_diameter
    )
    assert old_presentation.pre_bore_diameter == inputs.strategy.pre_bore_diameter
    assert old_presentation.radial_stock == inputs.strategy.radial_stock
    assert old_presentation.feed_per_minute == inputs.strategy.feed_per_minute
    registry.set_visible(inputs.operation.operation_id, False)

    obsolete_request = registry.request_display(
        inputs.operation.operation_id,
        generation=service.cam_generation,
    )
    second = service.compute_boring(
        inputs.operation.operation_id,
        expected_generation=service.cam_generation,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    current_request = registry.request_display(
        inputs.operation.operation_id,
        generation=service.cam_generation,
    )
    assert obsolete_request is not None and current_request is not None
    assert second.accepted and second.artifact is not None
    assert not registry.display(
        second.artifact,
        generation=service.cam_generation,
        request=obsolete_request,
    )
    assert registry.presentations[0].artifact_id == old_presentation.artifact_id
    assert _display_current(registry, service, second, request=current_request)
    assert registry.presentations[0].artifact_id == second.artifact.artifact_id
    assert not registry.presentations[0].visible


@pytest.mark.parametrize(
    "dependency",
    (
        "geometry", "canonical_order", "wcs", "finished_diameter",
        "pre_bore_diameter", "rpm_feed", "depth_clearance_retract",
        "dwell", "spindle_coolant_retract_policy", "tool_assembly",
        "tool_definition", "holder", "machine", "operation_revision",
    ),
)
def test_stale_boring_input_fingerprint_never_replaces_presentation(
    tmp_path,
    dependency: str,
) -> None:
    service, _session, inputs, resolved = _service_with_boring(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)
    success = service.compute_boring(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert _display_current(registry, service, success)
    previous = registry.presentations
    request = registry.request_display(
        inputs.operation.operation_id,
        generation=service.cam_generation,
    )
    assert request is not None and success.artifact is not None

    assert not registry.display(
        success.artifact,
        generation=service.cam_generation,
        request=request,
        expected_strategy_key="boring_v1",
        expected_input_fingerprint=DependencyFingerprint.from_payload({
            dependency: "changed",
        }),
    )
    assert registry.presentations == previous


def test_token_request_generation_operation_and_artifact_guards_keep_old_view(
    tmp_path,
) -> None:
    service, _session, inputs, resolved = _service_with_boring(tmp_path)
    registry = ToolpathPresentationRegistry()
    generation = service.cam_generation
    registry.bind_project(generation)
    success = service.compute_boring(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert _display_current(registry, service, success)
    assert success.artifact is not None
    previous = registry.presentations
    stale = registry.request_display(inputs.operation.operation_id, generation=generation)
    current = registry.request_display(inputs.operation.operation_id, generation=generation)
    assert stale is not None and current is not None

    rejected = (
        {"generation": generation - 1, "request": current},
        {"generation": generation, "request": stale},
        {"generation": generation, "request": current, "operation_exists": False},
        {"generation": generation, "request": current, "operation_enabled": False},
        {
            "generation": generation,
            "request": current,
            "expected_computation_token": ComputationToken(uuid4(), 999),
        },
        {
            "generation": generation,
            "request": current,
            "expected_operation_revision": Revision(
                success.artifact.operation_revision.value + 1
            ),
        },
        {
            "generation": generation,
            "request": current,
            "expected_artifact_fingerprint": ContentFingerprint.from_payload({
                "artifact": "changed",
            }),
        },
    )
    for guard in rejected:
        assert not registry.display(success.artifact, **guard)
        assert registry.presentations == previous


@pytest.mark.parametrize("failure_kind", ("stale_geometry", "invalid_result"))
def test_failed_recompute_keeps_valid_artifact_and_presentation(
    tmp_path,
    failure_kind: str,
) -> None:
    service, _session, inputs, resolved = _service_with_boring(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)
    success = service.compute_boring(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert _display_current(registry, service, success)
    old_presentation = registry.presentations
    old_artifact = service.load_toolpath_artifact(inputs.operation.operation_id)
    invalid_resolution = (
        ResolvedDrillingGeometry(
            GeometryResolutionStatus.STALE,
            diagnostics=(ValidationDiagnostic(
                DiagnosticSeverity.ERROR,
                DiagnosticCode.BORE_GEOMETRY_STALE,
                "stale",
            ),),
        )
        if failure_kind == "stale_geometry"
        else object()
    )

    failure = service.compute_boring(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: invalid_resolution,
    )

    assert not failure.accepted and failure.artifact is None
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact
    assert registry.presentations == old_presentation


@pytest.mark.parametrize("failure_kind", ("generation", "store", "stale_token"))
def test_publish_failures_keep_valid_artifact_and_presentation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    service, _session, inputs, resolved = _service_with_boring(tmp_path)
    registry = ToolpathPresentationRegistry()
    registry.bind_project(service.cam_generation)
    success = service.compute_boring(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert _display_current(registry, service, success)
    old_presentation = registry.presentations
    old_artifact = service.load_toolpath_artifact(inputs.operation.operation_id)
    old_operation = success.operation

    if failure_kind == "generation":
        def fail_generate(_self, _inputs_value):
            raise BoringGenerationError(
                DiagnosticCode.BORE_GENERATION_FAILED,
                "injected generation failure",
            )

        monkeypatch.setattr(BoringGenerator, "generate", fail_generate)
    elif failure_kind == "store":
        def fail_publish(*_args, **_kwargs):
            raise ToolpathArtifactStoreError("injected store failure")

        monkeypatch.setattr(
            service._cam_application._artifact_store,
            "publish",
            fail_publish,
        )
    else:
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

    failure = service.compute_boring(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )

    assert not failure.accepted and failure.artifact is None
    assert failure.operation.artifact_state.status is ArtifactStatus.VALID
    assert service.load_toolpath_artifact(inputs.operation.operation_id) == old_artifact
    assert registry.presentations == old_presentation


def test_project_new_open_close_reject_old_boring_callback(tmp_path) -> None:
    service, session, inputs, resolved = _service_with_boring(tmp_path)
    registry = ToolpathPresentationRegistry()
    old_generation = service.cam_generation
    registry.bind_project(old_generation)
    success = service.compute_boring(
        inputs.operation.operation_id,
        geometry_resolver=lambda _geometry, _depth: resolved,
    )
    assert _display_current(registry, service, success)
    pending = registry.request_display(
        inputs.operation.operation_id,
        generation=old_generation,
    )
    assert pending is not None and success.artifact is not None
    service.save()

    service.close_project()
    registry.bind_project(None)
    assert registry.presentations == ()
    assert not registry.display(
        success.artifact,
        generation=old_generation,
        request=pending,
    )
    with pytest.raises(ProjectError):
        service.load_toolpath_artifact(inputs.operation.operation_id)

    service.open_project(session.root_path)
    registry.bind_project(service.cam_generation)
    assert not registry.display(
        success.artifact,
        generation=old_generation,
        request=pending,
    )
    service.close_project()
    service.new_project(tmp_path, "Boring Viewer New")
    registry.bind_project(service.cam_generation)
    assert registry.presentations == ()
    assert not registry.display(
        success.artifact,
        generation=old_generation,
        request=pending,
    )

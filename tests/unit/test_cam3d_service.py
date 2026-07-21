"""Latest-wins, cancellation and atomic publish tests for CAM 3D service."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from uuid import uuid4

import pytest

from hms_cadcam.cam.cam3d import (
    Cam3DCalculationContext,
    Cam3DCalculationRequest,
    Cam3DCalculationState,
    Cam3DDiagnostic,
    Cam3DDiagnosticCode,
    Cam3DDiagnosticSeverity,
    Cam3DGeometryService,
    Cam3DMeshError,
    Cam3DResolvedSurfaceMesh,
    Cam3DTolerancePolicy,
)
from hms_cadcam.cam.domain import GeometryFingerprint, Point3, Revision, SetupId
from tests.unit._cam3d_fixtures import fragments, request, safe_motion, tolerance, zone


class _Mesher:
    def __init__(self, callback: Callable[[], None] | None = None) -> None:
        self.callback = callback
        self.calls = 0

    def tessellate(self, surface, policy, cancellation=None):
        self.calls += 1
        if self.callback is not None and self.calls == 1:
            self.callback()
        value = zone(
            project_id=surface.project_id,
            source_id=surface.geometry.source_id,
            revision=surface.geometry.expected_source_revision,
            with_check=False,
        )
        base = fragments(value)[0]
        return Cam3DResolvedSurfaceMesh(surface, base.vertices, base.triangles)


class _FailingMesher:
    def tessellate(self, surface, policy, cancellation=None):
        raise Cam3DMeshError(
            Cam3DDiagnostic(
                Cam3DDiagnosticCode.SURFACE_MISSING,
                Cam3DDiagnosticSeverity.ERROR,
                "Deleted face",
                source_reference_id=surface.geometry.reference_id,
            )
        )


def _bound() -> tuple[Cam3DGeometryService, Cam3DCalculationRequest]:
    value = request(zone())
    service = Cam3DGeometryService()
    service.bind_project(value.project_id, value.project_generation)
    return service, value


def test_service_successful_atomic_publish() -> None:
    service, value = _bound()
    result = service.calculate(value, _Mesher())
    assert result.published
    assert result.state is Cam3DCalculationState.CURRENT
    assert result.context is service.current_context
    assert result.context is not None
    assert result.context.request_token == value.request_token
    assert result.context.calculation_mesh.statistics.surface_count == 2
    restored = Cam3DCalculationContext.from_dict(result.context.to_dict())
    assert restored == result.context
    assert restored.fingerprint == result.context.fingerprint


def test_context_runtime_ids_do_not_affect_deterministic_identity() -> None:
    service, value = _bound()
    first = service.calculate(value, _Mesher())
    assert first.context is not None
    retry = dataclasses.replace(value, request_token=uuid4())
    second = service.calculate(retry, _Mesher())
    assert second.context is not None
    assert first.context.context_id != second.context.context_id
    assert first.context.request_token != second.context.request_token
    assert first.context.fingerprint == second.context.fingerprint


def test_context_future_version_is_rejected() -> None:
    service, value = _bound()
    result = service.calculate(value, _Mesher())
    assert result.context is not None
    payload = result.context.to_dict()
    payload["format_version"] = 2
    with pytest.raises(Exception):
        Cam3DCalculationContext.from_dict(payload)


def test_service_current_request_token_is_latest_wins() -> None:
    service, value = _bound()
    newer = dataclasses.replace(value, request_token=uuid4())
    result = service.calculate(value, _Mesher(), current_request=lambda: newer)
    assert not result.published
    assert result.state is Cam3DCalculationState.STALE
    assert result.context is None


@pytest.mark.parametrize("change", ["geometry", "setup", "tolerance", "selection"])
def test_service_rejects_changed_inputs_before_publish(change: str) -> None:
    service, value = _bound()
    changed_zone = value.zone
    if change == "geometry":
        changed_zone = dataclasses.replace(
            changed_zone,
            geometry_fingerprint=GeometryFingerprint.from_payload({"changed": True}),
        )
    elif change == "setup":
        changed_zone = dataclasses.replace(changed_zone, setup_id=SetupId.new())
    elif change == "tolerance":
        changed_zone = dataclasses.replace(changed_zone, tolerance=tolerance(0.02))
    else:
        changed_zone = zone(
            project_id=value.project_id,
            revision=value.zone.geometry_revision,
            with_check=False,
        )
    changed = Cam3DCalculationRequest.create(
        project_id=changed_zone.project_id,
        project_generation=value.project_generation,
        job_id=changed_zone.job_id,
        setup_id=changed_zone.setup_id,
        zone=changed_zone,
        tool_assembly_fingerprint=value.tool_assembly_fingerprint,
        tool_definition_fingerprint=value.tool_definition_fingerprint,
        safe_motion_policy=safe_motion(changed_zone),
    )
    result = service.calculate(value, _Mesher(), current_request=lambda: changed)
    assert result.state is Cam3DCalculationState.STALE
    assert not result.published


def test_service_generation_change_fails_closed() -> None:
    service, value = _bound()
    service.bind_project(value.project_id, value.project_generation + 1)
    result = service.calculate(value, _Mesher())
    assert not result.published
    assert result.state is Cam3DCalculationState.FAILED
    assert result.diagnostics[0].code is Cam3DDiagnosticCode.INVALID_REQUEST


def test_service_cancellation_has_no_partial_publish() -> None:
    service, value = _bound()
    result = service.calculate(value, _Mesher(), cancellation=lambda: True)
    assert result.state is Cam3DCalculationState.CANCELLED
    assert not result.published
    assert service.current_context is None


def test_service_project_close_discards_worker_callback() -> None:
    service, value = _bound()
    result = service.calculate(value, _Mesher(service.close_project))
    assert not result.published
    assert result.state in {
        Cam3DCalculationState.CANCELLED,
        Cam3DCalculationState.STALE,
    }
    assert service.current_context is None


def test_service_project_switch_discards_worker_callback() -> None:
    service, value = _bound()
    result = service.calculate(
        value,
        _Mesher(lambda: service.bind_project(uuid4(), 0)),
    )
    assert not result.published
    assert result.state in {
        Cam3DCalculationState.CANCELLED,
        Cam3DCalculationState.STALE,
    }


def test_previous_valid_context_retained_on_new_failure() -> None:
    service, value = _bound()
    first = service.calculate(value, _Mesher())
    assert first.context is not None
    retry = dataclasses.replace(value, request_token=uuid4())
    failed = service.calculate(retry, _FailingMesher())
    assert failed.state is Cam3DCalculationState.FAILED
    assert not failed.published
    assert failed.previous_valid_retained
    assert failed.context is first.context
    assert service.current_context is first.context


def test_newer_request_invalidates_older_token() -> None:
    service, older = _bound()
    newer = dataclasses.replace(older, request_token=uuid4())

    def publish_newer() -> None:
        result = service.calculate(newer, _Mesher())
        assert result.published

    old_result = service.calculate(older, _Mesher(publish_newer))
    assert not old_result.published
    assert service.current_context is not None
    assert service.current_context.request_token == newer.request_token


def test_service_surface_failure_is_structured_and_unpublished() -> None:
    service, value = _bound()
    result = service.calculate(value, _FailingMesher())
    assert result.state is Cam3DCalculationState.FAILED
    assert result.diagnostics[0].code is Cam3DDiagnosticCode.SURFACE_MISSING
    assert result.diagnostics[0].source_reference_id is not None
    assert result.context is None

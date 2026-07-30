"""Native OCP verification for the Stage 9A.8 WP3-B preview adapter."""

from __future__ import annotations

from dataclasses import replace
import math
from threading import Event
from uuid import uuid4

import pytest

from hms_cadcam.cad.models import CadDocumentId, CadGeometryKind
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cad.persistent_keys import build_persistent_object_map
from hms_cadcam.cam.adapters.ocp_cam3d import OcpCam3DSurfaceAdapter
from hms_cadcam.cam.adapters.ocp_cam3d_preview import (
    OcpCam3DPreviewTessellator,
    preview_tolerance_policy,
)
from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DPreviewCompletionState,
    Cam3DPreviewCoordinator,
    Cam3DPreviewDiagnosticCode,
    Cam3DPreviewMesh,
)
from hms_cadcam.cam.application.cam3d_request import (
    Cam3DActiveSetupContext,
    Cam3DCalculationInputSnapshot,
    Cam3DCalculationJobId,
    Cam3DCalculationOwnershipKey,
    Cam3DCalculationPolicy,
    Cam3DCalculationRequestContract,
    Cam3DPreviewCacheKey,
    Cam3DRequestFingerprint,
    Cam3DZoneInputSnapshot,
)
from hms_cadcam.cam.cam3d import (
    Cam3DCancelledError,
    Cam3DDiagnosticCode,
    Cam3DMeshError,
    Cam3DResolvedSurfaceMesh,
    CamSurfaceRole,
)
from hms_cadcam.cam.domain import (
    DependencyFingerprint,
    LengthUnit,
    Revision,
    SetupId,
    ToolAssemblyId,
    ToolProgramProfileId,
    WcsFrame,
)
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode


def _native_context(face_indices: tuple[int, ...] = (1,)):
    kernel = OcpCadKernel()
    document_id = kernel.create_box(20, 10, 5)
    project_id, source_id = uuid4(), uuid4()
    tree = kernel.get_document_tree(document_id)
    mapping = build_persistent_object_map(source_id, CadGeometryKind.BREP, tree)
    adapter = OcpCam3DSurfaceAdapter(
        kernel,
        document_id,
        source_id,
        project_id,
        mapping,
        source_revision=Revision(1),
    )
    object_id = tree.presentation_nodes[0].object_id
    bounds = kernel.get_bounding_box(document_id)
    surfaces = tuple(
        adapter.bind_selection(
            SelectionMetadata(
                document_id,
                f"{document_id}:face:{index}",
                SelectionMode.FACE,
                bounds,
                object_id,
            ),
            CamSurfaceRole.PART,
        )
        for index in face_indices
    )
    ownership = Cam3DCalculationOwnershipKey(
        project_id, document_id, source_id, SetupId.new()
    )
    return kernel, document_id, adapter, ownership, surfaces, bounds, object_id


def _request(
    ownership: Cam3DCalculationOwnershipKey,
    surfaces,
    *,
    generation: int = 3,
    tolerance_mm: float = 0.01,
) -> Cam3DCalculationRequestContract:
    setup = Cam3DActiveSetupContext(
        ownership,
        generation,
        Revision(4),
        WcsFrame.identity(LengthUnit.MM),
    )
    zone = Cam3DZoneInputSnapshot(ownership, generation, tuple(surfaces))
    inputs = Cam3DCalculationInputSnapshot(
        setup,
        zone,
        ToolAssemblyId.new(),
        DependencyFingerprint.from_payload({"assembly": 1}),
        ToolProgramProfileId.new(),
        DependencyFingerprint.from_payload({"profile": 1}),
        float(tolerance_mm),
        0.0,
        None,
        None,
        2.0,
        0.0,
        Cam3DCalculationPolicy(),
    )
    fingerprint = Cam3DRequestFingerprint.from_inputs(inputs)
    return Cam3DCalculationRequestContract(
        Cam3DCalculationJobId.new(),
        inputs,
        fingerprint,
        Cam3DPreviewCacheKey.from_request_fingerprint(
            fingerprint, inputs.policy
        ),
    )


def test_real_box_face_tessellates_to_deterministic_native_free_preview() -> None:
    kernel, document_id, adapter, ownership, surfaces, before_bounds, object_id = (
        _native_context((1, 2, 3))
    )
    try:
        request = _request(ownership, surfaces)
        tessellator = OcpCam3DPreviewTessellator(adapter, ownership)
        first = tessellator.tessellate(request, lambda: False)
        second = tessellator.tessellate(request, lambda: False)

        assert first == second
        assert isinstance(first, Cam3DPreviewMesh)
        assert first.vertex_count > 0 and first.triangle_count > 0
        assert all(math.isfinite(value) for point in first.vertices for value in point)
        assert all(
            0 <= index < first.vertex_count
            for triangle in first.triangles
            for index in triangle
        )
        assert all(
            math.isclose(
                math.sqrt(sum(value * value for value in normal)),
                1.0,
                rel_tol=0.0,
                abs_tol=1.0e-6,
            )
            for normal in first.triangle_normals
        )
        assert all(
            not type(value).__module__.startswith("OCP")
            for collection in (first.vertices, first.triangles, first.triangle_normals)
            for item in collection
            for value in item
        )

        after_bounds = kernel.get_bounding_box(document_id)
        rebound = adapter.bind_selection(
            SelectionMetadata(
                document_id,
                f"{document_id}:face:1",
                SelectionMode.FACE,
                after_bounds,
                object_id,
            ),
            CamSurfaceRole.PART,
        )
        assert after_bounds == before_bounds
        assert rebound.face_identity == surfaces[0].face_identity
        assert rebound.geometry.expected_geometry_fingerprint == (
            surfaces[0].geometry.expected_geometry_fingerprint
        )
    finally:
        kernel.release_document(document_id)


def test_tolerance_mapping_is_versioned_and_deterministic() -> None:
    kernel, document_id, adapter, ownership, surfaces, _bounds, _object_id = (
        _native_context()
    )
    try:
        request = _request(ownership, surfaces, tolerance_mm=0.025)
        policy = preview_tolerance_policy(request)
        assert policy.chordal_tolerance == 0.025
        assert policy.angular_tolerance == 0.2
        assert policy.calculation_epsilon == 1.0e-8
        assert policy.boundary_tolerance == 0.001
        assert policy.contact_tolerance == 0.001
    finally:
        kernel.release_document(document_id)


class _CancelAfterFirstSurface:
    def __init__(self, delegate: OcpCam3DSurfaceAdapter, cancelled: Event) -> None:
        self.delegate = delegate
        self.cancelled = cancelled
        self.calls = 0

    def tessellate(self, surface, tolerance, cancellation=None):
        result = self.delegate.tessellate(surface, tolerance, cancellation)
        self.calls += 1
        if self.calls == 1:
            self.cancelled.set()
        return result


def test_cancellation_before_and_between_surface_phases_is_fail_closed() -> None:
    kernel, document_id, adapter, ownership, surfaces, _bounds, _object_id = (
        _native_context((1, 2))
    )
    try:
        request = _request(ownership, surfaces)
        direct = OcpCam3DPreviewTessellator(adapter, ownership)
        with pytest.raises(Cam3DCancelledError):
            direct.tessellate(request, lambda: True)

        cancelled = Event()
        phased = OcpCam3DPreviewTessellator(
            _CancelAfterFirstSurface(adapter, cancelled), ownership
        )
        with pytest.raises(Cam3DCancelledError):
            phased.tessellate(request, cancelled.is_set)
    finally:
        kernel.release_document(document_id)


class _EmptyMesher:
    def tessellate(self, surface, tolerance, cancellation=None):
        return Cam3DResolvedSurfaceMesh(surface, (), ())


def test_empty_mesh_and_stale_geometry_fail_closed() -> None:
    kernel, document_id, adapter, ownership, surfaces, _bounds, _object_id = (
        _native_context()
    )
    try:
        request = _request(ownership, surfaces)
        with pytest.raises(Cam3DMeshError) as empty:
            OcpCam3DPreviewTessellator(_EmptyMesher(), ownership).tessellate(
                request, lambda: False
            )
        assert empty.value.diagnostic.code is Cam3DDiagnosticCode.MESH_EMPTY

        stale_surface = replace(
            surfaces[0],
            geometry=replace(
                surfaces[0].geometry,
                expected_source_revision=Revision(2),
            ),
        )
        stale = _request(ownership, (stale_surface,))
        with pytest.raises(Cam3DMeshError) as captured:
            OcpCam3DPreviewTessellator(adapter, ownership).tessellate(
                stale, lambda: False
            )
        assert captured.value.diagnostic.code is Cam3DDiagnosticCode.SURFACE_STALE
    finally:
        kernel.release_document(document_id)


def test_foreign_ownership_is_rejected_before_geometry_resolution() -> None:
    kernel, document_id, adapter, ownership, surfaces, _bounds, _object_id = (
        _native_context()
    )
    try:
        foreign = replace(ownership, setup_id=SetupId.new())
        request = _request(foreign, surfaces)
        with pytest.raises(ValueError):
            OcpCam3DPreviewTessellator(adapter, ownership).tessellate(
                request, lambda: False
            )
    finally:
        kernel.release_document(document_id)


def test_real_ocp_failure_is_mapped_before_coordinator_publication() -> None:
    kernel, document_id, adapter, ownership, surfaces, _bounds, _object_id = (
        _native_context()
    )
    try:
        stale_surface = replace(
            surfaces[0],
            geometry=replace(
                surfaces[0].geometry,
                expected_source_revision=Revision(2),
            ),
        )
        request = _request(ownership, (stale_surface,))
        coordinator = Cam3DPreviewCoordinator(
            OcpCam3DPreviewTessellator(adapter, ownership)
        )
        delivered = []
        finished = Event()
        try:
            coordinator.submit(
                request,
                callback=lambda result: (delivered.append(result), finished.set()),
            )
            assert finished.wait(5.0)
            result = delivered[0]
            assert result.state is Cam3DPreviewCompletionState.FAILED
            assert result.diagnostic is not None
            assert result.diagnostic.code is (
                Cam3DPreviewDiagnosticCode.GEOMETRY_UNAVAILABLE
            )
            assert result.mesh is None
            assert "TopoDS" not in repr(result)
        finally:
            coordinator.shutdown()
    finally:
        kernel.release_document(document_id)

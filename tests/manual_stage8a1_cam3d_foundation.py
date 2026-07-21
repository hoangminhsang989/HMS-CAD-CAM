"""Manual non-GUI smoke for CAM 3D Geometry Foundation Stage 8A.1."""

from __future__ import annotations

import dataclasses
import logging
import tempfile
from pathlib import Path
from uuid import uuid4

from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.BRepTools import BRepTools

from hms_cadcam.cad.models import CadGeometryKind
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cad.persistent_keys import build_persistent_object_map
from hms_cadcam.cam.adapters import OcpCam3DSurfaceAdapter
from hms_cadcam.cam.cam3d import (
    Cam3DCalculationMeshCache,
    Cam3DCalculationRequest,
    Cam3DCalculationState,
    Cam3DGeometryService,
    Cam3DProjectConfig,
    Cam3DSafeMotionPolicy,
    Cam3DSafeTransitionPolicy,
    Cam3DStockAllowance,
    Cam3DTolerancePolicy,
    CamSurfaceRole,
    CamSurfaceSelection,
    CheckSurfaceSet,
    MachiningZone3D,
    PartSurfaceSet,
    build_calculation_mesh,
    wcs_fingerprint,
)
from hms_cadcam.cam.domain import (
    CamJobId,
    CamSurfaceSelectionId,
    ContentFingerprint,
    GeometryFingerprint,
    LengthUnit,
    MachiningZone3DId,
    Revision,
    SetupId,
    WcsFrame,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode

logger = logging.getLogger("manual_stage8a1")


def _selection(kernel, document_id, object_id, face_index: int) -> SelectionMetadata:
    return SelectionMetadata(
        document_id,
        f"{document_id}:face:{face_index}",
        SelectionMode.FACE,
        kernel.get_bounding_box(document_id),
        object_id,
    )


def run(workspace: Path) -> None:
    """Exercise real OCP geometry and project lifecycle without production output."""
    source_path = workspace / "stage8a1_box.brep"
    if not BRepTools.Write_s(BRepPrimAPI_MakeBox(40, 30, 20).Shape(), str(source_path)):
        raise RuntimeError("Could not create the Stage 8A.1 BREP source")
    project_service = ProjectService.create_default(workspace / "config")
    session = project_service.create_project_from_source(
        workspace, "Stage8A1 Smoke", source_path
    )
    source_record = session.manifest.source_files[0]
    copied_source = session.root_path / source_record.stored_path

    kernel = OcpCadKernel()
    imported = kernel.import_brep(copied_source)
    if not imported.success or imported.document_id is None:
        raise RuntimeError("Could not import the Stage 8A.1 BREP source")
    document_id = imported.document_id
    try:
        tree = kernel.get_document_tree(document_id)
        mapping = build_persistent_object_map(
            source_record.source_id, CadGeometryKind.BREP, tree
        )
        adapter = OcpCam3DSurfaceAdapter(
            kernel,
            document_id,
            source_record.source_id,
            session.manifest.project_id,
            mapping,
            source_revision=Revision(1),
        )
        object_id = tree.presentation_nodes[0].object_id
        part = adapter.bind_selection(
            _selection(kernel, document_id, object_id, 1), CamSurfaceRole.PART
        )
        check = adapter.bind_selection(
            _selection(kernel, document_id, object_id, 2), CamSurfaceRole.CHECK
        )
        revision = Revision(1)
        part_set = PartSurfaceSet(
            CamSurfaceSelection(
                CamSurfaceSelectionId.new(),
                session.manifest.project_id,
                revision,
                (part,),
            )
        )
        check_set = CheckSurfaceSet(
            CamSurfaceSelection(
                CamSurfaceSelectionId.new(),
                session.manifest.project_id,
                revision,
                (check,),
                allow_empty=True,
            )
        )
        frame = WcsFrame.identity(LengthUnit.MM)
        setup_id = SetupId.new()
        tolerance = Cam3DTolerancePolicy(0.02, 0.2, 1.0e-8, 0.001, 0.001)
        zone = MachiningZone3D(
            MachiningZone3DId.new(),
            session.manifest.project_id,
            CamJobId.new(),
            setup_id,
            Revision(1),
            frame,
            part_set,
            check_set,
            None,
            None,
            frame.z_axis,
            frame.x_axis,
            None,
            None,
            tolerance,
            Cam3DStockAllowance(),
            revision,
            GeometryFingerprint.from_payload(
                {"source_sha256": source_record.sha256, "revision": 1}
            ),
        )
        fragments = tuple(
            adapter.tessellate(item, tolerance) for item in zone.all_surfaces()
        )
        mesh = build_calculation_mesh(
            fragments, tolerance, zone.geometry_fingerprint
        )
        changed_tolerance = dataclasses.replace(tolerance, chordal_tolerance=0.04)
        changed_fragments = tuple(
            adapter.tessellate(item, changed_tolerance)
            for item in zone.all_surfaces()
        )
        changed_mesh = build_calculation_mesh(
            changed_fragments, changed_tolerance, zone.geometry_fingerprint
        )
        if mesh.mesh_fingerprint == changed_mesh.mesh_fingerprint:
            raise RuntimeError("Tolerance did not participate in mesh identity")
        if mesh.statistics.surface_count != 2 or mesh.bounding_box.z_max > 20.0:
            raise RuntimeError("CAM 3D mesh statistics/bounds are inconsistent")

        safe = Cam3DSafeMotionPolicy(
            setup_id,
            Revision(1),
            wcs_fingerprint(frame),
            30.0,
            25.0,
            2.0,
            1.0,
            Cam3DSafeTransitionPolicy.RETRACT_THEN_RAPID,
            frame.z_axis,
        )
        request = Cam3DCalculationRequest.create(
            project_id=zone.project_id,
            project_generation=1,
            job_id=zone.job_id,
            setup_id=zone.setup_id,
            zone=zone,
            tool_assembly_fingerprint=ContentFingerprint.from_payload(
                {"manual_tool_assembly": 1}
            ),
            tool_definition_fingerprint=ContentFingerprint.from_payload(
                {"manual_tool": 1}
            ),
            safe_motion_policy=safe,
        )
        geometry_service = Cam3DGeometryService()
        geometry_service.bind_project(zone.project_id, 1)
        execution = geometry_service.calculate(request, adapter)
        if not execution.published or execution.context is None:
            raise RuntimeError("CAM 3D calculation context was not published")

        stale_request = dataclasses.replace(request, request_token=request.request_token)
        stale_zone = dataclasses.replace(
            zone,
            geometry_fingerprint=GeometryFingerprint.from_payload(
                {"source_sha256": source_record.sha256, "revision": 2}
            ),
        )
        current = dataclasses.replace(stale_request, zone=stale_zone)
        stale = geometry_service.calculate(
            dataclasses.replace(request, request_token=uuid4()),
            adapter,
            current_request=lambda: current,
        )
        if stale.state is not Cam3DCalculationState.STALE:
            raise RuntimeError("Geometry change did not stale the calculation")
        cancelled = geometry_service.calculate(
            Cam3DCalculationRequest.create(
                project_id=zone.project_id,
                project_generation=1,
                job_id=zone.job_id,
                setup_id=zone.setup_id,
                zone=zone,
                tool_assembly_fingerprint=request.tool_assembly_fingerprint,
                tool_definition_fingerprint=request.tool_definition_fingerprint,
                safe_motion_policy=safe,
            ),
            adapter,
            cancellation=lambda: True,
        )
        if cancelled.state is not Cam3DCalculationState.CANCELLED:
            raise RuntimeError("CAM 3D cancellation was not observed")

        project_service.stage_cam3d_config(
            Cam3DProjectConfig(session.manifest.project_id, (zone,))
        )
        project_service.save()
        Cam3DCalculationMeshCache().publish(
            session.root_path, session.manifest.project_id, mesh
        )
        project_service.close_project()
        reopened = project_service.open_project(session.root_path)
        if reopened.cam3d_config is None or reopened.cam3d_config.zones != (zone,):
            raise RuntimeError("CAM 3D config did not survive Save/Open")
        if not project_service.cam_snapshot.is_empty:
            raise RuntimeError("Manual 8A.1 smoke unexpectedly created a CAM operation/toolpath")
        project_service.close_project()
        logger.info(
            "Stage 8A.1 smoke passed: %d vertices, %d triangles, bbox Z %.3f..%.3f",
            mesh.statistics.vertex_count,
            mesh.statistics.triangle_count,
            mesh.bounding_box.z_min,
            mesh.bounding_box.z_max,
        )
    finally:
        kernel.release_document(document_id)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    with tempfile.TemporaryDirectory(prefix="hms-stage8a1-") as directory:
        run(Path(directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

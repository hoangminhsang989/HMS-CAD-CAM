"""Deterministic native-free fixtures for Parallel Finishing Stage 8A.2.1."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID, uuid4

from hms_cadcam.cam.cam3d import (
    Cam3DCalculationContext,
    Cam3DCalculationMesh,
    Cam3DGeometrySnapshot,
    Cam3DResolvedSurfaceMesh,
    Cam3DSafeMotionPolicy,
    Cam3DSafeTransitionPolicy,
    Cam3DStockAllowance,
    CamSurfaceSelection,
    MachiningZone3D,
    PartSurfaceSet,
    build_calculation_mesh,
    wcs_fingerprint,
)
from hms_cadcam.cam.cam3d.parallel import (
    ParallelCutDirection,
    ParallelFinishingParameters,
)
from hms_cadcam.cam.domain import (
    Cam3DCalculationContextId,
    Cam3DGeometrySnapshotId,
    CamNodeId,
    CamSurfaceSelectionId,
    ContentFingerprint,
    GeometryFingerprint,
    GeometryInputId,
    GeometryInputRole,
    GeometryReferenceKind,
    Length,
    LengthUnit,
    Operation,
    OperationFamily,
    OperationGeometryInput,
    OperationId,
    Revision,
    ToolAssembly,
    ToolAssemblyId,
    ToolAssemblyReference,
    ToolDefinition,
)
from hms_cadcam.cam.domain.spatial import Point3
from tests.unit._cam3d_fixtures import surface, tolerance, tool, zone


@dataclass(frozen=True, slots=True)
class ParallelFixture:
    zone: MachiningZone3D
    mesh: Cam3DCalculationMesh
    context: Cam3DCalculationContext
    tool: ToolDefinition
    assembly: ToolAssembly
    operation: Operation


def planar_fixture(
    *,
    width: float = 10.0,
    height: float = 10.0,
    z: float = 0.0,
    stepover: float = 2.0,
    maximum_segment_length: float = 2.0,
    cut_direction: ParallelCutDirection = ParallelCutDirection.ONE_WAY,
    with_boundary: bool = False,
    with_check: bool = False,
    allowance: float = 0.0,
    project_id: UUID | None = None,
) -> ParallelFixture:
    return parallel_fixture(
        (
            (
                "face-planar",
                (
                    (0.0, 0.0, z),
                    (width, 0.0, z),
                    (width, height, z),
                    (0.0, height, z),
                ),
                ((0, 1, 2), (0, 2, 3)),
            ),
        ),
        stepover=stepover,
        maximum_segment_length=maximum_segment_length,
        cut_direction=cut_direction,
        with_boundary=with_boundary,
        with_check=with_check,
        allowance=allowance,
        project_id=project_id,
    )


def inclined_fixture(*, stepover: float = 2.0) -> ParallelFixture:
    return parallel_fixture(
        (
            (
                "face-inclined",
                (
                    (0.0, 0.0, 0.0),
                    (10.0, 0.0, 5.0),
                    (10.0, 10.0, 5.0),
                    (0.0, 10.0, 0.0),
                ),
                ((0, 1, 2), (0, 2, 3)),
            ),
        ),
        stepover=stepover,
    )


def curved_coarse_mesh_fixture(*, stepover: float = 2.0) -> ParallelFixture:
    """Return an intentionally coarse faceted half-cylinder regression fixture."""
    ys = (-5.0, -2.5, 0.0, 2.5, 5.0)
    zs = (0.0, 4.330127018922193, 5.0, 4.330127018922193, 0.0)
    vertices = tuple((x, y, z) for y, z in zip(ys, zs, strict=True) for x in (0.0, 10.0))
    triangles = tuple(
        triangle
        for row in range(len(ys) - 1)
        for triangle in (
            (row * 2, row * 2 + 1, row * 2 + 3),
            (row * 2, row * 2 + 3, row * 2 + 2),
        )
    )
    return parallel_fixture(
        (("face-curved", vertices, triangles),), stepover=stepover
    )


def curved_fixture(*, stepover: float = 2.0) -> ParallelFixture:
    """Compatibility alias for the explicitly coarse curved regression fixture."""
    return curved_coarse_mesh_fixture(stepover=stepover)


def contiguous_fixture(*, stepover: float = 2.0) -> ParallelFixture:
    return parallel_fixture(
        (
            (
                "face-left",
                ((0, 0, 0), (5, 0, 0), (5, 10, 0), (0, 10, 0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
            (
                "face-right",
                ((5, 0, 0), (10, 0, 0), (10, 10, 0), (5, 10, 0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
        ),
        stepover=stepover,
    )


def disconnected_fixture(*, stepover: float = 2.0) -> ParallelFixture:
    return parallel_fixture(
        (
            (
                "face-left",
                ((0, 0, 0), (4, 0, 0), (4, 10, 0), (0, 10, 0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
            (
                "face-right",
                ((6, 0, 0), (10, 0, 0), (10, 10, 0), (6, 10, 0)),
                ((0, 1, 2), (0, 2, 3)),
            ),
        ),
        stepover=stepover,
    )


def parallel_fixture(
    definitions: tuple[
        tuple[
            str,
            tuple[tuple[float, float, float], ...],
            tuple[tuple[int, int, int], ...],
        ],
        ...
    ],
    *,
    stepover: float = 2.0,
    maximum_segment_length: float = 2.0,
    cut_direction: ParallelCutDirection = ParallelCutDirection.ONE_WAY,
    with_boundary: bool = False,
    with_check: bool = False,
    allowance: float = 0.0,
    project_id: UUID | None = None,
) -> ParallelFixture:
    project_id, source_id = project_id or uuid4(), uuid4()
    revision = Revision(1)
    value = zone(
        project_id=project_id,
        source_id=source_id,
        revision=revision,
        with_check=with_check,
        with_boundary=with_boundary,
    )
    surfaces = tuple(
        surface(project_id, source_id, selector, revision=revision)
        for selector, _vertices, _triangles in definitions
    )
    selection = CamSurfaceSelection(
        CamSurfaceSelectionId.new(), project_id, revision, surfaces
    )
    geometry_fingerprint = GeometryFingerprint.from_payload(
        {
            "fixture": [
                {"selector": selector, "vertices": vertices, "triangles": triangles}
                for selector, vertices, triangles in definitions
            ]
        }
    )
    value = replace(
        value,
        part_surfaces=PartSurfaceSet(selection),
        geometry_fingerprint=geometry_fingerprint,
        allowance=Cam3DStockAllowance(part_normal=allowance),
    )
    fragments = tuple(
        Cam3DResolvedSurfaceMesh(
            selected,
            tuple(Point3(*point, LengthUnit.MM) for point in vertices),
            triangles,
        )
        for selected, (_selector, vertices, triangles) in zip(
            surfaces, definitions, strict=True
        )
    )
    mesh = build_calculation_mesh(
        fragments, value.tolerance, value.geometry_fingerprint
    )
    selected_tool = tool(ball=True)
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Parallel ball assembly",
        selected_tool,
        Length(30.0, LengthUnit.MM),
        Length(40.0, LengthUnit.MM),
    )
    safe_motion = Cam3DSafeMotionPolicy(
        value.setup_id,
        value.setup_revision,
        wcs_fingerprint(value.wcs),
        50.0,
        40.0,
        2.0,
        1.0,
        Cam3DSafeTransitionPolicy.RETRACT_THEN_RAPID,
        value.tool_axis,
    )
    snapshot = Cam3DGeometrySnapshot(
        Cam3DGeometrySnapshotId.new(),
        project_id,
        1,
        value.setup_revision,
        value.geometry_revision,
        value.geometry_fingerprint,
        value,
    )
    context = Cam3DCalculationContext(
        Cam3DCalculationContextId.new(),
        uuid4(),
        project_id,
        1,
        value.job_id,
        value.setup_id,
        snapshot,
        value,
        mesh,
        ContentFingerprint.from_payload(assembly.to_dict()),
        selected_tool.content_fingerprint,
        value.tolerance,
        value.allowance,
        safe_motion,
        "hms_parallel_finishing_mesh_plane",
        1,
    )
    parameters = ParallelFinishingParameters(
        value.zone_id,
        stepover,
        cut_direction=cut_direction,
        maximum_segment_length_mm=maximum_segment_length,
    )
    geometry_inputs = tuple(
        OperationGeometryInput(
            GeometryInputId.new(),
            GeometryInputRole.DRIVE_GEOMETRY,
            selected.geometry,
            True,
            GeometryReferenceKind.FACE,
            index,
        )
        for index, selected in enumerate(surfaces)
    )
    operation = Operation(
        OperationId.new(),
        CamNodeId.new(),
        OperationFamily.MILLING,
        value.setup_id,
        ToolAssemblyReference.from_assembly(assembly),
        geometry_inputs,
        parameters.to_operation_parameters(),
    )
    return ParallelFixture(value, mesh, context, selected_tool, assembly, operation)

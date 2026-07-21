"""Shared native-free fixtures for CAM 3D Stage 8A.1 tests."""

from __future__ import annotations

from uuid import UUID, uuid4

from hms_cadcam.cam.cam3d import (
    Cam3DCalculationRequest,
    Cam3DResolvedSurfaceMesh,
    Cam3DSafeMotionPolicy,
    Cam3DSafeTransitionPolicy,
    Cam3DStockAllowance,
    Cam3DTolerancePolicy,
    CamSurfaceOrientation,
    CamSurfaceReference,
    CamSurfaceRole,
    CamSurfaceSelection,
    CheckSurfaceSet,
    FixtureSurfaceSet,
    MachiningBoundary3D,
    MachiningBoundary3DKind,
    MachiningZone3D,
    PartSurfaceSet,
    BoundaryInclusionPolicy3D,
    BoundaryOrientation3D,
    wcs_fingerprint,
)
from hms_cadcam.cam.domain import (
    BallEndGeometry,
    CamJobId,
    CamSurfaceSelectionId,
    ContentFingerprint,
    CylindricalGeometry,
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    Length,
    LengthUnit,
    MachiningZone3DId,
    Point3,
    Revision,
    SetupId,
    ShankGeometry,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    Vector3,
    WcsFrame,
)


def tolerance(chordal: float = 0.01) -> Cam3DTolerancePolicy:
    return Cam3DTolerancePolicy(chordal, 0.2, 1.0e-8, 0.001, 0.001)


def geometry_reference(
    source_id: UUID,
    selector: str,
    *,
    revision: Revision = Revision(1),
) -> GeometryReference:
    return GeometryReference(
        GeometryReferenceId.new(),
        "hms_cam3d_surface",
        1,
        source_id,
        GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"selector": selector}),
        revision,
        subshape_selector=selector,
    )


def surface(
    project_id: UUID,
    source_id: UUID,
    selector: str,
    role: CamSurfaceRole = CamSurfaceRole.PART,
    *,
    revision: Revision = Revision(1),
    orientation: CamSurfaceOrientation = CamSurfaceOrientation.FORWARD,
) -> CamSurfaceReference:
    return CamSurfaceReference(
        project_id,
        geometry_reference(source_id, selector, revision=revision),
        orientation,
        role,
        body_identity="body-1",
        face_identity=selector,
    )


def selection(
    project_id: UUID,
    surfaces: tuple[CamSurfaceReference, ...],
    *,
    revision: Revision = Revision(1),
    allow_empty: bool = False,
) -> CamSurfaceSelection:
    return CamSurfaceSelection(
        CamSurfaceSelectionId.new(), project_id, revision, surfaces, allow_empty
    )


def zone(
    *,
    project_id: UUID | None = None,
    source_id: UUID | None = None,
    revision: Revision = Revision(1),
    chordal: float = 0.01,
    with_check: bool = True,
    with_fixture: bool = False,
    with_boundary: bool = False,
) -> MachiningZone3D:
    project_id = project_id or uuid4()
    source_id = source_id or uuid4()
    part = surface(project_id, source_id, "face-part", revision=revision)
    part_set = PartSurfaceSet(selection(project_id, (part,), revision=revision))
    check_set = None
    if with_check:
        check = surface(
            project_id,
            source_id,
            "face-check",
            CamSurfaceRole.CHECK,
            revision=revision,
        )
        check_set = CheckSurfaceSet(
            selection(project_id, (check,), revision=revision, allow_empty=True)
        )
    fixture_set = None
    if with_fixture:
        fixture = surface(
            project_id,
            source_id,
            "face-fixture",
            CamSurfaceRole.FIXTURE,
            revision=revision,
        )
        fixture_set = FixtureSurfaceSet(
            selection(project_id, (fixture,), revision=revision, allow_empty=True)
        )
    setup_id = SetupId.new()
    frame = WcsFrame.identity(LengthUnit.MM)
    boundary = None
    if with_boundary:
        points = (
            Point3(0, 0, 0, LengthUnit.MM),
            Point3(10, 0, 0, LengthUnit.MM),
            Point3(10, 10, 0, LengthUnit.MM),
            Point3(0, 10, 0, LengthUnit.MM),
            Point3(0, 0, 0, LengthUnit.MM),
        )
        boundary = MachiningBoundary3D(
            MachiningBoundary3DKind.CLOSED_PLANAR_CONTOUR,
            setup_id,
            frame,
            0.001,
            BoundaryOrientation3D.COUNTERCLOCKWISE,
            BoundaryInclusionPolicy3D.INSIDE,
            revision,
            points,
            (part.geometry,),
        )
    return MachiningZone3D(
        MachiningZone3DId.new(),
        project_id,
        CamJobId.new(),
        setup_id,
        Revision(1),
        frame,
        part_set,
        check_set,
        fixture_set,
        boundary,
        frame.z_axis,
        frame.x_axis,
        None,
        None,
        tolerance(chordal),
        Cam3DStockAllowance(),
        revision,
        GeometryFingerprint.from_payload(
            {"source": str(source_id), "revision": revision.value}
        ),
    )


def fragments(value: MachiningZone3D) -> tuple[Cam3DResolvedSurfaceMesh, ...]:
    result = []
    for index, item in enumerate(value.all_surfaces()):
        z = float(index)
        result.append(
            Cam3DResolvedSurfaceMesh(
                item,
                (
                    Point3(0, 0, z, LengthUnit.MM),
                    Point3(10, 0, z, LengthUnit.MM),
                    Point3(10, 10, z, LengthUnit.MM),
                    Point3(0, 10, z, LengthUnit.MM),
                ),
                ((0, 1, 2), (0, 2, 3)),
            )
        )
    return tuple(result)


def safe_motion(value: MachiningZone3D) -> Cam3DSafeMotionPolicy:
    return Cam3DSafeMotionPolicy(
        value.setup_id,
        value.setup_revision,
        wcs_fingerprint(value.wcs),
        20.0,
        10.0,
        2.0,
        1.0,
        Cam3DSafeTransitionPolicy.RETRACT_THEN_RAPID,
        value.tool_axis,
    )


def request(value: MachiningZone3D) -> Cam3DCalculationRequest:
    return Cam3DCalculationRequest.create(
        project_id=value.project_id,
        project_generation=1,
        job_id=value.job_id,
        setup_id=value.setup_id,
        zone=value,
        tool_assembly_fingerprint=ContentFingerprint.from_payload({"assembly": 1}),
        tool_definition_fingerprint=ContentFingerprint.from_payload({"tool": 1}),
        safe_motion_policy=safe_motion(value),
    )


def tool(*, ball: bool) -> ToolDefinition:
    unit = LengthUnit.MM
    geometry = (
        BallEndGeometry(Length(10, unit), Length(20, unit))
        if ball
        else CylindricalGeometry(Length(10, unit), Length(20, unit))
    )
    family = ToolFamily.BALL_END_MILL if ball else ToolFamily.END_MILL
    return ToolDefinition(
        ToolDefinitionId.new(),
        "Ball" if ball else "Flat",
        family,
        unit,
        geometry,
        Length(60, unit),
        Length(30, unit),
        ShankGeometry(Length(10, unit), Length(40, unit)),
    )

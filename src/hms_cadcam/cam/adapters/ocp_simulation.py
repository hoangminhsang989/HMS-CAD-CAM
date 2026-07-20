"""Open CASCADE collision adapter for the headless 7C.1 simulation boundary.

All OCP imports are lazy.  The adapter owns no viewer/AIS/Qt object and callers
must invoke it on the thread that owns the supplied native document shapes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.geometry_reference import GeometryReference, GeometryReferenceKind, GeometryRepresentationKind
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.domain.revision import Revision
from hms_cadcam.cam.domain.setup import BoxStock, Setup
from hms_cadcam.cam.domain.spatial import Point3
from hms_cadcam.cam.toolpath.geometry import Bounds3, Pose
from hms_cadcam.cam.simulation.collision import CollisionBackend, CollisionEvidence, CollisionScene, CollisionTarget, CollisionTargetKind, aabb_overlap, primitive_bounds
from hms_cadcam.cam.simulation.coordinates import apply_affine_point, transform_bounds
from hms_cadcam.cam.simulation.envelope import EnvelopePrimitive, EnvelopePrimitiveKind


@dataclass(frozen=True, slots=True)
class ResolvedFixtureGeometry:
    """Runtime-only native handle plus validated native-free provenance."""

    shape: Any
    bounds: Bounds3
    geometry_fingerprint: ContentFingerprint
    source_id: UUID
    occurrence_path: str | None
    source_revision: Revision | None = None
    match_count: int = 0
    ownership_verified: bool = False


class FixtureGeometryResolver(Protocol):
    def resolve_fixture(self, reference: GeometryReference) -> ResolvedFixtureGeometry: ...


def _frame_point(stock: BoxStock, x: float, y: float, z: float) -> Point3:
    frame = stock.frame
    return Point3(frame.origin.x + frame.x_axis.x * x + frame.y_axis.x * y + frame.z_axis.x * z, frame.origin.y + frame.x_axis.y * x + frame.y_axis.y * y + frame.z_axis.y * z, frame.origin.z + frame.x_axis.z * x + frame.y_axis.z * y + frame.z_axis.z * z, frame.origin.unit)


def _box_bounds(stock: BoxStock) -> Bounds3:
    return Bounds3.from_points(tuple(_frame_point(stock, x, y, z) for x in (0.0, stock.size_x.value) for y in (0.0, stock.size_y.value) for z in (0.0, stock.size_z.value)))


class OcpSimulationCollisionBackend(CollisionBackend):
    """OCP narrow phase using exact minimum-distance evaluation."""

    def broad_overlap(self, target: CollisionTarget, candidate: Bounds3) -> bool:
        return aabb_overlap(target.bounds, candidate)

    def narrow_intersects(self, target: CollisionTarget, primitive: EnvelopePrimitive, pose: Pose, tolerance: float) -> CollisionEvidence | None:
        if target.geometry is None:
            raise CamValidationError("Narrow-phase target geometry is unavailable")
        tool_shape = self._primitive_shape(primitive, pose)
        try:
            from OCP.BRepExtrema import BRepExtrema_DistShapeShape
            distance = BRepExtrema_DistShapeShape(tool_shape, target.geometry)
            distance.Perform()
            if not distance.IsDone():
                raise CamValidationError("OCP distance evaluation failed")
            value = float(distance.Value())
        except ImportError as error:
            raise CamValidationError("Open CASCADE collision support is unavailable") from error
        return CollisionEvidence(True, value, target.entity_id) if value <= tolerance else None

    @staticmethod
    def _primitive_shape(primitive: EnvelopePrimitive, pose: Pose) -> Any:
        try:
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere
            from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt
        except ImportError as error:
            raise CamValidationError("Open CASCADE primitive support is unavailable") from error
        axis = pose.tool_axis
        start = gp_Pnt(pose.position.x + axis.x * primitive.axial_start, pose.position.y + axis.y * primitive.axial_start, pose.position.z + axis.z * primitive.axial_start)
        direction = gp_Dir(axis.x, axis.y, axis.z)
        height = primitive.axial_end - primitive.axial_start
        if primitive.kind is EnvelopePrimitiveKind.BALL:
            center = gp_Pnt(pose.position.x + axis.x * primitive.axial_end, pose.position.y + axis.y * primitive.axial_end, pose.position.z + axis.z * primitive.axial_end)
            return BRepPrimAPI_MakeSphere(center, primitive.radius).Shape()
        axes = gp_Ax2(start, direction)
        if primitive.kind is EnvelopePrimitiveKind.CYLINDER:
            return BRepPrimAPI_MakeCylinder(axes, primitive.radius, height).Shape()
        return BRepPrimAPI_MakeCone(axes, primitive.lower_radius, primitive.upper_radius, height).Shape()

    @staticmethod
    def build_scene(*, setup: Setup, resolver: FixtureGeometryResolver) -> CollisionScene:
        if not isinstance(setup.stock, BoxStock):
            raise CamValidationError("Simulation v1 supports BOX stock only")
        try:
            from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
            from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
            from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf
        except ImportError as error:
            raise CamValidationError("Open CASCADE simulation support is unavailable") from error
        frame = setup.stock.frame
        axes = gp_Ax2(gp_Pnt(frame.origin.x, frame.origin.y, frame.origin.z), gp_Dir(frame.z_axis.x, frame.z_axis.y, frame.z_axis.z), gp_Dir(frame.x_axis.x, frame.x_axis.y, frame.x_axis.z))
        stock_shape = BRepPrimAPI_MakeBox(axes, setup.stock.size_x.value, setup.stock.size_y.value, setup.stock.size_z.value).Shape()
        stock = CollisionTarget("stock", CollisionTargetKind.STOCK, _box_bounds(setup.stock), stock_shape)
        fixtures: list[CollisionTarget] = []
        for fixture in sorted((item for item in setup.fixtures if item.enabled), key=lambda item: str(item.fixture_id)):
            reference = fixture.geometry_reference
            if reference.kind not in {GeometryReferenceKind.BODY, GeometryReferenceKind.OCCURRENCE} or reference.geometry_kind is not GeometryRepresentationKind.BREP:
                raise CamValidationError("Fixture reference must be BODY or OCCURRENCE")
            resolved = resolver.resolve_fixture(reference)
            if not resolved.ownership_verified or resolved.match_count != 1:
                raise CamValidationError("Fixture native ownership or uniqueness is unproven")
            if resolved.geometry_fingerprint != reference.expected_geometry_fingerprint or resolved.source_id != reference.source_id or resolved.source_revision != reference.expected_source_revision or resolved.occurrence_path != reference.occurrence_path:
                raise CamValidationError("Fixture geometry provenance is stale or ambiguous")
            values = fixture.transform.values
            transform = gp_Trsf()
            transform.SetValues(*values[:12])
            placed = BRepBuilderAPI_Transform(resolved.shape, transform, True).Shape()
            fixtures.append(CollisionTarget(str(fixture.fixture_id), CollisionTargetKind.FIXTURE, transform_bounds(resolved.bounds, fixture.transform), placed))
        return CollisionScene(stock, tuple(fixtures))

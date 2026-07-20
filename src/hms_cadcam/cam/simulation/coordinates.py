"""Explicit coordinate transforms used by the simulation boundary."""

from __future__ import annotations

from hms_cadcam.cam.domain.errors import CamUnitError, CamValidationError
from hms_cadcam.cam.domain.spatial import AffineTransform, Point3, Vector3, WcsFrame
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.toolpath.geometry import Bounds3, Pose


def wcs_to_world_point(point: Point3, wcs: WcsFrame) -> Point3:
    """Map a Setup-WCS point into world coordinates exactly once."""
    if not isinstance(point, Point3) or not isinstance(wcs, WcsFrame):
        raise CamValidationError("WCS point transform input is invalid")
    if point.unit is not wcs.origin.unit:
        raise CamUnitError("WCS and point units do not match")
    return Point3(
        wcs.origin.x + point.x * wcs.x_axis.x + point.y * wcs.y_axis.x + point.z * wcs.z_axis.x,
        wcs.origin.y + point.x * wcs.x_axis.y + point.y * wcs.y_axis.y + point.z * wcs.z_axis.y,
        wcs.origin.z + point.x * wcs.x_axis.z + point.y * wcs.y_axis.z + point.z * wcs.z_axis.z,
        point.unit,
    )


def wcs_to_world_axis(axis: Vector3, wcs: WcsFrame) -> Vector3:
    """Map a direction through the WCS basis; WCS origin is never applied."""
    if not isinstance(axis, Vector3) or not isinstance(wcs, WcsFrame):
        raise CamValidationError("WCS axis transform input is invalid")
    return Vector3(
        axis.x * wcs.x_axis.x + axis.y * wcs.y_axis.x + axis.z * wcs.z_axis.x,
        axis.x * wcs.x_axis.y + axis.y * wcs.y_axis.y + axis.z * wcs.z_axis.y,
        axis.x * wcs.x_axis.z + axis.y * wcs.y_axis.z + axis.z * wcs.z_axis.z,
    )


def pose_to_world(pose: Pose, wcs: WcsFrame) -> Pose:
    if not isinstance(pose, Pose):
        raise CamValidationError("Pose transform input is invalid")
    return Pose(wcs_to_world_point(pose.position, wcs), wcs_to_world_axis(pose.tool_axis, wcs))


def apply_affine_point(point: Point3, transform: AffineTransform) -> Point3:
    if point.unit is not transform.translation_unit:
        raise CamUnitError("Affine point and transform units do not match")
    values = transform.values
    return Point3(values[0] * point.x + values[1] * point.y + values[2] * point.z + values[3], values[4] * point.x + values[5] * point.y + values[6] * point.z + values[7], values[8] * point.x + values[9] * point.y + values[10] * point.z + values[11], point.unit)


def apply_affine_vector(vector: Vector3, transform: AffineTransform) -> Vector3:
    if not isinstance(vector, Vector3) or not isinstance(transform, AffineTransform):
        raise CamValidationError("Affine vector transform input is invalid")
    values = transform.values
    return Vector3(values[0] * vector.x + values[1] * vector.y + values[2] * vector.z, values[4] * vector.x + values[5] * vector.y + values[6] * vector.z, values[8] * vector.x + values[9] * vector.y + values[10] * vector.z)


def transform_bounds(bounds: Bounds3, transform: AffineTransform) -> Bounds3:
    if bounds.minimum.unit is not transform.translation_unit:
        raise CamUnitError("Affine bounds and transform units do not match")
    minimum, maximum = bounds.minimum, bounds.maximum
    points = tuple(apply_affine_point(Point3(x, y, z, minimum.unit), transform) for x in (minimum.x, maximum.x) for y in (minimum.y, maximum.y) for z in (minimum.z, maximum.z))
    return Bounds3.from_points(points)

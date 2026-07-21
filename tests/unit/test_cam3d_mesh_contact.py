"""Calculation mesh, safe-motion and limited contact primitive tests."""

from __future__ import annotations

import dataclasses
import math

import pytest

from hms_cadcam.cam.cam3d import (
    Cam3DCalculationMesh,
    Cam3DCancelledError,
    Cam3DContactError,
    Cam3DMeshError,
    Cam3DResolvedSurfaceMesh,
    build_calculation_mesh,
    calculate_tool_contact,
    project_point_to_triangle,
    validate_safe_motion,
)
from hms_cadcam.cam.domain import (
    BullNoseGeometry,
    Length,
    LengthUnit,
    Point3,
    ToolFamily,
    Vector3,
)
from tests.unit._cam3d_fixtures import fragments, safe_motion, tool, zone


def _mesh(*, chordal: float = 0.01) -> Cam3DCalculationMesh:
    value = zone(chordal=chordal)
    return build_calculation_mesh(
        fragments(value), value.tolerance, value.geometry_fingerprint
    )


def test_mesh_is_deterministic_across_fragment_and_vertex_order() -> None:
    value = zone(with_check=True)
    original = fragments(value)
    reordered = tuple(
        Cam3DResolvedSurfaceMesh(
            fragment.surface,
            tuple(reversed(fragment.vertices)),
            tuple(
                tuple(len(fragment.vertices) - 1 - index for index in triangle)
                for triangle in reversed(fragment.triangles)
            ),
        )
        for fragment in reversed(original)
    )
    first = build_calculation_mesh(
        original, value.tolerance, value.geometry_fingerprint
    )
    second = build_calculation_mesh(
        reordered, value.tolerance, value.geometry_fingerprint
    )
    assert first.mesh_fingerprint == second.mesh_fingerprint
    assert first.vertices == second.vertices
    assert first.triangle_sources == second.triangle_sources


def test_mesh_round_trip_mapping_bounds_and_statistics() -> None:
    mesh = _mesh()
    restored = Cam3DCalculationMesh.from_dict(mesh.to_dict())
    assert restored == mesh
    assert restored.bounding_box.x_min == 0.0
    assert restored.bounding_box.x_max == 10.0
    assert restored.statistics.vertex_count == len(restored.vertices)
    assert restored.statistics.triangle_count == len(restored.triangle_indices)
    assert set(restored.triangle_sources)


def test_mesh_tolerance_changes_fingerprint() -> None:
    first = _mesh(chordal=0.01)
    second = _mesh(chordal=0.02)
    assert first.mesh_fingerprint != second.mesh_fingerprint


def test_mesh_rejects_degenerate_triangle_and_size_limit() -> None:
    value = zone(with_check=False)
    item = value.all_surfaces()[0]
    degenerate = Cam3DResolvedSurfaceMesh(
        item,
        (
            Point3(0, 0, 0, LengthUnit.MM),
            Point3(1, 0, 0, LengthUnit.MM),
            Point3(2, 0, 0, LengthUnit.MM),
        ),
        ((0, 1, 2),),
    )
    with pytest.raises(Cam3DMeshError) as captured:
        build_calculation_mesh(
            (degenerate,), value.tolerance, value.geometry_fingerprint
        )
    assert captured.value.diagnostic.code.value == "cam3d.mesh_degenerate"
    with pytest.raises(Cam3DMeshError) as captured:
        build_calculation_mesh(
            fragments(value),
            value.tolerance,
            value.geometry_fingerprint,
            max_triangles=1,
        )
    assert captured.value.diagnostic.code.value == "cam3d.mesh_too_large"


def test_mesh_cancellation_checkpoint() -> None:
    value = zone()
    with pytest.raises(Cam3DCancelledError):
        build_calculation_mesh(
            fragments(value),
            value.tolerance,
            value.geometry_fingerprint,
            cancellation=lambda: True,
        )


def test_mesh_future_version_and_fingerprint_tamper_fail_closed() -> None:
    mesh = _mesh()
    future = mesh.to_dict()
    future["format_version"] = 2
    with pytest.raises(Exception):
        Cam3DCalculationMesh.from_dict(future)
    changed = mesh.to_dict()
    changed["vertices"][0]["x"] = 99.0
    with pytest.raises(Exception):
        Cam3DCalculationMesh.from_dict(changed)


def test_safe_motion_valid_and_missing_z_diagnostic() -> None:
    value = zone()
    mesh = build_calculation_mesh(
        fragments(value), value.tolerance, value.geometry_fingerprint
    )
    policy = safe_motion(value)
    assert validate_safe_motion(policy, value, mesh) == ()
    missing = dataclasses.replace(policy, clearance_z=None)
    diagnostics = validate_safe_motion(missing, value, mesh)
    assert diagnostics[0].code.value == "cam3d.safe_motion_invalid"
    assert "explicit" in diagnostics[0].message


def test_safe_motion_retract_below_geometry_and_invalid_approach() -> None:
    value = zone()
    mesh = build_calculation_mesh(
        fragments(value), value.tolerance, value.geometry_fingerprint
    )
    policy = dataclasses.replace(
        safe_motion(value), retract_z=-1.0, approach_distance=-1.0
    )
    messages = {item.message for item in validate_safe_motion(policy, value, mesh)}
    assert any("Retract Z" in item for item in messages)
    assert any("Approach" in item for item in messages)


def test_safe_motion_wrong_setup_and_axis_mismatch() -> None:
    value = zone()
    mesh = build_calculation_mesh(
        fragments(value), value.tolerance, value.geometry_fingerprint
    )
    other = zone()
    policy = dataclasses.replace(
        safe_motion(value),
        setup_id=other.setup_id,
        tool_axis=Vector3(1, 0, 0),
    )
    diagnostics = validate_safe_motion(policy, value, mesh)
    assert len(diagnostics) >= 2


def test_project_point_to_planar_and_sloped_triangle() -> None:
    unit = LengthUnit.MM
    planar = project_point_to_triangle(
        Point3(2, 2, 5, unit),
        Point3(0, 0, 0, unit),
        Point3(10, 0, 0, unit),
        Point3(0, 10, 0, unit),
        tolerance=1.0e-6,
    )
    assert planar.point == Point3(2, 2, 0, unit)
    sloped = project_point_to_triangle(
        Point3(2, 2, 8, unit),
        Point3(0, 0, 0, unit),
        Point3(10, 0, 0, unit),
        Point3(0, 10, 10, unit),
        tolerance=1.0e-6,
    )
    assert math.isclose(sloped.point.y, sloped.point.z, abs_tol=1.0e-9)


def test_projection_rejects_degenerate_and_outside_triangle() -> None:
    unit = LengthUnit.MM
    with pytest.raises(Cam3DContactError):
        project_point_to_triangle(
            Point3(0, 0, 1, unit),
            Point3(0, 0, 0, unit),
            Point3(1, 0, 0, unit),
            Point3(2, 0, 0, unit),
            tolerance=1.0e-6,
        )
    with pytest.raises(Cam3DContactError):
        project_point_to_triangle(
            Point3(20, 20, 1, unit),
            Point3(0, 0, 0, unit),
            Point3(1, 0, 0, unit),
            Point3(0, 1, 0, unit),
            tolerance=1.0e-6,
        )


def test_ball_and_flat_tool_contact_limited_cases() -> None:
    mesh = _mesh()
    sample = Point3(2, 2, 5, LengthUnit.MM)
    ball = calculate_tool_contact(
        mesh,
        0,
        sample,
        tool(ball=True),
        Vector3(0, 0, 1),
        contact_tolerance=1.0e-6,
    )
    assert math.isclose(ball.tool_center_point.z - ball.contact_point.z, 5.0)
    flat = calculate_tool_contact(
        mesh,
        0,
        sample,
        tool(ball=False),
        Vector3(0, 0, 1),
        contact_tolerance=1.0e-6,
    )
    assert flat.tool_center_point == flat.contact_point


def test_ball_tool_contact_on_simple_sloped_triangle() -> None:
    value = zone(with_check=False)
    fragment = Cam3DResolvedSurfaceMesh(
        value.all_surfaces()[0],
        (
            Point3(0, 0, 0, LengthUnit.MM),
            Point3(10, 0, 0, LengthUnit.MM),
            Point3(0, 10, 10, LengthUnit.MM),
        ),
        ((0, 1, 2),),
    )
    mesh = build_calculation_mesh(
        (fragment,), value.tolerance, value.geometry_fingerprint
    )
    contact = calculate_tool_contact(
        mesh,
        0,
        Point3(2, 2, 8, LengthUnit.MM),
        tool(ball=True),
        Vector3(0, 0, 1),
        contact_tolerance=1.0e-6,
    )
    offset = Vector3(
        contact.tool_center_point.x - contact.contact_point.x,
        contact.tool_center_point.y - contact.contact_point.y,
        contact.tool_center_point.z - contact.contact_point.z,
    )
    assert math.isclose(offset.magnitude, 5.0, abs_tol=1.0e-9)
    assert offset.dot(contact.surface_normal) > 0.0


def test_unsupported_tool_and_invalid_normal_fail_closed() -> None:
    mesh = _mesh()
    unsupported = dataclasses.replace(
        tool(ball=False),
        family=ToolFamily.BULL_NOSE_END_MILL,
        cutting_geometry=BullNoseGeometry(
            Length(10, LengthUnit.MM),
            Length(20, LengthUnit.MM),
            Length(1, LengthUnit.MM),
        ),
    )
    with pytest.raises(Cam3DContactError) as captured:
        calculate_tool_contact(
            mesh,
            0,
            Point3(2, 2, 5, LengthUnit.MM),
            unsupported,
            Vector3(0, 0, 1),
            contact_tolerance=1.0e-6,
        )
    assert captured.value.diagnostic.code.value == "cam3d.tool_unsupported"
    with pytest.raises(Cam3DContactError):
        calculate_tool_contact(
            mesh,
            0,
            Point3(2, 2, 5, LengthUnit.MM),
            tool(ball=True),
            Vector3(0, 0, 0),
            contact_tolerance=1.0e-6,
        )

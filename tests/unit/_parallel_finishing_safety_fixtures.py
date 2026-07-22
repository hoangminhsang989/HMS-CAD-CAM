"""Deterministic protected-geometry fixtures for Parallel safety hardening."""

from __future__ import annotations

from dataclasses import replace

from hms_cadcam.cam.cam3d import Cam3DResolvedSurfaceMesh, build_calculation_mesh
from hms_cadcam.cam.domain import (
    BallEndGeometry,
    ContentFingerprint,
    HolderDefinition,
    HolderDefinitionId,
    HolderSection,
    Length,
    LengthUnit,
    Revision,
    ShankGeometry,
    ToolAssembly,
    ToolAssemblyId,
    ToolAssemblyReference,
    ToolDefinition,
)
from hms_cadcam.cam.domain.spatial import Point3

from tests.unit._parallel_finishing_fixtures import ParallelFixture, planar_fixture


def adjacent_wall_fixture() -> tuple[ParallelFixture, None]:
    return _protected_fixture(
        wall_x=14.0,
        z_min=0.0,
        z_max=12.0,
    ), None


def shank_collision_fixture() -> tuple[ParallelFixture, None]:
    base = planar_fixture(with_check=True, stepover=5.0)
    unit = LengthUnit.MM
    tool = replace(
        base.tool,
        shank=ShankGeometry(Length(16.0, unit), Length(40.0, unit)),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Parallel large-shank assembly",
        tool,
        Length(30.0, unit),
        Length(40.0, unit),
    )
    value = _protected_fixture(
        base=base,
        wall_x=16.0,
        z_min=20.0,
        z_max=35.0,
        tool=tool,
        assembly=assembly,
    )
    return value, None


def holder_collision_fixture() -> tuple[ParallelFixture, HolderDefinition]:
    base = planar_fixture(with_check=True, stepover=5.0)
    unit = LengthUnit.MM
    holder = HolderDefinition(
        HolderDefinitionId.new(),
        "Parallel collision holder",
        unit,
        (
            HolderSection(
                Length(0.0, unit),
                Length(30.0, unit),
                Length(30.0, unit),
                Length(30.0, unit),
            ),
        ),
        Length(0.0, unit),
        Revision(1),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Parallel holder assembly",
        base.tool,
        Length(30.0, unit),
        Length(40.0, unit),
        holder,
    )
    value = _protected_fixture(
        base=base,
        wall_x=20.0,
        z_min=35.0,
        z_max=39.0,
        tool=base.tool,
        assembly=assembly,
    )
    return value, holder


def safe_holder_fixture() -> tuple[ParallelFixture, HolderDefinition]:
    base = planar_fixture(stepover=5.0)
    unit = LengthUnit.MM
    holder = HolderDefinition(
        HolderDefinitionId.new(),
        "Parallel safe holder",
        unit,
        (
            HolderSection(
                Length(0.0, unit),
                Length(30.0, unit),
                Length(24.0, unit),
                Length(30.0, unit),
            ),
        ),
        Length(0.0, unit),
        Revision(1),
    )
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(),
        "Parallel safe holder assembly",
        base.tool,
        Length(30.0, unit),
        Length(40.0, unit),
        holder,
    )
    context = replace(
        base.context,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(assembly.to_dict()),
    )
    operation = replace(
        base.operation,
        tool_assembly=ToolAssemblyReference.from_assembly(assembly),
    )
    return ParallelFixture(
        base.zone,
        base.mesh,
        context,
        base.tool,
        assembly,
        operation,
    ), holder


def rapid_crossing_fixture() -> tuple[ParallelFixture, None]:
    return _protected_fixture(
        wall_x=5.0,
        z_min=0.0,
        z_max=12.0,
    ), None


def _protected_fixture(
    *,
    wall_x: float,
    z_min: float,
    z_max: float,
    base: ParallelFixture | None = None,
    tool: ToolDefinition | None = None,
    assembly: ToolAssembly | None = None,
) -> ParallelFixture:
    value = base or planar_fixture(with_check=True, stepover=5.0)
    selected_tool = tool or value.tool
    selected_assembly = assembly or value.assembly
    part_surface = value.zone.part_surfaces.selection.surfaces[0]
    assert value.zone.check_surfaces is not None
    protected_surface = value.zone.check_surfaces.selection.surfaces[0]
    unit = LengthUnit.MM
    part = Cam3DResolvedSurfaceMesh(
        part_surface,
        tuple(
            Point3(*point, unit)
            for point in (
                (0.0, 0.0, 0.0),
                (10.0, 0.0, 0.0),
                (10.0, 10.0, 0.0),
                (0.0, 10.0, 0.0),
            )
        ),
        ((0, 1, 2), (0, 2, 3)),
    )
    wall = Cam3DResolvedSurfaceMesh(
        protected_surface,
        tuple(
            Point3(*point, unit)
            for point in (
                (wall_x, 0.0, z_min),
                (wall_x, 10.0, z_min),
                (wall_x, 10.0, z_max),
                (wall_x, 0.0, z_max),
            )
        ),
        ((0, 1, 2), (0, 2, 3)),
    )
    mesh = build_calculation_mesh(
        (part, wall),
        value.zone.tolerance,
        value.zone.geometry_fingerprint,
    )
    safe_motion = replace(
        value.context.safe_motion_policy,
        retract_z=max(value.context.safe_motion_policy.retract_z or 0.0, z_max + 20.0),
        clearance_z=max(
            value.context.safe_motion_policy.clearance_z or 0.0,
            z_max + 30.0,
        ),
    )
    context = replace(
        value.context,
        calculation_mesh=mesh,
        tool_assembly_fingerprint=ContentFingerprint.from_payload(
            selected_assembly.to_dict()
        ),
        tool_definition_fingerprint=selected_tool.content_fingerprint,
        safe_motion_policy=safe_motion,
    )
    operation = replace(
        value.operation,
        tool_assembly=ToolAssemblyReference.from_assembly(selected_assembly),
    )
    return ParallelFixture(
        value.zone,
        mesh,
        context,
        selected_tool,
        selected_assembly,
        operation,
    )

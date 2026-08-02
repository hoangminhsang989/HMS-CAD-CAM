"""Conservative adapters for available HMS Tool/Holder geometry."""

from __future__ import annotations

from hms_cadcam.cam.domain.tooling import HolderDefinition, ToolDefinition, TurningInsertGeometry
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.lathe.simulation.models import ToolEnvelope


def tool_envelope_from_library(
    tool: ToolDefinition | None,
    holder: HolderDefinition | None,
    *,
    orientation_deg: float | None,
) -> ToolEnvelope:
    """Copy known library dimensions; preserve unknown geometry explicitly."""

    nose: float | None = None
    insert: float | None = None
    holder_radius: float | None = None
    if tool is not None:
        if not isinstance(tool, ToolDefinition):
            raise TypeError("Tool definition is invalid")
        if isinstance(tool.cutting_geometry, TurningInsertGeometry):
            nose = float(tool.cutting_geometry.nose_radius.to(LengthUnit.MM).value)
            insert = float(tool.cutting_geometry.inscribed_circle.to(LengthUnit.MM).value) * 0.5
    if holder is not None:
        if not isinstance(holder, HolderDefinition):
            raise TypeError("Holder definition is invalid")
        holder_radius = max(float(section.upper_diameter.to(LengthUnit.MM).value) for section in holder.sections) * 0.5
    return ToolEnvelope(nose, insert, orientation_deg, holder_radius)


__all__ = ["tool_envelope_from_library"]

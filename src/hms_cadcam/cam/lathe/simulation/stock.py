"""Bounded deterministic piecewise-axisymmetric stock operations."""

from __future__ import annotations

from dataclasses import replace
import math

from hms_cadcam.cam.lathe.simulation.coordinates import finite_mm
from hms_cadcam.cam.lathe.simulation.models import AxisymmetricStock, RemovedMaterialSummary, StockStation
from hms_cadcam.cam.lathe.toolpath.stock import LatheStockSnapshotV1


def cylindrical_stock(snapshot: LatheStockSnapshotV1, *, station_count: int) -> AxisymmetricStock:
    """Create a bounded uniform profile from the canonical cylindrical snapshot."""

    if not isinstance(snapshot, LatheStockSnapshotV1):
        raise TypeError("Lathe stock snapshot is invalid")
    if type(station_count) is not int or station_count < 2:
        raise ValueError("Stock station count must be at least two")
    z_min = min(snapshot.front_z_mm, snapshot.back_z_mm)
    z_max = max(snapshot.front_z_mm, snapshot.back_z_mm)
    span = z_max - z_min
    outer = snapshot.outer_diameter_mm * 0.5
    inner = snapshot.inner_diameter_mm * 0.5
    return AxisymmetricStock(tuple(StockStation(z_min + span * index / (station_count - 1), inner, outer) for index in range(station_count)))


def stock_metrics(initial: AxisymmetricStock, current: AxisymmetricStock) -> RemovedMaterialSummary:
    """Estimate removed meridian area and revolved volume by trapezoidal integration."""

    if len(initial.stations) != len(current.stations):
        raise ValueError("Stock profiles use different station grids")
    area = 0.0
    volume = 0.0
    for left, right in zip(range(len(initial.stations) - 1), range(1, len(initial.stations))):
        dz = initial.stations[right].z_mm - initial.stations[left].z_mm
        removed_area_values: list[float] = []
        removed_volume_values: list[float] = []
        for index in (left, right):
            before = initial.stations[index]
            after = current.stations[index]
            removed_area_values.append((before.outer_radius_mm - before.inner_radius_mm) - (after.outer_radius_mm - after.inner_radius_mm))
            removed_volume_values.append(math.pi * ((before.outer_radius_mm ** 2 - before.inner_radius_mm ** 2) - (after.outer_radius_mm ** 2 - after.inner_radius_mm ** 2)))
        area += max(0.0, sum(removed_area_values) * 0.5 * dz)
        volume += max(0.0, sum(removed_volume_values) * 0.5 * dz)
    return RemovedMaterialSummary(area, volume)


def remove_at(
    stock: AxisymmetricStock,
    *,
    z_mm: float,
    tool_radius_mm: float,
    envelope_mm: float,
    internal: bool,
    axial_drill: bool = False,
) -> AxisymmetricStock:
    """Apply one conservative cutter sample without ever restoring material."""

    z_value = finite_mm(z_mm, "Removal Z")
    tool_radius = finite_mm(tool_radius_mm, "Removal tool radius")
    envelope = finite_mm(envelope_mm, "Removal envelope")
    if tool_radius < 0.0 or envelope <= 0.0:
        raise ValueError("Removal geometry is invalid")
    changed = False
    stations: list[StockStation] = []
    for station in stock.stations:
        if abs(station.z_mm - z_value) > envelope:
            stations.append(station)
            continue
        if internal or axial_drill:
            requested_inner = tool_radius + envelope if axial_drill else max(0.0, tool_radius - envelope)
            inner = min(station.outer_radius_mm, max(station.inner_radius_mm, requested_inner))
            updated = replace(station, inner_radius_mm=inner)
        else:
            requested_outer = max(0.0, tool_radius + envelope)
            outer = max(station.inner_radius_mm, min(station.outer_radius_mm, requested_outer))
            updated = replace(station, outer_radius_mm=outer)
        changed = changed or updated != station
        stations.append(updated)
    return AxisymmetricStock(tuple(stations), stock.revision + (1 if changed else 0), stock.approximation)


__all__ = ["cylindrical_stock", "remove_at", "stock_metrics"]

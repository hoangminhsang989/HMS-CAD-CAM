"""Normalized immutable stock snapshot and CylinderStock adapter for Lathe V1."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from uuid import UUID

from hms_cadcam.cam.domain.ids import SetupId
from hms_cadcam.cam.domain.setup import CylinderStock
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.lathe.toolpath.model import finite_number


@dataclass(frozen=True, slots=True)
class LatheStockSnapshotV1:
    """Setup-local cylindrical envelope without a mutable stock/native object."""

    stock_identity: str
    source_id: UUID
    generation: int
    outer_diameter_mm: float
    inner_diameter_mm: float
    front_z_mm: float
    back_z_mm: float

    def __post_init__(self) -> None:
        if not isinstance(self.stock_identity, str) or not self.stock_identity.strip():
            raise ValueError("Lathe stock identity must be non-blank")
        object.__setattr__(self, "stock_identity", self.stock_identity.strip())
        if not isinstance(self.source_id, UUID) or self.source_id.int == 0:
            raise ValueError("Lathe stock source identity must be a non-nil UUID")
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("Lathe stock generation must be non-negative")
        outer = finite_number(self.outer_diameter_mm, "Lathe stock outer diameter")
        inner = finite_number(self.inner_diameter_mm, "Lathe stock inner diameter")
        front = finite_number(self.front_z_mm, "Lathe stock front Z")
        back = finite_number(self.back_z_mm, "Lathe stock back Z")
        if outer <= 0.0:
            raise ValueError("Lathe stock outer diameter must be positive")
        if inner < 0.0 or inner >= outer:
            raise ValueError("Lathe stock inner diameter must be below outer diameter")
        if front == back:
            raise ValueError("Lathe stock front and back Z must differ")
        object.__setattr__(self, "outer_diameter_mm", outer)
        object.__setattr__(self, "inner_diameter_mm", inner)
        object.__setattr__(self, "front_z_mm", front)
        object.__setattr__(self, "back_z_mm", back)

    @property
    def axial_direction(self) -> float:
        """Return normalized stock-front-to-back direction."""

        return 1.0 if self.back_z_mm > self.front_z_mm else -1.0

    @property
    def axial_length_mm(self) -> float:
        return abs(self.back_z_mm - self.front_z_mm)

    def canonical_payload(self) -> dict[str, object]:
        return {
            "stock_identity": self.stock_identity,
            "source_id": str(self.source_id),
            "generation": self.generation,
            "outer_diameter_mm": self.outer_diameter_mm,
            "inner_diameter_mm": self.inner_diameter_mm,
            "front_z_mm": self.front_z_mm,
            "back_z_mm": self.back_z_mm,
        }


def lathe_stock_from_cylinder(
    stock: CylinderStock,
    *,
    setup_id: SetupId,
    source_id: UUID,
    generation: int,
) -> LatheStockSnapshotV1:
    """Copy one CylinderStock into the authoritative setup-local XZ envelope."""

    if not isinstance(stock, CylinderStock):
        raise TypeError("Lathe stock adapter requires CylinderStock")
    if not isinstance(setup_id, SetupId):
        raise TypeError("Lathe stock adapter setup identity is invalid")
    if not isinstance(source_id, UUID) or source_id.int == 0:
        raise ValueError("Lathe stock adapter source identity is invalid")
    if type(generation) is not int or generation < 0:
        raise ValueError("Lathe stock adapter generation is invalid")
    outer = float(stock.diameter.to(LengthUnit.MM).value)
    length = float(stock.length.to(LengthUnit.MM).value)
    identity_payload = {
        "format": "HMS_LATHE_STOCK_IDENTITY",
        "format_version": 1,
        "setup_id": str(setup_id),
        "source_id": str(source_id),
        "cylinder": stock.to_dict(),
    }
    encoded = json.dumps(
        identity_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    stock_identity = f"lathe-stock-sha256:{hashlib.sha256(encoded).hexdigest()}"
    return LatheStockSnapshotV1(
        stock_identity,
        source_id,
        generation,
        outer,
        0.0,
        0.0,
        -length,
    )


__all__ = ["LatheStockSnapshotV1", "lathe_stock_from_cylinder"]

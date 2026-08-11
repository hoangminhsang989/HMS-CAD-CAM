"""Typed Stage18A Tranche2 setup and physical-readiness contracts.

The module deliberately models unknown physical facts as ``None`` and keeps
machine-coordinate validation separate from the Level1 span checks.  It does
not manufacture machine endpoints, fixture geometry, or collision clearance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from hms_cadcam.cam.domain.errors import CamInvariantError, CamValidationError
from hms_cadcam.cam.domain.revision import (
    ContentFingerprint,
    DependencyFingerprint,
    GeometryFingerprint,
)
from hms_cadcam.cam.qualification.model import (
    AuthorityClass,
    StockEnvelope,
    canonical_json_bytes,
)


SETUP_QUALIFICATION_FORMAT = "HMS_STAGE18A_MACHINE_SETUP_QUALIFICATION"
SETUP_QUALIFICATION_VERSION = 1


class SetupQualificationState(StrEnum):
    """Truthful authority state for one setup snapshot."""

    UNVERIFIED = "UNVERIFIED"
    OWNER_CONFIRMED = "OWNER_CONFIRMED"
    MEASURED = "MEASURED"
    PHYSICAL_TEST_CONFIRMED = "PHYSICAL_TEST_CONFIRMED"
    STALE = "STALE"


class FixtureVerificationState(StrEnum):
    UNVERIFIED = "UNVERIFIED"
    OWNER_CONFIRMED = "OWNER_CONFIRMED"
    MEASURED = "MEASURED"
    PHYSICAL_TEST_CONFIRMED = "PHYSICAL_TEST_CONFIRMED"


class PhysicalTravelState(StrEnum):
    PHYSICAL_TRAVEL_STATICALLY_VALIDATED = "PHYSICAL_TRAVEL_STATICALLY_VALIDATED"
    PHYSICAL_TRAVEL_VALIDATION_UNAVAILABLE = "PHYSICAL_TRAVEL_VALIDATION_UNAVAILABLE"
    PHYSICAL_TRAVEL_OUTSIDE_LIMITS = "PHYSICAL_TRAVEL_OUTSIDE_LIMITS"
    SETUP_TRANSFORM_INVALID = "SETUP_TRANSFORM_INVALID"


class PlacementState(StrEnum):
    PLACEMENT_STATICALLY_VALIDATED = "PLACEMENT_STATICALLY_VALIDATED"
    PLACEMENT_OUTSIDE_TABLE_ENVELOPE = "PLACEMENT_OUTSIDE_TABLE_ENVELOPE"
    STOCK_PLACEMENT_UNVERIFIED = "STOCK_PLACEMENT_UNVERIFIED"
    FIXTURE_PLACEMENT_UNVERIFIED = "FIXTURE_PLACEMENT_UNVERIFIED"


class ToolReachState(StrEnum):
    TOOL_REACH_STATICALLY_VALIDATED = "TOOL_REACH_STATICALLY_VALIDATED"
    TOOL_REACH_PHYSICALLY_CONFIRMED = "TOOL_REACH_PHYSICALLY_CONFIRMED"
    TOOL_REACH_INSUFFICIENT = "TOOL_REACH_INSUFFICIENT"
    TOOL_REACH_EVIDENCE_INCOMPLETE = "TOOL_REACH_EVIDENCE_INCOMPLETE"


class ClearanceState(StrEnum):
    HOLDER_FIXTURE_CLEARANCE_STATICALLY_VALIDATED = (
        "HOLDER_FIXTURE_CLEARANCE_STATICALLY_VALIDATED"
    )
    HOLDER_FIXTURE_CLEARANCE_PHYSICALLY_CONFIRMED = (
        "HOLDER_FIXTURE_CLEARANCE_PHYSICALLY_CONFIRMED"
    )
    HOLDER_FIXTURE_COLLISION_DETECTED = "HOLDER_FIXTURE_COLLISION_DETECTED"
    HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED = "HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED"


def _text(value: str, name: str, *, maximum: int = 1024) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise CamValidationError(f"{name} is invalid")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise CamValidationError(f"{name} contains control characters")
    return value.strip()


def _optional_text(value: str | None, name: str, *, maximum: int = 1024) -> str | None:
    return None if value is None else _text(value, name, maximum=maximum)


def _finite(value: float | None, name: str, *, positive: bool = False) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CamValidationError(f"{name} is invalid")
    normalized = float(value)
    if not math.isfinite(normalized) or (positive and normalized <= 0.0):
        raise CamValidationError(f"{name} is invalid")
    return normalized


def _timestamp(value: str, name: str) -> str:
    normalized = _text(value, name, maximum=64)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as error:
        raise CamValidationError(f"{name} is invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CamValidationError(f"{name} requires a timezone")
    return normalized


def _fingerprint(value: Any, name: str) -> ContentFingerprint:
    try:
        kind = value.get("kind") if isinstance(value, dict) else None
        fingerprint_type = {
            ContentFingerprint.KIND: ContentFingerprint,
            DependencyFingerprint.KIND: DependencyFingerprint,
            GeometryFingerprint.KIND: GeometryFingerprint,
        }.get(kind)
        if fingerprint_type is None:
            raise CamValidationError("Unsupported fingerprint kind")
        return fingerprint_type.from_dict(value)
    except (TypeError, CamValidationError) as error:
        raise CamValidationError(f"{name} is invalid") from error


@dataclass(frozen=True, slots=True)
class Coordinate3D:
    """One complete coordinate in millimetres."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        for name in ("x", "y", "z"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Coordinate3D":
        if not isinstance(data, dict) or set(data) != {"x", "y", "z"}:
            raise CamValidationError("Coordinate payload is malformed")
        return cls(data["x"], data["y"], data["z"])


@dataclass(frozen=True, slots=True)
class PartialCoordinate3D:
    """A coordinate whose individual physical components may be unknown."""

    x: float | None = None
    y: float | None = None
    z: float | None = None

    def __post_init__(self) -> None:
        for name in ("x", "y", "z"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))

    @property
    def complete(self) -> bool:
        return self.x is not None and self.y is not None and self.z is not None

    def to_coordinate(self) -> Coordinate3D:
        if not self.complete:
            raise CamValidationError("Coordinate contains unknown components")
        return Coordinate3D(self.x, self.y, self.z)

    def to_dict(self) -> dict[str, float | None]:
        return {"x": self.x, "y": self.y, "z": self.z}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PartialCoordinate3D":
        if not isinstance(data, dict) or set(data) != {"x", "y", "z"}:
            raise CamValidationError("Partial coordinate payload is malformed")
        return cls(data["x"], data["y"], data["z"])


@dataclass(frozen=True, slots=True)
class Orientation3D:
    """Explicit XYZ Euler orientation in degrees; components may be unknown."""

    x_deg: float | None = None
    y_deg: float | None = None
    z_deg: float | None = None

    def __post_init__(self) -> None:
        for name in ("x_deg", "y_deg", "z_deg"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))

    @property
    def complete(self) -> bool:
        return self.x_deg is not None and self.y_deg is not None and self.z_deg is not None

    def to_dict(self) -> dict[str, float | None]:
        return {"x_deg": self.x_deg, "y_deg": self.y_deg, "z_deg": self.z_deg}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Orientation3D":
        fields = {"x_deg", "y_deg", "z_deg"}
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Orientation payload is malformed")
        return cls(data["x_deg"], data["y_deg"], data["z_deg"])


@dataclass(frozen=True, slots=True)
class WorkOffsetTransform:
    """Explicit PART/PROGRAM -> MACHINE transform authority."""

    work_offset: str
    translation_mm: PartialCoordinate3D
    orientation_deg: Orientation3D
    source: str
    authority: AuthorityClass
    measured_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_offset", _text(self.work_offset, "Work offset", maximum=8).upper())
        if self.work_offset != "G54":
            raise CamValidationError("Only the canonical G54 setup path is supported")
        if not isinstance(self.translation_mm, PartialCoordinate3D):
            raise CamValidationError("Work-offset translation is invalid")
        if not isinstance(self.orientation_deg, Orientation3D):
            raise CamValidationError("Work-offset orientation is invalid")
        object.__setattr__(self, "source", _text(self.source, "Transform source"))
        if not isinstance(self.authority, AuthorityClass):
            raise CamValidationError("Transform authority is invalid")
        if self.measured_at is not None:
            object.__setattr__(self, "measured_at", _timestamp(self.measured_at, "Transform timestamp"))

    @property
    def complete(self) -> bool:
        return self.translation_mm.complete and self.orientation_deg.complete

    @property
    def authoritative(self) -> bool:
        return self.complete and self.authority is not AuthorityClass.UNVERIFIED

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def apply(self, point: Coordinate3D) -> Coordinate3D:
        """Apply XYZ Euler rotation and translation to one program point."""

        if not isinstance(point, Coordinate3D):
            raise TypeError("point must be Coordinate3D")
        if not self.authoritative:
            raise CamValidationError("Setup transform is incomplete or unverified")
        translation = self.translation_mm.to_coordinate()
        rx, ry, rz = (
            math.radians(self.orientation_deg.x_deg),
            math.radians(self.orientation_deg.y_deg),
            math.radians(self.orientation_deg.z_deg),
        )
        x, y, z = point.x, point.y, point.z
        y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
        x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
        x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
        return Coordinate3D(x + translation.x, y + translation.y, z + translation.z)

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_offset": self.work_offset,
            "translation_mm": self.translation_mm.to_dict(),
            "orientation_deg": self.orientation_deg.to_dict(),
            "source": self.source,
            "authority": self.authority.value,
            "measured_at": self.measured_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkOffsetTransform":
        fields = {
            "work_offset", "translation_mm", "orientation_deg", "source",
            "authority", "measured_at",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Work-offset transform payload is malformed")
        try:
            authority = AuthorityClass(data["authority"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Transform authority payload is invalid") from error
        return cls(
            data["work_offset"],
            PartialCoordinate3D.from_dict(data["translation_mm"]),
            Orientation3D.from_dict(data["orientation_deg"]),
            data["source"], authority, data["measured_at"],
        )


@dataclass(frozen=True, slots=True)
class AxisTravelLimit:
    minimum_mm: float | None = None
    maximum_mm: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum_mm", _finite(self.minimum_mm, "Axis minimum"))
        object.__setattr__(self, "maximum_mm", _finite(self.maximum_mm, "Axis maximum"))
        if (
            self.minimum_mm is not None
            and self.maximum_mm is not None
            and self.minimum_mm >= self.maximum_mm
        ):
            raise CamValidationError("Axis endpoints are invalid")

    @property
    def complete(self) -> bool:
        return self.minimum_mm is not None and self.maximum_mm is not None

    def contains(self, value: float) -> bool:
        if not self.complete:
            raise CamValidationError("Axis endpoints are unavailable")
        return self.minimum_mm <= value <= self.maximum_mm

    def to_dict(self) -> dict[str, float | None]:
        return {"minimum_mm": self.minimum_mm, "maximum_mm": self.maximum_mm}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AxisTravelLimit":
        if not isinstance(data, dict) or set(data) != {"minimum_mm", "maximum_mm"}:
            raise CamValidationError("Axis limit payload is malformed")
        return cls(data["minimum_mm"], data["maximum_mm"])


@dataclass(frozen=True, slots=True)
class MachineTravelContract:
    x: AxisTravelLimit
    y: AxisTravelLimit
    z: AxisTravelLimit
    source: str
    authority: AuthorityClass

    def __post_init__(self) -> None:
        if any(not isinstance(value, AxisTravelLimit) for value in (self.x, self.y, self.z)):
            raise CamValidationError("Machine travel contract is invalid")
        object.__setattr__(self, "source", _text(self.source, "Travel limit source"))
        if not isinstance(self.authority, AuthorityClass):
            raise CamValidationError("Travel limit authority is invalid")

    @property
    def authoritative(self) -> bool:
        return all(value.complete for value in (self.x, self.y, self.z)) and (
            self.authority is not AuthorityClass.UNVERIFIED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x.to_dict(), "y": self.y.to_dict(), "z": self.z.to_dict(),
            "source": self.source, "authority": self.authority.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MachineTravelContract":
        fields = {"x", "y", "z", "source", "authority"}
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Machine travel payload is malformed")
        try:
            authority = AuthorityClass(data["authority"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Machine travel authority is invalid") from error
        return cls(
            AxisTravelLimit.from_dict(data["x"]),
            AxisTravelLimit.from_dict(data["y"]),
            AxisTravelLimit.from_dict(data["z"]), data["source"], authority,
        )


@dataclass(frozen=True, slots=True)
class EnvelopeDimensions:
    x_mm: float
    y_mm: float
    z_mm: float

    def __post_init__(self) -> None:
        for name in ("x_mm", "y_mm", "z_mm"):
            object.__setattr__(self, name, _finite(getattr(self, name), name, positive=True))

    def to_dict(self) -> dict[str, float]:
        return {"x_mm": self.x_mm, "y_mm": self.y_mm, "z_mm": self.z_mm}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EnvelopeDimensions":
        if not isinstance(data, dict) or set(data) != {"x_mm", "y_mm", "z_mm"}:
            raise CamValidationError("Envelope dimensions payload is malformed")
        return cls(data["x_mm"], data["y_mm"], data["z_mm"])


@dataclass(frozen=True, slots=True)
class StockPlacementEvidence:
    dimensions: EnvelopeDimensions
    origin_machine_mm: PartialCoordinate3D
    orientation_deg: Orientation3D
    source: str
    authority: AuthorityClass

    def __post_init__(self) -> None:
        if not isinstance(self.dimensions, EnvelopeDimensions):
            raise CamValidationError("Stock dimensions are invalid")
        if not isinstance(self.origin_machine_mm, PartialCoordinate3D):
            raise CamValidationError("Stock origin is invalid")
        if not isinstance(self.orientation_deg, Orientation3D):
            raise CamValidationError("Stock orientation is invalid")
        object.__setattr__(self, "source", _text(self.source, "Stock placement source"))
        if not isinstance(self.authority, AuthorityClass):
            raise CamValidationError("Stock placement authority is invalid")

    @property
    def authoritative(self) -> bool:
        return (
            self.origin_machine_mm.complete
            and self.orientation_deg.complete
            and self.authority is not AuthorityClass.UNVERIFIED
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimensions": self.dimensions.to_dict(),
            "origin_machine_mm": self.origin_machine_mm.to_dict(),
            "orientation_deg": self.orientation_deg.to_dict(),
            "source": self.source, "authority": self.authority.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StockPlacementEvidence":
        fields = {"dimensions", "origin_machine_mm", "orientation_deg", "source", "authority"}
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Stock placement payload is malformed")
        try:
            authority = AuthorityClass(data["authority"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Stock placement authority is invalid") from error
        return cls(
            EnvelopeDimensions.from_dict(data["dimensions"]),
            PartialCoordinate3D.from_dict(data["origin_machine_mm"]),
            Orientation3D.from_dict(data["orientation_deg"]), data["source"], authority,
        )


@dataclass(frozen=True, slots=True)
class FixtureEvidence:
    fixture_id: str
    fixture_type: str
    bounding_envelope: EnvelopeDimensions | None
    location_machine_mm: PartialCoordinate3D
    orientation_deg: Orientation3D
    source: str
    authority: AuthorityClass
    verification_state: FixtureVerificationState

    def __post_init__(self) -> None:
        object.__setattr__(self, "fixture_id", _text(self.fixture_id, "Fixture ID"))
        object.__setattr__(self, "fixture_type", _text(self.fixture_type, "Fixture type"))
        if self.bounding_envelope is not None and not isinstance(
            self.bounding_envelope, EnvelopeDimensions
        ):
            raise CamValidationError("Fixture envelope is invalid")
        if not isinstance(self.location_machine_mm, PartialCoordinate3D):
            raise CamValidationError("Fixture location is invalid")
        if not isinstance(self.orientation_deg, Orientation3D):
            raise CamValidationError("Fixture orientation is invalid")
        object.__setattr__(self, "source", _text(self.source, "Fixture source"))
        if not isinstance(self.authority, AuthorityClass) or not isinstance(
            self.verification_state, FixtureVerificationState
        ):
            raise CamValidationError("Fixture authority/state is invalid")
        if self.verification_state is FixtureVerificationState.PHYSICAL_TEST_CONFIRMED and (
            self.authority is not AuthorityClass.PHYSICAL_TEST_CONFIRMED
        ):
            raise CamInvariantError("Physical fixture confirmation requires physical authority")

    @property
    def placement_verified(self) -> bool:
        return (
            self.bounding_envelope is not None
            and self.location_machine_mm.complete
            and self.orientation_deg.complete
            and self.verification_state is not FixtureVerificationState.UNVERIFIED
            and self.authority is not AuthorityClass.UNVERIFIED
        )

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "fixture_id": self.fixture_id, "fixture_type": self.fixture_type,
            "bounding_envelope": (
                None if self.bounding_envelope is None else self.bounding_envelope.to_dict()
            ),
            "location_machine_mm": self.location_machine_mm.to_dict(),
            "orientation_deg": self.orientation_deg.to_dict(), "source": self.source,
            "authority": self.authority.value,
            "verification_state": self.verification_state.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FixtureEvidence":
        fields = {
            "fixture_id", "fixture_type", "bounding_envelope", "location_machine_mm",
            "orientation_deg", "source", "authority", "verification_state",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Fixture evidence payload is malformed")
        try:
            authority = AuthorityClass(data["authority"])
            state = FixtureVerificationState(data["verification_state"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Fixture evidence enum is invalid") from error
        return cls(
            data["fixture_id"], data["fixture_type"],
            None if data["bounding_envelope"] is None else EnvelopeDimensions.from_dict(data["bounding_envelope"]),
            PartialCoordinate3D.from_dict(data["location_machine_mm"]),
            Orientation3D.from_dict(data["orientation_deg"]), data["source"], authority, state,
        )


@dataclass(frozen=True, slots=True)
class ToolHolderQualification:
    tool_number: int
    assembly_fingerprint: ContentFingerprint
    cutter_fingerprint: ContentFingerprint
    holder_fingerprint: ContentFingerprint | None
    gauge_length_mm: float | None
    stickout_mm: float | None
    total_assembly_length_mm: float | None
    usable_axial_length_mm: float | None
    requested_depth_mm: float | None
    cutter_diameter_mm: float | None
    holder_max_diameter_mm: float | None
    physical_reach_confirmed: bool = False

    def __post_init__(self) -> None:
        if type(self.tool_number) is not int or self.tool_number <= 0:
            raise CamValidationError("Tool number is invalid")
        for name in ("assembly_fingerprint", "cutter_fingerprint"):
            if not isinstance(getattr(self, name), ContentFingerprint):
                raise CamValidationError(f"{name} is invalid")
        if self.holder_fingerprint is not None and not isinstance(
            self.holder_fingerprint, ContentFingerprint
        ):
            raise CamValidationError("Holder fingerprint is invalid")
        for name in (
            "gauge_length_mm", "stickout_mm", "total_assembly_length_mm",
            "usable_axial_length_mm", "requested_depth_mm", "cutter_diameter_mm",
            "holder_max_diameter_mm",
        ):
            object.__setattr__(self, name, _finite(getattr(self, name), name, positive=True))
        if type(self.physical_reach_confirmed) is not bool:
            raise CamValidationError("Physical reach confirmation is invalid")

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.to_dict())

    @property
    def reach_state(self) -> ToolReachState:
        if self.requested_depth_mm is None or self.usable_axial_length_mm is None:
            return ToolReachState.TOOL_REACH_EVIDENCE_INCOMPLETE
        if self.requested_depth_mm > self.usable_axial_length_mm:
            return ToolReachState.TOOL_REACH_INSUFFICIENT
        if self.stickout_mm is None or self.gauge_length_mm is None:
            return ToolReachState.TOOL_REACH_EVIDENCE_INCOMPLETE
        if self.requested_depth_mm > self.stickout_mm:
            return ToolReachState.TOOL_REACH_INSUFFICIENT
        if self.physical_reach_confirmed:
            return ToolReachState.TOOL_REACH_PHYSICALLY_CONFIRMED
        return ToolReachState.TOOL_REACH_STATICALLY_VALIDATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_number": self.tool_number,
            "assembly_fingerprint": self.assembly_fingerprint.to_dict(),
            "cutter_fingerprint": self.cutter_fingerprint.to_dict(),
            "holder_fingerprint": (
                None if self.holder_fingerprint is None else self.holder_fingerprint.to_dict()
            ),
            "gauge_length_mm": self.gauge_length_mm, "stickout_mm": self.stickout_mm,
            "total_assembly_length_mm": self.total_assembly_length_mm,
            "usable_axial_length_mm": self.usable_axial_length_mm,
            "requested_depth_mm": self.requested_depth_mm,
            "cutter_diameter_mm": self.cutter_diameter_mm,
            "holder_max_diameter_mm": self.holder_max_diameter_mm,
            "physical_reach_confirmed": self.physical_reach_confirmed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolHolderQualification":
        fields = {
            "tool_number", "assembly_fingerprint", "cutter_fingerprint",
            "holder_fingerprint", "gauge_length_mm", "stickout_mm",
            "total_assembly_length_mm", "usable_axial_length_mm", "requested_depth_mm",
            "cutter_diameter_mm", "holder_max_diameter_mm", "physical_reach_confirmed",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Tool/holder qualification payload is malformed")
        return cls(
            data["tool_number"], _fingerprint(data["assembly_fingerprint"], "Assembly fingerprint"),
            _fingerprint(data["cutter_fingerprint"], "Cutter fingerprint"),
            None if data["holder_fingerprint"] is None else _fingerprint(data["holder_fingerprint"], "Holder fingerprint"),
            data["gauge_length_mm"], data["stickout_mm"], data["total_assembly_length_mm"],
            data["usable_axial_length_mm"], data["requested_depth_mm"],
            data["cutter_diameter_mm"], data["holder_max_diameter_mm"],
            data["physical_reach_confirmed"],
        )


@dataclass(frozen=True, slots=True)
class HolderFixtureClearanceEvidence:
    """Binding to existing simulation/collision or physical evidence."""

    setup_fingerprint: ContentFingerprint
    tool_set_fingerprint: ContentFingerprint
    fixture_fingerprint: ContentFingerprint
    result: ClearanceState
    source_reference: str
    authority: AuthorityClass

    def __post_init__(self) -> None:
        for name in ("setup_fingerprint", "tool_set_fingerprint", "fixture_fingerprint"):
            if not isinstance(getattr(self, name), ContentFingerprint):
                raise CamValidationError(f"Clearance {name} is invalid")
        if not isinstance(self.result, ClearanceState):
            raise CamValidationError("Clearance result is invalid")
        object.__setattr__(self, "source_reference", _text(self.source_reference, "Clearance source"))
        if not isinstance(self.authority, AuthorityClass):
            raise CamValidationError("Clearance authority is invalid")
        if self.result is ClearanceState.HOLDER_FIXTURE_CLEARANCE_PHYSICALLY_CONFIRMED and (
            self.authority is not AuthorityClass.PHYSICAL_TEST_CONFIRMED
        ):
            raise CamInvariantError("Physical clearance requires physical-test authority")

    def to_dict(self) -> dict[str, Any]:
        return {
            "setup_fingerprint": self.setup_fingerprint.to_dict(),
            "tool_set_fingerprint": self.tool_set_fingerprint.to_dict(),
            "fixture_fingerprint": self.fixture_fingerprint.to_dict(),
            "result": self.result.value, "source_reference": self.source_reference,
            "authority": self.authority.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "HolderFixtureClearanceEvidence":
        fields = {
            "setup_fingerprint", "tool_set_fingerprint", "fixture_fingerprint",
            "result", "source_reference", "authority",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("Clearance evidence payload is malformed")
        try:
            result = ClearanceState(data["result"])
            authority = AuthorityClass(data["authority"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Clearance evidence enum is invalid") from error
        return cls(
            _fingerprint(data["setup_fingerprint"], "Setup fingerprint"),
            _fingerprint(data["tool_set_fingerprint"], "Tool set fingerprint"),
            _fingerprint(data["fixture_fingerprint"], "Fixture fingerprint"),
            result, data["source_reference"], authority,
        )


@dataclass(frozen=True, slots=True)
class MachineSetupQualification:
    machine_profile_id: str
    machine_profile_fingerprint: ContentFingerprint
    nc_artifact_id: str
    nc_sha256: str
    post_fingerprint: ContentFingerprint
    work_offset_transform: WorkOffsetTransform
    part_zero: PartialCoordinate3D
    machine_coordinate_reference: str | None
    stock: StockPlacementEvidence
    fixture: FixtureEvidence | None
    tools: tuple[ToolHolderQualification, ...]
    setup_timestamp: str
    authority: AuthorityClass
    provenance: str
    qualification_state: SetupQualificationState
    clearance_evidence: HolderFixtureClearanceEvidence | None = None
    format_version: int = SETUP_QUALIFICATION_VERSION

    def __post_init__(self) -> None:
        if self.format_version != SETUP_QUALIFICATION_VERSION:
            raise CamValidationError("Unsupported setup qualification version")
        object.__setattr__(self, "machine_profile_id", _text(self.machine_profile_id, "Machine profile ID"))
        if not isinstance(self.machine_profile_fingerprint, ContentFingerprint):
            raise CamValidationError("Machine profile fingerprint is invalid")
        object.__setattr__(self, "nc_artifact_id", _text(self.nc_artifact_id, "NC artifact ID"))
        if not isinstance(self.nc_sha256, str) or len(self.nc_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.nc_sha256
        ):
            raise CamValidationError("NC SHA-256 is invalid")
        if not isinstance(self.post_fingerprint, ContentFingerprint):
            raise CamValidationError("Post fingerprint is invalid")
        if not isinstance(self.work_offset_transform, WorkOffsetTransform):
            raise CamValidationError("Setup transform is invalid")
        if not isinstance(self.part_zero, PartialCoordinate3D):
            raise CamValidationError("Part zero is invalid")
        object.__setattr__(self, "machine_coordinate_reference", _optional_text(self.machine_coordinate_reference, "Machine coordinate reference"))
        if not isinstance(self.stock, StockPlacementEvidence):
            raise CamValidationError("Stock placement is invalid")
        if self.fixture is not None and not isinstance(self.fixture, FixtureEvidence):
            raise CamValidationError("Fixture evidence is invalid")
        if not isinstance(self.tools, tuple) or not self.tools or any(
            not isinstance(item, ToolHolderQualification) for item in self.tools
        ):
            raise CamValidationError("Tool qualification set is invalid")
        if len({item.tool_number for item in self.tools}) != len(self.tools):
            raise CamInvariantError("Tool qualification numbers must be unique")
        if tuple(sorted(self.tools, key=lambda item: item.tool_number)) != self.tools:
            raise CamInvariantError("Tool qualification set must be ordered")
        object.__setattr__(self, "setup_timestamp", _timestamp(self.setup_timestamp, "Setup timestamp"))
        if not isinstance(self.authority, AuthorityClass):
            raise CamValidationError("Setup authority is invalid")
        object.__setattr__(self, "provenance", _text(self.provenance, "Setup provenance", maximum=2048))
        if not isinstance(self.qualification_state, SetupQualificationState):
            raise CamValidationError("Setup qualification state is invalid")
        if self.clearance_evidence is not None and not isinstance(
            self.clearance_evidence, HolderFixtureClearanceEvidence
        ):
            raise CamValidationError("Clearance evidence is invalid")

    @property
    def tool_set_fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload([item.to_dict() for item in self.tools])

    @property
    def binding_fingerprint(self) -> ContentFingerprint:
        """Fingerprint setup facts without the evidence that binds back to them."""

        payload = self.identity_payload()
        payload["clearance_evidence"] = None
        return ContentFingerprint.from_payload(payload)

    @property
    def fingerprint(self) -> ContentFingerprint:
        return ContentFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        return {
            "format": SETUP_QUALIFICATION_FORMAT,
            "format_version": self.format_version,
            "machine_profile_id": self.machine_profile_id,
            "machine_profile_fingerprint": self.machine_profile_fingerprint.to_dict(),
            "nc_artifact_id": self.nc_artifact_id, "nc_sha256": self.nc_sha256,
            "post_fingerprint": self.post_fingerprint.to_dict(),
            "work_offset_transform": self.work_offset_transform.to_dict(),
            "part_zero": self.part_zero.to_dict(),
            "machine_coordinate_reference": self.machine_coordinate_reference,
            "stock": self.stock.to_dict(),
            "fixture": None if self.fixture is None else self.fixture.to_dict(),
            "tools": [item.to_dict() for item in self.tools],
            "setup_timestamp": self.setup_timestamp, "authority": self.authority.value,
            "provenance": self.provenance,
            "qualification_state": self.qualification_state.value,
            "clearance_evidence": (
                None if self.clearance_evidence is None else self.clearance_evidence.to_dict()
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return self.identity_payload()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MachineSetupQualification":
        fields = {
            "format", "format_version", "machine_profile_id", "machine_profile_fingerprint",
            "nc_artifact_id", "nc_sha256", "post_fingerprint", "work_offset_transform",
            "part_zero", "machine_coordinate_reference", "stock", "fixture", "tools",
            "setup_timestamp", "authority", "provenance", "qualification_state",
            "clearance_evidence",
        }
        if (
            not isinstance(data, dict) or set(data) != fields
            or data["format"] != SETUP_QUALIFICATION_FORMAT
            or not isinstance(data["tools"], list)
        ):
            raise CamValidationError("Machine setup qualification payload is malformed")
        try:
            authority = AuthorityClass(data["authority"])
            state = SetupQualificationState(data["qualification_state"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Machine setup enum payload is invalid") from error
        return cls(
            data["machine_profile_id"],
            _fingerprint(data["machine_profile_fingerprint"], "Machine profile fingerprint"),
            data["nc_artifact_id"], data["nc_sha256"],
            _fingerprint(data["post_fingerprint"], "Post fingerprint"),
            WorkOffsetTransform.from_dict(data["work_offset_transform"]),
            PartialCoordinate3D.from_dict(data["part_zero"]),
            data["machine_coordinate_reference"], StockPlacementEvidence.from_dict(data["stock"]),
            None if data["fixture"] is None else FixtureEvidence.from_dict(data["fixture"]),
            tuple(ToolHolderQualification.from_dict(item) for item in data["tools"]),
            data["setup_timestamp"], authority, data["provenance"], state,
            None if data["clearance_evidence"] is None else HolderFixtureClearanceEvidence.from_dict(data["clearance_evidence"]),
            data["format_version"],
        )


@dataclass(frozen=True, slots=True)
class PhysicalReadinessResult:
    travel_state: PhysicalTravelState
    placement_states: tuple[PlacementState, ...]
    tool_reach_states: tuple[tuple[int, ToolReachState], ...]
    clearance_state: ClearanceState
    machine_points: tuple[Coordinate3D, ...]
    blockers: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def ready_for_external_evidence(self) -> bool:
        return not self.blockers and not self.missing

    def to_dict(self) -> dict[str, Any]:
        return {
            "travel_state": self.travel_state.value,
            "placement_states": [item.value for item in self.placement_states],
            "tool_reach_states": [[number, state.value] for number, state in self.tool_reach_states],
            "clearance_state": self.clearance_state.value,
            "machine_points": [item.to_dict() for item in self.machine_points],
            "blockers": list(self.blockers), "missing": list(self.missing),
            "ready_for_external_evidence": self.ready_for_external_evidence,
        }


def validate_physical_travel(
    program_points: tuple[Coordinate3D, ...],
    transform: WorkOffsetTransform,
    limits: MachineTravelContract,
) -> tuple[PhysicalTravelState, tuple[Coordinate3D, ...]]:
    """Validate true machine coordinates only when both authorities exist."""

    if not isinstance(program_points, tuple) or any(
        not isinstance(item, Coordinate3D) for item in program_points
    ):
        raise TypeError("program_points must be a Coordinate3D tuple")
    if not isinstance(transform, WorkOffsetTransform) or not isinstance(
        limits, MachineTravelContract
    ):
        raise TypeError("transform/limits contracts are invalid")
    if not transform.authoritative:
        return PhysicalTravelState.SETUP_TRANSFORM_INVALID, ()
    if not limits.authoritative:
        return PhysicalTravelState.PHYSICAL_TRAVEL_VALIDATION_UNAVAILABLE, ()
    machine_points = tuple(transform.apply(point) for point in program_points)
    inside = all(
        limits.x.contains(point.x) and limits.y.contains(point.y) and limits.z.contains(point.z)
        for point in machine_points
    )
    return (
        PhysicalTravelState.PHYSICAL_TRAVEL_STATICALLY_VALIDATED
        if inside else PhysicalTravelState.PHYSICAL_TRAVEL_OUTSIDE_LIMITS,
        machine_points,
    )


def validate_stock_and_fixture_placement(
    setup: MachineSetupQualification,
    table_width_mm: float,
    table_depth_mm: float,
) -> tuple[PlacementState, ...]:
    """Conservatively validate the known stock footprint and fixture authority."""

    if not isinstance(setup, MachineSetupQualification):
        raise TypeError("setup must be MachineSetupQualification")
    width = _finite(table_width_mm, "Table width", positive=True)
    depth = _finite(table_depth_mm, "Table depth", positive=True)
    states: list[PlacementState] = []
    if not setup.stock.authoritative:
        states.append(PlacementState.STOCK_PLACEMENT_UNVERIFIED)
    else:
        origin = setup.stock.origin_machine_mm.to_coordinate()
        angle = math.radians(setup.stock.orientation_deg.z_deg)
        x_footprint = abs(setup.stock.dimensions.x_mm * math.cos(angle)) + abs(
            setup.stock.dimensions.y_mm * math.sin(angle)
        )
        y_footprint = abs(setup.stock.dimensions.x_mm * math.sin(angle)) + abs(
            setup.stock.dimensions.y_mm * math.cos(angle)
        )
        if origin.x < 0.0 or origin.y < 0.0 or origin.x + x_footprint > width or origin.y + y_footprint > depth:
            states.append(PlacementState.PLACEMENT_OUTSIDE_TABLE_ENVELOPE)
        else:
            states.append(PlacementState.PLACEMENT_STATICALLY_VALIDATED)
    if setup.fixture is None or not setup.fixture.placement_verified:
        states.append(PlacementState.FIXTURE_PLACEMENT_UNVERIFIED)
    return tuple(states)


def clearance_state_for_setup(setup: MachineSetupQualification) -> ClearanceState:
    """Accept only current evidence from the existing collision/physical surface."""

    evidence = setup.clearance_evidence
    fixture = setup.fixture
    if evidence is None or fixture is None:
        return ClearanceState.HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED
    if (
        evidence.setup_fingerprint != setup.binding_fingerprint
        or evidence.tool_set_fingerprint != setup.tool_set_fingerprint
        or evidence.fixture_fingerprint != fixture.fingerprint
    ):
        return ClearanceState.HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED
    return evidence.result


def calculate_physical_readiness(
    setup: MachineSetupQualification,
    program_points: tuple[Coordinate3D, ...],
    limits: MachineTravelContract,
    *,
    table_width_mm: float,
    table_depth_mm: float,
) -> PhysicalReadinessResult:
    """Produce deterministic ready/missing/blocker detail for external testing."""

    travel_state, machine_points = validate_physical_travel(program_points, setup.work_offset_transform, limits)
    placements = validate_stock_and_fixture_placement(setup, table_width_mm, table_depth_mm)
    reach = tuple((item.tool_number, item.reach_state) for item in setup.tools)
    clearance = clearance_state_for_setup(setup)
    blockers: list[str] = []
    missing: list[str] = []
    if travel_state in {PhysicalTravelState.SETUP_TRANSFORM_INVALID, PhysicalTravelState.PHYSICAL_TRAVEL_OUTSIDE_LIMITS}:
        blockers.append(travel_state.value)
    elif travel_state is PhysicalTravelState.PHYSICAL_TRAVEL_VALIDATION_UNAVAILABLE:
        missing.append(travel_state.value)
    for state in placements:
        if state is PlacementState.PLACEMENT_OUTSIDE_TABLE_ENVELOPE:
            blockers.append(state.value)
        elif state is not PlacementState.PLACEMENT_STATICALLY_VALIDATED:
            missing.append(state.value)
    for number, state in reach:
        token = f"T{number}:{state.value}"
        if state is ToolReachState.TOOL_REACH_INSUFFICIENT:
            blockers.append(token)
        elif state is ToolReachState.TOOL_REACH_EVIDENCE_INCOMPLETE:
            missing.append(token)
    if clearance is ClearanceState.HOLDER_FIXTURE_COLLISION_DETECTED:
        blockers.append(clearance.value)
    elif clearance is ClearanceState.HOLDER_FIXTURE_CLEARANCE_NOT_VERIFIED:
        missing.append(clearance.value)
    return PhysicalReadinessResult(
        travel_state, placements, reach, clearance, machine_points,
        tuple(sorted(set(blockers))), tuple(sorted(set(missing))),
    )


def stock_envelope_from_setup(setup: MachineSetupQualification) -> StockEnvelope:
    """Adapt explicit Tranche2 stock dimensions to the existing Level1 model."""

    dimensions = setup.stock.dimensions
    return StockEnvelope(dimensions.x_mm, dimensions.y_mm, dimensions.z_mm)


__all__ = [
    "AxisTravelLimit", "ClearanceState", "Coordinate3D", "EnvelopeDimensions",
    "FixtureEvidence", "FixtureVerificationState", "HolderFixtureClearanceEvidence",
    "MachineSetupQualification", "MachineTravelContract", "Orientation3D",
    "PartialCoordinate3D", "PhysicalReadinessResult", "PhysicalTravelState",
    "PlacementState", "SETUP_QUALIFICATION_FORMAT", "SETUP_QUALIFICATION_VERSION",
    "SetupQualificationState", "StockPlacementEvidence", "ToolHolderQualification",
    "ToolReachState", "WorkOffsetTransform", "calculate_physical_readiness",
    "clearance_state_for_setup", "stock_envelope_from_setup",
    "validate_physical_travel", "validate_stock_and_fixture_placement",
]

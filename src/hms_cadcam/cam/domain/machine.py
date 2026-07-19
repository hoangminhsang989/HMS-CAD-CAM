"""Controller-neutral machine, axis and kinematic domain models."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from hms_cadcam.cam.domain.errors import (
    CamInvariantError,
    CamUnitError,
    CamValidationError,
    DuplicateCamIdError,
)
from hms_cadcam.cam.domain.ids import MachineDefinitionId
from hms_cadcam.cam.domain.revision import ContentFingerprint, Revision
from hms_cadcam.cam.domain.spatial import (
    WCS_ORTHONORMAL_TOLERANCE,
    AffineTransform,
    Vector3,
    _strict_payload,
)
from hms_cadcam.cam.domain.units import (
    Angle,
    AngleUnit,
    FeedRate,
    FeedUnit,
    Length,
    LengthUnit,
    SpindleSpeed,
    SpindleSpeedUnit,
)

_AXIS_FORMAT = "HMS_CAM_MACHINE_AXIS"
_CAPABILITY_FORMAT = "HMS_CAM_MACHINE_CAPABILITY"
_KINEMATIC_FORMAT = "HMS_CAM_KINEMATIC_CHAIN"
_MACHINE_FORMAT = "HMS_CAM_MACHINE_DEFINITION"
_VERSION = 1
_LOGICAL_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")


def _name(value: str, subject: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CamValidationError(f"{subject} must not be empty")
    normalized = value.strip()
    if len(normalized) > 255:
        raise CamValidationError(f"{subject} is too long")
    return normalized


def _logical(value: str, subject: str) -> str:
    if not isinstance(value, str) or not _LOGICAL_ID.fullmatch(value):
        raise CamValidationError(f"{subject} must be a logical identifier")
    return value


def _optional_text(value: str | None, subject: str) -> str | None:
    if value is None:
        return None
    return _name(value, subject)


def _length_dict(value: Length) -> dict[str, float | str]:
    return {"value": value.value, "unit": value.unit.value}


def _length_from_dict(data: dict[str, Any]) -> Length:
    if not isinstance(data, dict) or set(data) != {"value", "unit"}:
        raise CamValidationError("Length payload is malformed")
    try:
        unit = LengthUnit(data["unit"])
    except (TypeError, ValueError) as error:
        raise CamUnitError("Length unit payload is invalid") from error
    return Length(data["value"], unit)


def _angle_dict(value: Angle) -> dict[str, float | str]:
    return {"value": value.value, "unit": value.unit.value}


def _angle_from_dict(data: dict[str, Any]) -> Angle:
    if not isinstance(data, dict) or set(data) != {"value", "unit"}:
        raise CamValidationError("Angle payload is malformed")
    try:
        unit = AngleUnit(data["unit"])
    except (TypeError, ValueError) as error:
        raise CamUnitError("Angle unit payload is invalid") from error
    return Angle(data["value"], unit)


def _feed_dict(value: FeedRate) -> dict[str, float | str]:
    return {"value": value.value, "unit": value.unit.value}


def _feed_from_dict(data: dict[str, Any]) -> FeedRate:
    if not isinstance(data, dict) or set(data) != {"value", "unit"}:
        raise CamValidationError("Feed payload is malformed")
    try:
        unit = FeedUnit(data["unit"])
    except (TypeError, ValueError) as error:
        raise CamUnitError("Feed unit payload is invalid") from error
    return FeedRate(data["value"], unit)


def _spindle_dict(value: SpindleSpeed) -> dict[str, float | str]:
    return {"value": value.value, "unit": value.unit.value}


def _spindle_from_dict(data: dict[str, Any]) -> SpindleSpeed:
    if not isinstance(data, dict) or set(data) != {"value", "unit"}:
        raise CamValidationError("Spindle-speed payload is malformed")
    try:
        unit = SpindleSpeedUnit(data["unit"])
    except (TypeError, ValueError) as error:
        raise CamUnitError("Spindle-speed unit payload is invalid") from error
    return SpindleSpeed(data["value"], unit)


class MachineKind(StrEnum):
    """Broad machine capability kinds."""

    MILL = "mill"
    TURN = "turn"
    MILL_TURN = "mill_turn"


class MachineAxisType(StrEnum):
    """Physical axis quantity category."""

    LINEAR = "linear"
    ROTARY = "rotary"


class KinematicSide(StrEnum):
    """Side of the machining relationship moved by a node."""

    FIXED = "fixed"
    TOOL = "tool"
    WORKPIECE = "workpiece"


class KinematicMount(StrEnum):
    """Semantic mount exposed by a kinematic node."""

    NONE = "none"
    TOOL = "tool"
    WORKPIECE = "workpiece"
    SPINDLE = "spindle"


class MachineCoolantCapability(StrEnum):
    """Controller-neutral machine coolant capability."""

    FLOOD = "flood"
    MIST = "mist"
    AIR = "air"
    THROUGH_SPINDLE = "through_spindle"


class OperationCapability(StrEnum):
    """High-level future operation capability contract."""

    MILLING = "milling"
    DRILLING = "drilling"
    TURNING = "turning"
    TAPPING = "tapping"
    THREADING = "threading"
    PROBING = "probing"


AxisPosition = Length | Angle


@dataclass(frozen=True, slots=True)
class MachineAxis:
    """One semantic machine axis with typed travel limits."""

    name: str
    semantic: str
    axis_type: MachineAxisType
    direction: Vector3
    minimum: AxisPosition
    maximum: AxisPosition
    home: AxisPosition
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _logical(self.name, "Axis name"))
        object.__setattr__(self, "semantic", _logical(self.semantic, "Axis semantic"))
        if not isinstance(self.axis_type, MachineAxisType):
            raise CamValidationError("Machine axis type is invalid")
        if not isinstance(self.direction, Vector3) or not math.isclose(
            self.direction.magnitude,
            1.0,
            rel_tol=0.0,
            abs_tol=WCS_ORTHONORMAL_TOLERANCE,
        ):
            raise CamValidationError("Machine axis direction must be a unit vector")
        quantities = (self.minimum, self.maximum, self.home)
        expected_type = Length if self.axis_type is MachineAxisType.LINEAR else Angle
        if any(not isinstance(item, expected_type) for item in quantities):
            raise CamUnitError("Axis limits do not match linear or rotary axis type")
        units = {item.unit for item in quantities}
        if len(units) != 1:
            raise CamUnitError("Axis limits must use one explicit unit")
        if self.axis_type is MachineAxisType.LINEAR and self.minimum.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Linear axis requires a known length unit")
        if self.minimum.value >= self.maximum.value:
            raise CamInvariantError("Axis minimum must be less than maximum")
        if not self.minimum.value <= self.home.value <= self.maximum.value:
            raise CamInvariantError("Axis home must lie within travel limits")

    @property
    def unit(self) -> LengthUnit | AngleUnit:
        """Return the typed axis unit."""
        return self.minimum.unit

    def to_dict(self) -> dict[str, Any]:
        """Serialize this machine axis."""
        encoder = _length_dict if self.axis_type is MachineAxisType.LINEAR else _angle_dict
        return {
            "format": _AXIS_FORMAT,
            "format_version": _VERSION,
            "name": self.name,
            "semantic": self.semantic,
            "axis_type": self.axis_type.value,
            "direction": self.direction.to_dict(),
            "minimum": encoder(self.minimum),
            "maximum": encoder(self.maximum),
            "home": encoder(self.home),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MachineAxis":
        """Deserialize atomically into one typed machine axis."""
        _strict_payload(
            data,
            format_name=_AXIS_FORMAT,
            version=_VERSION,
            fields={
                "name",
                "semantic",
                "axis_type",
                "direction",
                "minimum",
                "maximum",
                "home",
            },
        )
        try:
            axis_type = MachineAxisType(data["axis_type"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Machine axis type payload is invalid") from error
        decoder = _length_from_dict if axis_type is MachineAxisType.LINEAR else _angle_from_dict
        return cls(
            data["name"],
            data["semantic"],
            axis_type,
            Vector3.from_dict(data["direction"]),
            decoder(data["minimum"]),
            decoder(data["maximum"]),
            decoder(data["home"]),
        )


@dataclass(frozen=True, slots=True)
class SpindleCapability:
    """One spindle speed envelope without controller commands."""

    name: str
    minimum_speed: SpindleSpeed
    maximum_speed: SpindleSpeed
    indexable: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _logical(self.name, "Spindle name"))
        if not isinstance(self.minimum_speed, SpindleSpeed) or not isinstance(
            self.maximum_speed, SpindleSpeed
        ):
            raise CamValidationError("Spindle speed range is invalid")
        if self.minimum_speed.value >= self.maximum_speed.value:
            raise CamInvariantError("Spindle minimum must be less than maximum")
        if type(self.indexable) is not bool:
            raise CamValidationError("Spindle indexable must be boolean")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "minimum_speed": _spindle_dict(self.minimum_speed),
            "maximum_speed": _spindle_dict(self.maximum_speed),
            "indexable": self.indexable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SpindleCapability":
        if not isinstance(data, dict) or set(data) != {
            "name",
            "minimum_speed",
            "maximum_speed",
            "indexable",
        }:
            raise CamValidationError("Spindle capability payload is malformed")
        return cls(
            data["name"],
            _spindle_from_dict(data["minimum_speed"]),
            _spindle_from_dict(data["maximum_speed"]),
            data["indexable"],
        )


@dataclass(frozen=True, slots=True)
class WorkEnvelope:
    """Axis-aligned work-volume metadata."""

    size_x: Length
    size_y: Length
    size_z: Length

    def __post_init__(self) -> None:
        for item, subject in (
            (self.size_x, "Envelope X"),
            (self.size_y, "Envelope Y"),
            (self.size_z, "Envelope Z"),
        ):
            if not isinstance(item, Length) or item.value <= 0.0:
                raise CamValidationError(f"{subject} must be a positive length")
            if item.unit is LengthUnit.UNKNOWN:
                raise CamUnitError(f"{subject} requires a known unit")
        if len({self.size_x.unit, self.size_y.unit, self.size_z.unit}) != 1:
            raise CamUnitError("Work-envelope dimensions must use one unit")

    @property
    def unit(self) -> LengthUnit:
        return self.size_x.unit

    def to_dict(self) -> dict[str, Any]:
        return {
            "size_x": _length_dict(self.size_x),
            "size_y": _length_dict(self.size_y),
            "size_z": _length_dict(self.size_z),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkEnvelope":
        if not isinstance(data, dict) or set(data) != {"size_x", "size_y", "size_z"}:
            raise CamValidationError("Work-envelope payload is malformed")
        return cls(
            _length_from_dict(data["size_x"]),
            _length_from_dict(data["size_y"]),
            _length_from_dict(data["size_z"]),
        )


@dataclass(frozen=True, slots=True)
class MachineCapabilities:
    """Controller-neutral feature and performance contract."""

    milling: bool
    turning: bool
    live_tooling: bool
    probing: bool
    tapping: bool
    threading: bool
    spindle_count: int
    maximum_feed: FeedRate
    maximum_rapid: FeedRate
    tool_capacity: int | None
    coolant: tuple[MachineCoolantCapability, ...]
    operations: tuple[OperationCapability, ...]
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        flags = (
            self.milling,
            self.turning,
            self.live_tooling,
            self.probing,
            self.tapping,
            self.threading,
        )
        if any(type(flag) is not bool for flag in flags):
            raise CamValidationError("Machine capability flags must be boolean")
        if type(self.spindle_count) is not int or self.spindle_count <= 0:
            raise CamValidationError("Machine spindle count must be positive")
        if not isinstance(self.maximum_feed, FeedRate) or not isinstance(
            self.maximum_rapid, FeedRate
        ):
            raise CamValidationError("Machine feed capabilities are invalid")
        if self.maximum_feed.unit is not self.maximum_rapid.unit:
            raise CamUnitError("Machine feed and rapid units must match")
        if self.maximum_rapid.value < self.maximum_feed.value:
            raise CamInvariantError("Maximum rapid cannot be lower than maximum feed")
        if self.tool_capacity is not None and (
            type(self.tool_capacity) is not int or self.tool_capacity <= 0
        ):
            raise CamValidationError("Tool capacity must be positive when provided")
        if not isinstance(self.coolant, tuple) or any(
            not isinstance(item, MachineCoolantCapability) for item in self.coolant
        ):
            raise CamValidationError("Machine coolant capabilities are invalid")
        if not isinstance(self.operations, tuple) or any(
            not isinstance(item, OperationCapability) for item in self.operations
        ):
            raise CamValidationError("Machine operation capabilities are invalid")
        if len(set(self.coolant)) != len(self.coolant) or len(set(self.operations)) != len(
            self.operations
        ):
            raise CamInvariantError("Machine capabilities must be unique")
        object.__setattr__(self, "coolant", tuple(sorted(self.coolant, key=str)))
        object.__setattr__(self, "operations", tuple(sorted(self.operations, key=str)))
        required = {
            OperationCapability.MILLING: self.milling,
            OperationCapability.TURNING: self.turning,
            OperationCapability.PROBING: self.probing,
            OperationCapability.TAPPING: self.tapping,
            OperationCapability.THREADING: self.threading,
        }
        if any(
            capability in self.operations and not enabled
            for capability, enabled in required.items()
        ):
            raise CamInvariantError("Operation capability conflicts with feature flags")

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _CAPABILITY_FORMAT,
            "format_version": _VERSION,
            "milling": self.milling,
            "turning": self.turning,
            "live_tooling": self.live_tooling,
            "probing": self.probing,
            "tapping": self.tapping,
            "threading": self.threading,
            "spindle_count": self.spindle_count,
            "maximum_feed": _feed_dict(self.maximum_feed),
            "maximum_rapid": _feed_dict(self.maximum_rapid),
            "tool_capacity": self.tool_capacity,
            "coolant": [item.value for item in self.coolant],
            "operations": [item.value for item in self.operations],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MachineCapabilities":
        _strict_payload(
            data,
            format_name=_CAPABILITY_FORMAT,
            version=_VERSION,
            fields={
                "milling",
                "turning",
                "live_tooling",
                "probing",
                "tapping",
                "threading",
                "spindle_count",
                "maximum_feed",
                "maximum_rapid",
                "tool_capacity",
                "coolant",
                "operations",
            },
        )
        if not isinstance(data["coolant"], list) or not isinstance(
            data["operations"], list
        ):
            raise CamValidationError("Machine capability collections must be lists")
        try:
            coolant = tuple(MachineCoolantCapability(item) for item in data["coolant"])
            operations = tuple(OperationCapability(item) for item in data["operations"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Machine capability enum payload is invalid") from error
        return cls(
            data["milling"],
            data["turning"],
            data["live_tooling"],
            data["probing"],
            data["tapping"],
            data["threading"],
            data["spindle_count"],
            _feed_from_dict(data["maximum_feed"]),
            _feed_from_dict(data["maximum_rapid"]),
            data["tool_capacity"],
            coolant,
            operations,
        )


@dataclass(frozen=True, slots=True)
class KinematicNode:
    """One ordered node in a future-expandable kinematic hierarchy."""

    node_id: str
    parent_id: str | None
    axis_name: str | None
    side: KinematicSide
    mount: KinematicMount
    fixed_transform: AffineTransform

    def __post_init__(self) -> None:
        object.__setattr__(self, "node_id", _logical(self.node_id, "Kinematic node ID"))
        if self.parent_id is not None:
            object.__setattr__(
                self, "parent_id", _logical(self.parent_id, "Kinematic parent ID")
            )
        if self.axis_name is not None:
            object.__setattr__(
                self, "axis_name", _logical(self.axis_name, "Kinematic axis name")
            )
        if not isinstance(self.side, KinematicSide):
            raise CamValidationError("Kinematic side is invalid")
        if not isinstance(self.mount, KinematicMount):
            raise CamValidationError("Kinematic mount is invalid")
        if not isinstance(self.fixed_transform, AffineTransform):
            raise CamValidationError("Kinematic fixed transform is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "axis_name": self.axis_name,
            "side": self.side.value,
            "mount": self.mount.value,
            "fixed_transform": self.fixed_transform.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KinematicNode":
        if not isinstance(data, dict) or set(data) != {
            "node_id",
            "parent_id",
            "axis_name",
            "side",
            "mount",
            "fixed_transform",
        }:
            raise CamValidationError("Kinematic node payload is malformed")
        try:
            side = KinematicSide(data["side"])
            mount = KinematicMount(data["mount"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Kinematic node enum payload is invalid") from error
        return cls(
            data["node_id"],
            data["parent_id"],
            data["axis_name"],
            side,
            mount,
            AffineTransform.from_dict(data["fixed_transform"]),
        )


@dataclass(frozen=True, slots=True)
class KinematicChain:
    """Parent-before-child kinematic hierarchy without IK behavior."""

    nodes: tuple[KinematicNode, ...]
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.nodes, tuple) or not self.nodes:
            raise CamValidationError("Kinematic chain requires immutable nodes")
        if any(not isinstance(node, KinematicNode) for node in self.nodes):
            raise CamValidationError("Kinematic chain node is invalid")
        node_ids = tuple(node.node_id for node in self.nodes)
        if len(set(node_ids)) != len(node_ids):
            raise DuplicateCamIdError("Kinematic node IDs must be unique")
        roots = [node for node in self.nodes if node.parent_id is None]
        if len(roots) != 1:
            raise CamInvariantError("Kinematic chain requires exactly one root")
        seen: set[str] = set()
        axis_names: list[str] = []
        for node in self.nodes:
            if node.parent_id is not None and node.parent_id not in seen:
                raise CamInvariantError("Kinematic parent must precede its child")
            seen.add(node.node_id)
            if node.axis_name is not None:
                axis_names.append(node.axis_name)
        if len(set(axis_names)) != len(axis_names):
            raise CamInvariantError("A machine axis can appear only once in a chain")

    @property
    def axis_names(self) -> tuple[str, ...]:
        """Return axis references in deterministic chain order."""
        return tuple(node.axis_name for node in self.nodes if node.axis_name is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _KINEMATIC_FORMAT,
            "format_version": _VERSION,
            "nodes": [node.to_dict() for node in self.nodes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "KinematicChain":
        _strict_payload(
            data,
            format_name=_KINEMATIC_FORMAT,
            version=_VERSION,
            fields={"nodes"},
        )
        nodes = data["nodes"]
        if not isinstance(nodes, list):
            raise CamValidationError("Kinematic nodes payload must be a list")
        return cls(tuple(KinematicNode.from_dict(item) for item in nodes))


@dataclass(frozen=True, slots=True)
class MachineDefinition:
    """Immutable controller-neutral machine definition."""

    machine_id: MachineDefinitionId
    name: str
    kind: MachineKind
    unit: LengthUnit
    axes: tuple[MachineAxis, ...]
    spindles: tuple[SpindleCapability, ...]
    capabilities: MachineCapabilities
    kinematic_chain: KinematicChain
    work_envelope: WorkEnvelope
    revision: Revision = Revision(0)
    manufacturer: str | None = None
    model: str | None = None
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.machine_id, MachineDefinitionId):
            raise CamValidationError("Machine definition ID is invalid")
        object.__setattr__(self, "name", _name(self.name, "Machine name"))
        if not isinstance(self.kind, MachineKind):
            raise CamValidationError("Machine kind is invalid")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Machine definition requires a known length unit")
        if not isinstance(self.axes, tuple) or not self.axes:
            raise CamValidationError("Machine requires immutable axes")
        if any(not isinstance(axis, MachineAxis) for axis in self.axes):
            raise CamValidationError("Machine axis is invalid")
        axis_names = tuple(axis.name for axis in self.axes)
        if len({name.casefold() for name in axis_names}) != len(axis_names):
            raise DuplicateCamIdError("Machine axis names must be unique")
        semantics = tuple(axis.semantic for axis in self.axes)
        if len(set(semantics)) != len(semantics):
            raise CamInvariantError("Machine axis semantics must be unique")
        if any(
            axis.axis_type is MachineAxisType.LINEAR and axis.unit is not self.unit
            for axis in self.axes
        ):
            raise CamUnitError("Linear axes must use machine length unit")
        if not isinstance(self.spindles, tuple) or any(
            not isinstance(spindle, SpindleCapability) for spindle in self.spindles
        ):
            raise CamValidationError("Machine spindles must be an immutable tuple")
        spindle_names = tuple(spindle.name for spindle in self.spindles)
        if len(set(spindle_names)) != len(spindle_names):
            raise DuplicateCamIdError("Machine spindle names must be unique")
        if not isinstance(self.capabilities, MachineCapabilities):
            raise CamValidationError("Machine capabilities are invalid")
        if self.capabilities.spindle_count != len(self.spindles):
            raise CamInvariantError("Spindle count must match spindle definitions")
        expected_feed_unit = (
            FeedUnit.MM_PER_MINUTE
            if self.unit is LengthUnit.MM
            else FeedUnit.INCH_PER_MINUTE
        )
        if self.capabilities.maximum_feed.unit is not expected_feed_unit:
            raise CamUnitError("Machine feed unit must match machine length unit")
        flags = {
            MachineKind.MILL: (True, False),
            MachineKind.TURN: (False, True),
            MachineKind.MILL_TURN: (True, True),
        }[self.kind]
        if (self.capabilities.milling, self.capabilities.turning) != flags:
            raise CamInvariantError("Machine kind conflicts with milling/turning flags")
        if not isinstance(self.kinematic_chain, KinematicChain):
            raise CamValidationError("Machine kinematic chain is invalid")
        if set(self.kinematic_chain.axis_names) != set(axis_names):
            raise CamInvariantError("Kinematic chain must reference every machine axis once")
        if any(
            node.fixed_transform.translation_unit is not self.unit
            for node in self.kinematic_chain.nodes
        ):
            raise CamUnitError("Kinematic transforms must use machine length unit")
        if not isinstance(self.work_envelope, WorkEnvelope):
            raise CamValidationError("Machine work envelope is invalid")
        if self.work_envelope.unit is not self.unit:
            raise CamUnitError("Work envelope must use machine length unit")
        if not isinstance(self.revision, Revision):
            raise CamValidationError("Machine revision is invalid")
        object.__setattr__(
            self, "manufacturer", _optional_text(self.manufacturer, "Manufacturer")
        )
        object.__setattr__(self, "model", _optional_text(self.model, "Machine model"))

    @property
    def content_fingerprint(self) -> ContentFingerprint:
        """Return deterministic serialized machine content fingerprint."""
        return ContentFingerprint.from_payload(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        """Serialize the complete machine definition."""
        return {
            "format": _MACHINE_FORMAT,
            "format_version": _VERSION,
            "machine_id": str(self.machine_id),
            "name": self.name,
            "kind": self.kind.value,
            "unit": self.unit.value,
            "axes": [axis.to_dict() for axis in self.axes],
            "spindles": [spindle.to_dict() for spindle in self.spindles],
            "capabilities": self.capabilities.to_dict(),
            "kinematic_chain": self.kinematic_chain.to_dict(),
            "work_envelope": self.work_envelope.to_dict(),
            "revision": self.revision.to_dict(),
            "manufacturer": self.manufacturer,
            "model": self.model,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MachineDefinition":
        """Deserialize atomically into a complete machine definition."""
        _strict_payload(
            data,
            format_name=_MACHINE_FORMAT,
            version=_VERSION,
            fields={
                "machine_id",
                "name",
                "kind",
                "unit",
                "axes",
                "spindles",
                "capabilities",
                "kinematic_chain",
                "work_envelope",
                "revision",
                "manufacturer",
                "model",
            },
        )
        if not isinstance(data["axes"], list) or not isinstance(
            data["spindles"], list
        ):
            raise CamValidationError("Machine axes and spindles must be lists")
        try:
            kind = MachineKind(data["kind"])
            unit = LengthUnit(data["unit"])
        except (TypeError, ValueError) as error:
            raise CamValidationError("Machine enum payload is invalid") from error
        return cls(
            MachineDefinitionId.parse(data["machine_id"]),
            data["name"],
            kind,
            unit,
            tuple(MachineAxis.from_dict(item) for item in data["axes"]),
            tuple(SpindleCapability.from_dict(item) for item in data["spindles"]),
            MachineCapabilities.from_dict(data["capabilities"]),
            KinematicChain.from_dict(data["kinematic_chain"]),
            WorkEnvelope.from_dict(data["work_envelope"]),
            Revision.from_dict(data["revision"]),
            data["manufacturer"],
            data["model"],
        )


class MachineCompatibilityStatus(StrEnum):
    """Native-free compatibility assessment outcome."""

    COMPATIBLE = "compatible"
    MISSING_MACHINE = "missing_machine"
    REVISION_MISMATCH = "revision_mismatch"
    INCOMPATIBLE_UNIT = "incompatible_unit"
    CAPABILITY_MISMATCH = "capability_mismatch"


@dataclass(frozen=True, slots=True)
class MachineRequirement:
    """Expected machine snapshot and semantic capability requirements."""

    machine_id: MachineDefinitionId
    expected_revision: Revision
    expected_fingerprint: ContentFingerprint
    unit: LengthUnit
    required_capabilities: tuple[OperationCapability, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.machine_id, MachineDefinitionId):
            raise CamValidationError("Required machine ID is invalid")
        if not isinstance(self.expected_revision, Revision):
            raise CamValidationError("Required machine revision is invalid")
        if not isinstance(self.expected_fingerprint, ContentFingerprint):
            raise CamValidationError("Required machine fingerprint is invalid")
        if not isinstance(self.unit, LengthUnit) or self.unit is LengthUnit.UNKNOWN:
            raise CamUnitError("Machine requirement needs a known unit")
        if not isinstance(self.required_capabilities, tuple) or any(
            not isinstance(item, OperationCapability)
            for item in self.required_capabilities
        ):
            raise CamValidationError("Required machine capabilities are invalid")
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise CamInvariantError("Required machine capabilities must be unique")
        object.__setattr__(
            self,
            "required_capabilities",
            tuple(sorted(self.required_capabilities, key=str)),
        )


@dataclass(frozen=True, slots=True)
class MachineEvidence:
    """Current machine state used for compatibility assessment."""

    exists: bool
    revision: Revision | None = None
    fingerprint: ContentFingerprint | None = None
    unit: LengthUnit | None = None
    capabilities: tuple[OperationCapability, ...] = ()

    def __post_init__(self) -> None:
        if type(self.exists) is not bool:
            raise CamValidationError("Machine evidence exists must be boolean")
        if self.exists and any(
            value is None for value in (self.revision, self.fingerprint, self.unit)
        ):
            raise CamValidationError("Existing machine evidence must be complete")
        if not isinstance(self.capabilities, tuple) or any(
            not isinstance(item, OperationCapability) for item in self.capabilities
        ):
            raise CamValidationError("Machine evidence capabilities are invalid")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise CamInvariantError("Machine evidence capabilities must be unique")


def assess_machine_compatibility(
    requirement: MachineRequirement,
    evidence: MachineEvidence,
) -> MachineCompatibilityStatus:
    """Detect missing, stale, unit or capability mismatch without operations."""
    if not evidence.exists:
        return MachineCompatibilityStatus.MISSING_MACHINE
    if evidence.unit is not requirement.unit:
        return MachineCompatibilityStatus.INCOMPATIBLE_UNIT
    if (
        evidence.revision != requirement.expected_revision
        or evidence.fingerprint != requirement.expected_fingerprint
    ):
        return MachineCompatibilityStatus.REVISION_MISMATCH
    if not set(requirement.required_capabilities).issubset(evidence.capabilities):
        return MachineCompatibilityStatus.CAPABILITY_MISMATCH
    return MachineCompatibilityStatus.COMPATIBLE

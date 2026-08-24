"""UI-independent deterministic 3-axis material-state calculations."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import weakref
from array import array
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable

from hms_cadcam.cam.domain import BoxStock, ContentFingerprint, Setup, ToolDefinition
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.units import LengthUnit
from hms_cadcam.cam.simulation.model import SimulationSamplingPolicy
from hms_cadcam.cam.simulation.sampling import sample_toolpath, SimulationSamplingError
from hms_cadcam.cam.toolpath.events import MotionClass
from hms_cadcam.cam.toolpath.fingerprint import compute_material_removal_fingerprint
from hms_cadcam.cam.toolpath.model import ToolpathArtifact
from hms_cadcam.cam.domain.tooling import BallEndGeometry, BullNoseGeometry, CylindricalGeometry

MATERIAL_STATE_ENGINE_VERSION = "heightfield-3axis-v1"


def material_state_setup_fingerprint(setup: Setup) -> ContentFingerprint:
    """Fingerprint only Setup authority that can change material removal.

    The operation tree and Setup revision are lifecycle/runtime state.  Including
    them would make publishing a Rest dependency invalidate its own provenance.
    Stock is deliberately bound by its separate fingerprint.
    """
    if not isinstance(setup, Setup):
        raise TypeError("Material-state Setup is invalid")
    return ContentFingerprint.from_payload({
        "format": "HMS_CAM_MATERIAL_STATE_SETUP_AUTHORITY",
        "format_version": 1,
        "setup_id": str(setup.setup_id),
        "kind": setup.kind.value,
        "wcs": setup.wcs.to_dict(),
        "work_offset": setup.work_offset.to_dict(),
    })


class MaterialStateStatus(StrEnum):
    BUILDING = "BUILDING"
    COMPLETE = "COMPLETE"


class MaterialStateVerificationOrigin(StrEnum):
    """Internal origin of a heightfield seal; callers cannot supply it."""

    UNVERIFIED = "unverified"
    TRUSTED_CALCULATED = "trusted_calculated"
    TRUSTED_PERSISTED = "trusted_persisted"
    VERIFIED_LEGACY_CHECKSUM = "verified_legacy_checksum"


class MaterialStateQuality(StrEnum):
    """Persisted CAM precision label without importing optional Simulation."""

    FAST = "fast"
    STANDARD = "standard"
    DETAILED = "detailed"


class NoRestMaterial(RuntimeError):
    """Raised only when a valid state contains no meaningful rest material."""


@dataclass(frozen=True, slots=True)
class MaterialStatePrecisionPolicy:
    """Deterministic CAM precision, independent from display quality."""

    grid_target: int = 192
    tolerance: float = 1.0e-4
    residual_threshold: float = 2.0e-4
    quality: MaterialStateQuality = MaterialStateQuality.STANDARD

    def __post_init__(self) -> None:
        if type(self.grid_target) is not int or self.grid_target < 2:
            raise CamValidationError("Material-state grid target is invalid")
        if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0.0
               for value in (self.tolerance, self.residual_threshold)):
            raise CamValidationError("Material-state precision values are invalid")
        if not isinstance(self.quality, MaterialStateQuality):
            raise CamValidationError("Material-state quality is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {"grid_target": self.grid_target, "tolerance": self.tolerance,
                "residual_threshold": self.residual_threshold, "quality": self.quality.value}


MaterialStateFingerprint = ContentFingerprint


@dataclass(frozen=True, slots=True, weakref_slot=True)
class MaterialState:
    """Immutable software-estimated stock state with explicit provenance."""

    format_version: int
    fingerprint: MaterialStateFingerprint
    parent_fingerprint: MaterialStateFingerprint | None
    toolpath_fingerprint: ContentFingerprint
    stock_fingerprint: ContentFingerprint
    setup_fingerprint: ContentFingerprint
    precision: MaterialStatePrecisionPolicy
    engine_version: str
    width: int
    height: int
    cell_size_x: float
    cell_size_y: float
    top_heights: tuple[float, ...]
    initial_volume: float
    remaining_volume: float
    unit: LengthUnit
    status: MaterialStateStatus = MaterialStateStatus.COMPLETE
    content_integrity_fingerprint: ContentFingerprint = field(init=False)
    _trust_token: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.format_version != 1 or self.width < 2 or self.height < 2:
            raise CamValidationError("Material-state schema or dimensions are invalid")
        if len(self.top_heights) != self.width * self.height or any(
            not math.isfinite(value) or value < 0.0 for value in self.top_heights
        ):
            raise CamValidationError("Material-state heights are invalid")
        if self.cell_size_x <= 0.0 or self.cell_size_y <= 0.0:
            raise CamValidationError("Material-state cell size is invalid")
        if not 0.0 <= self.remaining_volume <= self.initial_volume + self.precision.tolerance:
            raise CamValidationError("Material-state volume is invalid")
        if not isinstance(self.status, MaterialStateStatus):
            raise CamValidationError("Material-state status is invalid")
        object.__setattr__(self, "content_integrity_fingerprint", self.computed_content_integrity_fingerprint())
        # Construction deliberately creates no trust record.  The opaque token
        # is only a process-local identity witness for the closed trust
        # boundary installed below; it is neither serialized nor semantic.
        object.__setattr__(self, "_trust_token", object())

    def computed_content_integrity_fingerprint(self) -> ContentFingerprint:
        """Hash exact grid and volume bytes independently of motion semantics."""
        return ContentFingerprint.from_payload({
            "format": "HMS_CAM_MATERIAL_STATE_CONTENT_INTEGRITY",
            "format_version": 1,
            "schema_version": self.format_version,
            "width": self.width,
            "height": self.height,
            "cell_size_x": self.cell_size_x,
            "cell_size_y": self.cell_size_y,
            "top_heights": list(self.top_heights),
            "initial_volume": self.initial_volume,
            "remaining_volume": self.remaining_volume,
            "unit": self.unit.value,
        })

    @property
    def verification_origin(self) -> MaterialStateVerificationOrigin:
        """Return the derived process-local verification origin."""
        return MaterialStateVerificationOrigin.UNVERIFIED

    @property
    def content_is_verified(self) -> bool:
        return False

    @property
    def meaningful_remaining_volume(self) -> float:
        return max(0.0, self.remaining_volume - self.precision.residual_threshold * self.cell_size_x * self.cell_size_y)

    @property
    def has_rest_material(self) -> bool:
        return self.meaningful_remaining_volume > self.precision.tolerance

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": "HMS_CAM_MATERIAL_STATE",
            "format_version": self.format_version,
            "fingerprint": self.fingerprint.to_dict(),
            "parent_fingerprint": self.parent_fingerprint.to_dict() if self.parent_fingerprint else None,
            "toolpath_fingerprint": self.toolpath_fingerprint.to_dict(),
            "stock_fingerprint": self.stock_fingerprint.to_dict(),
            "setup_fingerprint": self.setup_fingerprint.to_dict(),
            "precision": self.precision.to_dict(),
            "engine_version": self.engine_version,
            "width": self.width, "height": self.height,
            "cell_size_x": self.cell_size_x, "cell_size_y": self.cell_size_y,
            "top_heights": list(self.top_heights),
            "initial_volume": self.initial_volume, "remaining_volume": self.remaining_volume,
            "unit": self.unit.value, "status": self.status.value,
            "content_integrity_fingerprint": self.content_integrity_fingerprint.to_dict(),
        }


def _install_material_state_trust_boundary():
    """Create the non-exported capability boundary for trusted states.

    The returned factories are captured as defaults by the two public ingress
    paths and then removed from module globals.  Ordinary callers consequently
    have no function that can promote a ``MaterialState`` they already hold.
    """
    # Capture the real class before this installer returns.  The public module
    # binding is intentionally not a trust boundary: ordinary monkeypatching
    # must never cause either ingress to mint and promote a substitute object.
    state_class = MaterialState
    lock = threading.RLock()
    records: dict[int, tuple[weakref.ReferenceType[MaterialState], object,
                             MaterialStateVerificationOrigin, ContentFingerprint,
                             ContentFingerprint]] = {}

    def remove(identifier: int, reference: weakref.ReferenceType[MaterialState]) -> None:
        with lock:
            record = records.get(identifier)
            if record is not None and record[0] is reference:
                records.pop(identifier, None)

    def full_fingerprint(state: MaterialState) -> ContentFingerprint:
        return ContentFingerprint.from_payload(state.to_dict())

    def valid_record(state: MaterialState) -> tuple[MaterialStateVerificationOrigin, ContentFingerprint] | None:
        if not isinstance(state, state_class):
            return None
        with lock:
            record = records.get(id(state))
            if record is None:
                return None
            reference, token, origin, seal, full = record
            if (reference() is not state or token is not state._trust_token
                    or state.content_integrity_fingerprint != seal
                    or state.computed_content_integrity_fingerprint() != seal
                    or full_fingerprint(state) != full):
                return None
            return origin, seal

    def verification_origin(state: MaterialState) -> MaterialStateVerificationOrigin:
        record = valid_record(state)
        return MaterialStateVerificationOrigin.UNVERIFIED if record is None else record[0]

    def content_is_verified(state: MaterialState) -> bool:
        return valid_record(state) is not None

    def register_new(state: MaterialState, origin: MaterialStateVerificationOrigin,
                     expected_seal: ContentFingerprint) -> MaterialState:
        if (not isinstance(state, state_class)
                or origin not in {
                MaterialStateVerificationOrigin.TRUSTED_CALCULATED,
                MaterialStateVerificationOrigin.TRUSTED_PERSISTED,
                MaterialStateVerificationOrigin.VERIFIED_LEGACY_CHECKSUM,
            }
                or state.computed_content_integrity_fingerprint() != expected_seal
                or state.content_integrity_fingerprint != expected_seal):
            raise CamValidationError("Material-state heightfield content integrity is invalid")
        identifier = id(state)
        reference = weakref.ref(state, lambda reference: remove(identifier, reference))
        with lock:
            records[identifier] = (reference, state._trust_token, origin, expected_seal,
                                   full_fingerprint(state))
        return state

    def calculated_factory(*args: Any) -> MaterialState:
        state = state_class(*args)
        return register_new(state, MaterialStateVerificationOrigin.TRUSTED_CALCULATED,
                            state.content_integrity_fingerprint)

    def persisted_document_factory(payload: bytes, requested_fingerprint: ContentFingerprint) -> MaterialState:
        if not isinstance(payload, bytes) or not isinstance(requested_fingerprint, ContentFingerprint):
            raise CamValidationError("Material-state persisted document inputs are invalid")
        try:
            document = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CamValidationError("Material-state persisted document is invalid") from error
        if not isinstance(document, dict):
            raise CamValidationError("Material-state persisted document is invalid")
        checksum = document.get("checksum_sha256")
        if not isinstance(checksum, str):
            raise CamValidationError("Material-state checksum is invalid")
        unsigned_document = dict(document)
        unsigned_document["checksum_sha256"] = ""
        unsigned = json.dumps(unsigned_document, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")
        if checksum != hashlib.sha256(unsigned).hexdigest():
            raise CamValidationError("Material-state checksum mismatch")
        if (document.get("format") != "HMS_CAM_MATERIAL_STATE"
                or document.get("format_version") != 1
                or document.get("status") != MaterialStateStatus.COMPLETE.value):
            raise CamValidationError("Material-state schema/status incompatible")
        seal_present = "content_integrity_fingerprint" in document
        seal_document = document.get("content_integrity_fingerprint")
        if seal_present and not isinstance(seal_document, dict):
            raise CamValidationError("Material-state content seal malformed")
        try:
            precision_document = document["precision"]
            if not isinstance(precision_document, dict):
                raise TypeError("precision")
            precision = MaterialStatePrecisionPolicy(
                precision_document["grid_target"], precision_document["tolerance"],
                precision_document["residual_threshold"],
                MaterialStateQuality(precision_document.get("quality", "standard")),
            )
            state = state_class(
                1, ContentFingerprint.from_dict(document["fingerprint"]),
                ContentFingerprint.from_dict(document["parent_fingerprint"])
                if document["parent_fingerprint"] else None,
                ContentFingerprint.from_dict(document["toolpath_fingerprint"]),
                ContentFingerprint.from_dict(document["stock_fingerprint"]),
                ContentFingerprint.from_dict(document["setup_fingerprint"]), precision,
                document["engine_version"], document["width"], document["height"],
                document["cell_size_x"], document["cell_size_y"], tuple(document["top_heights"]),
                document["initial_volume"], document["remaining_volume"], LengthUnit(document["unit"]),
            )
        except (KeyError, TypeError, ValueError, CamValidationError) as error:
            raise CamValidationError("Material-state persisted content is invalid") from error
        if state.fingerprint != requested_fingerprint:
            raise CamValidationError("Material-state fingerprint mismatch")
        if not seal_present:
            return register_new(state, MaterialStateVerificationOrigin.VERIFIED_LEGACY_CHECKSUM,
                                state.content_integrity_fingerprint)
        try:
            seal = ContentFingerprint.from_dict(seal_document)
        except (TypeError, ValueError, KeyError, CamValidationError) as error:
            raise CamValidationError("Material-state content seal malformed") from error
        return register_new(state, MaterialStateVerificationOrigin.TRUSTED_PERSISTED, seal)

    state_class.verification_origin = property(verification_origin)
    state_class.content_is_verified = property(content_is_verified)
    return calculated_factory, persisted_document_factory


_calculated_material_state_factory, _persisted_material_state_factory = _install_material_state_trust_boundary()
del _install_material_state_trust_boundary


@dataclass(frozen=True, slots=True)
class MaterialRemovalResult:
    state: MaterialState
    removed_volume: float
    no_rest_material: bool


@dataclass(frozen=True, slots=True)
class CutterEnvelope:
    """Shared analytic cutter envelope for the deterministic heightfield.

    This is intentionally a small, UI-independent geometry contract.  It is
    the same envelope used by :func:`calculate_material_state`, so consumers
    can never use a subtly different BALL/BULL contact law while reasoning
    about a persisted MaterialState.
    """
    radius: float
    corner_radius: float
    ball: bool

    def __post_init__(self) -> None:
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)
               for value in (self.radius, self.corner_radius)):
            raise CamValidationError("Cutter-envelope geometry is invalid")
        if self.radius <= 0.0 or self.corner_radius < 0.0 or self.corner_radius > self.radius:
            raise CamValidationError("Cutter-envelope dimensions are invalid")
        if type(self.ball) is not bool:
            raise CamValidationError("Cutter-envelope ball flag is invalid")
        if self.ball and self.corner_radius != self.radius:
            raise CamValidationError("Ball cutter-envelope radius is inconsistent")

    @classmethod
    def from_tool(cls, tool: ToolDefinition) -> "CutterEnvelope":
        geometry = tool.cutting_geometry
        if isinstance(geometry, CylindricalGeometry):
            return cls(geometry.diameter.value / 2.0, 0.0, False)
        if isinstance(geometry, BallEndGeometry):
            return cls(geometry.diameter.value / 2.0, geometry.diameter.value / 2.0, True)
        if isinstance(geometry, BullNoseGeometry):
            return cls(geometry.diameter.value / 2.0, geometry.corner_radius.value, False)
        raise CamValidationError(f"Unsupported cutter for material state: {geometry.kind.value}")

    def surface_offset(self, radial_distance: float) -> float:
        """Return the cutter surface height above its tip at *radial_distance*."""
        if (not isinstance(radial_distance, (int, float)) or isinstance(radial_distance, bool)
                or not math.isfinite(radial_distance) or radial_distance < 0.0):
            raise CamValidationError("Cutter-envelope radial distance is invalid")
        if radial_distance > self.radius:
            return math.inf
        if self.ball:
            return self.radius - math.sqrt(max(0.0, self.radius**2 - radial_distance**2))
        if self.corner_radius > 0.0 and radial_distance > self.radius - self.corner_radius:
            local = radial_distance - (self.radius - self.corner_radius)
            return self.corner_radius - math.sqrt(max(0.0, self.corner_radius**2 - local**2))
        return 0.0

    @property
    def fingerprint(self) -> ContentFingerprint:
        """Return a stable identity for this analytic contact envelope."""
        return ContentFingerprint.from_payload({
            "format": "HMS_CAM_CUTTER_ENVELOPE", "format_version": 1,
            "radius": self.radius, "corner_radius": self.corner_radius, "ball": self.ball,
        })

    def maximum_removable_radius(
        self,
        *,
        target_tip_z: float,
        current_height: float,
        threshold: float = 0.0,
    ) -> float | None:
        """Return the largest contact radius that can lower one cell safely.

        ``None`` means that even the cutter tip cannot remove meaningful
        material.  The threshold is deliberately applied to material height,
        matching the residual predicate used by the rest-contour planner.
        """
        values = (target_tip_z, current_height, threshold)
        if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) for value in values):
            raise CamValidationError("Cutter-envelope inverse inputs are invalid")
        if threshold < 0.0:
            raise CamValidationError("Cutter-envelope threshold is invalid")
        allowance = current_height - threshold - target_tip_z
        # Residual planning uses the strict predicate
        # ``current_height > tip_z + surface_offset + threshold``.  Equality
        # is already machined to the requested threshold, including for an END
        # mill's flat bottom.
        if allowance <= 0.0:
            return None
        if not self.ball and self.corner_radius <= 0.0:
            return self.radius
        if self.ball:
            if allowance >= self.radius:
                return self.radius
            return math.sqrt(max(0.0, 2.0 * self.radius * allowance - allowance ** 2))
        flat_radius = self.radius - self.corner_radius
        if allowance >= self.corner_radius:
            return self.radius
        return flat_radius + math.sqrt(
            max(0.0, 2.0 * self.corner_radius * allowance - allowance ** 2)
        )


def _remove_at(values: array, width: int, height: int, dx: float, dy: float,
               x: float, y: float, tip_z: float, profile: CutterEnvelope) -> None:
    min_x, max_x = max(0, math.floor((x - profile.radius) / dx)), min(width - 1, math.floor((x + profile.radius) / dx))
    min_y, max_y = max(0, math.floor((y - profile.radius) / dy)), min(height - 1, math.floor((y + profile.radius) / dy))
    for row in range(min_y, max_y + 1):
        center_y = (row + 0.5) * dy
        for column in range(min_x, max_x + 1):
            radius = math.hypot((column + 0.5) * dx - x, center_y - y)
            offset = profile.surface_offset(radius)
            if not math.isinf(offset):
                values[row * width + column] = min(values[row * width + column], max(0.0, tip_z + offset))


def calculate_material_state(*, stock: BoxStock, artifact: ToolpathArtifact, tool: ToolDefinition,
                             setup_fingerprint: ContentFingerprint,
                             parent: MaterialState | None = None,
                             precision: MaterialStatePrecisionPolicy | None = None,
                             cancellation: Callable[[], bool] | None = None,
                             _factory: Callable[..., MaterialState] = _calculated_material_state_factory) -> MaterialRemovalResult:
    """Calculate one complete state from an actual semantic toolpath."""
    policy = precision or MaterialStatePrecisionPolicy()
    if not isinstance(stock, BoxStock):
        raise CamValidationError("R260 Tranche-1 supports Box Stock only")
    if artifact.unit is not stock.size_x.unit or tool.unit is not artifact.unit:
        raise CamValidationError("Material-state units differ")
    profile = CutterEnvelope.from_tool(tool)
    aspect = stock.size_x.value / stock.size_y.value
    width = max(2, round(policy.grid_target * math.sqrt(aspect)))
    height = max(2, round(policy.grid_target / math.sqrt(aspect)))
    dx, dy = stock.size_x.value / width, stock.size_y.value / height
    initial_volume = stock.size_x.value * stock.size_y.value * stock.size_z.value
    if parent is None:
        values = array("d", [stock.size_z.value]) * (width * height)
        parent_fp = None
    else:
        if (not parent.content_is_verified
                or (parent.width, parent.height, parent.unit, parent.cell_size_x, parent.cell_size_y)
                != (width, height, artifact.unit, dx, dy)):
            raise CamValidationError("Parent material state grid is incompatible")
        values = array("d", parent.top_heights)
        initial_volume = parent.initial_volume
        parent_fp = parent.fingerprint
    try:
        sampling = sample_toolpath(artifact=artifact, wcs=stock.frame,
            policy=SimulationSamplingPolicy(max_linear_step=max(1.0e-4, min(dx, dy) * 0.75),
                chord_tolerance=max(1.0e-5, min(dx, dy) / 6.0), maximum_samples=1_000_000,
                memory_budget_bytes=512 * 1024 * 1024), cancellation=cancellation)
    except SimulationSamplingError as error:
        raise CamValidationError(str(error)) from error
    cutting = [index for segment in sampling.segments if segment.motion_class is MotionClass.CUTTING for index in segment.sample_indices]
    for index in dict.fromkeys(cutting):
        if cancellation is not None and cancellation():
            raise CamValidationError("Material-state calculation cancelled")
        point = sampling.samples[index].setup_pose.position
        _remove_at(values, width, height, dx, dy, point.x, point.y, point.z, profile)
    remaining = min(initial_volume, max(0.0, sum(values) * dx * dy))
    stock_fp = ContentFingerprint.from_payload(stock.to_dict())
    toolpath_fp = compute_material_removal_fingerprint(artifact)
    fingerprint = ContentFingerprint.from_payload({
        "format": "HMS_CAM_MATERIAL_STATE_FINGERPRINT", "format_version": 1,
        "parent": parent_fp.to_dict() if parent_fp else None,
        "toolpath": toolpath_fp.to_dict(), "tool": tool.to_dict(),
        "stock": stock_fp.to_dict(), "setup": setup_fingerprint.to_dict(),
        "precision": policy.to_dict(), "engine_version": MATERIAL_STATE_ENGINE_VERSION,
    })
    state = _factory(1, fingerprint, parent_fp, toolpath_fp, stock_fp, setup_fingerprint,
        policy, MATERIAL_STATE_ENGINE_VERSION, width, height, dx, dy, tuple(values),
        initial_volume, remaining, artifact.unit)
    return MaterialRemovalResult(state, max(0.0, initial_volume - remaining), not state.has_rest_material)


def material_state_from_persisted_bytes(
    payload: bytes,
    requested_fingerprint: ContentFingerprint,
    _factory: Callable[[bytes, ContentFingerprint], MaterialState] = _persisted_material_state_factory,
) -> MaterialState:
    """Decode one complete persisted state through the sealed document boundary."""
    return _factory(payload, requested_fingerprint)


# Factories are intentionally captured only in the two ingress callables above.
# They are not a module-level promotion capability for arbitrary state objects.
del _calculated_material_state_factory, _persisted_material_state_factory


def _close_material_state_ingress(calculator, decoder):
    """Hide factory-bearing implementation defaults behind public call shapes."""
    def calculate(*, stock: BoxStock, artifact: ToolpathArtifact, tool: ToolDefinition,
                  setup_fingerprint: ContentFingerprint, parent: MaterialState | None = None,
                  precision: MaterialStatePrecisionPolicy | None = None,
                  cancellation: Callable[[], bool] | None = None) -> MaterialRemovalResult:
        return calculator(stock=stock, artifact=artifact, tool=tool,
                          setup_fingerprint=setup_fingerprint, parent=parent,
                          precision=precision, cancellation=cancellation)

    def decode(payload: bytes, requested_fingerprint: ContentFingerprint) -> MaterialState:
        return decoder(payload, requested_fingerprint)

    return calculate, decode


calculate_material_state, material_state_from_persisted_bytes = _close_material_state_ingress(
    calculate_material_state, material_state_from_persisted_bytes,
)
del _close_material_state_ingress

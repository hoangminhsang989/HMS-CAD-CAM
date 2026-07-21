"""CAM 3D calculation context, state and safe-motion validation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar
from uuid import UUID

from hms_cadcam.cam.cam3d.mesh import Cam3DCalculationMesh
from hms_cadcam.cam.cam3d.models import (
    Cam3DDiagnostic,
    Cam3DDiagnosticCode,
    Cam3DDiagnosticSeverity,
    Cam3DGeometrySnapshot,
    Cam3DSafeMotionPolicy,
    Cam3DStockAllowance,
    Cam3DTolerancePolicy,
    MachiningZone3D,
    wcs_fingerprint,
)
from hms_cadcam.cam.domain.errors import CamValidationError, UnsupportedCamSchemaError
from hms_cadcam.cam.domain.ids import (
    Cam3DCalculationContextId,
    CamJobId,
    SetupId,
)
from hms_cadcam.cam.domain.revision import ContentFingerprint, DependencyFingerprint

_FORMAT = "HMS_CAM3D_CALCULATION_CONTEXT"
_VERSION = 1


class Cam3DCalculationState(StrEnum):
    """Exhaustive runtime state for one latest-wins CAM 3D request."""

    MISSING = "missing"
    VALIDATING = "validating"
    TESSELLATING = "tessellating"
    VALIDATING_MESH = "validating_mesh"
    CURRENT = "current"
    STALE = "stale"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Cam3DCalculationContext:
    """Complete deterministic input set for a future CAM 3D algorithm."""

    context_id: Cam3DCalculationContextId
    request_token: UUID
    project_id: UUID
    project_generation: int
    job_id: CamJobId
    setup_id: SetupId
    geometry_snapshot: Cam3DGeometrySnapshot
    machining_zone: MachiningZone3D
    calculation_mesh: Cam3DCalculationMesh
    tool_assembly_fingerprint: ContentFingerprint | DependencyFingerprint
    tool_definition_fingerprint: ContentFingerprint
    tolerance_policy: Cam3DTolerancePolicy
    stock_allowance: Cam3DStockAllowance
    safe_motion_policy: Cam3DSafeMotionPolicy
    algorithm: str
    algorithm_version: int
    SERIALIZATION_VERSION: ClassVar[int] = _VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.context_id, Cam3DCalculationContextId):
            raise CamValidationError("CAM 3D calculation context ID is invalid")
        if not isinstance(self.request_token, UUID) or self.request_token.int == 0:
            raise CamValidationError("CAM 3D calculation request token is invalid")
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise CamValidationError("CAM 3D calculation project ID is invalid")
        if type(self.project_generation) is not int or self.project_generation < 0:
            raise CamValidationError("CAM 3D project generation is invalid")
        if not isinstance(self.job_id, CamJobId) or not isinstance(self.setup_id, SetupId):
            raise CamValidationError("CAM 3D calculation Job/Setup is invalid")
        if not isinstance(self.geometry_snapshot, Cam3DGeometrySnapshot) or not isinstance(
            self.machining_zone, MachiningZone3D
        ):
            raise CamValidationError("CAM 3D calculation geometry input is invalid")
        if not isinstance(self.calculation_mesh, Cam3DCalculationMesh):
            raise CamValidationError("CAM 3D calculation mesh is invalid")
        if self.geometry_snapshot.project_id != self.project_id or self.machining_zone.project_id != self.project_id:
            raise CamValidationError("CAM 3D context combines different projects")
        if self.geometry_snapshot.project_generation != self.project_generation:
            raise CamValidationError("CAM 3D project generation changed")
        if self.machining_zone.job_id != self.job_id or self.machining_zone.setup_id != self.setup_id:
            raise CamValidationError("CAM 3D context combines different Job/Setup inputs")
        if self.geometry_snapshot.zone.fingerprint != self.machining_zone.fingerprint:
            raise CamValidationError("CAM 3D machining zone changed after capture")
        if self.calculation_mesh.source_geometry_fingerprint != self.geometry_snapshot.geometry_fingerprint:
            raise CamValidationError("CAM 3D mesh source geometry is stale")
        if not isinstance(
            self.tool_assembly_fingerprint, (ContentFingerprint, DependencyFingerprint)
        ) or not isinstance(self.tool_definition_fingerprint, ContentFingerprint):
            raise CamValidationError("CAM 3D tool fingerprints are invalid")
        if self.tolerance_policy != self.machining_zone.tolerance or self.stock_allowance != self.machining_zone.allowance:
            raise CamValidationError("CAM 3D calculation policies changed after capture")
        if not isinstance(self.safe_motion_policy, Cam3DSafeMotionPolicy):
            raise CamValidationError("CAM 3D safe-motion policy is invalid")
        diagnostics = validate_safe_motion(
            self.safe_motion_policy, self.machining_zone, self.calculation_mesh
        )
        if diagnostics:
            raise CamValidationError(diagnostics[0].message)
        if not isinstance(self.algorithm, str) or not self.algorithm.strip():
            raise CamValidationError("CAM 3D algorithm identity is invalid")
        object.__setattr__(self, "algorithm", self.algorithm.strip())
        if type(self.algorithm_version) is not int or self.algorithm_version <= 0:
            raise CamValidationError("CAM 3D algorithm version is invalid")

    @property
    def fingerprint(self) -> DependencyFingerprint:
        """Fingerprint deterministic inputs, excluding request/runtime identities."""
        return DependencyFingerprint.from_payload(self.identity_payload())

    def identity_payload(self) -> dict[str, Any]:
        return {
            "project_id": str(self.project_id),
            "project_generation": self.project_generation,
            "job_id": str(self.job_id),
            "setup_id": str(self.setup_id),
            "geometry_snapshot_fingerprint": self.geometry_snapshot.fingerprint.to_dict(),
            "machining_zone_fingerprint": self.machining_zone.fingerprint.to_dict(),
            "calculation_mesh_fingerprint": self.calculation_mesh.mesh_fingerprint.to_dict(),
            "tool_assembly_fingerprint": self.tool_assembly_fingerprint.to_dict(),
            "tool_definition_fingerprint": self.tool_definition_fingerprint.to_dict(),
            "tolerance_policy": self.tolerance_policy.to_dict(),
            "stock_allowance": self.stock_allowance.to_dict(),
            "safe_motion_policy": self.safe_motion_policy.to_dict(),
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": _FORMAT,
            "format_version": _VERSION,
            "context_id": str(self.context_id),
            "request_token": str(self.request_token),
            "geometry_snapshot": self.geometry_snapshot.to_dict(),
            "machining_zone": self.machining_zone.to_dict(),
            "calculation_mesh": self.calculation_mesh.to_dict(),
            **self.identity_payload(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Cam3DCalculationContext":
        fields = {
            "format",
            "format_version",
            "context_id",
            "request_token",
            "project_id",
            "project_generation",
            "job_id",
            "setup_id",
            "geometry_snapshot",
            "geometry_snapshot_fingerprint",
            "machining_zone",
            "machining_zone_fingerprint",
            "calculation_mesh",
            "calculation_mesh_fingerprint",
            "tool_assembly_fingerprint",
            "tool_definition_fingerprint",
            "tolerance_policy",
            "stock_allowance",
            "safe_motion_policy",
            "algorithm",
            "algorithm_version",
        }
        if not isinstance(data, dict) or set(data) != fields:
            raise CamValidationError("CAM 3D calculation context payload is malformed")
        if data["format"] != _FORMAT:
            raise UnsupportedCamSchemaError("Unsupported CAM 3D calculation context format")
        if type(data["format_version"]) is not int or data["format_version"] != _VERSION:
            raise UnsupportedCamSchemaError("Unsupported CAM 3D calculation context version")
        assembly_payload = data["tool_assembly_fingerprint"]
        if not isinstance(assembly_payload, dict):
            raise CamValidationError("CAM 3D assembly fingerprint payload is invalid")
        assembly = (
            DependencyFingerprint.from_dict(assembly_payload)
            if assembly_payload.get("kind") == DependencyFingerprint.KIND
            else ContentFingerprint.from_dict(assembly_payload)
        )
        candidate = cls(
            Cam3DCalculationContextId.parse(data["context_id"]),
            UUID(data["request_token"]),
            UUID(data["project_id"]),
            data["project_generation"],
            CamJobId.parse(data["job_id"]),
            SetupId.parse(data["setup_id"]),
            Cam3DGeometrySnapshot.from_dict(data["geometry_snapshot"]),
            MachiningZone3D.from_dict(data["machining_zone"]),
            Cam3DCalculationMesh.from_dict(data["calculation_mesh"]),
            assembly,
            ContentFingerprint.from_dict(data["tool_definition_fingerprint"]),
            Cam3DTolerancePolicy.from_dict(data["tolerance_policy"]),
            Cam3DStockAllowance.from_dict(data["stock_allowance"]),
            Cam3DSafeMotionPolicy.from_dict(data["safe_motion_policy"]),
            data["algorithm"],
            data["algorithm_version"],
        )
        checks = (
            (candidate.geometry_snapshot.fingerprint, data["geometry_snapshot_fingerprint"]),
            (candidate.machining_zone.fingerprint, data["machining_zone_fingerprint"]),
            (candidate.calculation_mesh.mesh_fingerprint, data["calculation_mesh_fingerprint"]),
        )
        if any(value.to_dict() != payload for value, payload in checks):
            raise CamValidationError("CAM 3D context embedded fingerprint mismatch")
        return candidate


def validate_safe_motion(
    policy: Cam3DSafeMotionPolicy,
    zone: MachiningZone3D,
    mesh: Cam3DCalculationMesh,
) -> tuple[Cam3DDiagnostic, ...]:
    """Return fail-closed diagnostics without inventing machine coordinates."""
    if not isinstance(policy, Cam3DSafeMotionPolicy) or not isinstance(
        zone, MachiningZone3D
    ) or not isinstance(mesh, Cam3DCalculationMesh):
        raise CamValidationError("Safe-motion validation input is invalid")
    diagnostics: list[Cam3DDiagnostic] = []

    def add(message: str, evidence: tuple[tuple[str, str], ...] = ()) -> None:
        diagnostics.append(
            Cam3DDiagnostic(
                Cam3DDiagnosticCode.SAFE_MOTION_INVALID,
                Cam3DDiagnosticSeverity.ERROR,
                message,
                setup_id=zone.setup_id,
                evidence=evidence,
            )
        )

    if policy.setup_id != zone.setup_id or policy.setup_revision != zone.setup_revision:
        add("Safe-motion policy belongs to a stale or different Setup")
    if policy.wcs_fingerprint != wcs_fingerprint(zone.wcs):
        add("Safe-motion policy uses a stale Setup WCS")
    axes = (policy.tool_axis.x, policy.tool_axis.y, policy.tool_axis.z)
    expected = (zone.tool_axis.x, zone.tool_axis.y, zone.tool_axis.z)
    if any(not math.isclose(a, b, rel_tol=0.0, abs_tol=1.0e-9) for a, b in zip(axes, expected, strict=True)):
        add("Safe-motion tool axis does not match the fixed Setup Z axis")
    if policy.clearance_z is None or policy.retract_z is None:
        add("Clearance Z and retract Z must be explicit")
        return tuple(diagnostics)
    required_top = mesh.bounding_box.z_max + max(
        zone.allowance.part_normal, zone.allowance.check_surface_clearance
    )
    if policy.clearance_z <= required_top:
        add(
            "Clearance Z is not above the selected geometry",
            (("required_above_z", f"{required_top:.12g}"),),
        )
    if policy.retract_z <= required_top:
        add(
            "Retract Z is not above the selected geometry",
            (("required_above_z", f"{required_top:.12g}"),),
        )
    if policy.clearance_z < policy.retract_z:
        add("Clearance Z must not be below retract Z")
    if policy.approach_distance <= 0.0:
        add("Approach distance must be positive")
    if policy.link_clearance < 0.0:
        add("Link clearance must not be negative")
    return tuple(diagnostics)

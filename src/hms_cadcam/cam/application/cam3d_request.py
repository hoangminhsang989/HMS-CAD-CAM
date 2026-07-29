"""Pure Stage 9A.8 WP3-A calculation submission contracts.

This module deliberately creates no worker and performs no tessellation, Qt,
OCP, filesystem, persistence, database, or current-time operation.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import ClassVar, Self
from uuid import UUID, uuid4

from hms_cadcam.cad.models import CadDocumentId
from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorApplicationService,
    Cam3DEditorReadiness,
    Cam3DEditorState,
    Cam3DProjectContext,
)
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectionRole,
    Cam3DSelectionState,
    Cam3DSelectionStatus,
    Cam3DSelectionValidity,
)
from hms_cadcam.cam.cam3d.models import CamSurfaceReference
from hms_cadcam.cam.domain.ids import SetupId, ToolAssemblyId, ToolProgramProfileId
from hms_cadcam.cam.domain.revision import DependencyFingerprint, Revision
from hms_cadcam.cam.domain.spatial import WcsFrame
from hms_cadcam.cam.domain.units import LengthUnit


REQUEST_CONTRACT_VERSION = 1
CACHE_KEY_CONTRACT_VERSION = 1
CALCULATION_POLICY_VERSION = 1
TESSELLATION_POLICY_VERSION = 1
PREVIEW_POLICY_VERSION = 1


@dataclass(frozen=True, slots=True, order=True)
class Cam3DCalculationJobId:
    """Opaque identity of one explicitly submitted calculation attempt."""

    value: UUID
    PREFIX: ClassVar[str] = "cam3d_calculation_job"

    def __post_init__(self) -> None:
        if not isinstance(self.value, UUID) or self.value.int == 0:
            raise ValueError("CAM 3D calculation job identity is invalid")

    @classmethod
    def new(cls) -> Self:
        """Create a job identity at the application submission boundary."""

        return cls(uuid4())

    def __str__(self) -> str:
        return f"{self.PREFIX}:{self.value}"


@dataclass(frozen=True, slots=True)
class Cam3DCalculationOwnershipKey:
    """Stable owner of one independent latest-wins calculation session."""

    project_id: UUID
    document_id: CadDocumentId
    source_id: UUID
    setup_id: SetupId

    def __post_init__(self) -> None:
        if not isinstance(self.project_id, UUID) or self.project_id.int == 0:
            raise ValueError("CAM 3D ownership project identity is invalid")
        if not isinstance(self.document_id, CadDocumentId):
            raise TypeError("CAM 3D ownership document identity is invalid")
        if not isinstance(self.source_id, UUID) or self.source_id.int == 0:
            raise ValueError("CAM 3D ownership source identity is invalid")
        if not isinstance(self.setup_id, SetupId):
            raise TypeError("CAM 3D ownership Setup identity is invalid")

    def canonical_payload(self) -> dict[str, object]:
        """Return a fresh canonical JSON-compatible identity payload."""

        return {
            "project_id": str(self.project_id),
            "document_id": str(self.document_id),
            "source_id": str(self.source_id),
            "setup_id": str(self.setup_id),
        }


@dataclass(frozen=True, slots=True)
class Cam3DActiveSetupContext:
    """Immutable active Setup facts supplied by the CAM application layer."""

    ownership: Cam3DCalculationOwnershipKey
    project_generation: int
    setup_revision: Revision
    wcs: WcsFrame
    active: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, Cam3DCalculationOwnershipKey):
            raise TypeError("CAM 3D active Setup ownership is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise ValueError("CAM 3D active Setup generation must be positive")
        if not isinstance(self.setup_revision, Revision):
            raise TypeError("CAM 3D active Setup revision is invalid")
        if not isinstance(self.wcs, WcsFrame) or self.wcs.origin.unit is not LengthUnit.MM:
            raise ValueError("CAM 3D active Setup requires an MM WCS")
        if type(self.active) is not bool:
            raise TypeError("CAM 3D active Setup state must be bool")

    def canonical_payload(self) -> dict[str, object]:
        """Return stable Setup semantics without display state."""

        return {
            "ownership": self.ownership.canonical_payload(),
            "project_generation": self.project_generation,
            "setup_revision": self.setup_revision.to_dict(),
            "wcs": self.wcs.to_dict(),
        }


def _surface_payload(surface: CamSurfaceReference) -> dict[str, object]:
    geometry = surface.geometry
    return {
        "project_id": str(surface.project_id),
        "scheme": geometry.scheme,
        "scheme_version": geometry.scheme_version,
        "source_id": str(geometry.source_id),
        "reference_kind": geometry.kind.value,
        "geometry_kind": geometry.geometry_kind.value,
        "occurrence_path": geometry.occurrence_path,
        "subshape_selector": geometry.subshape_selector,
        "expected_geometry_fingerprint": geometry.expected_geometry_fingerprint.to_dict(),
        "expected_source_revision": geometry.expected_source_revision.to_dict(),
        "orientation": surface.orientation.value,
        "role": surface.role.value,
        "body_identity": surface.body_identity,
        "face_identity": surface.face_identity,
    }


def _canonical_surfaces(
    surfaces: tuple[CamSurfaceReference, ...],
) -> tuple[CamSurfaceReference, ...]:
    return tuple(
        sorted(
            surfaces,
            key=lambda item: (
                item.role.value,
                DependencyFingerprint.from_payload(_surface_payload(item)).digest,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class Cam3DZoneInputSnapshot:
    """Native-free immutable Part/Check/Fixture calculation input."""

    ownership: Cam3DCalculationOwnershipKey
    project_generation: int
    part: tuple[CamSurfaceReference, ...]
    check: tuple[CamSurfaceReference, ...] = ()
    fixture: tuple[CamSurfaceReference, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, Cam3DCalculationOwnershipKey):
            raise TypeError("CAM 3D zone ownership is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise ValueError("CAM 3D zone generation must be positive")
        roles = (
            (Cam3DSelectionRole.PART, self.part),
            (Cam3DSelectionRole.CHECK, self.check),
            (Cam3DSelectionRole.FIXTURE, self.fixture),
        )
        if not self.part:
            raise ValueError("CAM 3D zone requires PART surfaces")
        all_surfaces: list[CamSurfaceReference] = []
        for role, surfaces in roles:
            if not isinstance(surfaces, tuple) or any(
                not isinstance(item, CamSurfaceReference) for item in surfaces
            ):
                raise TypeError("CAM 3D zone surfaces must be typed tuples")
            if any(item.role is not role.cam_role for item in surfaces):
                raise ValueError("CAM 3D zone surface role is inconsistent")
            canonical = _canonical_surfaces(surfaces)
            object.__setattr__(self, role.value, canonical)
            all_surfaces.extend(canonical)
        if any(item.project_id != self.ownership.project_id for item in all_surfaces):
            raise ValueError("CAM 3D zone surface belongs to another project")
        if any(
            item.geometry.source_id != self.ownership.source_id
            for item in all_surfaces
        ):
            raise ValueError("CAM 3D zone surface belongs to another source")
        target_keys = tuple(item.target_key for item in all_surfaces)
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("CAM 3D zone contains duplicate surfaces")
        source_revisions = {
            item.geometry.expected_source_revision for item in all_surfaces
        }
        if len(source_revisions) != 1:
            raise ValueError("CAM 3D zone surfaces use different source revisions")

    @property
    def geometry_revision(self) -> Revision:
        """Return the common immutable source revision."""

        return self.part[0].geometry.expected_source_revision

    def canonical_payload(self) -> dict[str, object]:
        """Return deterministic semantic geometry input."""

        return {
            "ownership": self.ownership.canonical_payload(),
            "project_generation": self.project_generation,
            "geometry_revision": self.geometry_revision.to_dict(),
            "part": [_surface_payload(item) for item in self.part],
            "check": [_surface_payload(item) for item in self.check],
            "fixture": [_surface_payload(item) for item in self.fixture],
        }


@dataclass(frozen=True, slots=True)
class Cam3DCalculationPolicy:
    """Versioned policy identity for later calculation/preview execution."""

    algorithm: str = "hms_cam3d_geometry_foundation"
    algorithm_version: int = 1
    calculation_policy_version: int = CALCULATION_POLICY_VERSION
    tessellation_policy_version: int = TESSELLATION_POLICY_VERSION
    preview_policy_version: int = PREVIEW_POLICY_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.algorithm, str) or not self.algorithm.strip():
            raise ValueError("CAM 3D calculation algorithm is invalid")
        object.__setattr__(self, "algorithm", self.algorithm.strip())
        for name in (
            "algorithm_version",
            "calculation_policy_version",
            "tessellation_policy_version",
            "preview_policy_version",
        ):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

    @property
    def supported(self) -> bool:
        """Whether this application build understands every policy version."""

        return (
            self.calculation_policy_version == CALCULATION_POLICY_VERSION
            and self.tessellation_policy_version == TESSELLATION_POLICY_VERSION
            and self.preview_policy_version == PREVIEW_POLICY_VERSION
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "algorithm": self.algorithm,
            "algorithm_version": self.algorithm_version,
            "calculation_policy_version": self.calculation_policy_version,
            "tessellation_policy_version": self.tessellation_policy_version,
            "preview_policy_version": self.preview_policy_version,
        }


@dataclass(frozen=True, slots=True)
class Cam3DCalculationInputSnapshot:
    """Complete immutable semantic inputs of one explicit submission."""

    setup: Cam3DActiveSetupContext
    zone: Cam3DZoneInputSnapshot
    tool_assembly_id: ToolAssemblyId
    tool_assembly_fingerprint: DependencyFingerprint
    tool_profile_id: ToolProgramProfileId
    tool_profile_fingerprint: DependencyFingerprint
    tolerance_mm: float
    allowance_mm: float
    clearance_z_mm: float | None
    retract_z_mm: float | None
    approach_distance_mm: float
    link_clearance_mm: float
    policy: Cam3DCalculationPolicy
    unit: LengthUnit = LengthUnit.MM

    def __post_init__(self) -> None:
        if not isinstance(self.setup, Cam3DActiveSetupContext):
            raise TypeError("CAM 3D request Setup snapshot is invalid")
        if not isinstance(self.zone, Cam3DZoneInputSnapshot):
            raise TypeError("CAM 3D request zone snapshot is invalid")
        if self.zone.ownership != self.setup.ownership or (
            self.zone.project_generation != self.setup.project_generation
        ):
            raise ValueError("CAM 3D request Setup/zone ownership is inconsistent")
        if not isinstance(self.tool_assembly_id, ToolAssemblyId) or not isinstance(
            self.tool_profile_id, ToolProgramProfileId
        ):
            raise TypeError("CAM 3D request Tool/Profile identity is invalid")
        if not isinstance(
            self.tool_assembly_fingerprint, DependencyFingerprint
        ) or not isinstance(self.tool_profile_fingerprint, DependencyFingerprint):
            raise TypeError("CAM 3D request Tool/Profile fingerprint is invalid")
        if self.unit is not LengthUnit.MM:
            raise ValueError("CAM 3D request supports MM only")
        numeric_names = (
            "tolerance_mm",
            "allowance_mm",
            "clearance_z_mm",
            "retract_z_mm",
            "approach_distance_mm",
            "link_clearance_mm",
        )
        for name in numeric_names:
            value = getattr(self, name)
            if value is not None and type(value) is not float:
                raise TypeError(f"{name} must be a normalized float or None")
        if not isinstance(self.policy, Cam3DCalculationPolicy):
            raise TypeError("CAM 3D request policy is invalid")

    @property
    def ownership(self) -> Cam3DCalculationOwnershipKey:
        return self.setup.ownership

    @property
    def project_generation(self) -> int:
        return self.setup.project_generation

    def canonical_payload(self) -> dict[str, object]:
        """Return every semantic input, excluding job and presentation state."""

        return {
            "format": "HMS_CAM3D_CALCULATION_INPUT",
            "format_version": REQUEST_CONTRACT_VERSION,
            "setup": self.setup.canonical_payload(),
            "zone": self.zone.canonical_payload(),
            "tool_assembly": {
                "id": str(self.tool_assembly_id),
                "fingerprint": self.tool_assembly_fingerprint.to_dict(),
            },
            "tool_profile": {
                "id": str(self.tool_profile_id),
                "fingerprint": self.tool_profile_fingerprint.to_dict(),
            },
            "parameters_mm": {
                "tolerance": self.tolerance_mm,
                "allowance": self.allowance_mm,
                "clearance_z": self.clearance_z_mm,
                "retract_z": self.retract_z_mm,
                "approach_distance": self.approach_distance_mm,
                "link_clearance": self.link_clearance_mm,
                "unit": self.unit.value,
            },
            "policy": self.policy.canonical_payload(),
        }


@dataclass(frozen=True, slots=True)
class Cam3DRequestFingerprint:
    """Versioned semantic request fingerprint."""

    contract_version: int
    value: DependencyFingerprint

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version <= 0:
            raise ValueError("CAM 3D request fingerprint version is invalid")
        if not isinstance(self.value, DependencyFingerprint):
            raise TypeError("CAM 3D request fingerprint value is invalid")

    @classmethod
    def from_inputs(cls, inputs: Cam3DCalculationInputSnapshot) -> Self:
        if not isinstance(inputs, Cam3DCalculationInputSnapshot):
            raise TypeError("CAM 3D fingerprint inputs are invalid")
        return cls(
            REQUEST_CONTRACT_VERSION,
            DependencyFingerprint.from_payload(inputs.canonical_payload()),
        )

    @property
    def digest(self) -> str:
        return self.value.digest


@dataclass(frozen=True, slots=True)
class Cam3DPreviewCacheKey:
    """Versioned reusable preview key derived only from semantic inputs."""

    contract_version: int
    value: DependencyFingerprint

    def __post_init__(self) -> None:
        if type(self.contract_version) is not int or self.contract_version <= 0:
            raise ValueError("CAM 3D cache-key version is invalid")
        if not isinstance(self.value, DependencyFingerprint):
            raise TypeError("CAM 3D cache-key fingerprint is invalid")

    @classmethod
    def from_request_fingerprint(
        cls,
        fingerprint: Cam3DRequestFingerprint,
        policy: Cam3DCalculationPolicy,
    ) -> Self:
        if not isinstance(fingerprint, Cam3DRequestFingerprint) or not isinstance(
            policy, Cam3DCalculationPolicy
        ):
            raise TypeError("CAM 3D cache-key inputs are invalid")
        return cls(
            CACHE_KEY_CONTRACT_VERSION,
            DependencyFingerprint.from_payload(
                {
                    "format": "HMS_CAM3D_PREVIEW_CACHE_KEY",
                    "format_version": CACHE_KEY_CONTRACT_VERSION,
                    "request_fingerprint": fingerprint.value.to_dict(),
                    "tessellation_policy_version": policy.tessellation_policy_version,
                    "preview_policy_version": policy.preview_policy_version,
                }
            ),
        )

    @property
    def digest(self) -> str:
        return self.value.digest


@dataclass(frozen=True, slots=True)
class Cam3DCalculationRequestContract:
    """Atomic immutable output of the WP3-A request builder."""

    job_id: Cam3DCalculationJobId
    inputs: Cam3DCalculationInputSnapshot
    fingerprint: Cam3DRequestFingerprint
    cache_key: Cam3DPreviewCacheKey

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, Cam3DCalculationJobId):
            raise TypeError("CAM 3D request job identity is invalid")
        if not isinstance(self.inputs, Cam3DCalculationInputSnapshot):
            raise TypeError("CAM 3D request input snapshot is invalid")
        expected_fingerprint = Cam3DRequestFingerprint.from_inputs(self.inputs)
        if self.fingerprint != expected_fingerprint:
            raise ValueError("CAM 3D request fingerprint does not match inputs")
        expected_cache_key = Cam3DPreviewCacheKey.from_request_fingerprint(
            self.fingerprint, self.inputs.policy
        )
        if self.cache_key != expected_cache_key:
            raise ValueError("CAM 3D cache key does not match request")

    @property
    def ownership(self) -> Cam3DCalculationOwnershipKey:
        return self.inputs.ownership

    @property
    def project_generation(self) -> int:
        return self.inputs.project_generation


class Cam3DRequestDiagnosticCode(StrEnum):
    """Stable, localization-neutral request build failures."""

    JOB_ID_MISSING = "cam3d.request.job_id_missing"
    PROJECT_CLOSED = "cam3d.request.project_closed"
    READ_ONLY = "cam3d.request.read_only"
    STALE_GENERATION = "cam3d.request.stale_generation"
    PROJECT_MISMATCH = "cam3d.request.project_mismatch"
    DOCUMENT_MISSING = "cam3d.request.document_missing"
    DOCUMENT_MISMATCH = "cam3d.request.document_mismatch"
    SOURCE_MISSING = "cam3d.request.source_missing"
    SOURCE_MISMATCH = "cam3d.request.source_mismatch"
    SETUP_MISSING = "cam3d.request.setup_missing"
    SETUP_MISMATCH = "cam3d.request.setup_mismatch"
    PART_MISSING = "cam3d.request.part_missing"
    SELECTION_INVALID = "cam3d.request.selection_invalid"
    SELECTION_CHANGED = "cam3d.request.selection_changed"
    EDITOR_PARTIAL = "cam3d.request.editor_partial"
    EDITOR_INVALID = "cam3d.request.editor_invalid"
    TOOL_PROFILE_INVALID = "cam3d.request.tool_profile_invalid"
    NUMERIC_INVALID = "cam3d.request.numeric_invalid"
    ZONE_PROVENANCE_MISMATCH = "cam3d.request.zone_provenance_mismatch"
    UNSUPPORTED_POLICY_VERSION = "cam3d.request.unsupported_policy_version"


@dataclass(frozen=True, slots=True)
class Cam3DRequestDiagnostic:
    """One typed failure without localized or exception text."""

    code: Cam3DRequestDiagnosticCode
    details: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.code, Cam3DRequestDiagnosticCode):
            raise TypeError("CAM 3D request diagnostic code is invalid")
        if not isinstance(self.details, tuple) or any(
            not isinstance(item, tuple)
            or len(item) != 2
            or not all(isinstance(value, str) for value in item)
            for item in self.details
        ):
            raise TypeError("CAM 3D request diagnostic details are invalid")
        ordered = tuple(sorted(self.details))
        if len({key for key, _value in ordered}) != len(ordered):
            raise ValueError("CAM 3D request diagnostic detail keys are duplicated")
        object.__setattr__(self, "details", ordered)


@dataclass(frozen=True, slots=True)
class Cam3DRequestBuildResult:
    """Atomic valid request or typed diagnostics, never a partial request."""

    request: Cam3DCalculationRequestContract | None
    diagnostics: tuple[Cam3DRequestDiagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.request is not None and not isinstance(
            self.request, Cam3DCalculationRequestContract
        ):
            raise TypeError("CAM 3D build result request is invalid")
        if not isinstance(self.diagnostics, tuple) or any(
            not isinstance(item, Cam3DRequestDiagnostic) for item in self.diagnostics
        ):
            raise TypeError("CAM 3D build diagnostics are invalid")
        if (self.request is None) == (not self.diagnostics):
            raise ValueError("CAM 3D build result must contain request xor diagnostics")

    @property
    def accepted(self) -> bool:
        return self.request is not None


def _failure(
    code: Cam3DRequestDiagnosticCode,
    **details: object,
) -> Cam3DRequestBuildResult:
    return Cam3DRequestBuildResult(
        None,
        (
            Cam3DRequestDiagnostic(
                code,
                tuple((key, str(value)) for key, value in details.items()),
            ),
        ),
    )


def _first_selection_provenance_mismatch(
    selection: Cam3DSelectionState,
    context: Cam3DProjectContext,
) -> Cam3DRequestDiagnosticCode | None:
    for _role, items in selection.role_items:
        for item in items:
            provenance = item.provenance
            if provenance.project_id != context.project_id:
                return Cam3DRequestDiagnosticCode.PROJECT_MISMATCH
            if provenance.project_generation != context.project_generation:
                return Cam3DRequestDiagnosticCode.STALE_GENERATION
            if provenance.document_id != context.document_id:
                return Cam3DRequestDiagnosticCode.DOCUMENT_MISMATCH
            if provenance.source_id != context.source_id:
                return Cam3DRequestDiagnosticCode.SOURCE_MISMATCH
    return None


class Cam3DCalculationRequestBuilder:
    """Pure application mapper from live WP2 state to a submission request."""

    def build(
        self,
        *,
        editor: Cam3DEditorState,
        live_context: Cam3DProjectContext,
        live_selection: Cam3DSelectionState,
        active_setup: Cam3DActiveSetupContext | None,
        job_id: Cam3DCalculationJobId | None,
        policy: Cam3DCalculationPolicy,
    ) -> Cam3DRequestBuildResult:
        """Build atomically or return one deterministic fail-closed diagnostic."""

        if not isinstance(editor, Cam3DEditorState):
            raise TypeError("editor must be Cam3DEditorState")
        if not isinstance(live_context, Cam3DProjectContext):
            raise TypeError("live_context must be Cam3DProjectContext")
        if not isinstance(live_selection, Cam3DSelectionState):
            raise TypeError("live_selection must be Cam3DSelectionState")
        if active_setup is not None and not isinstance(
            active_setup, Cam3DActiveSetupContext
        ):
            raise TypeError("active_setup must be Cam3DActiveSetupContext or None")
        if job_id is not None and not isinstance(job_id, Cam3DCalculationJobId):
            raise TypeError("job_id must be Cam3DCalculationJobId or None")
        if not isinstance(policy, Cam3DCalculationPolicy):
            raise TypeError("policy must be Cam3DCalculationPolicy")
        if job_id is None:
            return _failure(Cam3DRequestDiagnosticCode.JOB_ID_MISSING)
        if not editor.context.is_open or not live_context.is_open:
            return _failure(Cam3DRequestDiagnosticCode.PROJECT_CLOSED)
        if editor.context.read_only or live_context.read_only or live_selection.read_only:
            return _failure(Cam3DRequestDiagnosticCode.READ_ONLY)
        if editor.context.project_id != live_context.project_id or (
            live_selection.project_id != live_context.project_id
        ):
            return _failure(Cam3DRequestDiagnosticCode.PROJECT_MISMATCH)
        if editor.context.project_generation != live_context.project_generation or (
            live_selection.project_generation != live_context.project_generation
        ):
            return _failure(Cam3DRequestDiagnosticCode.STALE_GENERATION)
        if live_context.document_id is None:
            return _failure(Cam3DRequestDiagnosticCode.DOCUMENT_MISSING)
        if editor.context.document_id != live_context.document_id:
            return _failure(Cam3DRequestDiagnosticCode.DOCUMENT_MISMATCH)
        if live_context.source_id is None:
            return _failure(Cam3DRequestDiagnosticCode.SOURCE_MISSING)
        if editor.context.source_id != live_context.source_id:
            return _failure(Cam3DRequestDiagnosticCode.SOURCE_MISMATCH)
        provenance_issue = _first_selection_provenance_mismatch(
            live_selection, live_context
        )
        if provenance_issue is not None:
            return _failure(provenance_issue)
        if live_selection != editor.selection:
            return _failure(Cam3DRequestDiagnosticCode.SELECTION_CHANGED)
        if not live_selection.part:
            return _failure(Cam3DRequestDiagnosticCode.PART_MISSING)
        if live_selection.status in {
            Cam3DSelectionStatus.PROJECT_CLOSED,
            Cam3DSelectionStatus.STALE,
            Cam3DSelectionStatus.INVALID,
        } or any(
            item.validity is not Cam3DSelectionValidity.VALID
            for _role, items in live_selection.role_items
            for item in items
        ):
            return _failure(Cam3DRequestDiagnosticCode.SELECTION_INVALID)
        if active_setup is None:
            return _failure(Cam3DRequestDiagnosticCode.SETUP_MISSING)
        expected_ownership = Cam3DCalculationOwnershipKey(
            live_context.project_id,  # type: ignore[arg-type]
            live_context.document_id,
            live_context.source_id,
            active_setup.ownership.setup_id,
        )
        if not active_setup.active or active_setup.ownership != expected_ownership:
            return _failure(Cam3DRequestDiagnosticCode.SETUP_MISMATCH)
        if active_setup.project_generation != live_context.project_generation:
            return _failure(Cam3DRequestDiagnosticCode.STALE_GENERATION)
        if not policy.supported:
            return _failure(Cam3DRequestDiagnosticCode.UNSUPPORTED_POLICY_VERSION)
        if editor.diagnostics:
            if any(item.field is not None for item in editor.diagnostics):
                return _failure(Cam3DRequestDiagnosticCode.NUMERIC_INVALID)
            return _failure(Cam3DRequestDiagnosticCode.EDITOR_INVALID)

        validator = Cam3DEditorApplicationService(
            editor.context,
            editor.selection,
            parameters=editor.parameters,
        )
        if editor.tool_assembly is not None:
            validator.assign_tool_assembly(
                editor.tool_assembly, live_context=live_context
            )
        if editor.tool_profile is not None:
            validator.assign_tool_profile(editor.tool_profile, live_context=live_context)
        evaluation = validator.evaluate(live_context)
        if evaluation.readiness is not Cam3DEditorReadiness.READY_FOR_EDITOR_BINDING:
            code = (
                Cam3DRequestDiagnosticCode.EDITOR_PARTIAL
                if evaluation.readiness is Cam3DEditorReadiness.PARTIAL
                else Cam3DRequestDiagnosticCode.TOOL_PROFILE_INVALID
                if editor.tool_assembly is not None or editor.tool_profile is not None
                else Cam3DRequestDiagnosticCode.EDITOR_INVALID
            )
            return _failure(code, readiness=evaluation.readiness.value)
        assert editor.tool_assembly is not None
        assert editor.tool_profile is not None

        try:
            zone = Cam3DZoneInputSnapshot(
                expected_ownership,
                live_context.project_generation,  # type: ignore[arg-type]
                tuple(item.reference for item in live_selection.part),
                tuple(item.reference for item in live_selection.check),
                tuple(item.reference for item in live_selection.fixture),
            )
        except (TypeError, ValueError):
            return _failure(Cam3DRequestDiagnosticCode.ZONE_PROVENANCE_MISMATCH)
        parameters = editor.parameters
        inputs = Cam3DCalculationInputSnapshot(
            active_setup,
            zone,
            editor.tool_assembly.assembly_id,
            editor.tool_assembly.assembly.content_fingerprint,
            editor.tool_profile.profile_id,
            DependencyFingerprint.from_payload(
                editor.tool_profile.profile.fingerprint.to_dict()
            ),
            parameters.tolerance_mm,
            parameters.allowance_mm,
            parameters.clearance_z_mm,
            parameters.retract_z_mm,
            parameters.approach_distance_mm,
            parameters.link_clearance_mm,
            policy,
            parameters.unit,
        )
        fingerprint = Cam3DRequestFingerprint.from_inputs(inputs)
        cache_key = Cam3DPreviewCacheKey.from_request_fingerprint(
            fingerprint, policy
        )
        return Cam3DRequestBuildResult(
            Cam3DCalculationRequestContract(job_id, inputs, fingerprint, cache_key)
        )


class Cam3DSessionDecision(StrEnum):
    """Deterministic outcome of one pure session operation."""

    ACCEPTED = "accepted"
    DUPLICATE_REQUEST = "duplicate_request"
    CLOSED = "closed"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    STALE_GENERATION = "stale_generation"
    SUPERSEDED = "superseded"
    FINGERPRINT_MISMATCH = "fingerprint_mismatch"
    CANCELLED = "cancelled"
    NOT_LATEST = "not_latest"
    DUPLICATE_RESULT = "duplicate_result"


@dataclass(frozen=True, slots=True)
class Cam3DResultIdentity:
    """Native-free callback identity checked before result publication."""

    job_id: Cam3DCalculationJobId
    ownership: Cam3DCalculationOwnershipKey
    project_generation: int
    fingerprint: Cam3DRequestFingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, Cam3DCalculationJobId):
            raise TypeError("CAM 3D result job identity is invalid")
        if not isinstance(self.ownership, Cam3DCalculationOwnershipKey):
            raise TypeError("CAM 3D result ownership is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise ValueError("CAM 3D result generation must be positive")
        if not isinstance(self.fingerprint, Cam3DRequestFingerprint):
            raise TypeError("CAM 3D result fingerprint is invalid")

    @classmethod
    def from_request(cls, request: Cam3DCalculationRequestContract) -> Self:
        if not isinstance(request, Cam3DCalculationRequestContract):
            raise TypeError("CAM 3D result source request is invalid")
        return cls(
            request.job_id,
            request.ownership,
            request.project_generation,
            request.fingerprint,
        )


@dataclass(frozen=True, slots=True)
class Cam3DCalculationSession:
    """Pure application-owned latest-wins and cancellation state machine."""

    ownership: Cam3DCalculationOwnershipKey
    project_generation: int
    live: bool = True
    latest_job_id: Cam3DCalculationJobId | None = None
    latest_fingerprint: Cam3DRequestFingerprint | None = None
    cancelled_jobs: frozenset[Cam3DCalculationJobId] = frozenset()
    published_job_id: Cam3DCalculationJobId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, Cam3DCalculationOwnershipKey):
            raise TypeError("CAM 3D session ownership is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise ValueError("CAM 3D session generation must be positive")
        if type(self.live) is not bool:
            raise TypeError("CAM 3D session live state must be bool")
        if (self.latest_job_id is None) != (self.latest_fingerprint is None):
            raise ValueError("CAM 3D latest job/fingerprint must be paired")
        if not isinstance(self.cancelled_jobs, frozenset) or any(
            not isinstance(item, Cam3DCalculationJobId)
            for item in self.cancelled_jobs
        ):
            raise TypeError("CAM 3D cancelled job set is invalid")
        if self.published_job_id is not None and not isinstance(
            self.published_job_id, Cam3DCalculationJobId
        ):
            raise TypeError("CAM 3D published job identity is invalid")

    def register(
        self, request: Cam3DCalculationRequestContract
    ) -> "Cam3DSessionUpdate":
        """Register the latest accepted request without starting work."""

        if not isinstance(request, Cam3DCalculationRequestContract):
            raise TypeError("CAM 3D session request is invalid")
        if not self.live:
            return Cam3DSessionUpdate(self, False, Cam3DSessionDecision.CLOSED)
        if request.ownership != self.ownership:
            return Cam3DSessionUpdate(
                self, False, Cam3DSessionDecision.OWNERSHIP_MISMATCH
            )
        if request.project_generation != self.project_generation:
            return Cam3DSessionUpdate(
                self, False, Cam3DSessionDecision.STALE_GENERATION
            )
        if request.job_id == self.latest_job_id and (
            request.fingerprint == self.latest_fingerprint
        ):
            return Cam3DSessionUpdate(
                self, False, Cam3DSessionDecision.DUPLICATE_REQUEST
            )
        state = replace(
            self,
            latest_job_id=request.job_id,
            latest_fingerprint=request.fingerprint,
            published_job_id=None,
        )
        return Cam3DSessionUpdate(state, True, Cam3DSessionDecision.ACCEPTED)

    def request_cancellation(
        self, job_id: Cam3DCalculationJobId
    ) -> "Cam3DSessionUpdate":
        """Cancel only the latest job owned by this session."""

        if not isinstance(job_id, Cam3DCalculationJobId):
            raise TypeError("CAM 3D cancellation job identity is invalid")
        if not self.live:
            return Cam3DSessionUpdate(self, False, Cam3DSessionDecision.CLOSED)
        if job_id != self.latest_job_id:
            return Cam3DSessionUpdate(self, False, Cam3DSessionDecision.NOT_LATEST)
        if job_id in self.cancelled_jobs:
            return Cam3DSessionUpdate(self, False, Cam3DSessionDecision.CANCELLED)
        return Cam3DSessionUpdate(
            replace(self, cancelled_jobs=self.cancelled_jobs | {job_id}),
            True,
            Cam3DSessionDecision.ACCEPTED,
        )

    def publication_decision(
        self, result: Cam3DResultIdentity
    ) -> Cam3DSessionDecision:
        """Check every late-result guard without mutating state."""

        if not isinstance(result, Cam3DResultIdentity):
            raise TypeError("CAM 3D result identity is invalid")
        if not self.live:
            return Cam3DSessionDecision.CLOSED
        if result.ownership != self.ownership:
            return Cam3DSessionDecision.OWNERSHIP_MISMATCH
        if result.project_generation != self.project_generation:
            return Cam3DSessionDecision.STALE_GENERATION
        if result.job_id != self.latest_job_id:
            return Cam3DSessionDecision.SUPERSEDED
        if result.fingerprint != self.latest_fingerprint:
            return Cam3DSessionDecision.FINGERPRINT_MISMATCH
        if result.job_id in self.cancelled_jobs:
            return Cam3DSessionDecision.CANCELLED
        if result.job_id == self.published_job_id:
            return Cam3DSessionDecision.DUPLICATE_RESULT
        return Cam3DSessionDecision.ACCEPTED

    def accept_result(self, result: Cam3DResultIdentity) -> "Cam3DSessionUpdate":
        """Consume one result identity exactly once when every guard matches."""

        decision = self.publication_decision(result)
        if decision is not Cam3DSessionDecision.ACCEPTED:
            return Cam3DSessionUpdate(self, False, decision)
        return Cam3DSessionUpdate(
            replace(self, published_job_id=result.job_id),
            True,
            Cam3DSessionDecision.ACCEPTED,
        )

    def close(self) -> "Cam3DCalculationSession":
        """Invalidate all future callbacks after project close."""

        return replace(self, live=False)

    def rebind(
        self,
        ownership: Cam3DCalculationOwnershipKey,
        project_generation: int,
    ) -> "Cam3DCalculationSession":
        """Create a fresh session after project/document/source/Setup switch."""

        return Cam3DCalculationSession(ownership, project_generation)


@dataclass(frozen=True, slots=True)
class Cam3DSessionUpdate:
    """Typed immutable result of a session mutation attempt."""

    session: Cam3DCalculationSession
    accepted: bool
    decision: Cam3DSessionDecision

    def __post_init__(self) -> None:
        if not isinstance(self.session, Cam3DCalculationSession):
            raise TypeError("CAM 3D session update state is invalid")
        if type(self.accepted) is not bool:
            raise TypeError("CAM 3D session update accepted flag is invalid")
        if not isinstance(self.decision, Cam3DSessionDecision):
            raise TypeError("CAM 3D session update decision is invalid")


@dataclass(frozen=True, slots=True)
class Cam3DCacheRecordIdentity:
    """Pure identity of a previously derived preview cache record."""

    ownership: Cam3DCalculationOwnershipKey
    project_generation: int
    cache_key: Cam3DPreviewCacheKey

    def __post_init__(self) -> None:
        if not isinstance(self.ownership, Cam3DCalculationOwnershipKey):
            raise TypeError("CAM 3D cache record ownership is invalid")
        if type(self.project_generation) is not int or self.project_generation <= 0:
            raise ValueError("CAM 3D cache record generation must be positive")
        if not isinstance(self.cache_key, Cam3DPreviewCacheKey):
            raise TypeError("CAM 3D cache record key is invalid")

    @classmethod
    def from_request(cls, request: Cam3DCalculationRequestContract) -> Self:
        if not isinstance(request, Cam3DCalculationRequestContract):
            raise TypeError("CAM 3D cache source request is invalid")
        return cls(request.ownership, request.project_generation, request.cache_key)


class Cam3DCacheDecision(StrEnum):
    """Pure cache reuse/invalidation decision."""

    REUSE = "reuse"
    INVALIDATE_OWNERSHIP = "invalidate_ownership"
    INVALIDATE_GENERATION = "invalidate_generation"
    INVALIDATE_SEMANTIC_INPUT = "invalidate_semantic_input"


def evaluate_cam3d_cache_reuse(
    record: Cam3DCacheRecordIdentity,
    request: Cam3DCalculationRequestContract,
) -> Cam3DCacheDecision:
    """Evaluate reuse without reading, writing or deleting a cache artifact."""

    if not isinstance(record, Cam3DCacheRecordIdentity):
        raise TypeError("CAM 3D cache record is invalid")
    if not isinstance(request, Cam3DCalculationRequestContract):
        raise TypeError("CAM 3D cache request is invalid")
    if record.ownership != request.ownership:
        return Cam3DCacheDecision.INVALIDATE_OWNERSHIP
    if record.project_generation != request.project_generation:
        return Cam3DCacheDecision.INVALIDATE_GENERATION
    if record.cache_key != request.cache_key:
        return Cam3DCacheDecision.INVALIDATE_SEMANTIC_INPUT
    return Cam3DCacheDecision.REUSE


__all__ = [
    "CACHE_KEY_CONTRACT_VERSION",
    "CALCULATION_POLICY_VERSION",
    "Cam3DActiveSetupContext",
    "Cam3DCacheDecision",
    "Cam3DCacheRecordIdentity",
    "Cam3DCalculationInputSnapshot",
    "Cam3DCalculationJobId",
    "Cam3DCalculationOwnershipKey",
    "Cam3DCalculationPolicy",
    "Cam3DCalculationRequestBuilder",
    "Cam3DCalculationRequestContract",
    "Cam3DCalculationSession",
    "Cam3DPreviewCacheKey",
    "Cam3DRequestBuildResult",
    "Cam3DRequestDiagnostic",
    "Cam3DRequestDiagnosticCode",
    "Cam3DRequestFingerprint",
    "Cam3DResultIdentity",
    "Cam3DSessionDecision",
    "Cam3DSessionUpdate",
    "Cam3DZoneInputSnapshot",
    "PREVIEW_POLICY_VERSION",
    "REQUEST_CONTRACT_VERSION",
    "TESSELLATION_POLICY_VERSION",
    "evaluate_cam3d_cache_reuse",
]

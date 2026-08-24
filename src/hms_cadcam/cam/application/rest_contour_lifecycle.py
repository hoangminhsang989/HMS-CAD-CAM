"""Application-facing lifecycle for the R271 Rest Contour core.

This module deliberately owns orchestration only.  Geometry, toolpath safety,
and MaterialState trust remain owned by the sealed R270/R271 boundaries.  A
caller must therefore supply aggregate-resolved evidence, never a free-form
``MaterialState`` or a manually constructed Phase-B reservation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Callable

from hms_cadcam.cam.application.rest_contour import (
    RestContourFoundation,
    RestContourFoundationInputs,
    RestContourFoundationResult,
)
from hms_cadcam.cam.application.rest_contour_geometry import (
    NoRestContourMaterial,
    RestContourGeometryInputs,
    RestContourResidualPlan,
    plan_rest_contour_residual,
)
from hms_cadcam.cam.application.rest_contour_toolpath import (
    RestContourPhaseBCandidate,
    RestContourPhaseBExecutionContext,
    RestContourPhaseBNoRestMaterial,
    RestContourPhaseBPrepared,
    RestContourPhaseBPublication,
    generate_rest_contour_phase_b,
    prepare_rest_contour_phase_b,
    publish_rest_contour_phase_b,
    _input_fingerprint,
)
from hms_cadcam.cam.domain import GeometryReference, MachineEvidence, ResolvedContourProfile
from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.rest_contour import (
    RestContourDiagnosticCode,
    RestContourValidationError,
)
from hms_cadcam.cam.material_state import material_state_setup_fingerprint
from hms_cadcam.cam.persistence.errors import ToolpathArtifactStoreError
from hms_cadcam.cam.persistence.models import MaterialStateSuccessorPublication
from hms_cadcam.cam.toolpath import compute_material_removal_fingerprint


class RestContourLifecycleStatus(StrEnum):
    """Semantic application outcome; no empty artifact stands in for a status."""

    PREPARED = "PREPARED"
    SUCCESS = "SUCCESS"
    NO_REST_MATERIAL = "NO_REST_MATERIAL"
    FAILURE = "FAILURE"


RestContourPublicationCallback = Callable[
    [RestContourPhaseBCandidate, RestContourPhaseBExecutionContext, Path],
    RestContourPhaseBPublication,
]


@dataclass(frozen=True, slots=True)
class RestContourLifecycleContext:
    """Aggregate-resolved input required to prepare one Rest Contour operation.

    ``foundation_inputs`` carries the persisted operation, explicit DAG edge,
    material dependency, profile, tool assembly and machine requirement.
    Service code is responsible for resolving those values from its snapshot.
    """

    foundation_inputs: RestContourFoundationInputs
    machine_evidence: MachineEvidence
    profile_resolver: Callable[[GeometryReference], ResolvedContourProfile]
    cancellation: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.foundation_inputs, RestContourFoundationInputs):
            raise TypeError("Rest Contour lifecycle foundation inputs are invalid")
        if not isinstance(self.machine_evidence, MachineEvidence):
            raise TypeError("Rest Contour lifecycle machine evidence is invalid")
        if not callable(self.profile_resolver):
            raise TypeError("Rest Contour lifecycle profile resolver is invalid")
        if self.cancellation is not None and not callable(self.cancellation):
            raise TypeError("Rest Contour lifecycle cancellation callback is invalid")


@dataclass(frozen=True, slots=True)
class RestContourLifecyclePrepared:
    """A successful lifecycle reservation backed by R271's sealed Prepared."""

    context: RestContourLifecycleContext
    foundation: RestContourFoundationResult
    phase_b_context: RestContourPhaseBExecutionContext
    phase_b_prepared: RestContourPhaseBPrepared


@dataclass(frozen=True, slots=True)
class RestContourLifecyclePreparation:
    """Typed preparation outcome with no durable side effects."""

    status: RestContourLifecycleStatus
    prepared: RestContourLifecyclePrepared | None = None
    no_rest_material: NoRestContourMaterial | None = None
    diagnostic_code: RestContourDiagnosticCode | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.status is RestContourLifecycleStatus.PREPARED:
            if self.prepared is None or self.no_rest_material is not None or self.diagnostic_code is not None:
                raise ValueError("Rest Contour prepared lifecycle result is inconsistent")
        elif self.status is RestContourLifecycleStatus.NO_REST_MATERIAL:
            if self.prepared is not None or self.no_rest_material is None or self.diagnostic_code is not None:
                raise ValueError("Rest Contour no-rest lifecycle result is inconsistent")
        elif self.status is RestContourLifecycleStatus.FAILURE:
            if self.prepared is not None or self.no_rest_material is not None or self.diagnostic_code is None:
                raise ValueError("Rest Contour failure lifecycle result is inconsistent")
        else:
            raise ValueError("Rest Contour preparation status is invalid")


@dataclass(frozen=True, slots=True)
class RestContourLifecycleResult:
    """Typed generate result.  ``NO_REST_MATERIAL`` has no candidate or write."""

    status: RestContourLifecycleStatus
    preparation: RestContourLifecyclePreparation
    candidate: RestContourPhaseBCandidate | None = None
    publication: RestContourPhaseBPublication | None = None
    successor_publication: MaterialStateSuccessorPublication | None = None
    diagnostic_code: RestContourDiagnosticCode | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.status is RestContourLifecycleStatus.SUCCESS:
            if (self.preparation.status is not RestContourLifecycleStatus.PREPARED
                    or self.candidate is None or self.publication is None
                    or self.successor_publication is None or self.diagnostic_code is not None):
                raise ValueError("Rest Contour success lifecycle result is inconsistent")
        elif self.status is RestContourLifecycleStatus.NO_REST_MATERIAL:
            if (self.preparation.status is not RestContourLifecycleStatus.NO_REST_MATERIAL
                    or self.candidate is not None or self.publication is not None
                    or self.successor_publication is not None or self.diagnostic_code is not None):
                raise ValueError("Rest Contour no-rest result is inconsistent")
        elif self.status is RestContourLifecycleStatus.FAILURE:
            if (self.diagnostic_code is None or self.candidate is not None
                    or self.publication is not None or self.successor_publication is not None):
                raise ValueError("Rest Contour failure result is inconsistent")
        else:
            raise ValueError("Rest Contour lifecycle result status is invalid")


def _failure(
    preparation: RestContourLifecyclePreparation,
    error: RestContourValidationError,
) -> RestContourLifecycleResult:
    return RestContourLifecycleResult(
        RestContourLifecycleStatus.FAILURE,
        preparation,
        diagnostic_code=error.code,
        message=str(error),
    )


def _geometry_inputs(
    context: RestContourLifecycleContext,
    foundation: RestContourFoundationResult,
) -> RestContourGeometryInputs:
    """Derive Phase-A input exclusively from the current aggregate evidence."""
    values = context.foundation_inputs
    setup = values.setup
    return RestContourGeometryInputs(
        foundation=foundation,
        profile_descriptor=values.profile.descriptor,
        stock=setup.stock,
        setup=setup,
        setup_fingerprint=material_state_setup_fingerprint(setup),
        parameters=values.parameters,
        tool=values.tool,
        assembly=values.assembly,
        assembly_evidence=values.assembly_evidence,
        machine=values.machine,
        machine_evidence=context.machine_evidence,
        cancellation=context.cancellation,
    )


def derive_rest_contour_input_fingerprint(context: RestContourLifecycleContext):
    """Derive the current R271 semantic execution key without minting Prepared.

    Durable replay uses this read-only derivation to bind a v2 record to live
    aggregate authority.  It deliberately stops before the process-local
    Phase-B reservation, which cannot be recreated for an already VALID
    operation after reopen.
    """
    if not isinstance(context, RestContourLifecycleContext):
        raise TypeError("Rest Contour lifecycle context is invalid")
    foundation = RestContourFoundation(context.profile_resolver).resolve(context.foundation_inputs)
    geometry_inputs = _geometry_inputs(context, foundation)
    plan = plan_rest_contour_residual(geometry_inputs)
    if isinstance(plan, NoRestContourMaterial):
        return None
    if not isinstance(plan, RestContourResidualPlan):
        raise TypeError("Rest Contour residual planning returned an invalid value")
    candidate = foundation.material.candidate
    if candidate is None:
        raise RestContourValidationError(
            RestContourDiagnosticCode.MATERIAL_STATE_INVALID,
            "Rest Contour input fingerprint lacks an authoritative predecessor",
        )
    return _input_fingerprint(plan, candidate.state, geometry_inputs.setup)


def _successor_publication(candidate: RestContourPhaseBCandidate) -> MaterialStateSuccessorPublication:
    """Create v2 durable-result evidence only from the sealed R271 candidate."""
    artifact = candidate.artifact
    parent = candidate.prepared.predecessor_state
    successor = candidate.successor_state
    return MaterialStateSuccessorPublication.create(
        consumer_operation_id=artifact.source_operation_id,
        artifact_id=artifact.artifact_id,
        artifact_fingerprint=artifact.artifact_fingerprint,
        input_fingerprint=artifact.input_fingerprint,
        semantic_material_removal_fingerprint=compute_material_removal_fingerprint(artifact),
        parent_state_fingerprint=parent.fingerprint,
        parent_state_content_seal=parent.content_integrity_fingerprint,
        successor_state_fingerprint=successor.fingerprint,
        successor_state_content_seal=successor.content_integrity_fingerprint,
        setup_fingerprint=successor.setup_fingerprint,
        stock_fingerprint=successor.stock_fingerprint,
        engine_version=successor.engine_version,
        precision=successor.precision.to_dict(),
    )


class RestContourLifecycle:
    """Prepare and publish one Rest Contour operation through R270/R271.

    The optional publisher is the only extension point for project persistence.
    It receives the sealed R271 candidate, not caller-created state.  A project
    service can publish artifact/state and atomically install v2 metadata in
    its own transaction; if publication raises, this lifecycle reports typed
    failure and never returns a success result.
    """

    def prepare(self, context: RestContourLifecycleContext) -> RestContourLifecyclePreparation:
        if not isinstance(context, RestContourLifecycleContext):
            raise TypeError("Rest Contour lifecycle context is invalid")
        try:
            foundation = RestContourFoundation(context.profile_resolver).resolve(context.foundation_inputs)
            geometry_inputs = _geometry_inputs(context, foundation)
            plan = plan_rest_contour_residual(geometry_inputs)
            phase_b_context = RestContourPhaseBExecutionContext(geometry_inputs, plan)
            prepared = prepare_rest_contour_phase_b(phase_b_context)
        except RestContourValidationError as error:
            return RestContourLifecyclePreparation(
                RestContourLifecycleStatus.FAILURE,
                diagnostic_code=error.code,
                message=str(error),
            )
        if isinstance(prepared, RestContourPhaseBNoRestMaterial):
            return RestContourLifecyclePreparation(
                RestContourLifecycleStatus.NO_REST_MATERIAL,
                no_rest_material=prepared.outcome,
            )
        if not isinstance(prepared, RestContourPhaseBPrepared):
            raise TypeError("Rest Contour Phase B preparation returned an invalid value")
        return RestContourLifecyclePreparation(
            RestContourLifecycleStatus.PREPARED,
            prepared=RestContourLifecyclePrepared(context, foundation, phase_b_context, prepared),
        )

    def generate(
        self,
        preparation: RestContourLifecyclePreparation,
        *,
        project_root: Path,
        publisher: RestContourPublicationCallback | None = None,
    ) -> RestContourLifecycleResult:
        if not isinstance(preparation, RestContourLifecyclePreparation):
            raise TypeError("Rest Contour lifecycle preparation is invalid")
        if not isinstance(project_root, Path):
            raise TypeError("Rest Contour project root is invalid")
        if publisher is not None and not callable(publisher):
            raise TypeError("Rest Contour publication callback is invalid")
        if preparation.status is RestContourLifecycleStatus.NO_REST_MATERIAL:
            return RestContourLifecycleResult(RestContourLifecycleStatus.NO_REST_MATERIAL, preparation)
        if preparation.status is RestContourLifecycleStatus.FAILURE:
            return RestContourLifecycleResult(
                RestContourLifecycleStatus.FAILURE,
                preparation,
                diagnostic_code=preparation.diagnostic_code,
                message=preparation.message,
            )
        if preparation.status is not RestContourLifecycleStatus.PREPARED or preparation.prepared is None:
            raise TypeError("Rest Contour lifecycle preparation status is invalid")
        value = preparation.prepared
        try:
            candidate = generate_rest_contour_phase_b(
                value.phase_b_prepared,
                cancellation=value.context.cancellation,
            )
            publication = (
                publisher(candidate, value.phase_b_context, project_root)
                if publisher is not None
                else publish_rest_contour_phase_b(
                    candidate,
                    current_context=value.phase_b_context,
                    project_root=project_root,
                )
            )
            if (
                not isinstance(publication, RestContourPhaseBPublication)
                or publication.artifact != candidate.artifact
                or publication.successor_state != candidate.successor_state
                # R271 publishes a new Operation revision with valid artifact
                # state, so exact operation object equality is intentionally
                # not expected here.  Its immutable identity must not change.
                or publication.operation.operation_id
                != candidate.prepared.computing_operation.operation_id
            ):
                raise RestContourValidationError(
                    RestContourDiagnosticCode.PUBLICATION_FAILED,
                    "Rest Contour publication did not return the sealed candidate",
                )
            successor_publication = _successor_publication(candidate)
        except RestContourValidationError as error:
            return _failure(preparation, error)
        except (OSError, ToolpathArtifactStoreError, CamValidationError) as error:
            return _failure(
                preparation,
                RestContourValidationError(
                    RestContourDiagnosticCode.PUBLICATION_FAILED,
                    f"Rest Contour durable publication failed: {error}",
                ),
            )
        return RestContourLifecycleResult(
            RestContourLifecycleStatus.SUCCESS,
            preparation,
            candidate=candidate,
            publication=publication,
            successor_publication=successor_publication,
        )


def prepare_rest_contour(context: RestContourLifecycleContext) -> RestContourLifecyclePreparation:
    """Prepare through the sole public lifecycle boundary."""
    return RestContourLifecycle().prepare(context)


def generate_rest_contour_toolpath(
    preparation: RestContourLifecyclePreparation,
    *,
    project_root: Path,
    publisher: RestContourPublicationCallback | None = None,
) -> RestContourLifecycleResult:
    """Generate/publish a previously prepared Rest Contour result."""
    return RestContourLifecycle().generate(preparation, project_root=project_root, publisher=publisher)

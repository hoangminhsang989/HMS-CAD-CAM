"""Public in-memory lifecycle for R273 Rest Finishing 3-axis core."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum, StrEnum
from typing import Callable
from uuid import UUID

from hms_cadcam.cam.application.rest_contour import RestMaterialStateCandidate
from hms_cadcam.cam.application.rest_finishing_geometry import (
    NoRestFinishingMaterial,
    RestFinishingGeometryInputs,
    RestFinishingRasterPlan,
    plan_rest_finishing_geometry,
)
from hms_cadcam.cam.application.rest_finishing_toolpath import (
    RestFinishingCandidate,
    RestFinishingPrepared,
    generate_rest_finishing_toolpath,
    prepare_rest_finishing_toolpath,
    require_rest_finishing_candidate,
    require_rest_finishing_prepared,
)
from hms_cadcam.cam.application.rest_contour_toolpath import (
    R272ValidatedSuccessorCertificate,
)
from hms_cadcam.cam.domain import (
    DependencyGraph,
    MachineDefinition,
    MachineEvidence,
    MachineRequirement,
    OperationId,
    Setup,
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolDefinition,
)
from hms_cadcam.cam.domain.contour import ResolvedContourProfile
from hms_cadcam.cam.domain.geometry_reference import GeometryReference
from hms_cadcam.cam.domain.rest_finishing import (
    RestFinishingDiagnosticCode,
    RestFinishingParameters,
    RestFinishingProfileSelection,
    RestFinishingValidationError,
)
from hms_cadcam.cam.material_state import MaterialState
from hms_cadcam.cam.persistence.models import (
    MaterialStateDependency,
    MaterialStateSuccessorPublication,
)


class RestFinishingLifecycleStatus(StrEnum):
    PREPARED = "PREPARED"
    SUCCESS = "SUCCESS"
    NO_REST_FINISHING_MATERIAL = "NO_REST_FINISHING_MATERIAL"
    FAILURE = "FAILURE"


@dataclass(frozen=True, slots=True)
class RestFinishingLifecycleContext:
    """Current aggregate evidence accepted by the R273 core boundary."""

    setup: Setup
    parameters: RestFinishingParameters
    profile_selection: RestFinishingProfileSelection
    material_candidates: tuple[RestMaterialStateCandidate, ...]
    producer_completion: MaterialStateSuccessorPublication
    producer_dependency: MaterialStateDependency
    producer_parent_state: MaterialState
    producer_validation_certificate: R272ValidatedSuccessorCertificate
    dependency_graph: DependencyGraph
    assembly: ToolAssembly
    assembly_evidence: ToolAssemblyEvidence
    tool: ToolDefinition
    machine: MachineDefinition
    machine_requirement: MachineRequirement
    machine_evidence: MachineEvidence
    consumer_operation_id: OperationId
    profile_resolver: Callable[[GeometryReference], ResolvedContourProfile]
    cancellation: Callable[[], bool] | None = None

    def __post_init__(self) -> None:
        if not callable(self.profile_resolver):
            raise TypeError("Rest Finishing profile resolver must be callable")
        if self.cancellation is not None and not callable(self.cancellation):
            raise TypeError("Rest Finishing cancellation must be callable")

    def geometry_inputs(self) -> RestFinishingGeometryInputs:
        return RestFinishingGeometryInputs(
            self.setup,
            self.parameters,
            self.profile_selection,
            self.material_candidates,
            self.producer_completion,
            self.producer_dependency,
            self.producer_parent_state,
            self.producer_validation_certificate,
            self.dependency_graph,
            self.assembly,
            self.assembly_evidence,
            self.tool,
            self.machine,
            self.machine_requirement,
            self.machine_evidence,
            self.consumer_operation_id,
            self.profile_resolver,
            self.cancellation,
        )


@dataclass(frozen=True, slots=True)
class RestFinishingLifecyclePreparation:
    status: RestFinishingLifecycleStatus
    context: RestFinishingLifecycleContext
    plan: RestFinishingRasterPlan | NoRestFinishingMaterial | None = None
    prepared: RestFinishingPrepared | None = None
    diagnostic_code: RestFinishingDiagnosticCode | None = None
    message: str = ""
    _factory_seal: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_factory_seal", object())
        if self.status is RestFinishingLifecycleStatus.PREPARED:
            valid = (
                isinstance(self.plan, RestFinishingRasterPlan)
                and isinstance(self.prepared, RestFinishingPrepared)
                and self.diagnostic_code is None
                and not self.message
            )
        elif self.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL:
            valid = (
                isinstance(self.plan, NoRestFinishingMaterial)
                and self.prepared is None
                and self.diagnostic_code is None
                and not self.message
            )
        elif self.status is RestFinishingLifecycleStatus.FAILURE:
            valid = (
                self.plan is None
                and self.prepared is None
                and isinstance(self.diagnostic_code, RestFinishingDiagnosticCode)
                and bool(self.message)
            )
        else:
            valid = False
        if not valid:
            raise ValueError("Rest Finishing lifecycle preparation is inconsistent")


@dataclass(frozen=True, slots=True)
class RestFinishingLifecycleResult:
    status: RestFinishingLifecycleStatus
    preparation: RestFinishingLifecyclePreparation
    candidate: RestFinishingCandidate | None = None
    diagnostic_code: RestFinishingDiagnosticCode | None = None
    message: str = ""

    def __post_init__(self) -> None:
        if self.status is RestFinishingLifecycleStatus.SUCCESS:
            valid = (
                self.preparation.status is RestFinishingLifecycleStatus.PREPARED
                and isinstance(self.candidate, RestFinishingCandidate)
                and self.diagnostic_code is None
                and not self.message
            )
        elif self.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL:
            valid = (
                self.preparation.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL
                and self.candidate is None
                and self.diagnostic_code is None
                and not self.message
            )
        elif self.status is RestFinishingLifecycleStatus.FAILURE:
            valid = (
                self.candidate is None
                and isinstance(self.diagnostic_code, RestFinishingDiagnosticCode)
                and bool(self.message)
            )
        else:
            valid = False
        if not valid:
            raise ValueError("Rest Finishing lifecycle result is inconsistent")


def _failure_preparation(
    context: RestFinishingLifecycleContext,
    error: RestFinishingValidationError,
) -> RestFinishingLifecyclePreparation:
    return RestFinishingLifecyclePreparation(
        RestFinishingLifecycleStatus.FAILURE,
        context,
        diagnostic_code=error.code,
        message=str(error),
    )


def _install_lifecycle_boundary():
    lock = threading.RLock()
    records: dict[int, tuple[object, ...]] = {}

    def authority_snapshot(value: object) -> object:
        """Bind value and identity of one complete nested lifecycle authority."""
        seen: dict[int, int] = {}

        def encode(value: object) -> object:
            if type(value) is float:
                return ("float", value.hex())
            if value is None or type(value) in {bool, int, str, bytes}:
                return (type(value).__qualname__, value)
            if isinstance(value, Enum):
                return (type(value).__module__, type(value).__qualname__, value.value)
            if isinstance(value, UUID):
                return ("uuid", str(value))
            identifier = id(value)
            if identifier in seen:
                return ("ref", seen[identifier])
            ordinal = len(seen)
            seen[identifier] = ordinal
            identity = (type(value).__module__, type(value).__qualname__, identifier)
            if is_dataclass(value) and not isinstance(value, type):
                return (
                    "dataclass",
                    identity,
                    tuple(
                        (item.name, encode(getattr(value, item.name)))
                        for item in fields(value)
                    ),
                )
            if isinstance(value, tuple):
                return ("tuple", identity, tuple(encode(item) for item in value))
            if isinstance(value, list):
                return ("list", identity, tuple(encode(item) for item in value))
            if isinstance(value, dict):
                return (
                    "dict",
                    identity,
                    tuple(
                        sorted(
                            ((encode(key), encode(item)) for key, item in value.items()),
                            key=repr,
                        )
                    ),
                )
            if isinstance(value, (set, frozenset)):
                return (
                    "set",
                    identity,
                    tuple(sorted((encode(item) for item in value), key=repr)),
                )
            return ("object", identity)

        return encode(value)

    def snapshot(value: RestFinishingLifecyclePreparation) -> tuple[object, ...]:
        return (
            value,
            value._factory_seal,
            value.status,
            authority_snapshot(value.context),
            id(value.plan),
            getattr(value.plan, "fingerprint", None),
            authority_snapshot(value.plan),
            id(value.prepared),
            getattr(value.prepared, "prepared_fingerprint", None),
            authority_snapshot(value.prepared),
            value.diagnostic_code,
            value.message,
        )

    def register(value: RestFinishingLifecyclePreparation) -> RestFinishingLifecyclePreparation:
        with lock:
            records[id(value)] = snapshot(value)
        return value

    def require(value: RestFinishingLifecyclePreparation) -> None:
        if not isinstance(value, RestFinishingLifecyclePreparation):
            raise TypeError("Rest Finishing lifecycle preparation is invalid")
        with lock:
            record = records.get(id(value))
        if record is None or record != snapshot(value):
            raise RestFinishingValidationError(
                RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                "Rest Finishing lifecycle preparation was not minted by this process",
            )

    def prepare(
        context: RestFinishingLifecycleContext,
    ) -> RestFinishingLifecyclePreparation:
        if not isinstance(context, RestFinishingLifecycleContext):
            raise TypeError("Rest Finishing lifecycle context is invalid")
        try:
            inputs = context.geometry_inputs()
            plan = plan_rest_finishing_geometry(inputs)
            if isinstance(plan, NoRestFinishingMaterial):
                return register(
                    RestFinishingLifecyclePreparation(
                        RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL,
                        context,
                        plan=plan,
                    )
                )
            if not isinstance(plan, RestFinishingRasterPlan):
                raise TypeError("Rest Finishing planner returned an invalid result")
            prepared = prepare_rest_finishing_toolpath(inputs, plan)
            return register(
                RestFinishingLifecyclePreparation(
                    RestFinishingLifecycleStatus.PREPARED,
                    context,
                    plan=plan,
                    prepared=prepared,
                )
            )
        except RestFinishingValidationError as error:
            return register(_failure_preparation(context, error))

    def generate(
        preparation: RestFinishingLifecyclePreparation,
    ) -> RestFinishingLifecycleResult:
        try:
            require(preparation)
            if preparation.status is RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL:
                current = plan_rest_finishing_geometry(
                    preparation.context.geometry_inputs()
                )
                if (
                    not isinstance(current, NoRestFinishingMaterial)
                    or not isinstance(preparation.plan, NoRestFinishingMaterial)
                    or current.fingerprint != preparation.plan.fingerprint
                ):
                    raise RestFinishingValidationError(
                        RestFinishingDiagnosticCode.MATERIAL_STATE_STALE,
                        "Rest Finishing no-work authority drifted before consumption",
                    )
                return RestFinishingLifecycleResult(
                    RestFinishingLifecycleStatus.NO_REST_FINISHING_MATERIAL,
                    preparation,
                )
            if preparation.status is RestFinishingLifecycleStatus.FAILURE:
                assert preparation.diagnostic_code is not None
                return RestFinishingLifecycleResult(
                    RestFinishingLifecycleStatus.FAILURE,
                    preparation,
                    diagnostic_code=preparation.diagnostic_code,
                    message=preparation.message,
                )
            if preparation.prepared is None:
                raise RestFinishingValidationError(
                    RestFinishingDiagnosticCode.MATERIAL_STATE_INVALID,
                    "Rest Finishing prepared payload is absent",
                )
            require_rest_finishing_prepared(preparation.prepared)
            candidate = generate_rest_finishing_toolpath(
                preparation.prepared,
                cancellation=preparation.context.cancellation,
            )
            require_rest_finishing_candidate(
                candidate,
                cancellation=preparation.context.cancellation,
            )
            if (
                preparation.context.cancellation is not None
                and preparation.context.cancellation()
            ):
                raise RestFinishingValidationError(
                    RestFinishingDiagnosticCode.CANCELLED,
                    "Rest Finishing was cancelled before success",
                )
            return RestFinishingLifecycleResult(
                RestFinishingLifecycleStatus.SUCCESS,
                preparation,
                candidate=candidate,
            )
        except RestFinishingValidationError as error:
            return RestFinishingLifecycleResult(
                RestFinishingLifecycleStatus.FAILURE,
                preparation,
                diagnostic_code=error.code,
                message=str(error),
            )

    return prepare, generate


prepare_rest_finishing_3axis, generate_rest_finishing_3axis = _install_lifecycle_boundary()
del _install_lifecycle_boundary


__all__ = [
    "RestFinishingLifecycleContext",
    "RestFinishingLifecyclePreparation",
    "RestFinishingLifecycleResult",
    "RestFinishingLifecycleStatus",
    "generate_rest_finishing_3axis",
    "prepare_rest_finishing_3axis",
]

"""Strict, presentation-only state projection for Stage 9A.7 WP1.

The projector accepts typed immutable evidence only. Legacy/domain values must
first cross :class:`PostAssemblyEvidenceBoundary`, which rejects unknown
values. The module has no Qt, project-service, SQLite, Post, or filesystem
dependency and cannot perform a downstream action.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class ReadinessState(StrEnum):
    MISSING_INPUT = "MISSING_INPUT"
    CALCULATION_REQUIRED = "CALCULATION_REQUIRED"
    SIMULATION_REQUIRED = "SIMULATION_REQUIRED"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    BLOCKED = "BLOCKED"
    READY_TO_GENERATE = "READY_TO_GENERATE"


class GenerationState(StrEnum):
    IDLE = "IDLE"
    GENERATING = "GENERATING"
    GENERATED_CURRENT = "GENERATED_CURRENT"
    GENERATED_STALE = "GENERATED_STALE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ManagedArtifactState(StrEnum):
    MISSING = "MISSING"
    CURRENT = "CURRENT"
    STALE = "STALE"
    TAMPERED = "TAMPERED"
    FAILED = "FAILED"


class ExternalExportState(StrEnum):
    NOT_SELECTED = "NOT_SELECTED"
    READY = "READY"
    EXPORTING = "EXPORTING"
    EXPORTED_CURRENT = "EXPORTED_CURRENT"
    EXPORTED_STALE = "EXPORTED_STALE"
    FAILED = "FAILED"


class ExternalSourceIdentityState(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INCOMPLETE = "INCOMPLETE"
    CURRENT = "CURRENT"
    STALE = "STALE"


class HeadlineState(StrEnum):
    MISSING_INPUT = "MISSING_INPUT"
    CALCULATION_REQUIRED = "CALCULATION_REQUIRED"
    SIMULATION_REQUIRED = "SIMULATION_REQUIRED"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    BLOCKED = "BLOCKED"
    READY_TO_GENERATE = "READY_TO_GENERATE"
    GENERATING = "GENERATING"
    GENERATED_CURRENT = "GENERATED_CURRENT"
    GENERATED_STALE = "GENERATED_STALE"
    SAVE_MANAGED_REQUIRED = "SAVE_MANAGED_REQUIRED"
    MANAGED_CURRENT = "MANAGED_CURRENT"
    MANAGED_STALE = "MANAGED_STALE"
    MANAGED_TAMPERED = "MANAGED_TAMPERED"
    EXTERNAL_READY = "EXTERNAL_READY"
    EXPORTING = "EXPORTING"
    EXPORTED_CURRENT = "EXPORTED_CURRENT"
    EXPORTED_STALE = "EXPORTED_STALE"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class SimulationGatePolicy(StrEnum):
    REQUIRE_PASS = "REQUIRE_PASS"
    ALLOW_WARN = "ALLOW_WARN"
    OPTIONAL = "OPTIONAL"


class SimulationStatus(StrEnum):
    MISSING = "MISSING"
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    STALE = "STALE"
    INVALID = "INVALID"


class OperationArtifactState(StrEnum):
    MISSING = "MISSING"
    CURRENT = "CURRENT"
    STALE = "STALE"
    FAILED = "FAILED"
    CALCULATION_REQUIRED = "CALCULATION_REQUIRED"


class GenerationTerminalState(StrEnum):
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class CallbackDiscardReason(StrEnum):
    INCOMPLETE_IDENTITY = "INCOMPLETE_IDENTITY"
    STALE_ATTEMPT = "STALE_ATTEMPT"
    TERMINAL_ATTEMPT = "TERMINAL_ATTEMPT"


class WorkflowIntent(StrEnum):
    NONE = "NONE"
    MANAGED = "MANAGED"
    EXTERNAL = "EXTERNAL"


class DiagnosticSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class ProjectionDiagnosticCode(StrEnum):
    MISSING_INPUT = "MISSING_INPUT"
    CALCULATION_REQUIRED = "CALCULATION_REQUIRED"
    SIMULATION_REQUIRED = "SIMULATION_REQUIRED"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    CURRENT_FATAL_DIAGNOSTIC = "CURRENT_FATAL_DIAGNOSTIC"
    CURRENT_REQUEST_IDENTITY_MISSING = "CURRENT_REQUEST_IDENTITY_MISSING"
    INCOMPLETE_GENERATION_ATTEMPT = "INCOMPLETE_GENERATION_ATTEMPT"
    STALE_RESULT_CALLBACK = "STALE_RESULT_CALLBACK"
    RESULT_PROVENANCE_MISMATCH = "RESULT_PROVENANCE_MISMATCH"
    MANAGED_IDENTITY_INCOMPLETE = "MANAGED_IDENTITY_INCOMPLETE"
    EXTERNAL_IDENTITY_INCOMPLETE = "EXTERNAL_IDENTITY_INCOMPLETE"
    COMPOUND_SOURCE_MISMATCH = "COMPOUND_SOURCE_MISMATCH"


def _require_enum(value: object, enum_type: type[StrEnum], field: str) -> None:
    if not isinstance(value, enum_type):
        raise TypeError(f"{field} must be {enum_type.__name__}")


def _valid_text(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_length(value: int | None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_sha256(value: str | None) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdefABCDEF" for character in value)


def _require_bool(value: object, field: str) -> None:
    if type(value) is not bool:
        raise TypeError(f"{field} must be bool")


def _require_optional_text(value: object, field: str) -> None:
    if value is not None and not _valid_text(value):
        raise ValueError(f"{field} must not be empty or whitespace")


def _require_optional_non_negative_int(value: object, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be int")
    if value < 0:
        raise ValueError(f"{field} must not be negative")


def _require_non_negative_int(value: object, field: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{field} must be int")
    if value < 0:
        raise ValueError(f"{field} must not be negative")


@dataclass(frozen=True, slots=True)
class DiagnosticEvidence:
    code: str
    severity: DiagnosticSeverity = DiagnosticSeverity.INFO
    summary: str = ""

    def __post_init__(self) -> None:
        if not _valid_text(self.code):
            raise ValueError("diagnostic code must not be empty")
        _require_enum(self.severity, DiagnosticSeverity, "severity")
        if not isinstance(self.summary, str):
            raise TypeError("diagnostic summary must be str")

    @property
    def is_error(self) -> bool:
        return self.severity is DiagnosticSeverity.ERROR


@dataclass(frozen=True, slots=True)
class BlockEvidence:
    code: ProjectionDiagnosticCode
    summary: str

    def __post_init__(self) -> None:
        _require_enum(self.code, ProjectionDiagnosticCode, "code")
        if not _valid_text(self.summary):
            raise ValueError("block evidence summary must not be empty")


@dataclass(frozen=True, slots=True)
class GenerationAttemptIdentity:
    generation_attempt_id: str | None = None
    worker_epoch: int | None = None
    project_generation: int | None = None
    request_fingerprint: str | None = None
    operation_order_fingerprint: str | None = None

    def __post_init__(self) -> None:
        _require_optional_text(self.generation_attempt_id, "generation_attempt_id")
        _require_optional_non_negative_int(self.worker_epoch, "worker_epoch")
        _require_optional_non_negative_int(
            self.project_generation, "project_generation"
        )
        _require_optional_text(self.request_fingerprint, "request_fingerprint")
        _require_optional_text(
            self.operation_order_fingerprint, "operation_order_fingerprint"
        )

    @property
    def is_complete(self) -> bool:
        return (
            _valid_text(self.generation_attempt_id)
            and isinstance(self.worker_epoch, int)
            and not isinstance(self.worker_epoch, bool)
            and self.worker_epoch >= 0
            and isinstance(self.project_generation, int)
            and not isinstance(self.project_generation, bool)
            and self.project_generation >= 0
            and _valid_text(self.request_fingerprint)
            and _valid_text(self.operation_order_fingerprint)
        )

    def matches(self, other: "GenerationAttemptIdentity | None") -> bool:
        return (
            self.is_complete
            and other is not None
            and other.is_complete
            and self == other
        )


@dataclass(frozen=True, slots=True)
class ExternalDispatchSourceIdentity:
    """Content-bound identity of the verified managed source for dispatch."""

    managed_artifact_id: str | None = None
    managed_sha256: str | None = None
    managed_source_checksum: str | None = None
    managed_byte_length: int | None = None
    request_fingerprint: str | None = None
    operation_order_fingerprint: str | None = None
    project_generation: int | None = None
    post_identity: str | None = None
    machine_identity: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "managed_artifact_id",
            "managed_sha256",
            "managed_source_checksum",
            "request_fingerprint",
            "operation_order_fingerprint",
            "post_identity",
            "machine_identity",
        ):
            _require_optional_text(getattr(self, field_name), field_name)
        _require_optional_non_negative_int(
            self.managed_byte_length, "managed_byte_length"
        )
        _require_optional_non_negative_int(
            self.project_generation, "project_generation"
        )

    @property
    def is_complete(self) -> bool:
        return (
            _valid_text(self.managed_artifact_id)
            and _valid_sha256(self.managed_sha256)
            and _valid_sha256(self.managed_source_checksum)
            and _valid_length(self.managed_byte_length)
            and _valid_text(self.request_fingerprint)
            and _valid_text(self.operation_order_fingerprint)
            and isinstance(self.project_generation, int)
            and not isinstance(self.project_generation, bool)
            and self.project_generation >= 0
            and _valid_text(self.post_identity)
            and _valid_text(self.machine_identity)
        )


@dataclass(frozen=True, slots=True)
class ExternalDispatchAttemptIdentity:
    """Identity at the synchronous external service-call boundary."""

    external_dispatch_attempt_id: str | None = None
    project_generation: int | None = None
    request_fingerprint: str | None = None
    target_intent_id: str | None = None
    source_identity: ExternalDispatchSourceIdentity | None = None
    active: bool = False
    failed: bool = False

    def __post_init__(self) -> None:
        _require_optional_text(
            self.external_dispatch_attempt_id, "external_dispatch_attempt_id"
        )
        _require_optional_non_negative_int(
            self.project_generation, "project_generation"
        )
        _require_optional_text(self.request_fingerprint, "request_fingerprint")
        _require_optional_text(self.target_intent_id, "target_intent_id")
        if self.source_identity is not None and not isinstance(
            self.source_identity, ExternalDispatchSourceIdentity
        ):
            raise TypeError(
                "source_identity must be ExternalDispatchSourceIdentity"
            )
        _require_bool(self.active, "external dispatch active")
        _require_bool(self.failed, "external dispatch failed")
        if self.active and self.failed:
            raise ValueError("external dispatch cannot be active and failed")
        if self.source_identity is not None and self.source_identity.is_complete:
            if self.source_identity.project_generation != self.project_generation:
                raise ValueError(
                    "dispatch and source project_generation must match"
                )
            if self.source_identity.request_fingerprint != self.request_fingerprint:
                raise ValueError(
                    "dispatch and source request_fingerprint must match"
                )
        if (self.active or self.failed) and not self.is_complete:
            raise ValueError(
                "active or failed external dispatch requires complete identity"
            )

    @property
    def is_complete(self) -> bool:
        return (
            _valid_text(self.external_dispatch_attempt_id)
            and isinstance(self.project_generation, int)
            and not isinstance(self.project_generation, bool)
            and self.project_generation >= 0
            and _valid_text(self.request_fingerprint)
            and _valid_text(self.target_intent_id)
            and self.source_identity is not None
            and self.source_identity.is_complete
        )


@dataclass(frozen=True, slots=True)
class AcceptedGenerationResultEvidence:
    """Stored result accepted by the generation boundary, never a callback candidate."""

    attempt_identity: GenerationAttemptIdentity | None = None
    result_id: str | None = None
    fingerprint: str | None = None
    byte_length: int | None = None
    sha256: str | None = None
    source_checksum: str | None = None
    request_fingerprint: str | None = None
    operation_order_fingerprint: str | None = None
    project_generation: int | None = None
    post_identity: str | None = None
    machine_identity: str | None = None
    published: bool = False

    def __post_init__(self) -> None:
        if self.attempt_identity is not None and not isinstance(
            self.attempt_identity, GenerationAttemptIdentity
        ):
            raise TypeError(
                "result attempt_identity must be GenerationAttemptIdentity"
            )
        for field_name in (
            "result_id",
            "fingerprint",
            "sha256",
            "source_checksum",
            "request_fingerprint",
            "operation_order_fingerprint",
            "post_identity",
            "machine_identity",
        ):
            _require_optional_text(getattr(self, field_name), field_name)
        _require_optional_non_negative_int(self.byte_length, "byte_length")
        _require_optional_non_negative_int(
            self.project_generation, "project_generation"
        )
        _require_bool(self.published, "published")
        if self.attempt_identity is not None:
            consistency_pairs = (
                (
                    "project_generation",
                    self.attempt_identity.project_generation,
                    self.project_generation,
                ),
                (
                    "request_fingerprint",
                    self.attempt_identity.request_fingerprint,
                    self.request_fingerprint,
                ),
                (
                    "operation_order_fingerprint",
                    self.attempt_identity.operation_order_fingerprint,
                    self.operation_order_fingerprint,
                ),
            )
            for field_name, attempt_value, result_value in consistency_pairs:
                if result_value is not None and attempt_value != result_value:
                    raise ValueError(
                        f"result {field_name} must match attempt provenance"
                    )

    @property
    def has_evidence(self) -> bool:
        return any(
            (
                self.attempt_identity is not None,
                self.published,
                self.result_id is not None,
                self.fingerprint is not None,
            )
        )

    @property
    def identity_is_complete(self) -> bool:
        return (
            self.attempt_identity is not None
            and self.attempt_identity.is_complete
            and _valid_text(self.result_id)
            and _valid_text(self.fingerprint)
            and _valid_length(self.byte_length)
            and _valid_sha256(self.sha256)
            and _valid_sha256(self.source_checksum)
            and _valid_text(self.request_fingerprint)
            and _valid_text(self.operation_order_fingerprint)
            and isinstance(self.project_generation, int)
            and not isinstance(self.project_generation, bool)
            and self.project_generation >= 0
            and _valid_text(self.post_identity)
            and _valid_text(self.machine_identity)
            and self.attempt_provenance_is_consistent
        )

    @property
    def attempt_provenance_is_consistent(self) -> bool:
        attempt = self.attempt_identity
        return bool(
            attempt is not None
            and attempt.is_complete
            and attempt.project_generation == self.project_generation
            and attempt.request_fingerprint == self.request_fingerprint
            and attempt.operation_order_fingerprint
            == self.operation_order_fingerprint
        )


@dataclass(frozen=True, slots=True)
class GenerationCallbackAuditEvidence:
    """Immutable callback audit; it cannot replace an accepted result."""

    callback_attempt_identity: GenerationAttemptIdentity | None = None
    received_count: int = 0
    discarded_count: int = 0
    published_count: int = 0
    artifact_write_count: int = 0
    ui_mutation_count: int = 0
    selection_mutation_count: int = 0
    project_mutation_count: int = 0
    discard_reason: CallbackDiscardReason | None = None

    def __post_init__(self) -> None:
        if self.callback_attempt_identity is not None and not isinstance(
            self.callback_attempt_identity, GenerationAttemptIdentity
        ):
            raise TypeError(
                "callback_attempt_identity must be GenerationAttemptIdentity"
            )
        for field_name in (
            "received_count",
            "discarded_count",
            "published_count",
            "artifact_write_count",
            "ui_mutation_count",
            "selection_mutation_count",
            "project_mutation_count",
        ):
            _require_non_negative_int(getattr(self, field_name), field_name)
        if self.discard_reason is not None:
            _require_enum(
                self.discard_reason, CallbackDiscardReason, "discard_reason"
            )
        if self.discarded_count > self.received_count:
            raise ValueError("discarded_count cannot exceed received_count")
        if self.published_count > self.received_count:
            raise ValueError("published_count cannot exceed received_count")
        if self.published_count + self.discarded_count > self.received_count:
            raise ValueError(
                "published_count plus discarded_count cannot exceed received_count"
            )
        if self.artifact_write_count > self.published_count:
            raise ValueError("artifact_write_count cannot exceed published_count")
        if self.artifact_write_count:
            raise ValueError("generation callbacks cannot write artifacts")
        if self.selection_mutation_count:
            raise ValueError("generation callbacks cannot mutate selection")
        if self.published_count > 1:
            raise ValueError("a generation attempt can publish at most one result")
        for field_name in (
            "ui_mutation_count",
            "selection_mutation_count",
            "project_mutation_count",
        ):
            if getattr(self, field_name) > self.published_count:
                raise ValueError(f"{field_name} cannot exceed published_count")
        if self.discarded_count and self.discard_reason is None:
            raise ValueError("discarded callbacks require a discard_reason")
        if not self.discarded_count and self.discard_reason is not None:
            raise ValueError("discard_reason requires discarded callbacks")
        if self.discarded_count == self.received_count and any(
            (
                self.published_count,
                self.artifact_write_count,
                self.ui_mutation_count,
                self.selection_mutation_count,
                self.project_mutation_count,
            )
        ):
            raise ValueError("fully discarded callbacks cannot report side effects")

@dataclass(frozen=True, slots=True)
class ManagedArtifactEvidence:
    artifact_id: str | None = None
    manifest_entry_id: str | None = None
    present: bool = False
    readback_verified: bool = False
    tampered: bool = False
    failed: bool = False
    source_checksum: str | None = None
    byte_length: int | None = None
    sha256: str | None = None
    request_fingerprint: str | None = None
    operation_order_fingerprint: str | None = None
    project_generation: int | None = None
    post_identity: str | None = None
    machine_identity: str | None = None
    explicit_state: ManagedArtifactState | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "artifact_id",
            "manifest_entry_id",
            "source_checksum",
            "sha256",
            "request_fingerprint",
            "operation_order_fingerprint",
            "post_identity",
            "machine_identity",
        ):
            _require_optional_text(getattr(self, field_name), field_name)
        _require_optional_non_negative_int(self.byte_length, "byte_length")
        _require_optional_non_negative_int(
            self.project_generation, "project_generation"
        )
        for field_name in ("present", "readback_verified", "tampered", "failed"):
            _require_bool(getattr(self, field_name), f"managed {field_name}")
        if self.explicit_state is not None:
            _require_enum(
                self.explicit_state, ManagedArtifactState, "managed explicit_state"
            )
        if self.explicit_state is ManagedArtifactState.CURRENT:
            raise ValueError("managed explicit CURRENT is not authoritative")


@dataclass(frozen=True, slots=True)
class ExternalArtifactEvidence:
    artifact_id: str | None = None
    provenance_identity: str | None = None
    source_artifact_id: str | None = None
    present: bool = False
    readback_verified: bool = False
    target_intent_id: str | None = None
    target_path: str | None = None
    byte_length: int | None = None
    sha256: str | None = None
    source_checksum: str | None = None
    request_fingerprint: str | None = None
    operation_order_fingerprint: str | None = None
    project_generation: int | None = None
    post_identity: str | None = None
    machine_identity: str | None = None
    explicit_stale: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "artifact_id",
            "provenance_identity",
            "source_artifact_id",
            "target_intent_id",
            "target_path",
            "sha256",
            "source_checksum",
            "request_fingerprint",
            "operation_order_fingerprint",
            "post_identity",
            "machine_identity",
        ):
            _require_optional_text(getattr(self, field_name), field_name)
        _require_optional_non_negative_int(self.byte_length, "byte_length")
        _require_optional_non_negative_int(
            self.project_generation, "project_generation"
        )
        _require_bool(self.present, "external present")
        _require_bool(self.readback_verified, "external readback_verified")
        _require_bool(self.explicit_stale, "external explicit_stale")


@dataclass(frozen=True, slots=True)
class PostAssemblyProjectionInput:
    """Typed immutable evidence captured by a boundary adapter."""

    project_id: str | UUID | None = None
    project_generation: int | None = None
    dirty_state: bool = False

    operation_ids: tuple[str, ...] = ()
    operation_order_fingerprint: str | None = None
    operation_enabled: bool = True
    operation_missing: bool = False
    operation_artifact_state: OperationArtifactState = OperationArtifactState.CURRENT
    operation_artifact_fingerprint: str | None = None

    simulation_status: SimulationStatus = SimulationStatus.MISSING
    simulation_gate_policy: SimulationGatePolicy = SimulationGatePolicy.OPTIONAL
    simulation_result_fingerprint: str | None = None
    simulation_diagnostics: tuple[DiagnosticEvidence, ...] = ()

    generation_attempt: GenerationAttemptIdentity | None = None
    generation_active: bool = False
    generation_terminal_state: GenerationTerminalState | None = None
    generation_terminal_attempt: GenerationAttemptIdentity | None = None
    accepted_generation_result: AcceptedGenerationResultEvidence | None = None
    generation_callback_audit: GenerationCallbackAuditEvidence | None = None

    managed_artifact: ManagedArtifactEvidence | None = None

    external_target_intent_id: str | None = None
    external_target_path: str | None = None
    current_external_target_path: str | None = None
    external_dispatch: ExternalDispatchAttemptIdentity | None = None
    external_artifact: ExternalArtifactEvidence | None = None

    active_intent: WorkflowIntent = WorkflowIntent.NONE
    current_request_fingerprint: str | None = None
    current_source_checksum: str | None = None
    current_post_identity: str | None = None
    current_machine_identity: str | None = None
    current_diagnostics: tuple[DiagnosticEvidence, ...] = ()
    upstream_readiness_blocked: bool = False
    external_confirmation_rejected_count: int = 0

    def __post_init__(self) -> None:
        _require_enum(
            self.operation_artifact_state,
            OperationArtifactState,
            "operation_artifact_state",
        )
        _require_enum(self.simulation_status, SimulationStatus, "simulation_status")
        _require_enum(
            self.simulation_gate_policy,
            SimulationGatePolicy,
            "simulation_gate_policy",
        )
        _require_enum(self.active_intent, WorkflowIntent, "active_intent")
        if self.generation_terminal_state is not None:
            _require_enum(
                self.generation_terminal_state,
                GenerationTerminalState,
                "generation_terminal_state",
            )
        if self.generation_attempt is not None and not isinstance(
            self.generation_attempt, GenerationAttemptIdentity
        ):
            raise TypeError("generation_attempt must be GenerationAttemptIdentity")
        if self.generation_terminal_attempt is not None and not isinstance(
            self.generation_terminal_attempt, GenerationAttemptIdentity
        ):
            raise TypeError(
                "generation_terminal_attempt must be GenerationAttemptIdentity"
            )
        if self.accepted_generation_result is not None and not isinstance(
            self.accepted_generation_result, AcceptedGenerationResultEvidence
        ):
            raise TypeError(
                "accepted_generation_result must be "
                "AcceptedGenerationResultEvidence"
            )
        if self.generation_callback_audit is not None and not isinstance(
            self.generation_callback_audit, GenerationCallbackAuditEvidence
        ):
            raise TypeError(
                "generation_callback_audit must be "
                "GenerationCallbackAuditEvidence"
            )
        if self.managed_artifact is not None and not isinstance(
            self.managed_artifact, ManagedArtifactEvidence
        ):
            raise TypeError("managed_artifact must be ManagedArtifactEvidence")
        if self.external_dispatch is not None and not isinstance(
            self.external_dispatch, ExternalDispatchAttemptIdentity
        ):
            raise TypeError(
                "external_dispatch must be ExternalDispatchAttemptIdentity"
            )
        if self.external_artifact is not None and not isinstance(
            self.external_artifact, ExternalArtifactEvidence
        ):
            raise TypeError("external_artifact must be ExternalArtifactEvidence")
        if isinstance(self.operation_ids, (str, bytes)):
            raise TypeError("operation_ids must be an iterable of strings")
        operation_ids = tuple(self.operation_ids)
        if any(not _valid_text(item) for item in operation_ids):
            raise ValueError("operation_ids must contain non-empty strings")
        if len(set(operation_ids)) != len(operation_ids):
            raise ValueError("operation_ids must not contain duplicates")
        if isinstance(self.simulation_diagnostics, (str, bytes)):
            raise TypeError(
                "simulation_diagnostics must contain DiagnosticEvidence"
            )
        if isinstance(self.current_diagnostics, (str, bytes)):
            raise TypeError("current_diagnostics must contain DiagnosticEvidence")
        simulation_diagnostics = tuple(self.simulation_diagnostics)
        current_diagnostics = tuple(self.current_diagnostics)
        if any(
            not isinstance(item, DiagnosticEvidence)
            for item in simulation_diagnostics
        ):
            raise TypeError(
                "simulation_diagnostics must contain DiagnosticEvidence"
            )
        if any(
            not isinstance(item, DiagnosticEvidence) for item in current_diagnostics
        ):
            raise TypeError("current_diagnostics must contain DiagnosticEvidence")
        object.__setattr__(self, "operation_ids", operation_ids)
        object.__setattr__(self, "simulation_diagnostics", simulation_diagnostics)
        object.__setattr__(self, "current_diagnostics", current_diagnostics)
        if isinstance(self.project_id, str):
            _require_optional_text(self.project_id, "project_id")
        elif self.project_id is not None and not isinstance(self.project_id, UUID):
            raise TypeError("project_id must be str, UUID, or None")
        _require_optional_non_negative_int(
            self.project_generation, "project_generation"
        )
        _require_non_negative_int(
            self.external_confirmation_rejected_count,
            "external_confirmation_rejected_count",
        )
        for field_name in (
            "dirty_state",
            "operation_enabled",
            "operation_missing",
            "generation_active",
            "upstream_readiness_blocked",
        ):
            _require_bool(getattr(self, field_name), field_name)
        for field_name in (
            "operation_order_fingerprint",
            "operation_artifact_fingerprint",
            "simulation_result_fingerprint",
            "external_target_intent_id",
            "external_target_path",
            "current_external_target_path",
            "current_request_fingerprint",
            "current_source_checksum",
            "current_post_identity",
            "current_machine_identity",
        ):
            _require_optional_text(getattr(self, field_name), field_name)
        if self.generation_active and (
            self.generation_attempt is None or not self.generation_attempt.is_complete
        ):
            raise ValueError(
                "generation_active requires a complete GenerationAttemptIdentity"
            )
        if self.generation_active and not generation_attempt_matches_current_source(
            self.generation_attempt, self
        ):
            raise ValueError(
                "generation_active attempt must match current source identity"
            )
        if self.generation_terminal_state is not None and (
            self.generation_terminal_attempt is None
            or not self.generation_terminal_attempt.is_complete
        ):
            raise ValueError(
                "generation terminal evidence requires a complete attempt identity"
            )
        audit = self.generation_callback_audit
        if audit is not None and audit.received_count:
            callback = audit.callback_attempt_identity
            current = self.generation_attempt
            current_matches_source = generation_attempt_matches_current_source(
                current, self
            )
            current_terminal = (
                self.generation_terminal_state is not None
                and current_matches_source
                and current is not None
                and current.matches(self.generation_terminal_attempt)
            )
            if callback is None or not callback.is_complete:
                expected_reason = CallbackDiscardReason.INCOMPLETE_IDENTITY
            elif current_terminal and current is not None and current.matches(callback):
                expected_reason = CallbackDiscardReason.TERMINAL_ATTEMPT
            elif (
                not current_matches_source
                or current is None
                or not current.matches(callback)
            ):
                expected_reason = CallbackDiscardReason.STALE_ATTEMPT
            else:
                expected_reason = None
            if expected_reason is None and audit.discarded_count:
                raise ValueError("current callback cannot be reported as discarded")
            if expected_reason is not None:
                if audit.discarded_count != audit.received_count:
                    raise ValueError("stale callbacks must all be discarded")
                if audit.discard_reason is not expected_reason:
                    raise ValueError("callback discard_reason does not match identity")
            if audit.published_count:
                result = self.accepted_generation_result
                if expected_reason is not None:
                    raise ValueError("stale callbacks cannot publish a result")
                if (
                    callback is None
                    or result is None
                    or not result.published
                    or not result.identity_is_complete
                    or result.attempt_identity is None
                    or not result.attempt_identity.matches(callback)
                    or not _accepted_result_current_for_source(self, result)
                ):
                    raise ValueError(
                        "published callback requires its accepted current result"
                    )


@dataclass(frozen=True, slots=True)
class PostAssemblyProjection:
    readiness_state: ReadinessState
    generation_state: GenerationState
    managed_artifact_state: ManagedArtifactState
    external_export_state: ExternalExportState
    headline_state: HeadlineState
    current_generation_attempt: GenerationAttemptIdentity | None = None
    current_external_dispatch_attempt_id: str | None = None
    active_intent: WorkflowIntent = WorkflowIntent.NONE
    presentation_readiness_blocked: bool = False
    block_evidence: tuple[BlockEvidence, ...] = ()
    diagnostic_summary: str = ""
    diagnostics: tuple[DiagnosticEvidence, ...] = ()
    source_fingerprints: tuple[tuple[str, str], ...] = ()
    target_intent_id: str | None = None
    stale_callback_discarded: bool = False
    stale_callback_received_count: int = 0
    stale_callback_discarded_count: int = 0
    callback_published_count: int = 0
    callback_artifact_write_count: int = 0
    callback_ui_mutation_count: int = 0
    callback_selection_mutation_count: int = 0
    callback_project_mutation_count: int = 0
    accepted_generation_result: AcceptedGenerationResultEvidence | None = None
    accepted_result_available: bool = False
    accepted_result_id: str | None = None
    accepted_result_fingerprint: str | None = None
    accepted_result_sha256: str | None = None
    accepted_result_current_for_source: bool = False
    preview_runtime_result_available: bool = False
    managed_source_ready: bool = False
    external_source_identity_state: ExternalSourceIdentityState = (
        ExternalSourceIdentityState.NOT_APPLICABLE
    )
    project_dirty_state: bool = False
    external_confirmation_rejected_count: int = 0
    automatic_downstream_action_count: int = 0

    @property
    def readiness(self) -> ReadinessState:
        return self.readiness_state

    @property
    def generation(self) -> GenerationState:
        return self.generation_state

    @property
    def managed(self) -> ManagedArtifactState:
        return self.managed_artifact_state

    @property
    def external(self) -> ExternalExportState:
        return self.external_export_state

    @property
    def headline(self) -> HeadlineState:
        return self.headline_state

    @property
    def attempt_identity(self) -> GenerationAttemptIdentity | None:
        return self.current_generation_attempt

    @property
    def external_dispatch_attempt_id(self) -> str | None:
        return self.current_external_dispatch_attempt_id


class PostAssemblyEvidenceBoundary:
    """Explicit legacy/domain-to-presentation enum boundary."""

    @staticmethod
    def _parse(
        value: object,
        enum_type: type[StrEnum],
        *,
        aliases: dict[str, str] | None = None,
    ) -> StrEnum:
        raw = value.value if isinstance(value, StrEnum) else value
        if not isinstance(raw, str):
            raise ValueError(f"cannot map {value!r} to {enum_type.__name__}")
        normalized = raw.strip().upper()
        normalized = (aliases or {}).get(normalized, normalized)
        try:
            return enum_type(normalized)
        except ValueError as error:
            raise ValueError(
                f"unknown {enum_type.__name__} value: {raw!r}"
            ) from error

    @classmethod
    def simulation_status(cls, value: object) -> SimulationStatus:
        return cls._parse(
            value,
            SimulationStatus,
            aliases={"FAILED": "FAIL", "WARNING": "WARN"},
        )  # type: ignore[return-value]

    @classmethod
    def simulation_gate_policy(cls, value: object) -> SimulationGatePolicy:
        return cls._parse(value, SimulationGatePolicy)  # type: ignore[return-value]

    @classmethod
    def operation_artifact_state(cls, value: object) -> OperationArtifactState:
        return cls._parse(value, OperationArtifactState)  # type: ignore[return-value]

    @classmethod
    def workflow_intent(cls, value: object) -> WorkflowIntent:
        return cls._parse(value, WorkflowIntent)  # type: ignore[return-value]

    @classmethod
    def managed_state(cls, value: object) -> ManagedArtifactState:
        return cls._parse(value, ManagedArtifactState)  # type: ignore[return-value]

    @classmethod
    def generation_terminal_state(cls, value: object) -> GenerationTerminalState:
        return cls._parse(value, GenerationTerminalState)  # type: ignore[return-value]

    @staticmethod
    def external_dispatch_attempt(
        *,
        external_dispatch_attempt_id: str | None = None,
        external_export_attempt_id: str | None = None,
        project_generation: int | None = None,
        request_fingerprint: str | None = None,
        target_intent_id: str | None = None,
        source_identity: ExternalDispatchSourceIdentity | None = None,
        active: bool = False,
        failed: bool = False,
    ) -> ExternalDispatchAttemptIdentity:
        """Map the legacy alias once; the typed core keeps one canonical ID."""

        if (
            external_dispatch_attempt_id is not None
            and external_export_attempt_id is not None
            and external_dispatch_attempt_id != external_export_attempt_id
        ):
            raise ValueError("conflicting external dispatch attempt IDs")
        canonical_id = (
            external_dispatch_attempt_id
            if external_dispatch_attempt_id is not None
            else external_export_attempt_id
        )
        return ExternalDispatchAttemptIdentity(
            external_dispatch_attempt_id=canonical_id,
            project_generation=project_generation,
            request_fingerprint=request_fingerprint,
            target_intent_id=target_intent_id,
            source_identity=source_identity,
            active=active,
            failed=failed,
        )


def _expected_request(evidence: PostAssemblyProjectionInput) -> str | None:
    return evidence.current_request_fingerprint


def generation_attempt_matches_current_source(
    attempt: GenerationAttemptIdentity | None,
    evidence: PostAssemblyProjectionInput,
) -> bool:
    """Return whether a complete attempt belongs to the current source identity."""

    return bool(
        attempt is not None
        and attempt.is_complete
        and isinstance(evidence.project_generation, int)
        and not isinstance(evidence.project_generation, bool)
        and attempt.project_generation == evidence.project_generation
        and _valid_text(evidence.current_request_fingerprint)
        and attempt.request_fingerprint == evidence.current_request_fingerprint
        and _valid_text(evidence.operation_order_fingerprint)
        and attempt.operation_order_fingerprint
        == evidence.operation_order_fingerprint
    )


def _generation_source_identity_complete(
    evidence: PostAssemblyProjectionInput,
) -> bool:
    return (
        _valid_text(_expected_request(evidence))
        and _valid_text(evidence.operation_order_fingerprint)
        and isinstance(evidence.project_generation, int)
        and not isinstance(evidence.project_generation, bool)
        and evidence.project_generation >= 0
        and _valid_sha256(evidence.current_source_checksum)
        and _valid_text(evidence.current_post_identity)
        and _valid_text(evidence.current_machine_identity)
    )


def _project_readiness(evidence: PostAssemblyProjectionInput) -> ReadinessState:
    if (
        evidence.project_id is None
        or evidence.project_generation is None
        or not evidence.operation_ids
    ):
        return ReadinessState.MISSING_INPUT
    diagnostics = (*evidence.simulation_diagnostics, *evidence.current_diagnostics)
    if evidence.upstream_readiness_blocked or any(
        item.is_error for item in diagnostics
    ):
        return ReadinessState.BLOCKED
    if (
        not evidence.operation_enabled
        or evidence.operation_missing
        or evidence.operation_artifact_state
        is not OperationArtifactState.CURRENT
        or not _valid_text(evidence.operation_artifact_fingerprint)
    ):
        return ReadinessState.CALCULATION_REQUIRED

    policy = evidence.simulation_gate_policy
    status = evidence.simulation_status
    if status is SimulationStatus.MISSING:
        if policy in {
            SimulationGatePolicy.REQUIRE_PASS,
            SimulationGatePolicy.ALLOW_WARN,
        }:
            return ReadinessState.SIMULATION_REQUIRED
    elif status is SimulationStatus.WARN:
        if policy is SimulationGatePolicy.REQUIRE_PASS:
            return ReadinessState.SIMULATION_FAILED
    elif status in {
        SimulationStatus.FAIL,
        SimulationStatus.STALE,
        SimulationStatus.INVALID,
    }:
        return ReadinessState.SIMULATION_FAILED
    if status in {SimulationStatus.PASS, SimulationStatus.WARN} and not _valid_text(
        evidence.simulation_result_fingerprint
    ):
        return ReadinessState.SIMULATION_FAILED

    if not _generation_source_identity_complete(evidence):
        return ReadinessState.BLOCKED
    return ReadinessState.READY_TO_GENERATE


def _accepted_result_current_for_source(
    evidence: PostAssemblyProjectionInput,
    result: AcceptedGenerationResultEvidence,
) -> bool:
    return (
        result.identity_is_complete
        and result.published
        and result.request_fingerprint == _expected_request(evidence)
        and result.operation_order_fingerprint
        == evidence.operation_order_fingerprint
        and result.project_generation == evidence.project_generation
        and result.source_checksum == evidence.current_source_checksum
        and result.post_identity == evidence.current_post_identity
        and result.machine_identity == evidence.current_machine_identity
    )


def _project_generation(
    evidence: PostAssemblyProjectionInput,
) -> GenerationState:
    current = evidence.generation_attempt
    current_matches_source = generation_attempt_matches_current_source(
        current, evidence
    )
    if evidence.generation_active and current_matches_source:
        return GenerationState.GENERATING
    if (
        evidence.generation_terminal_state is not None
        and current_matches_source
        and current is not None
        and current.matches(evidence.generation_terminal_attempt)
    ):
        return (
            GenerationState.CANCELLED
            if evidence.generation_terminal_state
            is GenerationTerminalState.CANCELLED
            else GenerationState.FAILED
        )

    result = evidence.accepted_generation_result
    if result is None or not result.has_evidence or not result.published:
        return GenerationState.IDLE
    if _accepted_result_current_for_source(evidence, result):
        return GenerationState.GENERATED_CURRENT
    return GenerationState.GENERATED_STALE


def _managed_matches_current(
    evidence: PostAssemblyProjectionInput,
    artifact: ManagedArtifactEvidence,
) -> bool:
    result = evidence.accepted_generation_result
    return (
        result is not None
        and _accepted_result_current_for_source(evidence, result)
        and artifact.present
        and artifact.readback_verified
        and _valid_text(artifact.artifact_id)
        and _valid_text(artifact.manifest_entry_id)
        and _valid_length(artifact.byte_length)
        and _valid_sha256(artifact.sha256)
        and _valid_sha256(artifact.source_checksum)
        and artifact.request_fingerprint == _expected_request(evidence)
        and artifact.operation_order_fingerprint
        == evidence.operation_order_fingerprint
        and artifact.project_generation == evidence.project_generation
        and artifact.source_checksum == result.source_checksum
        and artifact.byte_length == result.byte_length
        and artifact.sha256 == result.sha256
        and artifact.post_identity == evidence.current_post_identity
        and artifact.machine_identity == evidence.current_machine_identity
    )


def _project_managed(evidence: PostAssemblyProjectionInput) -> ManagedArtifactState:
    artifact = evidence.managed_artifact
    if (
        artifact is None
        or not artifact.present
        or artifact.explicit_state is ManagedArtifactState.MISSING
    ):
        return ManagedArtifactState.MISSING
    if artifact.failed or artifact.explicit_state is ManagedArtifactState.FAILED:
        return ManagedArtifactState.FAILED
    if artifact.tampered or artifact.explicit_state is ManagedArtifactState.TAMPERED:
        return ManagedArtifactState.TAMPERED
    if artifact.explicit_state is ManagedArtifactState.STALE:
        return ManagedArtifactState.STALE
    if _managed_matches_current(evidence, artifact):
        return ManagedArtifactState.CURRENT
    return ManagedArtifactState.STALE


def _dispatch_source_matches_managed(
    evidence: PostAssemblyProjectionInput,
    source: ExternalDispatchSourceIdentity,
    managed: ManagedArtifactEvidence | None,
    managed_state: ManagedArtifactState,
) -> bool:
    return (
        source.is_complete
        and managed is not None
        and managed_state is ManagedArtifactState.CURRENT
        and source.managed_artifact_id == managed.artifact_id
        and source.managed_sha256 == managed.sha256
        and source.managed_source_checksum == managed.source_checksum
        and source.managed_byte_length == managed.byte_length
        and source.request_fingerprint == managed.request_fingerprint
        and source.operation_order_fingerprint
        == managed.operation_order_fingerprint
        and source.project_generation == managed.project_generation
        and source.post_identity == managed.post_identity
        and source.machine_identity == managed.machine_identity
        and source.request_fingerprint == _expected_request(evidence)
        and source.operation_order_fingerprint
        == evidence.operation_order_fingerprint
        and source.project_generation == evidence.project_generation
        and source.post_identity == evidence.current_post_identity
        and source.machine_identity == evidence.current_machine_identity
    )


def _dispatch_matches_current(
    evidence: PostAssemblyProjectionInput,
    dispatch: ExternalDispatchAttemptIdentity,
    managed: ManagedArtifactEvidence | None,
    managed_state: ManagedArtifactState,
) -> bool:
    source = dispatch.source_identity
    return (
        dispatch.is_complete
        and source is not None
        and _dispatch_source_matches_managed(
            evidence, source, managed, managed_state
        )
        and dispatch.project_generation == evidence.project_generation
        and dispatch.request_fingerprint == _expected_request(evidence)
        and dispatch.target_intent_id == evidence.external_target_intent_id
    )


def _external_source_identity_state(
    evidence: PostAssemblyProjectionInput,
    managed_state: ManagedArtifactState,
) -> ExternalSourceIdentityState:
    dispatch = evidence.external_dispatch
    if dispatch is None:
        return ExternalSourceIdentityState.NOT_APPLICABLE
    source = dispatch.source_identity
    if source is None or not source.is_complete:
        return ExternalSourceIdentityState.INCOMPLETE
    if _dispatch_source_matches_managed(
        evidence, source, evidence.managed_artifact, managed_state
    ):
        return ExternalSourceIdentityState.CURRENT
    return ExternalSourceIdentityState.STALE


def _external_matches_current(
    evidence: PostAssemblyProjectionInput,
    artifact: ExternalArtifactEvidence,
    managed: ManagedArtifactEvidence | None,
    managed_state: ManagedArtifactState,
) -> bool:
    return (
        managed is not None
        and managed_state is ManagedArtifactState.CURRENT
        and artifact.present
        and artifact.readback_verified
        and _valid_text(artifact.artifact_id)
        and _valid_text(artifact.provenance_identity)
        and artifact.source_artifact_id == managed.artifact_id
        and artifact.target_intent_id == evidence.external_target_intent_id
        and _valid_text(artifact.target_path)
        and artifact.target_path == evidence.external_target_path
        and artifact.target_path == evidence.current_external_target_path
        and _valid_length(artifact.byte_length)
        and artifact.byte_length == managed.byte_length
        and _valid_sha256(artifact.sha256)
        and artifact.sha256 == managed.sha256
        and _valid_sha256(artifact.source_checksum)
        and artifact.source_checksum == managed.source_checksum
        and artifact.request_fingerprint == _expected_request(evidence)
        and artifact.operation_order_fingerprint
        == evidence.operation_order_fingerprint
        and artifact.project_generation == evidence.project_generation
        and artifact.post_identity == evidence.current_post_identity
        and artifact.machine_identity == evidence.current_machine_identity
        and not artifact.explicit_stale
    )


def _project_external(
    evidence: PostAssemblyProjectionInput,
    managed_state: ManagedArtifactState,
) -> tuple[ExternalExportState, str | None, ExternalSourceIdentityState]:
    source_state = _external_source_identity_state(evidence, managed_state)
    if not _valid_text(evidence.external_target_intent_id):
        return ExternalExportState.NOT_SELECTED, None, source_state
    dispatch = evidence.external_dispatch
    dispatch_current = (
        dispatch is not None
        and _dispatch_matches_current(
            evidence, dispatch, evidence.managed_artifact, managed_state
        )
    )
    if dispatch_current and dispatch.active:
        return (
            ExternalExportState.EXPORTING,
            dispatch.external_dispatch_attempt_id,
            source_state,
        )
    if dispatch_current and dispatch.failed:
        return (
            ExternalExportState.FAILED,
            dispatch.external_dispatch_attempt_id,
            source_state,
        )
    artifact = evidence.external_artifact
    if artifact is not None and artifact.present:
        if _external_matches_current(
            evidence, artifact, evidence.managed_artifact, managed_state
        ):
            return ExternalExportState.EXPORTED_CURRENT, None, source_state
        return ExternalExportState.EXPORTED_STALE, None, source_state
    return ExternalExportState.READY, None, source_state


def _compound_source_equal(
    evidence: PostAssemblyProjectionInput,
    generation_state: GenerationState,
    managed_state: ManagedArtifactState,
) -> bool:
    result = evidence.accepted_generation_result
    managed = evidence.managed_artifact
    if (
        generation_state is not GenerationState.GENERATED_CURRENT
        or managed_state is not ManagedArtifactState.CURRENT
        or result is None
        or managed is None
        or not _accepted_result_current_for_source(evidence, result)
        or not _managed_matches_current(evidence, managed)
    ):
        return False
    required_pairs: tuple[tuple[object, object], ...] = (
        (result.byte_length, managed.byte_length),
        (result.sha256, managed.sha256),
        (result.source_checksum, managed.source_checksum),
        (result.request_fingerprint, managed.request_fingerprint),
        (
            result.operation_order_fingerprint,
            managed.operation_order_fingerprint,
        ),
        (result.project_generation, managed.project_generation),
        (result.post_identity, managed.post_identity),
        (result.machine_identity, managed.machine_identity),
    )
    return all(left is not None and right is not None and left == right for left, right in required_pairs)


def _project_headline(
    evidence: PostAssemblyProjectionInput,
    readiness: ReadinessState,
    generation: GenerationState,
    managed: ManagedArtifactState,
    external: ExternalExportState,
) -> HeadlineState:
    if readiness is not ReadinessState.READY_TO_GENERATE:
        return HeadlineState[readiness.value]
    if generation is GenerationState.GENERATING:
        return HeadlineState.GENERATING
    if generation is GenerationState.FAILED or external is ExternalExportState.FAILED:
        return HeadlineState.FAILED
    if generation is GenerationState.CANCELLED:
        return HeadlineState.CANCELLED

    target_intent = (
        evidence.active_intent is WorkflowIntent.EXTERNAL
        or _valid_text(evidence.external_target_intent_id)
    )
    managed_intent = evidence.active_intent is WorkflowIntent.MANAGED

    if target_intent or managed_intent:
        if managed is ManagedArtifactState.MISSING:
            if generation is GenerationState.GENERATED_CURRENT:
                return HeadlineState.SAVE_MANAGED_REQUIRED
            if generation is GenerationState.GENERATED_STALE:
                return HeadlineState.GENERATED_STALE
            return HeadlineState.READY_TO_GENERATE
        if managed is ManagedArtifactState.STALE:
            return HeadlineState.MANAGED_STALE
        if managed is ManagedArtifactState.TAMPERED:
            return HeadlineState.MANAGED_TAMPERED
        if managed is ManagedArtifactState.FAILED:
            return HeadlineState.FAILED

    if target_intent and managed is ManagedArtifactState.CURRENT:
        if external is ExternalExportState.EXPORTING:
            return HeadlineState.EXPORTING
        if external is ExternalExportState.EXPORTED_CURRENT:
            return HeadlineState.EXPORTED_CURRENT
        if external is ExternalExportState.EXPORTED_STALE:
            return HeadlineState.EXPORTED_STALE
        if external is ExternalExportState.READY and _compound_source_equal(
            evidence, generation, managed
        ):
            return HeadlineState.EXTERNAL_READY
        return HeadlineState.MANAGED_CURRENT

    if managed_intent and managed is ManagedArtifactState.CURRENT:
        return HeadlineState.MANAGED_CURRENT
    if generation is GenerationState.GENERATED_CURRENT:
        return HeadlineState.GENERATED_CURRENT
    if generation is GenerationState.GENERATED_STALE:
        return HeadlineState.GENERATED_STALE
    return HeadlineState.READY_TO_GENERATE


def _block_evidence(
    evidence: PostAssemblyProjectionInput,
    readiness: ReadinessState,
    generation: GenerationState,
    managed: ManagedArtifactState,
    external: ExternalExportState,
    stale_callback: bool,
) -> tuple[BlockEvidence, ...]:
    items: list[BlockEvidence] = []
    if readiness is ReadinessState.MISSING_INPUT:
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.MISSING_INPUT,
                "Project or operation identity is missing.",
            )
        )
    elif readiness is ReadinessState.CALCULATION_REQUIRED:
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.CALCULATION_REQUIRED,
                "A current operation artifact is required.",
            )
        )
    elif readiness is ReadinessState.SIMULATION_REQUIRED:
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.SIMULATION_REQUIRED,
                "Current Simulation evidence is required by policy.",
            )
        )
    elif readiness is ReadinessState.SIMULATION_FAILED:
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.SIMULATION_FAILED,
                "Simulation evidence is not permitted by policy.",
            )
        )
    elif readiness is ReadinessState.BLOCKED:
        if not _valid_text(evidence.current_request_fingerprint):
            items.append(
                BlockEvidence(
                    ProjectionDiagnosticCode.CURRENT_REQUEST_IDENTITY_MISSING,
                    "Current request identity is missing.",
                )
            )
        else:
            items.append(
                BlockEvidence(
                    ProjectionDiagnosticCode.CURRENT_FATAL_DIAGNOSTIC,
                    "A current fatal diagnostic or incomplete identity blocks readiness.",
                )
            )
    if stale_callback:
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.STALE_RESULT_CALLBACK,
                "Generation result callback identity is not current and was discarded.",
            )
        )
    if generation is GenerationState.GENERATED_STALE:
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.RESULT_PROVENANCE_MISMATCH,
                "Generation result provenance is incomplete or stale.",
            )
        )
    if managed is ManagedArtifactState.STALE:
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.MANAGED_IDENTITY_INCOMPLETE,
                "Managed read-back identity is incomplete or stale; Save Managed again.",
            )
        )
    if external is ExternalExportState.EXPORTED_STALE:
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.EXTERNAL_IDENTITY_INCOMPLETE,
                "External read-back identity is incomplete or stale.",
            )
        )
    if (
        _valid_text(evidence.external_target_intent_id)
        and generation is GenerationState.GENERATED_CURRENT
        and managed is ManagedArtifactState.CURRENT
        and not _compound_source_equal(evidence, generation, managed)
    ):
        items.append(
            BlockEvidence(
                ProjectionDiagnosticCode.COMPOUND_SOURCE_MISMATCH,
                "Generated and managed identities differ; Save Managed again.",
            )
        )
    return tuple(items)


def project_post_assembly(
    evidence: PostAssemblyProjectionInput,
) -> PostAssemblyProjection:
    """Recompute the complete WP1 projection from current typed evidence."""

    readiness = _project_readiness(evidence)
    generation = _project_generation(evidence)
    callback_audit = evidence.generation_callback_audit
    stale_callback = bool(
        callback_audit is not None and callback_audit.discarded_count
    )
    managed = _project_managed(evidence)
    external, external_attempt_id, external_source_state = _project_external(
        evidence, managed
    )
    blocks = _block_evidence(
        evidence, readiness, generation, managed, external, stale_callback
    )
    diagnostics = (*evidence.simulation_diagnostics, *evidence.current_diagnostics)
    summary = (
        blocks[0].summary
        if blocks
        else next(
            (item.summary or item.code for item in diagnostics if item.is_error),
            next((item.summary or item.code for item in diagnostics), ""),
        )
    )
    accepted_result = evidence.accepted_generation_result
    accepted_available = bool(
        accepted_result is not None
        and accepted_result.has_evidence
        and accepted_result.published
    )
    accepted_current = bool(
        accepted_result is not None
        and _accepted_result_current_for_source(evidence, accepted_result)
    )
    fingerprints = {
        "accepted_result_attempt_id": (
            accepted_result.attempt_identity.generation_attempt_id
            if accepted_result is not None
            and accepted_result.attempt_identity is not None
            else None
        ),
        "accepted_result_fingerprint": (
            accepted_result.fingerprint if accepted_result is not None else None
        ),
        "accepted_result_id": (
            accepted_result.result_id if accepted_result is not None else None
        ),
        "accepted_result_sha256": (
            accepted_result.sha256 if accepted_result is not None else None
        ),
        "machine_identity": evidence.current_machine_identity,
        "operation_order_fingerprint": evidence.operation_order_fingerprint,
        "post_identity": evidence.current_post_identity,
        "project_generation": (
            str(evidence.project_generation)
            if evidence.project_generation is not None
            else None
        ),
        "request_fingerprint": _expected_request(evidence),
        "source_checksum": evidence.current_source_checksum,
        "target_path": evidence.current_external_target_path,
    }
    return PostAssemblyProjection(
        readiness_state=readiness,
        generation_state=generation,
        managed_artifact_state=managed,
        external_export_state=external,
        headline_state=_project_headline(
            evidence, readiness, generation, managed, external
        ),
        current_generation_attempt=(
            evidence.generation_attempt
            if generation_attempt_matches_current_source(
                evidence.generation_attempt, evidence
            )
            else None
        ),
        current_external_dispatch_attempt_id=external_attempt_id,
        active_intent=evidence.active_intent,
        presentation_readiness_blocked=readiness is ReadinessState.BLOCKED,
        block_evidence=blocks,
        diagnostic_summary=summary,
        diagnostics=diagnostics,
        source_fingerprints=tuple(
            sorted(
                (key, value)
                for key, value in fingerprints.items()
                if _valid_text(value)
            )
        ),
        target_intent_id=evidence.external_target_intent_id,
        stale_callback_discarded=stale_callback,
        stale_callback_received_count=(
            callback_audit.received_count if callback_audit is not None else 0
        ),
        stale_callback_discarded_count=(
            callback_audit.discarded_count if callback_audit is not None else 0
        ),
        callback_published_count=(
            callback_audit.published_count if callback_audit is not None else 0
        ),
        callback_artifact_write_count=(
            callback_audit.artifact_write_count
            if callback_audit is not None
            else 0
        ),
        callback_ui_mutation_count=(
            callback_audit.ui_mutation_count if callback_audit is not None else 0
        ),
        callback_selection_mutation_count=(
            callback_audit.selection_mutation_count
            if callback_audit is not None
            else 0
        ),
        callback_project_mutation_count=(
            callback_audit.project_mutation_count
            if callback_audit is not None
            else 0
        ),
        accepted_generation_result=accepted_result,
        accepted_result_available=accepted_available,
        accepted_result_id=(
            accepted_result.result_id if accepted_result is not None else None
        ),
        accepted_result_fingerprint=(
            accepted_result.fingerprint if accepted_result is not None else None
        ),
        accepted_result_sha256=(
            accepted_result.sha256 if accepted_result is not None else None
        ),
        accepted_result_current_for_source=accepted_current,
        preview_runtime_result_available=accepted_available and accepted_current,
        managed_source_ready=managed is ManagedArtifactState.CURRENT,
        external_source_identity_state=external_source_state,
        project_dirty_state=evidence.dirty_state,
        external_confirmation_rejected_count=(
            evidence.external_confirmation_rejected_count
        ),
        automatic_downstream_action_count=(
            callback_audit.artifact_write_count
            + callback_audit.selection_mutation_count
            if callback_audit is not None
            else 0
        ),
    )


class PostAssemblyProjector:
    def project(self, evidence: PostAssemblyProjectionInput) -> PostAssemblyProjection:
        return project_post_assembly(evidence)


def is_current_generation_attempt(
    evidence: PostAssemblyProjectionInput,
    attempt: GenerationAttemptIdentity,
) -> bool:
    current = evidence.generation_attempt
    return (
        current is not None
        and generation_attempt_matches_current_source(current, evidence)
        and attempt.is_complete
        and current.matches(attempt)
    )


def discard_stale_generation_callback(
    evidence: PostAssemblyProjectionInput,
    callback_attempt: GenerationAttemptIdentity,
) -> bool:
    if not is_current_generation_attempt(evidence, callback_attempt):
        return True
    current = evidence.generation_attempt
    return bool(
        evidence.generation_terminal_state is not None
        and generation_attempt_matches_current_source(current, evidence)
        and current is not None
        and current.matches(evidence.generation_terminal_attempt)
    )


__all__ = [
    "AcceptedGenerationResultEvidence",
    "BlockEvidence",
    "CallbackDiscardReason",
    "DiagnosticEvidence",
    "DiagnosticSeverity",
    "ExternalArtifactEvidence",
    "ExternalDispatchAttemptIdentity",
    "ExternalDispatchSourceIdentity",
    "ExternalExportState",
    "ExternalSourceIdentityState",
    "GenerationAttemptIdentity",
    "GenerationCallbackAuditEvidence",
    "GenerationState",
    "GenerationTerminalState",
    "generation_attempt_matches_current_source",
    "HeadlineState",
    "ManagedArtifactEvidence",
    "ManagedArtifactState",
    "OperationArtifactState",
    "PostAssemblyEvidenceBoundary",
    "PostAssemblyProjection",
    "PostAssemblyProjectionInput",
    "PostAssemblyProjector",
    "ProjectionDiagnosticCode",
    "ReadinessState",
    "SimulationGatePolicy",
    "SimulationStatus",
    "WorkflowIntent",
    "discard_stale_generation_callback",
    "is_current_generation_attempt",
    "project_post_assembly",
]

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, fields, replace

import pytest

from hms_cadcam.ui.post_assembly_projection import (
    AcceptedGenerationResultEvidence,
    BlockEvidence,
    CallbackDiscardReason,
    DiagnosticEvidence,
    DiagnosticSeverity,
    ExternalArtifactEvidence,
    ExternalDispatchAttemptIdentity,
    ExternalDispatchSourceIdentity,
    ExternalExportState,
    ExternalSourceIdentityState,
    GenerationAttemptIdentity,
    GenerationCallbackAuditEvidence,
    GenerationState,
    GenerationTerminalState,
    HeadlineState,
    ManagedArtifactEvidence,
    ManagedArtifactState,
    OperationArtifactState,
    PostAssemblyEvidenceBoundary,
    PostAssemblyProjectionInput,
    ProjectionDiagnosticCode,
    ReadinessState,
    SimulationGatePolicy,
    SimulationStatus,
    WorkflowIntent,
    discard_stale_generation_callback,
    generation_attempt_matches_current_source,
    is_current_generation_attempt,
    project_post_assembly,
)


PAYLOAD_SHA = "a" * 64
OTHER_SHA = "b" * 64
SOURCE_SHA = "c" * 64
OTHER_SOURCE_SHA = "d" * 64
REQUEST = "request-a"
ORDER = "order-a"
POST = "robodrill-21i"
MACHINE = "machine-a"
TARGET_ID = "target-1"
TARGET_PATH = r"D:\NC\PROGRAM.FN"


def _attempt(**changes: object) -> GenerationAttemptIdentity:
    values: dict[str, object] = {
        "generation_attempt_id": "generation-1",
        "worker_epoch": 3,
        "project_generation": 4,
        "request_fingerprint": REQUEST,
        "operation_order_fingerprint": ORDER,
    }
    values.update(changes)
    return GenerationAttemptIdentity(**values)


def _ready(**changes: object) -> PostAssemblyProjectionInput:
    values: dict[str, object] = {
        "project_id": "project-1",
        "project_generation": 4,
        "operation_ids": ("op-1",),
        "operation_order_fingerprint": ORDER,
        "operation_artifact_state": OperationArtifactState.CURRENT,
        "operation_artifact_fingerprint": "operation-artifact-a",
        "simulation_status": SimulationStatus.PASS,
        "simulation_gate_policy": SimulationGatePolicy.REQUIRE_PASS,
        "simulation_result_fingerprint": "simulation-result-a",
        "current_request_fingerprint": REQUEST,
        "current_source_checksum": SOURCE_SHA,
        "current_post_identity": POST,
        "current_machine_identity": MACHINE,
    }
    values.update(changes)
    return PostAssemblyProjectionInput(**values)


def _result(
    attempt: GenerationAttemptIdentity | None = None,
    **changes: object,
) -> AcceptedGenerationResultEvidence:
    values: dict[str, object] = {
        "attempt_identity": attempt or _attempt(),
        "result_id": "result-1",
        "fingerprint": "result-fingerprint",
        "byte_length": 128,
        "sha256": PAYLOAD_SHA,
        "source_checksum": SOURCE_SHA,
        "request_fingerprint": REQUEST,
        "operation_order_fingerprint": ORDER,
        "project_generation": 4,
        "post_identity": POST,
        "machine_identity": MACHINE,
        "published": True,
    }
    values.update(changes)
    return AcceptedGenerationResultEvidence(**values)


def _discarded_callback(
    attempt: GenerationAttemptIdentity,
) -> GenerationCallbackAuditEvidence:
    reason = (
        CallbackDiscardReason.INCOMPLETE_IDENTITY
        if not attempt.is_complete
        else CallbackDiscardReason.STALE_ATTEMPT
    )
    return GenerationCallbackAuditEvidence(
        callback_attempt_identity=attempt,
        received_count=1,
        discarded_count=1,
        discard_reason=reason,
    )


def _managed(**changes: object) -> ManagedArtifactEvidence:
    values: dict[str, object] = {
        "artifact_id": "managed-1",
        "manifest_entry_id": "manifest-1",
        "present": True,
        "readback_verified": True,
        "source_checksum": SOURCE_SHA,
        "byte_length": 128,
        "sha256": PAYLOAD_SHA,
        "request_fingerprint": REQUEST,
        "operation_order_fingerprint": ORDER,
        "project_generation": 4,
        "post_identity": POST,
        "machine_identity": MACHINE,
    }
    values.update(changes)
    return ManagedArtifactEvidence(**values)


def _external(**changes: object) -> ExternalArtifactEvidence:
    values: dict[str, object] = {
        "artifact_id": "external-1",
        "provenance_identity": "external-provenance-1",
        "source_artifact_id": "managed-1",
        "present": True,
        "readback_verified": True,
        "target_intent_id": TARGET_ID,
        "target_path": TARGET_PATH,
        "byte_length": 128,
        "sha256": PAYLOAD_SHA,
        "source_checksum": SOURCE_SHA,
        "request_fingerprint": REQUEST,
        "operation_order_fingerprint": ORDER,
        "project_generation": 4,
        "post_identity": POST,
        "machine_identity": MACHINE,
    }
    values.update(changes)
    return ExternalArtifactEvidence(**values)


def _dispatch_source(**changes: object) -> ExternalDispatchSourceIdentity:
    values: dict[str, object] = {
        "managed_artifact_id": "managed-1",
        "managed_sha256": PAYLOAD_SHA,
        "managed_source_checksum": SOURCE_SHA,
        "managed_byte_length": 128,
        "request_fingerprint": REQUEST,
        "operation_order_fingerprint": ORDER,
        "project_generation": 4,
        "post_identity": POST,
        "machine_identity": MACHINE,
    }
    values.update(changes)
    return ExternalDispatchSourceIdentity(**values)


def _generated_input(**changes: object) -> PostAssemblyProjectionInput:
    values: dict[str, object] = {
        "generation_attempt": _attempt(),
        "accepted_generation_result": _result(),
    }
    values.update(changes)
    return _ready(**values)


def _external_input(**changes: object) -> PostAssemblyProjectionInput:
    values: dict[str, object] = {
        "generation_attempt": _attempt(),
        "accepted_generation_result": _result(),
        "managed_artifact": _managed(),
        "external_target_intent_id": TARGET_ID,
        "external_target_path": TARGET_PATH,
        "current_external_target_path": TARGET_PATH,
        "active_intent": WorkflowIntent.EXTERNAL,
    }
    values.update(changes)
    return _ready(**values)


def test_enum_ids_stable_and_evidence_is_immutable() -> None:
    assert [item.value for item in SimulationGatePolicy] == [
        "REQUIRE_PASS",
        "ALLOW_WARN",
        "OPTIONAL",
    ]
    assert [item.value for item in SimulationStatus] == [
        "MISSING",
        "PASS",
        "WARN",
        "FAIL",
        "STALE",
        "INVALID",
    ]
    assert len(HeadlineState) == 19
    evidence = _ready()
    with pytest.raises(FrozenInstanceError):
        evidence.project_generation = 5  # type: ignore[misc]
    projection = project_post_assembly(evidence)
    with pytest.raises(FrozenInstanceError):
        projection.headline_state = HeadlineState.FAILED  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("simulation_status", "PASS"),
        ("simulation_gate_policy", "ALLOW_WARN"),
        ("operation_artifact_state", "CURRENT"),
        ("active_intent", "NONE"),
        ("generation_terminal_state", "CANCELLED"),
    ],
)
def test_projector_input_rejects_stringly_typed_states(
    field: str, value: str
) -> None:
    with pytest.raises(TypeError):
        _ready(**{field: value})


def test_managed_evidence_rejects_stringly_explicit_state() -> None:
    with pytest.raises(TypeError):
        ManagedArtifactEvidence(present=True, explicit_state="CURRENT")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("adapter", "invalid"),
    [
        (PostAssemblyEvidenceBoundary.simulation_status, "GREEN"),
        (PostAssemblyEvidenceBoundary.simulation_gate_policy, "RELAXED"),
        (PostAssemblyEvidenceBoundary.workflow_intent, "AUTO_EXPORT"),
        (PostAssemblyEvidenceBoundary.managed_state, "VERIFIED"),
        (PostAssemblyEvidenceBoundary.generation_terminal_state, "DONE"),
    ],
)
def test_legacy_boundary_rejects_invalid_values(adapter, invalid: str) -> None:
    with pytest.raises(ValueError, match="unknown"):
        adapter(invalid)


def test_legacy_boundary_maps_known_domain_aliases() -> None:
    assert (
        PostAssemblyEvidenceBoundary.simulation_status("FAILED")
        is SimulationStatus.FAIL
    )
    assert (
        PostAssemblyEvidenceBoundary.simulation_status("warning")
        is SimulationStatus.WARN
    )


@pytest.mark.parametrize(
    ("policy", "status", "expected"),
    [
        (
            SimulationGatePolicy.REQUIRE_PASS,
            SimulationStatus.MISSING,
            ReadinessState.SIMULATION_REQUIRED,
        ),
        (
            SimulationGatePolicy.REQUIRE_PASS,
            SimulationStatus.PASS,
            ReadinessState.READY_TO_GENERATE,
        ),
        (
            SimulationGatePolicy.REQUIRE_PASS,
            SimulationStatus.WARN,
            ReadinessState.SIMULATION_FAILED,
        ),
        (
            SimulationGatePolicy.REQUIRE_PASS,
            SimulationStatus.FAIL,
            ReadinessState.SIMULATION_FAILED,
        ),
        (
            SimulationGatePolicy.REQUIRE_PASS,
            SimulationStatus.STALE,
            ReadinessState.SIMULATION_FAILED,
        ),
        (
            SimulationGatePolicy.REQUIRE_PASS,
            SimulationStatus.INVALID,
            ReadinessState.SIMULATION_FAILED,
        ),
        (
            SimulationGatePolicy.ALLOW_WARN,
            SimulationStatus.MISSING,
            ReadinessState.SIMULATION_REQUIRED,
        ),
        (
            SimulationGatePolicy.ALLOW_WARN,
            SimulationStatus.PASS,
            ReadinessState.READY_TO_GENERATE,
        ),
        (
            SimulationGatePolicy.ALLOW_WARN,
            SimulationStatus.WARN,
            ReadinessState.READY_TO_GENERATE,
        ),
        (
            SimulationGatePolicy.ALLOW_WARN,
            SimulationStatus.FAIL,
            ReadinessState.SIMULATION_FAILED,
        ),
        (
            SimulationGatePolicy.OPTIONAL,
            SimulationStatus.MISSING,
            ReadinessState.READY_TO_GENERATE,
        ),
        (
            SimulationGatePolicy.OPTIONAL,
            SimulationStatus.PASS,
            ReadinessState.READY_TO_GENERATE,
        ),
        (
            SimulationGatePolicy.OPTIONAL,
            SimulationStatus.WARN,
            ReadinessState.READY_TO_GENERATE,
        ),
        (
            SimulationGatePolicy.OPTIONAL,
            SimulationStatus.FAIL,
            ReadinessState.SIMULATION_FAILED,
        ),
        (
            SimulationGatePolicy.OPTIONAL,
            SimulationStatus.STALE,
            ReadinessState.SIMULATION_FAILED,
        ),
        (
            SimulationGatePolicy.OPTIONAL,
            SimulationStatus.INVALID,
            ReadinessState.SIMULATION_FAILED,
        ),
    ],
)
def test_simulation_gate_policy_truth_table(
    policy: SimulationGatePolicy,
    status: SimulationStatus,
    expected: ReadinessState,
) -> None:
    projection = project_post_assembly(
        _ready(simulation_gate_policy=policy, simulation_status=status)
    )
    assert projection.readiness_state is expected
    assert projection.automatic_downstream_action_count == 0


def test_operation_and_simulation_current_states_require_fingerprints() -> None:
    assert (
        project_post_assembly(
            _ready(operation_artifact_fingerprint=None)
        ).readiness_state
        is ReadinessState.CALCULATION_REQUIRED
    )


def test_initial_valid_workflow_is_ready_without_generated_payload() -> None:
    input_fields = {item.name for item in fields(PostAssemblyProjectionInput)}
    assert "current_payload_byte_length" not in input_fields
    assert "current_payload_sha256" not in input_fields
    projection = project_post_assembly(_ready())
    assert projection.readiness_state is ReadinessState.READY_TO_GENERATE
    assert projection.generation_state is GenerationState.IDLE
    assert projection.headline_state is HeadlineState.READY_TO_GENERATE
    assert not projection.accepted_result_available


def test_no_current_result_cannot_claim_save_managed_or_external_ready() -> None:
    managed_intent = project_post_assembly(
        _ready(active_intent=WorkflowIntent.MANAGED)
    )
    external_intent = project_post_assembly(
        _external_input(
            generation_attempt=None,
            accepted_generation_result=None,
        )
    )
    assert managed_intent.headline_state is HeadlineState.READY_TO_GENERATE
    assert managed_intent.headline_state is not HeadlineState.SAVE_MANAGED_REQUIRED
    assert external_intent.managed_artifact_state is ManagedArtifactState.STALE
    assert external_intent.headline_state is not HeadlineState.EXTERNAL_READY
    assert (
        project_post_assembly(
            _ready(simulation_result_fingerprint=None)
        ).readiness_state
        is ReadinessState.SIMULATION_FAILED
    )


def test_current_fatal_diagnostic_wins_over_simulation_failure_and_active_attempt() -> None:
    projection = project_post_assembly(
        _ready(
            simulation_status=SimulationStatus.FAIL,
            generation_attempt=_attempt(),
            generation_active=True,
            current_diagnostics=(
                DiagnosticEvidence(
                    "post.profile.missing",
                    DiagnosticSeverity.ERROR,
                    "Post profile missing",
                ),
            ),
        )
    )
    assert projection.readiness_state is ReadinessState.BLOCKED
    assert projection.headline_state is HeadlineState.BLOCKED
    assert projection.block_evidence[0].code is (
        ProjectionDiagnosticCode.CURRENT_FATAL_DIAGNOSTIC
    )


@pytest.mark.parametrize(
    "attempt",
    [
        GenerationAttemptIdentity(),
        _attempt(generation_attempt_id=None),
        _attempt(worker_epoch=None),
        _attempt(project_generation=None),
        _attempt(request_fingerprint=None),
        _attempt(operation_order_fingerprint=None),
    ],
)
def test_incomplete_generation_attempt_never_matches(
    attempt: GenerationAttemptIdentity,
) -> None:
    assert not attempt.is_complete
    assert not attempt.matches(attempt)
    assert not is_current_generation_attempt(
        _ready(generation_attempt=attempt), attempt
    )


@pytest.mark.parametrize(
    "attempt",
    [
        GenerationAttemptIdentity(),
        _attempt(worker_epoch=None),
        _attempt(request_fingerprint=None),
        _attempt(operation_order_fingerprint=None),
        _attempt(project_generation=None),
    ],
)
def test_incomplete_active_attempt_is_rejected(
    attempt: GenerationAttemptIdentity,
) -> None:
    with pytest.raises(ValueError, match="complete"):
        _ready(generation_attempt=attempt, generation_active=True)


def test_incomplete_terminal_attempt_is_rejected() -> None:
    with pytest.raises(ValueError, match="complete"):
        _ready(
            generation_attempt=_attempt(),
            generation_terminal_state=GenerationTerminalState.CANCELLED,
            generation_terminal_attempt=GenerationAttemptIdentity(),
        )


@pytest.mark.parametrize(
    "old",
    [
        _attempt(generation_attempt_id="old"),
        _attempt(worker_epoch=2),
        _attempt(project_generation=3),
        _attempt(request_fingerprint="old-request"),
        _attempt(operation_order_fingerprint="old-order"),
    ],
)
def test_generation_identity_requires_exact_five_field_match(
    old: GenerationAttemptIdentity,
) -> None:
    current = _attempt()
    evidence = _ready(generation_attempt=current)
    assert not current.matches(old)
    assert discard_stale_generation_callback(evidence, old)


def test_current_request_is_the_only_request_authority() -> None:
    attempt = _attempt(request_fingerprint="historical-request")
    projection = project_post_assembly(
        _ready(
            current_request_fingerprint=None,
            generation_attempt=attempt,
        )
    )

    assert projection.readiness_state is ReadinessState.BLOCKED
    assert projection.generation_state is GenerationState.IDLE
    assert projection.headline_state is HeadlineState.BLOCKED
    assert projection.current_generation_attempt is None
    assert dict(projection.source_fingerprints).get("request_fingerprint") is None
    assert projection.block_evidence[0].code is (
        ProjectionDiagnosticCode.CURRENT_REQUEST_IDENTITY_MISSING
    )


@pytest.mark.parametrize(
    "attempt",
    [
        _attempt(project_generation=3),
        _attempt(request_fingerprint="old-request"),
        _attempt(operation_order_fingerprint="old-order"),
    ],
)
def test_generation_attempt_must_match_current_source(
    attempt: GenerationAttemptIdentity,
) -> None:
    evidence = _ready(generation_attempt=attempt)

    assert not generation_attempt_matches_current_source(attempt, evidence)
    assert not is_current_generation_attempt(evidence, attempt)
    assert discard_stale_generation_callback(evidence, attempt)


def test_exact_generation_attempt_matches_current_source() -> None:
    attempt = _attempt()
    evidence = _ready(generation_attempt=attempt)

    assert generation_attempt_matches_current_source(attempt, evidence)
    assert is_current_generation_attempt(evidence, attempt)


@pytest.mark.parametrize(
    "attempt",
    [
        _attempt(project_generation=3),
        _attempt(request_fingerprint="old-request"),
        _attempt(operation_order_fingerprint="old-order"),
    ],
)
def test_stale_active_attempt_is_rejected(
    attempt: GenerationAttemptIdentity,
) -> None:
    with pytest.raises(ValueError, match="current source"):
        _ready(generation_attempt=attempt, generation_active=True)


@pytest.mark.parametrize(
    ("terminal", "attempt"),
    [
        (GenerationTerminalState.CANCELLED, _attempt(project_generation=3)),
        (GenerationTerminalState.FAILED, _attempt(request_fingerprint="old-request")),
        (
            GenerationTerminalState.CANCELLED,
            _attempt(operation_order_fingerprint="old-order"),
        ),
    ],
)
def test_stale_terminal_attempt_is_ignored(
    terminal: GenerationTerminalState,
    attempt: GenerationAttemptIdentity,
) -> None:
    projection = project_post_assembly(
        _ready(
            generation_attempt=attempt,
            generation_terminal_state=terminal,
            generation_terminal_attempt=attempt,
        )
    )

    assert projection.generation_state is GenerationState.IDLE
    assert projection.headline_state is HeadlineState.READY_TO_GENERATE


def test_missing_current_request_cannot_activate_or_terminalize_attempt() -> None:
    current = _attempt()
    with pytest.raises(ValueError, match="current source"):
        _ready(
            current_request_fingerprint=None,
            generation_attempt=current,
            generation_active=True,
        )

    projection = project_post_assembly(
        _ready(
            current_request_fingerprint=None,
            generation_attempt=current,
            generation_terminal_state=GenerationTerminalState.CANCELLED,
            generation_terminal_attempt=current,
        )
    )
    assert projection.generation_state is GenerationState.IDLE


def test_missing_current_request_makes_accepted_result_stale() -> None:
    projection = project_post_assembly(
        _generated_input(current_request_fingerprint=None)
    )

    assert projection.readiness_state is ReadinessState.BLOCKED
    assert projection.generation_state is GenerationState.GENERATED_STALE
    assert not projection.accepted_result_current_for_source


def test_current_active_and_terminal_generation_states() -> None:
    current = _attempt()
    assert (
        project_post_assembly(
            _ready(generation_attempt=current, generation_active=True)
        ).generation_state
        is GenerationState.GENERATING
    )
    for terminal, expected in (
        (GenerationTerminalState.CANCELLED, GenerationState.CANCELLED),
        (GenerationTerminalState.FAILED, GenerationState.FAILED),
    ):
        projection = project_post_assembly(
            _ready(
                generation_attempt=current,
                generation_terminal_state=terminal,
                generation_terminal_attempt=current,
            )
        )
        assert projection.generation_state is expected


def test_old_terminal_does_not_mask_current_attempt() -> None:
    projection = project_post_assembly(
        _ready(
            generation_attempt=_attempt(generation_attempt_id="new"),
            generation_terminal_state=GenerationTerminalState.CANCELLED,
            generation_terminal_attempt=_attempt(generation_attempt_id="old"),
        )
    )
    assert projection.generation_state is GenerationState.IDLE


@pytest.mark.parametrize(
    "old_attempt",
    [
        GenerationAttemptIdentity(),
        _attempt(generation_attempt_id="old"),
        _attempt(worker_epoch=2),
        _attempt(project_generation=3),
        _attempt(request_fingerprint="old-request"),
        _attempt(operation_order_fingerprint="old-order"),
    ],
)
def test_old_or_incomplete_result_callback_is_discarded_even_with_current_fingerprints(
    old_attempt: GenerationAttemptIdentity,
) -> None:
    before_evidence = _generated_input(managed_artifact=_managed())
    before = project_post_assembly(before_evidence)
    after = project_post_assembly(
        replace(
            before_evidence,
            generation_callback_audit=_discarded_callback(old_attempt),
        )
    )
    assert (
        after.readiness_state,
        after.generation_state,
        after.managed_artifact_state,
        after.external_export_state,
        after.headline_state,
        after.source_fingerprints,
        after.accepted_result_id,
    ) == (
        before.readiness_state,
        before.generation_state,
        before.managed_artifact_state,
        before.external_export_state,
        before.headline_state,
        before.source_fingerprints,
        before.accepted_result_id,
    )
    assert after.generation_state is GenerationState.GENERATED_CURRENT
    assert after.stale_callback_discarded
    assert after.stale_callback_received_count == 1
    assert after.stale_callback_discarded_count == 1
    assert after.callback_published_count == 0
    assert after.callback_artifact_write_count == 0
    assert after.callback_ui_mutation_count == 0
    assert after.callback_selection_mutation_count == 0
    assert after.callback_project_mutation_count == 0
    assert after.automatic_downstream_action_count == 0
    assert any(
        item.code is ProjectionDiagnosticCode.STALE_RESULT_CALLBACK
        for item in after.block_evidence
    )


def test_current_callback_audit_does_not_replace_accepted_result() -> None:
    evidence = _generated_input()
    before = project_post_assembly(evidence)
    after = project_post_assembly(
        replace(
            evidence,
            generation_callback_audit=GenerationCallbackAuditEvidence(
                callback_attempt_identity=_attempt(),
                received_count=1,
            ),
        )
    )
    assert after.accepted_generation_result is before.accepted_generation_result
    assert after.generation_state is before.generation_state
    assert after.headline_state is before.headline_state
    assert not after.stale_callback_discarded


def test_published_callback_requires_accepted_result() -> None:
    with pytest.raises(ValueError, match="accepted current result"):
        _ready(
            generation_attempt=_attempt(),
            generation_callback_audit=GenerationCallbackAuditEvidence(
                callback_attempt_identity=_attempt(),
                received_count=1,
                published_count=1,
            ),
        )


def test_published_callback_rejects_result_attempt_mismatch() -> None:
    result_attempt = _attempt(generation_attempt_id="other-attempt")
    with pytest.raises(ValueError, match="accepted current result"):
        _ready(
            generation_attempt=_attempt(),
            accepted_generation_result=_result(result_attempt),
            generation_callback_audit=GenerationCallbackAuditEvidence(
                callback_attempt_identity=_attempt(),
                received_count=1,
                published_count=1,
            ),
        )


def test_published_callback_rejects_incomplete_result() -> None:
    with pytest.raises(ValueError, match="accepted current result"):
        _ready(
            generation_attempt=_attempt(),
            accepted_generation_result=_result(result_id=None),
            generation_callback_audit=GenerationCallbackAuditEvidence(
                callback_attempt_identity=_attempt(),
                received_count=1,
                published_count=1,
            ),
        )


def test_published_callback_rejects_attempt_stale_against_current_source() -> None:
    stale = _attempt(request_fingerprint="old-request")
    stale_result = _result(
        stale,
        request_fingerprint="old-request",
    )
    with pytest.raises(ValueError, match="all be discarded"):
        _ready(
            generation_attempt=stale,
            accepted_generation_result=stale_result,
            generation_callback_audit=GenerationCallbackAuditEvidence(
                callback_attempt_identity=stale,
                received_count=1,
                published_count=1,
            ),
        )


def test_current_published_callback_is_bound_to_exact_accepted_result() -> None:
    current = _attempt()
    projection = project_post_assembly(
        _ready(
            generation_attempt=current,
            accepted_generation_result=_result(current),
            generation_callback_audit=GenerationCallbackAuditEvidence(
                callback_attempt_identity=current,
                received_count=1,
                published_count=1,
                ui_mutation_count=1,
                project_mutation_count=1,
            ),
        )
    )

    assert projection.generation_state is GenerationState.GENERATED_CURRENT
    assert projection.callback_published_count == 1
    assert projection.callback_artifact_write_count == 0
    assert projection.callback_selection_mutation_count == 0
    assert projection.automatic_downstream_action_count == 0


def test_complete_current_result_is_generated_current() -> None:
    projection = project_post_assembly(_generated_input())
    assert projection.generation_state is GenerationState.GENERATED_CURRENT
    assert projection.headline_state is HeadlineState.GENERATED_CURRENT


@pytest.mark.parametrize(
    "result",
    [
        _result(result_id=None),
        _result(fingerprint=None),
        _result(byte_length=None),
        _result(sha256=None),
        _result(source_checksum=None),
        _result(request_fingerprint=None),
        _result(
            _attempt(request_fingerprint="other"),
            request_fingerprint="other",
        ),
        _result(operation_order_fingerprint=None),
        _result(
            _attempt(operation_order_fingerprint="other"),
            operation_order_fingerprint="other",
        ),
        _result(project_generation=None),
        _result(
            _attempt(project_generation=3),
            project_generation=3,
        ),
        _result(post_identity=None),
        _result(post_identity="other"),
        _result(machine_identity=None),
        _result(machine_identity="other"),
    ],
)
def test_current_attempt_result_with_incomplete_or_stale_provenance_is_stale(
    result: AcceptedGenerationResultEvidence,
) -> None:
    projection = project_post_assembly(
        _ready(generation_attempt=_attempt(), accepted_generation_result=result)
    )
    assert projection.generation_state is GenerationState.GENERATED_STALE


@pytest.mark.parametrize(
    ("attempt_changes", "result_changes"),
    [
        ({"project_generation": 3}, {}),
        ({"request_fingerprint": "old-request"}, {}),
        ({"operation_order_fingerprint": "old-order"}, {}),
    ],
)
def test_accepted_result_rejects_attempt_result_provenance_mismatch(
    attempt_changes: dict[str, object], result_changes: dict[str, object]
) -> None:
    with pytest.raises(ValueError, match="attempt provenance"):
        _result(_attempt(**attempt_changes), **result_changes)


def test_cancelled_attempt_wins_and_late_callback_is_not_published() -> None:
    current = _attempt()
    projection = project_post_assembly(
        _ready(
            generation_attempt=current,
            generation_terminal_state=GenerationTerminalState.CANCELLED,
            generation_terminal_attempt=current,
            accepted_generation_result=_result(current),
            managed_artifact=_managed(),
            generation_callback_audit=GenerationCallbackAuditEvidence(
                callback_attempt_identity=current,
                received_count=1,
                discarded_count=1,
                discard_reason=CallbackDiscardReason.TERMINAL_ATTEMPT,
            ),
        )
    )
    assert projection.generation_state is GenerationState.CANCELLED
    assert projection.headline_state is HeadlineState.CANCELLED
    assert projection.accepted_result_available
    assert projection.accepted_result_id == "result-1"
    assert projection.accepted_result_fingerprint == "result-fingerprint"
    assert projection.accepted_result_sha256 == PAYLOAD_SHA
    assert projection.accepted_result_current_for_source
    assert projection.preview_runtime_result_available
    assert projection.managed_artifact_state is ManagedArtifactState.CURRENT
    assert projection.stale_callback_discarded
    assert projection.automatic_downstream_action_count == 0


def test_new_attempt_hides_old_terminal_but_preserves_accepted_result() -> None:
    old = _attempt(generation_attempt_id="old")
    current = _attempt(generation_attempt_id="new")
    projection = project_post_assembly(
        _ready(
            generation_attempt=current,
            generation_terminal_state=GenerationTerminalState.FAILED,
            generation_terminal_attempt=old,
            accepted_generation_result=_result(old),
        )
    )
    assert projection.generation_state is GenerationState.GENERATED_CURRENT
    assert projection.accepted_result_id == "result-1"
    assert not projection.stale_callback_discarded


def test_managed_explicit_current_without_identity_is_not_authority() -> None:
    with pytest.raises(ValueError, match="not authoritative"):
        ManagedArtifactEvidence(
                present=True,
                explicit_state=ManagedArtifactState.CURRENT,
        )


@pytest.mark.parametrize(
    "artifact",
    [
        _managed(artifact_id=None),
        _managed(manifest_entry_id=None),
        _managed(readback_verified=False),
        _managed(byte_length=None),
        _managed(byte_length=0),
        _managed(sha256=None),
        _managed(sha256=OTHER_SHA),
        _managed(source_checksum=None),
        _managed(source_checksum=OTHER_SOURCE_SHA),
        _managed(request_fingerprint=None),
        _managed(request_fingerprint="other"),
        _managed(operation_order_fingerprint=None),
        _managed(operation_order_fingerprint="other"),
        _managed(project_generation=None),
        _managed(project_generation=3),
        _managed(post_identity=None),
        _managed(post_identity="other"),
        _managed(machine_identity=None),
        _managed(machine_identity="other"),
    ],
)
def test_managed_current_requires_complete_exact_readback_identity(
    artifact: ManagedArtifactEvidence,
) -> None:
    projection = project_post_assembly(_generated_input(managed_artifact=artifact))
    assert projection.managed_artifact_state is ManagedArtifactState.STALE


def test_managed_current_requires_all_evidence_and_exact_provenance() -> None:
    projection = project_post_assembly(_generated_input(managed_artifact=_managed()))
    assert projection.managed_artifact_state is ManagedArtifactState.CURRENT
    assert projection.managed_source_ready


def test_managed_artifact_cannot_be_current_without_accepted_result() -> None:
    projection = project_post_assembly(_ready(managed_artifact=_managed()))
    assert projection.managed_artifact_state is ManagedArtifactState.STALE
    assert not projection.managed_source_ready


def test_managed_current_is_compared_with_accepted_result_payload() -> None:
    projection = project_post_assembly(
        _generated_input(
            accepted_generation_result=_result(sha256=OTHER_SHA),
            managed_artifact=_managed(),
        )
    )
    assert projection.generation_state is GenerationState.GENERATED_CURRENT
    assert projection.managed_artifact_state is ManagedArtifactState.STALE
    assert not projection.managed_source_ready


@pytest.mark.parametrize(
    ("artifact", "expected"),
    [
        (
            ManagedArtifactEvidence(
                present=True, explicit_state=ManagedArtifactState.MISSING
            ),
            ManagedArtifactState.MISSING,
        ),
        (
            ManagedArtifactEvidence(
                present=True, explicit_state=ManagedArtifactState.FAILED
            ),
            ManagedArtifactState.FAILED,
        ),
        (
            ManagedArtifactEvidence(
                present=True, explicit_state=ManagedArtifactState.TAMPERED
            ),
            ManagedArtifactState.TAMPERED,
        ),
    ],
)
def test_managed_explicit_non_current_terminal_states_are_honored(
    artifact: ManagedArtifactEvidence, expected: ManagedArtifactState
) -> None:
    assert (
        project_post_assembly(_ready(managed_artifact=artifact))
        .managed_artifact_state
        is expected
    )


def test_external_explicit_current_without_identity_is_not_authority() -> None:
    with pytest.raises(TypeError, match="explicit_current"):
        ExternalArtifactEvidence(
                present=True,
                target_intent_id=TARGET_ID,
                explicit_current=True,
        )  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "artifact",
    [
        _external(artifact_id=None),
        _external(provenance_identity=None),
        _external(source_artifact_id="other"),
        _external(readback_verified=False),
        _external(target_intent_id="other"),
        _external(target_path=r"D:\NC\OTHER.FN"),
        _external(byte_length=None),
        _external(byte_length=127),
        _external(sha256=None),
        _external(sha256=OTHER_SHA),
        _external(source_checksum=None),
        _external(source_checksum=OTHER_SOURCE_SHA),
        _external(request_fingerprint=None),
        _external(request_fingerprint="other"),
        _external(operation_order_fingerprint=None),
        _external(operation_order_fingerprint="other"),
        _external(project_generation=None),
        _external(project_generation=3),
        _external(post_identity=None),
        _external(post_identity="other"),
        _external(machine_identity=None),
        _external(machine_identity="other"),
    ],
)
def test_external_current_requires_complete_exact_target_and_source_identity(
    artifact: ExternalArtifactEvidence,
) -> None:
    projection = project_post_assembly(
        _external_input(external_artifact=artifact)
    )
    assert projection.external_export_state is ExternalExportState.EXPORTED_STALE


def test_external_target_path_expected_value_drift_is_stale() -> None:
    projection = project_post_assembly(
        _external_input(
            current_external_target_path=r"D:\NC\RENAMED.FN",
            external_artifact=_external(),
        )
    )
    assert projection.external_export_state is ExternalExportState.EXPORTED_STALE


def test_external_complete_artifact_is_current() -> None:
    projection = project_post_assembly(
        _external_input(external_artifact=_external())
    )
    assert projection.external_export_state is ExternalExportState.EXPORTED_CURRENT
    assert projection.headline_state is HeadlineState.EXPORTED_CURRENT


def test_external_current_dispatch_active_and_failed_precedence() -> None:
    dispatch = ExternalDispatchAttemptIdentity(
        "dispatch-1",
        4,
        REQUEST,
        TARGET_ID,
        _dispatch_source(),
        active=True,
    )
    active = project_post_assembly(
        _external_input(external_dispatch=dispatch, external_artifact=_external())
    )
    assert active.external_export_state is ExternalExportState.EXPORTING
    failed = project_post_assembly(
        _external_input(
            external_dispatch=replace(dispatch, active=False, failed=True),
            external_artifact=_external(),
        )
    )
    assert failed.external_export_state is ExternalExportState.FAILED
    assert active.external_source_identity_state is ExternalSourceIdentityState.CURRENT


@pytest.mark.parametrize(
    ("dispatch_changes", "source_changes"),
    [
        ({}, {"managed_sha256": OTHER_SHA}),
        ({}, {"managed_source_checksum": OTHER_SOURCE_SHA}),
        ({}, {"operation_order_fingerprint": "old-order"}),
        (
            {"project_generation": 3},
            {"project_generation": 3},
        ),
        (
            {"request_fingerprint": "old-request"},
            {"request_fingerprint": "old-request"},
        ),
        ({}, {"post_identity": "old-post"}),
        ({}, {"machine_identity": "old-machine"}),
    ],
)
def test_external_dispatch_source_identity_is_bound_to_managed_content(
    dispatch_changes: dict[str, object], source_changes: dict[str, object]
) -> None:
    values: dict[str, object] = {
        "external_dispatch_attempt_id": "dispatch-1",
        "project_generation": 4,
        "request_fingerprint": REQUEST,
        "target_intent_id": TARGET_ID,
        "source_identity": _dispatch_source(**source_changes),
        "active": True,
    }
    values.update(dispatch_changes)
    projection = project_post_assembly(
        _external_input(
            external_dispatch=ExternalDispatchAttemptIdentity(**values),
            external_artifact=None,
        )
    )
    assert projection.external_export_state is ExternalExportState.READY
    assert (
        projection.external_source_identity_state
        is ExternalSourceIdentityState.STALE
    )


@pytest.mark.parametrize(
    "managed",
    [
        _managed(readback_verified=False),
        _managed(tampered=True),
    ],
)
def test_external_dispatch_source_requires_current_verified_managed_artifact(
    managed: ManagedArtifactEvidence,
) -> None:
    dispatch = ExternalDispatchAttemptIdentity(
        "dispatch-1", 4, REQUEST, TARGET_ID, _dispatch_source(), active=True
    )
    projection = project_post_assembly(
        _external_input(
            managed_artifact=managed,
            external_dispatch=dispatch,
            external_artifact=None,
        )
    )
    assert projection.external_export_state is ExternalExportState.READY
    assert projection.external_source_identity_state is ExternalSourceIdentityState.STALE


def test_historical_external_failure_does_not_hide_current_ready_target() -> None:
    historical = ExternalDispatchAttemptIdentity(
        "old-dispatch",
        3,
        "old-request",
        TARGET_ID,
        _dispatch_source(
            project_generation=3,
            request_fingerprint="old-request",
        ),
        failed=True,
    )
    projection = project_post_assembly(
        _external_input(external_dispatch=historical)
    )
    assert projection.external_export_state is ExternalExportState.READY


@pytest.mark.parametrize(
    "dispatch",
    [
        ExternalDispatchAttemptIdentity(),
        ExternalDispatchAttemptIdentity("historical"),
        ExternalDispatchAttemptIdentity(
            "dispatch",
            3,
            "old-request",
            TARGET_ID,
            _dispatch_source(
                project_generation=3,
                request_fingerprint="old-request",
            ),
            failed=True,
        ),
        ExternalDispatchAttemptIdentity(
            "dispatch", 4, REQUEST, "old-target", _dispatch_source(), failed=True
        ),
        ExternalDispatchAttemptIdentity(
            "dispatch",
            4,
            REQUEST,
            TARGET_ID,
            _dispatch_source(managed_sha256=OTHER_SHA),
            active=True,
        ),
    ],
)
def test_incomplete_or_historical_dispatch_does_not_hide_artifact(
    dispatch: ExternalDispatchAttemptIdentity,
) -> None:
    projection = project_post_assembly(
        _external_input(external_dispatch=dispatch, external_artifact=_external())
    )
    assert projection.external_export_state is ExternalExportState.EXPORTED_CURRENT


def test_selected_target_without_external_evidence_is_ready_only() -> None:
    projection = project_post_assembly(_external_input())
    assert projection.external_export_state is ExternalExportState.READY
    assert projection.headline_state is HeadlineState.EXTERNAL_READY


@pytest.mark.parametrize(
    ("result", "managed"),
    [
        (_result(sha256=None), _managed()),
        (_result(), _managed(sha256=None)),
        (_result(byte_length=None), _managed()),
        (_result(), _managed(byte_length=None)),
        (_result(source_checksum=None), _managed()),
        (_result(), _managed(source_checksum=None)),
        (_result(request_fingerprint=None), _managed()),
        (_result(), _managed(request_fingerprint=None)),
        (_result(operation_order_fingerprint=None), _managed()),
        (_result(), _managed(operation_order_fingerprint=None)),
        (_result(project_generation=None), _managed()),
        (_result(), _managed(project_generation=None)),
        (_result(post_identity=None), _managed()),
        (_result(), _managed(post_identity=None)),
        (_result(machine_identity=None), _managed()),
        (_result(), _managed(machine_identity=None)),
        (_result(sha256=OTHER_SHA), _managed()),
        (_result(), _managed(sha256=OTHER_SHA)),
        (_result(byte_length=127), _managed()),
        (_result(), _managed(byte_length=127)),
        (_result(source_checksum=OTHER_SOURCE_SHA), _managed()),
        (_result(), _managed(source_checksum=OTHER_SOURCE_SHA)),
        (
            _result(
                _attempt(request_fingerprint="other"),
                request_fingerprint="other",
            ),
            _managed(),
        ),
        (_result(), _managed(request_fingerprint="other")),
        (
            _result(
                _attempt(operation_order_fingerprint="other"),
                operation_order_fingerprint="other",
            ),
            _managed(),
        ),
        (_result(), _managed(operation_order_fingerprint="other")),
        (
            _result(_attempt(project_generation=3), project_generation=3),
            _managed(),
        ),
        (_result(), _managed(project_generation=3)),
        (_result(post_identity="other"), _managed()),
        (_result(), _managed(post_identity="other")),
        (_result(machine_identity="other"), _managed()),
        (_result(), _managed(machine_identity="other")),
    ],
)
def test_compound_source_equality_is_fail_closed_for_missing_or_mismatch(
    result: AcceptedGenerationResultEvidence,
    managed: ManagedArtifactEvidence,
) -> None:
    projection = project_post_assembly(
        _external_input(
            accepted_generation_result=result, managed_artifact=managed
        )
    )
    assert projection.headline_state is not HeadlineState.EXTERNAL_READY


def test_compound_source_equality_passes_only_when_all_fields_match() -> None:
    projection = project_post_assembly(_external_input())
    assert projection.generation_state is GenerationState.GENERATED_CURRENT
    assert projection.managed_artifact_state is ManagedArtifactState.CURRENT
    assert projection.external_export_state is ExternalExportState.READY
    assert projection.headline_state is HeadlineState.EXTERNAL_READY


@pytest.mark.parametrize(
    ("intent", "generation_changes", "expected"),
    [
        (
            WorkflowIntent.EXTERNAL,
            {"generation_attempt": None, "accepted_generation_result": None},
            HeadlineState.READY_TO_GENERATE,
        ),
        (
            WorkflowIntent.MANAGED,
            {"generation_attempt": None, "accepted_generation_result": None},
            HeadlineState.READY_TO_GENERATE,
        ),
        (
            WorkflowIntent.EXTERNAL,
            {
                "generation_attempt": _attempt(),
                "accepted_generation_result": _result(),
            },
            HeadlineState.SAVE_MANAGED_REQUIRED,
        ),
        (
            WorkflowIntent.MANAGED,
            {
                "generation_attempt": _attempt(),
                "accepted_generation_result": _result(),
            },
            HeadlineState.SAVE_MANAGED_REQUIRED,
        ),
        (
            WorkflowIntent.EXTERNAL,
            {
                "generation_attempt": _attempt(),
                "accepted_generation_result": _result(sha256=None),
            },
            HeadlineState.GENERATED_STALE,
        ),
        (
            WorkflowIntent.MANAGED,
            {
                "generation_attempt": _attempt(),
                "accepted_generation_result": _result(sha256=None),
            },
            HeadlineState.GENERATED_STALE,
        ),
    ],
)
def test_managed_missing_headline_never_claims_managed_current(
    intent: WorkflowIntent,
    generation_changes: dict[str, object],
    expected: HeadlineState,
) -> None:
    projection = project_post_assembly(
        _ready(
            active_intent=intent,
            external_target_intent_id=(
                TARGET_ID if intent is WorkflowIntent.EXTERNAL else None
            ),
            external_target_path=(
                TARGET_PATH if intent is WorkflowIntent.EXTERNAL else None
            ),
            current_external_target_path=(
                TARGET_PATH if intent is WorkflowIntent.EXTERNAL else None
            ),
            **generation_changes,
        )
    )
    assert projection.managed_artifact_state is ManagedArtifactState.MISSING
    assert projection.headline_state is expected
    assert projection.headline_state is not HeadlineState.MANAGED_CURRENT


def test_managed_missing_generation_activity_and_terminals_keep_generation_headline() -> None:
    current = _attempt()
    generating = project_post_assembly(
        _ready(
            active_intent=WorkflowIntent.EXTERNAL,
            external_target_intent_id=TARGET_ID,
            generation_attempt=current,
            generation_active=True,
        )
    )
    assert generating.headline_state is HeadlineState.GENERATING
    for terminal, headline in (
        (GenerationTerminalState.CANCELLED, HeadlineState.CANCELLED),
        (GenerationTerminalState.FAILED, HeadlineState.FAILED),
    ):
        projection = project_post_assembly(
            _ready(
                active_intent=WorkflowIntent.EXTERNAL,
                external_target_intent_id=TARGET_ID,
                generation_attempt=current,
                generation_terminal_state=terminal,
                generation_terminal_attempt=current,
            )
        )
        assert projection.headline_state is headline


@pytest.mark.parametrize("expected", list(HeadlineState))
def test_all_19_headlines_remain_reachable(expected: HeadlineState) -> None:
    current = _attempt()
    if expected is HeadlineState.MISSING_INPUT:
        evidence = _ready(project_id=None)
    elif expected is HeadlineState.CALCULATION_REQUIRED:
        evidence = _ready(
            operation_artifact_state=OperationArtifactState.CALCULATION_REQUIRED
        )
    elif expected is HeadlineState.SIMULATION_REQUIRED:
        evidence = _ready(simulation_status=SimulationStatus.MISSING)
    elif expected is HeadlineState.SIMULATION_FAILED:
        evidence = _ready(simulation_status=SimulationStatus.FAIL)
    elif expected is HeadlineState.BLOCKED:
        evidence = _ready(upstream_readiness_blocked=True)
    elif expected is HeadlineState.READY_TO_GENERATE:
        evidence = _ready()
    elif expected is HeadlineState.GENERATING:
        evidence = _ready(generation_attempt=current, generation_active=True)
    elif expected is HeadlineState.GENERATED_CURRENT:
        evidence = _generated_input()
    elif expected is HeadlineState.GENERATED_STALE:
        evidence = _generated_input(
            accepted_generation_result=_result(sha256=None)
        )
    elif expected is HeadlineState.SAVE_MANAGED_REQUIRED:
        evidence = _generated_input(active_intent=WorkflowIntent.MANAGED)
    elif expected is HeadlineState.MANAGED_CURRENT:
        evidence = _generated_input(
            active_intent=WorkflowIntent.MANAGED,
            managed_artifact=_managed(),
        )
    elif expected is HeadlineState.MANAGED_STALE:
        evidence = _generated_input(
            active_intent=WorkflowIntent.MANAGED,
            managed_artifact=_managed(readback_verified=False),
        )
    elif expected is HeadlineState.MANAGED_TAMPERED:
        evidence = _generated_input(
            active_intent=WorkflowIntent.MANAGED,
            managed_artifact=_managed(tampered=True),
        )
    elif expected is HeadlineState.EXTERNAL_READY:
        evidence = _external_input()
    elif expected is HeadlineState.EXPORTING:
        evidence = _external_input(
            external_dispatch=ExternalDispatchAttemptIdentity(
                "dispatch", 4, REQUEST, TARGET_ID, _dispatch_source(), active=True
            )
        )
    elif expected is HeadlineState.EXPORTED_CURRENT:
        evidence = _external_input(external_artifact=_external())
    elif expected is HeadlineState.EXPORTED_STALE:
        evidence = _external_input(
            external_artifact=_external(target_path=r"D:\NC\OTHER.FN")
        )
    elif expected is HeadlineState.CANCELLED:
        evidence = _ready(
            generation_attempt=current,
            generation_terminal_state=GenerationTerminalState.CANCELLED,
            generation_terminal_attempt=current,
        )
    else:
        evidence = _ready(
            generation_attempt=current,
            generation_terminal_state=GenerationTerminalState.FAILED,
            generation_terminal_attempt=current,
        )
    assert project_post_assembly(evidence).headline_state is expected


def test_external_projection_totality_and_precedence() -> None:
    current_dispatch = ExternalDispatchAttemptIdentity(
        "dispatch", 4, REQUEST, TARGET_ID, _dispatch_source()
    )
    inputs = (
        _ready(),
        _external_input(),
        _external_input(external_dispatch=replace(current_dispatch, active=True)),
        _external_input(external_dispatch=replace(current_dispatch, failed=True)),
        _external_input(external_artifact=_external()),
        _external_input(
            external_artifact=_external(target_path=r"D:\NC\OTHER.FN")
        ),
    )
    assert [project_post_assembly(item).external_export_state for item in inputs] == [
        ExternalExportState.NOT_SELECTED,
        ExternalExportState.READY,
        ExternalExportState.EXPORTING,
        ExternalExportState.FAILED,
        ExternalExportState.EXPORTED_CURRENT,
        ExternalExportState.EXPORTED_STALE,
    ]


def test_projection_has_typed_block_evidence_and_no_transition_api() -> None:
    from hms_cadcam.ui import post_assembly_projection as module

    projection = project_post_assembly(_ready(upstream_readiness_blocked=True))
    assert not hasattr(module, "transition")
    assert projection.block_evidence
    assert all(isinstance(item, BlockEvidence) for item in projection.block_evidence)
    assert all(
        isinstance(item.code, ProjectionDiagnosticCode)
        for item in projection.block_evidence
    )


def test_confirmation_rejection_is_counter_only() -> None:
    first = project_post_assembly(_external_input())
    second = project_post_assembly(
        replace(
            _external_input(),
            external_confirmation_rejected_count=1,
        )
    )
    assert first.external_export_state is second.external_export_state
    assert first.headline_state is second.headline_state
    assert second.current_external_dispatch_attempt_id is None
    assert first.external_confirmation_rejected_count == 0
    assert second.external_confirmation_rejected_count == 1
    assert second.automatic_downstream_action_count == 0


def test_dirty_state_is_projected_without_changing_component_states() -> None:
    clean = project_post_assembly(_ready())
    dirty = project_post_assembly(_ready(dirty_state=True))
    assert not clean.project_dirty_state
    assert dirty.project_dirty_state
    assert (
        dirty.readiness_state,
        dirty.generation_state,
        dirty.managed_artifact_state,
        dirty.external_export_state,
        dirty.headline_state,
    ) == (
        clean.readiness_state,
        clean.generation_state,
        clean.managed_artifact_state,
        clean.external_export_state,
        clean.headline_state,
    )


def test_every_core_input_field_is_read_or_projected() -> None:
    from hms_cadcam.ui import post_assembly_projection as module

    input_fields = {item.name for item in fields(PostAssemblyProjectionInput)}
    source = inspect.getsource(module)
    assert len(input_fields) == 33
    assert [
        field_name
        for field_name in sorted(input_fields)
        if f"evidence.{field_name}" not in source
    ] == []


def test_old_callback_cannot_mutate_projection_while_new_attempt_is_active() -> None:
    current = _attempt(generation_attempt_id="new")
    old = _attempt(generation_attempt_id="old")
    before_evidence = _generated_input(
        generation_attempt=current,
        generation_active=True,
    )
    before = project_post_assembly(before_evidence)
    after = project_post_assembly(
        replace(
            before_evidence,
            generation_callback_audit=_discarded_callback(old),
        )
    )
    assert before.generation_state is GenerationState.GENERATING
    assert after.generation_state is before.generation_state
    assert after.headline_state is before.headline_state
    assert after.source_fingerprints == before.source_fingerprints
    assert after.accepted_generation_result is before.accepted_generation_result


def test_new_result_replaces_previous_result_only_after_acceptance() -> None:
    old_result = _result()
    new_attempt = _attempt(generation_attempt_id="generation-2")
    new_result = _result(
        new_attempt,
        result_id="result-2",
        fingerprint="result-fingerprint-2",
    )
    before = project_post_assembly(
        _ready(
            generation_attempt=new_attempt,
            accepted_generation_result=old_result,
        )
    )
    after = project_post_assembly(
        _ready(
            generation_attempt=new_attempt,
            accepted_generation_result=new_result,
        )
    )
    assert before.accepted_result_id == "result-1"
    assert after.accepted_result_id == "result-2"
    assert after.accepted_result_current_for_source


def test_stale_callback_audit_requires_all_received_callbacks_discarded() -> None:
    with pytest.raises(ValueError, match="all be discarded"):
        _generated_input(
            generation_callback_audit=GenerationCallbackAuditEvidence(
                callback_attempt_identity=_attempt(generation_attempt_id="old"),
                received_count=1,
            )
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"received_count": 0, "published_count": 1},
        {"received_count": 0, "artifact_write_count": 1},
        {"received_count": 1, "published_count": 2},
        {"received_count": 1, "artifact_write_count": 1},
        {"received_count": 1, "ui_mutation_count": 1},
        {"received_count": 1, "selection_mutation_count": 1},
        {"received_count": 1, "project_mutation_count": 1},
        {
            "received_count": 1,
            "published_count": 1,
            "discarded_count": 1,
            "discard_reason": CallbackDiscardReason.STALE_ATTEMPT,
        },
    ],
)
def test_callback_audit_rejects_inconsistent_counter_accounting(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        GenerationCallbackAuditEvidence(**changes)


def test_generation_callback_forbids_artifact_and_selection_side_effects() -> None:
    for field_name in ("artifact_write_count", "selection_mutation_count"):
        with pytest.raises(ValueError, match="generation callbacks"):
            GenerationCallbackAuditEvidence(
                callback_attempt_identity=_attempt(),
                received_count=1,
                published_count=1,
                **{field_name: 1},
            )


def test_automatic_downstream_count_is_derived_from_forbidden_evidence() -> None:
    source = inspect.getsource(project_post_assembly)
    assert "automatic_downstream_action_count=0" not in source
    projection = project_post_assembly(_ready())
    assert projection.automatic_downstream_action_count == (
        projection.callback_artifact_write_count
        + projection.callback_selection_mutation_count
    )


def test_external_attempt_has_one_canonical_core_id_and_boundary_rejects_conflict() -> None:
    assert "external_export_attempt_id" not in {
        item.name for item in fields(ExternalDispatchAttemptIdentity)
    }
    with pytest.raises(ValueError, match="conflicting"):
        PostAssemblyEvidenceBoundary.external_dispatch_attempt(
            external_dispatch_attempt_id="dispatch-a",
            external_export_attempt_id="dispatch-b",
        )
    mapped = PostAssemblyEvidenceBoundary.external_dispatch_attempt(
        external_export_attempt_id="dispatch-a",
        project_generation=4,
        request_fingerprint=REQUEST,
        target_intent_id=TARGET_ID,
        source_identity=_dispatch_source(),
    )
    assert mapped.external_dispatch_attempt_id == "dispatch-a"


def test_external_attempt_rejects_active_and_failed_together() -> None:
    with pytest.raises(ValueError, match="active and failed"):
        ExternalDispatchAttemptIdentity(active=True, failed=True)


@pytest.mark.parametrize("terminal_field", ["active", "failed"])
def test_incomplete_active_or_failed_external_attempt_is_rejected(
    terminal_field: str,
) -> None:
    with pytest.raises(ValueError, match="complete identity"):
        ExternalDispatchAttemptIdentity(
            external_dispatch_attempt_id="dispatch",
            **{terminal_field: True},
        )


@pytest.mark.parametrize("diagnostic_field", ["simulation_diagnostics", "current_diagnostics"])
def test_invalid_nested_diagnostic_items_are_rejected(
    diagnostic_field: str,
) -> None:
    with pytest.raises(TypeError, match="DiagnosticEvidence"):
        _ready(**{diagnostic_field: ("not diagnostic",)})


@pytest.mark.parametrize(
    "operation_ids",
    [("",), ("   ",), ("op-1", "op-1"), (1,)],
)
def test_empty_duplicate_or_wrong_type_operation_ids_are_rejected(
    operation_ids: tuple[object, ...],
) -> None:
    with pytest.raises(ValueError, match="operation_ids"):
        _ready(operation_ids=operation_ids)


@pytest.mark.parametrize(
    ("field_name", "invalid"),
    [
        ("external_confirmation_rejected_count", -1),
        ("external_confirmation_rejected_count", True),
        ("project_generation", -1),
        ("project_generation", False),
    ],
)
def test_projection_input_counters_and_generations_reject_invalid_integers(
    field_name: str, invalid: object
) -> None:
    with pytest.raises((TypeError, ValueError)):
        _ready(**{field_name: invalid})


@pytest.mark.parametrize(
    "constructor",
    [
        lambda: GenerationAttemptIdentity(generation_attempt_id=" "),
        lambda: GenerationAttemptIdentity(worker_epoch=-1),
        lambda: ExternalDispatchAttemptIdentity(external_dispatch_attempt_id=" "),
        lambda: ExternalDispatchAttemptIdentity(project_generation=-1),
        lambda: _ready(current_post_identity="  "),
        lambda: ExternalArtifactEvidence(target_path="\t"),
    ],
)
def test_identity_paths_and_epochs_reject_whitespace_or_negative_values(
    constructor: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        constructor()  # type: ignore[operator]


def test_diagnostic_code_severity_and_summary_are_strict() -> None:
    with pytest.raises(ValueError, match="code"):
        DiagnosticEvidence(" ")
    with pytest.raises(TypeError, match="severity"):
        DiagnosticEvidence("stable.code", "ERROR")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="summary"):
        DiagnosticEvidence("stable.code", summary=1)  # type: ignore[arg-type]


def test_managed_explicit_stale_is_fail_closed_over_complete_current_evidence() -> None:
    projection = project_post_assembly(
        _generated_input(
            managed_artifact=_managed(explicit_state=ManagedArtifactState.STALE)
        )
    )
    assert projection.managed_artifact_state is ManagedArtifactState.STALE


def test_external_explicit_stale_is_fail_closed_and_has_no_current_field() -> None:
    assert "explicit_current" not in {item.name for item in fields(ExternalArtifactEvidence)}
    projection = project_post_assembly(
        _external_input(external_artifact=_external(explicit_stale=True))
    )
    assert projection.external_export_state is ExternalExportState.EXPORTED_STALE


def test_projection_readiness_boolean_is_not_wp2_action_authority() -> None:
    projection = project_post_assembly(
        _ready(upstream_readiness_blocked=True)
    )
    assert projection.presentation_readiness_blocked
    assert not hasattr(projection, "blocked")
    assert not hasattr(projection, "disabled")
    assert projection.automatic_downstream_action_count == 0

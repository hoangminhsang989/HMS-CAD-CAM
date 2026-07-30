"""Qt-free explicit CAM 3D preview workflow for Stage 9A.8 WP4."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
import logging
from typing import Protocol

from hms_cadcam.cam.application.cam3d_editor import (
    Cam3DEditorState,
    Cam3DProjectContext,
)
from hms_cadcam.cam.application.cam3d_preview import (
    Cam3DCancelDecision,
    Cam3DPreviewCompletionState,
    Cam3DPreviewDiagnostic,
    Cam3DPreviewResult,
    Cam3DPreviewSource,
    Cam3DSubmissionReceipt,
)
from hms_cadcam.cam.application.cam3d_request import (
    Cam3DActiveSetupContext,
    Cam3DCalculationJobId,
    Cam3DCalculationOwnershipKey,
    Cam3DCalculationPolicy,
    Cam3DCalculationRequestBuilder,
    Cam3DCalculationRequestContract,
    Cam3DRequestDiagnostic,
    Cam3DResultIdentity,
)
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectionState,
    Cam3DSelectionStatus,
)

logger = logging.getLogger(__name__)


class Cam3DPreviewSubmissionGateway(Protocol):
    """Narrow WP3-B gateway consumed by the Qt-free workflow."""

    def submit(
        self,
        request: Cam3DCalculationRequestContract,
    ) -> Cam3DSubmissionReceipt: ...

    def cancel(self, job_id: Cam3DCalculationJobId) -> Cam3DCancelDecision: ...

    def close_ownership(self, ownership: Cam3DCalculationOwnershipKey) -> None: ...

    def switch_ownership(
        self,
        previous: Cam3DCalculationOwnershipKey,
        current: Cam3DCalculationOwnershipKey,
        project_generation: int,
    ) -> None: ...

    def shutdown(self, *, wait: bool = False) -> None: ...


class Cam3DPreviewSink(Protocol):
    """UI-thread publication boundary for one immutable preview mesh."""

    def publish(self, result: Cam3DPreviewResult) -> bool: ...

    def clear(self, ownership: Cam3DCalculationOwnershipKey) -> None: ...


class Cam3DWorkflowStatus(StrEnum):
    """Localization-neutral state rendered by the CAM 3D panel."""

    CLOSED = "closed"
    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    CURRENT = "current"
    CANCELLED = "cancelled"
    ERROR = "error"

    @property
    def label_key(self) -> str:
        return {
            Cam3DWorkflowStatus.CLOSED: "Not calculated",
            Cam3DWorkflowStatus.BLOCKED: "Not calculated",
            Cam3DWorkflowStatus.READY: "READY",
            Cam3DWorkflowStatus.RUNNING: "RUNNING",
            Cam3DWorkflowStatus.CURRENT: "Preview CURRENT",
            Cam3DWorkflowStatus.CANCELLED: "Cancelled",
            Cam3DWorkflowStatus.ERROR: "ERROR",
        }[self]


class Cam3DWorkflowDiagnosticCode(StrEnum):
    """Stable integration failures without localized or exception text."""

    NONE = "none"
    NOT_READY = "not_ready"
    REQUEST_REJECTED = "request_rejected"
    SUBMISSION_REJECTED = "submission_rejected"
    PREVIEW_FAILED = "preview_failed"
    PUBLICATION_UNAVAILABLE = "publication_unavailable"

    @property
    def label_key(self) -> str:
        return {
            Cam3DWorkflowDiagnosticCode.NONE: "Preview only; no files created",
            Cam3DWorkflowDiagnosticCode.NOT_READY: "Readiness unavailable",
            Cam3DWorkflowDiagnosticCode.REQUEST_REJECTED: "Readiness unavailable",
            Cam3DWorkflowDiagnosticCode.SUBMISSION_REJECTED: "Failed",
            Cam3DWorkflowDiagnosticCode.PREVIEW_FAILED: "Failed",
            Cam3DWorkflowDiagnosticCode.PUBLICATION_UNAVAILABLE: (
                "CAD rendering backend is unavailable."
            ),
        }[self]


@dataclass(frozen=True, slots=True)
class Cam3DWorkflowInput:
    """One immutable snapshot of all live facts needed at explicit submit."""

    editor: Cam3DEditorState
    live_context: Cam3DProjectContext
    live_selection: Cam3DSelectionState
    active_setup: Cam3DActiveSetupContext | None
    editor_ready: bool

    def __post_init__(self) -> None:
        if not isinstance(self.editor, Cam3DEditorState):
            raise TypeError("CAM 3D workflow editor state is invalid")
        if not isinstance(self.live_context, Cam3DProjectContext):
            raise TypeError("CAM 3D workflow live context is invalid")
        if not isinstance(self.live_selection, Cam3DSelectionState):
            raise TypeError("CAM 3D workflow selection state is invalid")
        if self.active_setup is not None and not isinstance(
            self.active_setup, Cam3DActiveSetupContext
        ):
            raise TypeError("CAM 3D workflow active Setup is invalid")
        if type(self.editor_ready) is not bool:
            raise TypeError("CAM 3D workflow editor readiness is invalid")

    @property
    def ownership(self) -> Cam3DCalculationOwnershipKey | None:
        return self.active_setup.ownership if self.active_setup is not None else None


@dataclass(frozen=True, slots=True)
class Cam3DWorkflowState:
    """Complete immutable state consumed by Qt presentation code."""

    status: Cam3DWorkflowStatus
    ownership: Cam3DCalculationOwnershipKey | None = None
    project_generation: int | None = None
    active_job_id: Cam3DCalculationJobId | None = None
    latest_identity: Cam3DResultIdentity | None = None
    accepted_identity: Cam3DResultIdentity | None = None
    preview_source: Cam3DPreviewSource | None = None
    diagnostic: Cam3DWorkflowDiagnosticCode = Cam3DWorkflowDiagnosticCode.NONE
    request_diagnostics: tuple[Cam3DRequestDiagnostic, ...] = ()
    preview_diagnostic: Cam3DPreviewDiagnostic | None = None
    preview_enabled: bool = False
    cancel_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.status, Cam3DWorkflowStatus):
            raise TypeError("CAM 3D workflow status is invalid")
        if self.ownership is not None and not isinstance(
            self.ownership, Cam3DCalculationOwnershipKey
        ):
            raise TypeError("CAM 3D workflow ownership is invalid")
        if (self.ownership is None) != (self.project_generation is None):
            raise ValueError("CAM 3D workflow ownership/generation must be paired")
        if self.project_generation is not None and (
            type(self.project_generation) is not int or self.project_generation <= 0
        ):
            raise ValueError("CAM 3D workflow generation is invalid")
        if self.active_job_id is not None and not isinstance(
            self.active_job_id, Cam3DCalculationJobId
        ):
            raise TypeError("CAM 3D workflow active job is invalid")
        if self.latest_identity is not None and not isinstance(
            self.latest_identity, Cam3DResultIdentity
        ):
            raise TypeError("CAM 3D workflow latest identity is invalid")
        if self.accepted_identity is not None and not isinstance(
            self.accepted_identity, Cam3DResultIdentity
        ):
            raise TypeError("CAM 3D workflow accepted identity is invalid")
        if self.preview_source is not None and not isinstance(
            self.preview_source, Cam3DPreviewSource
        ):
            raise TypeError("CAM 3D workflow preview source is invalid")
        if not isinstance(self.diagnostic, Cam3DWorkflowDiagnosticCode):
            raise TypeError("CAM 3D workflow diagnostic is invalid")
        if not isinstance(self.request_diagnostics, tuple) or any(
            not isinstance(item, Cam3DRequestDiagnostic)
            for item in self.request_diagnostics
        ):
            raise TypeError("CAM 3D workflow request diagnostics are invalid")
        if self.preview_diagnostic is not None and not isinstance(
            self.preview_diagnostic, Cam3DPreviewDiagnostic
        ):
            raise TypeError("CAM 3D workflow preview diagnostic is invalid")
        if type(self.preview_enabled) is not bool or type(self.cancel_enabled) is not bool:
            raise TypeError("CAM 3D workflow command policy is invalid")
        if self.cancel_enabled != (self.active_job_id is not None):
            raise ValueError("CAM 3D workflow cancel policy does not match active job")
        if self.active_job_id is not None and (
            self.latest_identity is None
            or self.latest_identity.job_id != self.active_job_id
        ):
            raise ValueError("CAM 3D workflow active job is not latest")

    @classmethod
    def closed(cls) -> "Cam3DWorkflowState":
        return cls(Cam3DWorkflowStatus.CLOSED)

    @property
    def status_key(self) -> str:
        return self.status.label_key

    @property
    def diagnostic_key(self) -> str:
        return self.diagnostic.label_key


class Cam3DPreviewWorkflow:
    """Explicit request/submit/cancel/publication controller.

    All methods are expected to run on the owning application/UI thread. WP3-B
    owns worker locking, latest-wins scheduling, cache reuse and queued delivery.
    """

    def __init__(
        self,
        gateway: Cam3DPreviewSubmissionGateway,
        sink: Cam3DPreviewSink,
        *,
        builder: Cam3DCalculationRequestBuilder | None = None,
        policy: Cam3DCalculationPolicy | None = None,
    ) -> None:
        if not all(
            callable(getattr(gateway, name, None))
            for name in (
                "submit",
                "cancel",
                "close_ownership",
                "switch_ownership",
                "shutdown",
            )
        ):
            raise TypeError("CAM 3D workflow submission gateway is invalid")
        if not callable(getattr(sink, "publish", None)) or not callable(
            getattr(sink, "clear", None)
        ):
            raise TypeError("CAM 3D workflow preview sink is invalid")
        self._gateway = gateway
        self._sink = sink
        self._builder = builder or Cam3DCalculationRequestBuilder()
        self._policy = policy or Cam3DCalculationPolicy()
        self._inputs: Cam3DWorkflowInput | None = None
        self._state = Cam3DWorkflowState.closed()
        self._shutdown = False

    @property
    def state(self) -> Cam3DWorkflowState:
        return self._state

    @property
    def inputs(self) -> Cam3DWorkflowInput | None:
        return self._inputs

    def bind_inputs(self, inputs: Cam3DWorkflowInput) -> Cam3DWorkflowState:
        """Bind live immutable facts, cancelling invalidated work only."""
        if not isinstance(inputs, Cam3DWorkflowInput):
            raise TypeError("CAM 3D workflow input is invalid")
        if self._shutdown:
            return self._state
        if inputs == self._inputs:
            return self._state

        previous = self._inputs
        previous_ownership = previous.ownership if previous is not None else None
        next_ownership = inputs.ownership
        previous_generation = (
            previous.live_context.project_generation if previous is not None else None
        )
        next_generation = inputs.live_context.project_generation

        if previous_ownership is not None and (
            previous_ownership != next_ownership
            or previous_generation != next_generation
        ):
            if next_ownership is None or next_generation is None:
                self._gateway.close_ownership(previous_ownership)
            else:
                self._gateway.switch_ownership(
                    previous_ownership,
                    next_ownership,
                    next_generation,
                )
            self._clear_preview(previous_ownership)
        elif previous_ownership is None and next_ownership is not None:
            assert next_generation is not None
            self._gateway.switch_ownership(
                next_ownership,
                next_ownership,
                next_generation,
            )
        elif self._state.active_job_id is not None:
            self._gateway.cancel(self._state.active_job_id)
            if previous_ownership is not None:
                self._clear_preview(previous_ownership)
        elif previous_ownership is not None:
            self._clear_preview(previous_ownership)

        self._inputs = inputs
        self._state = self._bound_state(inputs)
        return self._state

    def submit_preview(self) -> Cam3DWorkflowState:
        """Build and submit only in response to an explicit UI command."""
        inputs = self._inputs
        if self._shutdown or inputs is None or not self._eligible(inputs):
            self._state = self._blocked_state(inputs)
            return self._state

        try:
            build = self._builder.build(
                editor=inputs.editor,
                live_context=inputs.live_context,
                live_selection=inputs.live_selection,
                active_setup=inputs.active_setup,
                job_id=Cam3DCalculationJobId.new(),
                policy=self._policy,
            )
        except (RuntimeError, TypeError, ValueError):
            logger.exception("CAM 3D preview request-build boundary failed")
            self._state = replace(
                self._bound_state(inputs),
                status=Cam3DWorkflowStatus.ERROR,
                diagnostic=Cam3DWorkflowDiagnosticCode.REQUEST_REJECTED,
                preview_enabled=False,
            )
            return self._state
        if not build.accepted:
            self._state = replace(
                self._bound_state(inputs),
                status=Cam3DWorkflowStatus.BLOCKED,
                diagnostic=Cam3DWorkflowDiagnosticCode.REQUEST_REJECTED,
                request_diagnostics=build.diagnostics,
                preview_enabled=False,
            )
            return self._state

        request = build.request
        assert request is not None
        identity = Cam3DResultIdentity.from_request(request)
        self._state = Cam3DWorkflowState(
            Cam3DWorkflowStatus.RUNNING,
            request.ownership,
            request.project_generation,
            request.job_id,
            identity,
            preview_enabled=True,
            cancel_enabled=True,
        )
        try:
            receipt = self._gateway.submit(request)
        except (RuntimeError, TypeError, ValueError):
            logger.exception("CAM 3D preview submission boundary failed")
            self._state = replace(
                self._state,
                status=Cam3DWorkflowStatus.ERROR,
                active_job_id=None,
                diagnostic=Cam3DWorkflowDiagnosticCode.SUBMISSION_REJECTED,
                cancel_enabled=False,
            )
            return self._state
        if not receipt.accepted or receipt.job_id != request.job_id:
            self._state = replace(
                self._state,
                status=Cam3DWorkflowStatus.ERROR,
                active_job_id=None,
                diagnostic=Cam3DWorkflowDiagnosticCode.SUBMISSION_REJECTED,
                cancel_enabled=False,
            )
        return self._state

    def cancel_preview(self) -> Cam3DCancelDecision:
        """Cancel exactly the current owned job; repeated calls are idempotent."""
        job_id = self._state.active_job_id
        if job_id is None or self._shutdown:
            return Cam3DCancelDecision.NOT_FOUND
        decision = self._gateway.cancel(job_id)
        if decision in {
            Cam3DCancelDecision.REQUESTED,
            Cam3DCancelDecision.ALREADY_CANCELLED,
            Cam3DCancelDecision.CLOSED,
            Cam3DCancelDecision.NOT_LATEST,
        }:
            inputs = self._inputs
            self._state = replace(
                self._state,
                status=Cam3DWorkflowStatus.CANCELLED,
                active_job_id=None,
                diagnostic=Cam3DWorkflowDiagnosticCode.NONE,
                preview_enabled=bool(inputs is not None and self._eligible(inputs)),
                cancel_enabled=False,
            )
        return decision

    def accept_result(self, result: object) -> bool:
        """Publish one latest queued result exactly once on the UI thread."""
        if not isinstance(result, Cam3DPreviewResult) or self._shutdown:
            return False
        state = self._state
        if (
            state.latest_identity != result.identity
            or state.active_job_id != result.identity.job_id
            or state.accepted_identity == result.identity
        ):
            return False
        inputs = self._inputs
        can_retry = bool(inputs is not None and self._eligible(inputs))
        if result.state is Cam3DPreviewCompletionState.CANCELLED:
            self._state = replace(
                state,
                status=Cam3DWorkflowStatus.CANCELLED,
                active_job_id=None,
                accepted_identity=result.identity,
                preview_source=result.source,
                diagnostic=Cam3DWorkflowDiagnosticCode.NONE,
                preview_diagnostic=result.diagnostic,
                preview_enabled=can_retry,
                cancel_enabled=False,
            )
            return False
        if result.state is Cam3DPreviewCompletionState.FAILED:
            self._state = replace(
                state,
                status=Cam3DWorkflowStatus.ERROR,
                active_job_id=None,
                accepted_identity=result.identity,
                preview_source=result.source,
                diagnostic=Cam3DWorkflowDiagnosticCode.PREVIEW_FAILED,
                preview_diagnostic=result.diagnostic,
                preview_enabled=can_retry,
                cancel_enabled=False,
            )
            return False

        try:
            published = bool(self._sink.publish(result))
        except (RuntimeError, TypeError, ValueError):
            logger.exception("CAM 3D preview publication boundary failed")
            published = False
        self._state = replace(
            state,
            status=(
                Cam3DWorkflowStatus.CURRENT
                if published
                else Cam3DWorkflowStatus.ERROR
            ),
            active_job_id=None,
            accepted_identity=result.identity,
            preview_source=result.source,
            diagnostic=(
                Cam3DWorkflowDiagnosticCode.NONE
                if published
                else Cam3DWorkflowDiagnosticCode.PUBLICATION_UNAVAILABLE
            ),
            preview_enabled=can_retry,
            cancel_enabled=False,
        )
        return published

    def close(self) -> Cam3DWorkflowState:
        """Cancel and clear the current ownership without shutting the gateway."""
        inputs = self._inputs
        ownership = inputs.ownership if inputs is not None else None
        if ownership is not None:
            self._gateway.close_ownership(ownership)
            self._clear_preview(ownership)
        self._inputs = None
        self._state = Cam3DWorkflowState.closed()
        return self._state

    def shutdown(self, *, wait: bool = False) -> Cam3DWorkflowState:
        """Suppress late delivery and stop WP3-B before viewport teardown."""
        if self._shutdown:
            return self._state
        self.close()
        self._shutdown = True
        self._gateway.shutdown(wait=wait)
        return self._state

    def _bound_state(self, inputs: Cam3DWorkflowInput) -> Cam3DWorkflowState:
        ownership = inputs.ownership
        generation = inputs.live_context.project_generation
        if ownership is None or generation is None or not self._eligible(inputs):
            return self._blocked_state(inputs)
        return Cam3DWorkflowState(
            Cam3DWorkflowStatus.READY,
            ownership,
            generation,
            diagnostic=Cam3DWorkflowDiagnosticCode.NONE,
            preview_enabled=True,
        )

    @staticmethod
    def _blocked_state(
        inputs: Cam3DWorkflowInput | None,
    ) -> Cam3DWorkflowState:
        ownership = inputs.ownership if inputs is not None else None
        generation = (
            inputs.live_context.project_generation
            if inputs is not None and ownership is not None
            else None
        )
        return Cam3DWorkflowState(
            Cam3DWorkflowStatus.BLOCKED,
            ownership,
            generation,
            diagnostic=Cam3DWorkflowDiagnosticCode.NOT_READY,
        )

    @staticmethod
    def _eligible(inputs: Cam3DWorkflowInput) -> bool:
        context = inputs.live_context
        setup = inputs.active_setup
        selection = inputs.live_selection
        if (
            not inputs.editor_ready
            or not context.is_open
            or context.read_only
            or context.project_id is None
            or context.project_generation is None
            or context.document_id is None
            or context.source_id is None
            or inputs.editor.context != context
            or inputs.editor.selection != selection
            or selection.read_only
            or not selection.part
            or selection.status
            in {
                Cam3DSelectionStatus.PROJECT_CLOSED,
                Cam3DSelectionStatus.STALE,
                Cam3DSelectionStatus.INVALID,
            }
            or setup is None
            or not setup.active
            or setup.project_generation != context.project_generation
        ):
            return False
        ownership = setup.ownership
        return (
            ownership.project_id == context.project_id
            and ownership.document_id == context.document_id
            and ownership.source_id == context.source_id
        )

    def _clear_preview(self, ownership: Cam3DCalculationOwnershipKey) -> None:
        try:
            self._sink.clear(ownership)
        except (RuntimeError, TypeError, ValueError):
            logger.exception("CAM 3D preview clear boundary failed")


__all__ = [
    "Cam3DPreviewSink",
    "Cam3DPreviewSubmissionGateway",
    "Cam3DPreviewWorkflow",
    "Cam3DWorkflowDiagnosticCode",
    "Cam3DWorkflowInput",
    "Cam3DWorkflowState",
    "Cam3DWorkflowStatus",
]

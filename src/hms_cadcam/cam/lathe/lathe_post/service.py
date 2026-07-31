"""In-memory Lathe Program Preview application service and presenter DTOs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from hms_cadcam.cam.lathe.lathe_post.assembler import (
    LatheProgramAssemblerV1,
    LatheProgramAssemblyResult,
    LatheProgramDiagnosticCode,
)
from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity
from hms_cadcam.cam.lathe.lathe_post.ir import LatheProgramDiagnostic, LatheProgramIRV1
from hms_cadcam.cam.lathe.lathe_post.listing import render_neutral_listing
from hms_cadcam.cam.lathe.lathe_post.profile import (
    LathePostProfile,
    LathePostProfileRegistry,
    LathePostUnavailableError,
    lathe_post_profile_registry,
)


class LatheProgramReadiness(StrEnum):
    INVALID = "INVALID"
    INCOMPLETE = "INCOMPLETE"
    NEUTRAL_PREVIEW_READY = "NEUTRAL_PREVIEW_READY"
    PRODUCTION_POST_UNAVAILABLE = "PRODUCTION_POST_UNAVAILABLE"
    BASIC_NC_PREVIEW_READY_UNVERIFIED = "BASIC_NC_PREVIEW_READY_UNVERIFIED"
    BASIC_NC_EXPORT_READY_UNVERIFIED = "BASIC_NC_EXPORT_READY_UNVERIFIED"
    MACHINE_OUTPUT_READY = "MACHINE_OUTPUT_READY"


@dataclass(frozen=True, slots=True)
class LatheProgramReadinessSnapshot:
    readiness: LatheProgramReadiness
    diagnostics: tuple[LatheProgramDiagnostic, ...] = ()

    @property
    def state(self) -> LatheProgramReadiness:
        return self.readiness


@dataclass(frozen=True, slots=True)
class LatheProgramSnapshot:
    identity: LatheProgramIdentity
    program: LatheProgramIRV1
    readiness: LatheProgramReadiness
    listing: str
    diagnostics: tuple[LatheProgramDiagnostic, ...] = ()

    @property
    def fingerprint(self) -> str:
        return self.program.fingerprint

    @property
    def neutral_listing(self) -> str:
        return self.listing


@dataclass(frozen=True, slots=True)
class LatheNeutralListingSnapshot:
    listing_version: str
    text: str
    fingerprint: str
    warning_footer: str = "PREVIEW ONLY — NOT MACHINE-READY — DO NOT RUN ON A CNC MACHINE"


class LatheProgramService:
    """Synchronous, context-owned service with no persistence or file output."""

    def __init__(self, assembler: LatheProgramAssemblerV1 | None = None, registry: LathePostProfileRegistry | None = None) -> None:
        self._registry = registry or lathe_post_profile_registry()
        self._assembler = assembler or LatheProgramAssemblerV1(self._registry.neutral_preview())
        self._latest: LatheProgramSnapshot | None = None
        self._last_diagnostics: tuple[LatheProgramDiagnostic, ...] = ()
        self._closed = False

    @property
    def latest(self) -> LatheProgramSnapshot | None:
        return self._latest

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def profile_registry(self) -> LathePostProfileRegistry:
        return self._registry

    def assemble(self, identity: LatheProgramIdentity, operations: Sequence[object], results: Mapping[object, object] | Sequence[object] | None = None, tool_bindings: Mapping[object, object] | None = None) -> LatheProgramAssemblyResult:
        if self._closed:
            diagnostic = LatheProgramDiagnostic("CLOSED", "lathe.program.diagnostic.closed")
            self._last_diagnostics = (diagnostic,)
            return LatheProgramAssemblyResult(None, self._last_diagnostics, None)
        result = self._assembler.assemble(identity, operations, results, tool_bindings)
        self._last_diagnostics = result.diagnostics
        if result.accepted and result.program is not None:
            listing = render_neutral_listing(result.program)
            self._latest = LatheProgramSnapshot(identity, result.program, LatheProgramReadiness.NEUTRAL_PREVIEW_READY, listing, ())
        else:
            self._latest = None
        return result

    preview = assemble

    def readiness(self) -> LatheProgramReadinessSnapshot:
        if self._latest is not None:
            return LatheProgramReadinessSnapshot(LatheProgramReadiness.NEUTRAL_PREVIEW_READY)
        if not self._last_diagnostics:
            return LatheProgramReadinessSnapshot(LatheProgramReadiness.INCOMPLETE)
        incomplete_codes = {LatheProgramDiagnosticCode.EMPTY_OPERATION_LIST.value, LatheProgramDiagnosticCode.MISSING_TOOLPATH.value, "CLOSED"}
        state = LatheProgramReadiness.INCOMPLETE if all(item.code in incomplete_codes for item in self._last_diagnostics) else LatheProgramReadiness.INVALID
        return LatheProgramReadinessSnapshot(state, self._last_diagnostics)

    program_readiness = readiness

    def neutral_listing(self) -> LatheNeutralListingSnapshot | None:
        latest = self._latest
        if latest is None:
            return None
        from hms_cadcam.cam.lathe.lathe_post.ir import NEUTRAL_LISTING_VERSION
        return LatheNeutralListingSnapshot(NEUTRAL_LISTING_VERSION, latest.listing, latest.fingerprint)

    def clear(self, identity: LatheProgramIdentity | None = None) -> bool:
        if self._latest is None:
            return False
        if identity is not None and self._latest.identity != identity:
            return False
        self._latest = None
        self._last_diagnostics = ()
        return True

    def invalidate(self, *, project_id: object | None = None, document_id: object | None = None, source_id: object | None = None, source_generation: int | None = None, setup_id: object | None = None, operation_id: object | None = None, operation_revision: int | None = None) -> bool:
        latest = self._latest
        if latest is None:
            return False
        identity = latest.identity
        matches = (
            (project_id is None or str(project_id) == identity.project_id)
            and (document_id is None or str(document_id) == identity.document_id)
            and (source_id is None or str(source_id) == identity.source_id)
            and (source_generation is None or source_generation == identity.source_generation)
            and (setup_id is None or str(setup_id) == identity.setup_id)
        )
        if operation_id is not None and any(block.operation_id == str(operation_id) for block in latest.program.blocks):
            matches = True
        if operation_revision is not None and operation_revision != identity.revision:
            matches = True
        return self.clear(identity) if matches else False

    def request_production_post(self, profile_id: str | None = None) -> LathePostProfile:
        return self._registry.request_production_post(profile_id)

    def close(self) -> None:
        self._latest = None
        self._last_diagnostics = ()
        self._closed = True


ProgramReadiness = LatheProgramReadiness
LatheProgramReadinessState = LatheProgramReadiness
LatheProgramServiceV1 = LatheProgramService


__all__ = [
    "LatheNeutralListingSnapshot", "LatheProgramReadiness", "LatheProgramReadinessSnapshot",
    "LatheProgramReadinessState", "LatheProgramService", "LatheProgramServiceV1",
    "LatheProgramSnapshot", "LathePostUnavailableError", "ProgramReadiness",
]

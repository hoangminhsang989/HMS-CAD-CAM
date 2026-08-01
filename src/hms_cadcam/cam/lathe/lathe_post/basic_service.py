"""Context-owned basic NC preview/export service."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from hms_cadcam.cam.lathe.lathe_post.basic_profile import BasicLathePostProfile, basic_lathe_post_profile
from hms_cadcam.cam.lathe.lathe_post.basic_types import (
    BasicPostDiagnostic,
    BasicPostDiagnosticCode,
    BasicPostMetadata,
    BasicToolMapping,
)
from hms_cadcam.cam.lathe.lathe_post.export import BasicNcExportResult, BasicNcExportService
from hms_cadcam.cam.lathe.lathe_post.conformance import (
    LatheNcConformanceAnalyzerV1,
    LatheNcConformanceReport,
)
from hms_cadcam.cam.lathe.lathe_post.ir import (
    LatheProgramBlockKind,
    LatheProgramIRV1,
    OperationPayload,
)
from hms_cadcam.cam.lathe.lathe_post.renderer import BasicNcOutputSnapshot, BasicNcRenderResult, LatheBasicFanucPostRendererV1
from hms_cadcam.cam.lathe.types import LatheStrategyId


def _operation_strategy_ids(program: LatheProgramIRV1) -> tuple[str, ...]:
    """Extract ordered canonical coverage from operation-begin boundaries only."""

    strategy_ids: list[str] = []
    for block in program.blocks:
        if block.kind is not LatheProgramBlockKind.OPERATION_BEGIN:
            continue
        if not isinstance(block.payload, OperationPayload):
            raise ValueError("operation-begin block requires OperationPayload")
        try:
            strategy = LatheStrategyId(block.payload.strategy_id)
        except ValueError as exc:
            raise ValueError("operation strategy must be a canonical LatheStrategyId") from exc
        if strategy.value not in strategy_ids:
            strategy_ids.append(strategy.value)
    if not strategy_ids:
        raise ValueError("program requires at least one typed Lathe operation")
    return tuple(strategy_ids)


def _invalid_operation_strategy_result() -> BasicNcRenderResult:
    diagnostic = BasicPostDiagnostic(
        BasicPostDiagnosticCode.INVALID_PROGRAM.value,
        "lathe.basic_post.diagnostic.invalid_program",
        "operation_strategy",
    )
    return BasicNcRenderResult(None, (diagnostic,))


@dataclass(frozen=True, slots=True)
class BasicNcServiceState:
    snapshot: BasicNcOutputSnapshot | None = None
    last_result: BasicNcRenderResult | None = None
    strategy_ids: tuple[str, ...] = ()
    conformance_report: LatheNcConformanceReport | None = None


class LatheBasicNcService:
    """No-persistence service that requires an explicit typed mapping."""

    def __init__(
        self,
        profile: BasicLathePostProfile | None = None,
        tool_mappings: Mapping[str, BasicToolMapping] | Sequence[BasicToolMapping] = (),
        metadata: BasicPostMetadata | None = None,
    ) -> None:
        self.profile = profile or basic_lathe_post_profile()
        self.tool_mappings = tool_mappings
        self.metadata = metadata
        self._renderer = LatheBasicFanucPostRendererV1(self.profile)
        self._exporter = BasicNcExportService()
        self._conformance_analyzer = LatheNcConformanceAnalyzerV1()
        self._state = BasicNcServiceState()

    @property
    def state(self) -> BasicNcServiceState:
        return self._state

    @property
    def latest(self) -> BasicNcOutputSnapshot | None:
        return self._state.snapshot

    def generate(self, program: LatheProgramIRV1 | None) -> BasicNcRenderResult:
        if program is None:
            result = BasicNcRenderResult(None, ())
            strategy_ids: tuple[str, ...] = ()
        elif not isinstance(program, LatheProgramIRV1):
            result = self._renderer.render(program, self.tool_mappings, self.metadata)
            strategy_ids = ()
        else:
            try:
                strategy_ids = _operation_strategy_ids(program)
            except ValueError:
                result = _invalid_operation_strategy_result()
                strategy_ids = ()
            else:
                result = self._renderer.render(program, self.tool_mappings, self.metadata)
        self._state = BasicNcServiceState(result.snapshot, result, strategy_ids)
        return result

    def review_latest(self) -> LatheNcConformanceReport:
        """Explicitly review the current immutable NC text without filesystem access."""

        text = self._state.snapshot.text if self._state.snapshot is not None else ""
        report = self._conformance_analyzer.analyze(
            text,
            strategy_ids=self._state.strategy_ids,
            profile_id=self.profile.profile_id,
            behavior_revision=self.profile.sample_contract_revision,
        )
        self._state = BasicNcServiceState(
            self._state.snapshot,
            self._state.last_result,
            self._state.strategy_ids,
            report,
        )
        return report

    def export(self, destination: str, *, acknowledged_unverified: bool, overwrite_confirmed: bool = False) -> BasicNcExportResult:
        return self._exporter.export(self._state.snapshot, destination, acknowledged_unverified=acknowledged_unverified, overwrite_confirmed=overwrite_confirmed)

    def clear(self) -> None:
        self._state = BasicNcServiceState()


__all__ = ["BasicNcServiceState", "LatheBasicNcService"]

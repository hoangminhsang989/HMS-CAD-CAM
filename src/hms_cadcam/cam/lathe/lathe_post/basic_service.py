"""Context-owned basic NC preview/export service."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence

from hms_cadcam.cam.lathe.lathe_post.basic_profile import BasicLathePostProfile, basic_lathe_post_profile
from hms_cadcam.cam.lathe.lathe_post.basic_types import BasicPostMetadata, BasicToolMapping
from hms_cadcam.cam.lathe.lathe_post.export import BasicNcExportResult, BasicNcExportService
from hms_cadcam.cam.lathe.lathe_post.conformance import (
    LatheNcConformanceAnalyzerV1,
    LatheNcConformanceReport,
)
from hms_cadcam.cam.lathe.lathe_post.ir import LatheProgramIRV1
from hms_cadcam.cam.lathe.lathe_post.renderer import BasicNcOutputSnapshot, BasicNcRenderResult, LatheBasicFanucPostRendererV1


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
        else:
            result = self._renderer.render(program, self.tool_mappings, self.metadata)
            strategy_ids = tuple(
                dict.fromkeys(
                    str(getattr(block.payload, "strategy_id"))
                    for block in program.blocks
                    if getattr(block.payload, "strategy_id", None)
                )
            )
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

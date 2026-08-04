"""Actual Lathe production objects used by Stage 13C WP2 tests."""
from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass
import sys

from _lathe_ui_fixtures import stable_uuid, workspace_for
from hms_cadcam.ai_assist.model_loader import load_canonical_model
from hms_cadcam.ai_assist.cutting_supervisor import CuttingWorkerSupervisor
from hms_cadcam.ai_assist.lifecycle import AiAssistBroker, AiRuntimeState
from hms_cadcam.ai_assist.policy import AiMode, GIB
from hms_cadcam.ai_assist.resources import (
    ProbeStatus,
    RamResourceSnapshot,
    ResourceSnapshot,
    VramResourceSnapshot,
)
from hms_cadcam.ai_assist.production_draft_bridge import LatheParameterEditorDraftBridge
from hms_cadcam.ai_assist.turning_production_adapter import (
    TurningProductionAdapter,
    TurningProductionContext,
    TurningRuntimeBridge,
)
from hms_cadcam.cam.lathe.parameters import LatheParameterState
from hms_cadcam.cam.lathe.toolpath.stock import LatheStockSnapshotV1
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags


CLONE_ROOT = Path(__file__).parents[2]
MODEL_MANIFEST = CLONE_ROOT / "src/hms_cadcam/ai_assist/models/cutting_parameters_v1.manifest.json"
WORKER = CLONE_ROOT / "src/hms_cadcam/ai_assist/cutting_worker.py"
TURNING_STRATEGIES = (
    LatheStrategyId.OD_ROUGH,
    LatheStrategyId.OD_FINISH,
    LatheStrategyId.ID_ROUGH,
    LatheStrategyId.ID_FINISH,
)


@dataclass
class _Clock:
    value: int = 0

    def now_ns(self) -> int:
        return self.value

    def advance(self) -> None:
        self.value += 5_000_000_000


def _safe_resource_sample() -> ResourceSnapshot:
    return ResourceSnapshot(
        RamResourceSnapshot(
            8 * GIB, 4 * GIB, 4 * GIB, 4 * GIB, 1, "stage13c-test", ProbeStatus.AVAILABLE
        ),
        VramResourceSnapshot(
            None, None, 1, "stage13c-test", ProbeStatus.UNKNOWN, "none"
        ),
    )


def brokered_worker() -> tuple[AiAssistBroker, CuttingWorkerSupervisor]:
    supervisor = CuttingWorkerSupervisor(Path(sys.executable), WORKER)
    clock = _Clock()
    broker = AiAssistBroker(supervisor=supervisor, clock=clock)
    broker.configure(
        capability_enabled=True,
        master_enabled=True,
        mode=AiMode.LITE,
        user_cap_bytes=None,
    )
    broker.request_task()
    assert broker.observe(_safe_resource_sample()).state is AiRuntimeState.WAITING_FOR_RESOURCES
    clock.advance()
    assert broker.observe(_safe_resource_sample()).state is AiRuntimeState.READY
    assert broker.start_ready_task().state is AiRuntimeState.RUNNING
    return broker, supervisor


def enabled_flags() -> UiFeatureFlags:
    return UiFeatureFlags(
        {
            UiFeatureFlag.OFFLINE_CAM_AI_ASSIST_13A: True,
            UiFeatureFlag.OFFLINE_CAM_AI_PARAMETER_ADVISOR_13B: True,
            UiFeatureFlag.OFFLINE_CAM_AI_TURNING_COVERAGE_13C: True,
        }
    )


def runtime_for(
    strategy_id: LatheStrategyId = LatheStrategyId.OD_ROUGH,
    *,
    material_token: str | None = "ISO_P",
    tool_material: str | None = "CARBIDE",
    use_worker: bool = False,
) -> tuple[TurningRuntimeBridge, object]:
    workspace, presenter, reference = workspace_for(strategy_id)
    presenter.create_operation(strategy_id)
    presenter.refresh()
    active = presenter.snapshot.operations[0]
    resolution = presenter.facade.service._capability_resolver.resolve(reference)
    state = LatheParameterState.build(strategy_id, dict(active.parameter_values))
    stock = LatheStockSnapshotV1(
        "stage13c-stock",
        stable_uuid("stage13c-stock-source"),
        0,
        100.0,
        10.0 if strategy_id in {LatheStrategyId.ID_ROUGH, LatheStrategyId.ID_FINISH} else 0.0,
        0.0,
        -50.0,
    )
    draft_bridge = LatheParameterEditorDraftBridge(
        workspace.parameter_editor,
        "stage13c-lathe-editor",
        str(active.ownership.operation_id),
        "stage13c-project",
        strategy_id,
    )
    context = TurningProductionContext(
        "stage13c-project",
        "stage13c-lathe-editor",
        str(active.ownership.operation_id),
        state,
        stock,
        resolution,
        material_token,
        tool_material,
        draft_bridge,
    )
    adapter = TurningProductionAdapter(context)
    model = load_canonical_model(MODEL_MANIFEST)
    if use_worker:
        broker, supervisor = brokered_worker()
        runtime = TurningRuntimeBridge(
            adapter,
            enabled_flags(),
            model=model,
            broker=broker,
            supervisor=supervisor,
        )
    else:
        runtime = TurningRuntimeBridge(adapter, enabled_flags(), model=model)
    return runtime, workspace


def bind_runtime(
    strategy_id: LatheStrategyId,
    *,
    use_worker: bool = False,
) -> tuple[TurningRuntimeBridge, object, object]:
    runtime, workspace = runtime_for(strategy_id, use_worker=use_worker)
    assert workspace.bind_turning_advisor(runtime)
    session = workspace.turning_advisor_session
    assert session is not None
    return runtime, workspace, session


def select_materials(workspace: object) -> None:
    panel = workspace.advisor_panel
    panel.workpiece_material.setCurrentIndex(panel.workpiece_material.findData("ISO_P"))
    panel.tool_material.setCurrentIndex(panel.tool_material.findData("CARBIDE"))

"""Production PostResult fixtures shared by Stage 7D.2.2 tests."""

from pathlib import Path
from dataclasses import replace
from uuid import UUID

from hms_cadcam.cam.post import (
    ExportOverwritePolicy,
    ExportTarget,
    NCExportRequest,
    NCExportSourceSnapshot,
    PostRequest,
    PostRuntimeService,
    SimulationGateMode,
    SimulationGatePolicy,
    robodrill_21i_definition,
)
from tests.unit._fanuc_fixtures import fixture_context
from tests.unit.test_fanuc_robodrill_21i_runtime import _runtime_source


def production_export_fixture(
    project_root: Path,
    *,
    target: ExportTarget = ExportTarget.PROJECT_MANAGED,
    target_directory: Path | None = None,
    overwrite: ExportOverwritePolicy = ExportOverwritePolicy.FAIL_IF_EXISTS,
    project_id: UUID | None = None,
    project_generation: int = 1,
    post_runtime: PostRuntimeService | None = None,
):
    project_root.mkdir(parents=True, exist_ok=True)
    source = _runtime_source()
    if project_id is not None:
        source = replace(source, project_id=project_id)
    definition = robodrill_21i_definition()
    context = fixture_context(source, file_name="runtime_facing.fn")
    post_request = PostRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        definition,
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.OPTIONAL),
        program_context=context,
    )
    runtime = post_runtime or PostRuntimeService()
    post_result = runtime.post(post_request, source).result
    assert post_result is not None
    snapshot = NCExportSourceSnapshot(
        project_generation, post_request, post_result, source
    )
    request = NCExportRequest(
        source.project_id,
        source.operation.operation_id,
        source.artifact.artifact_id,
        post_result.result_id,
        "runtime_facing.fn",
        target=target,
        overwrite_policy=overwrite,
        target_directory=target_directory,
    )
    return request, snapshot

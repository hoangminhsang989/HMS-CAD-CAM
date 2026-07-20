from dataclasses import replace

from hms_cadcam.cam.post import (
    ExportOverwritePolicy,
    ExportTarget,
    NCArtifactStatus,
    NCExportDiagnosticCode,
    NCExportService,
    NCExportStatus,
)
from hms_cadcam.cam.post.export_codec import dumps, loads
from tests.unit._export_fixtures import production_export_fixture


def test_managed_export_is_exact_crlf_no_bom_and_round_trips_manifest(tmp_path) -> None:
    project = tmp_path / "Managed.HMS"
    request, snapshot = production_export_fixture(project)
    service = NCExportService()
    execution = service.export(project, request, snapshot)
    assert execution.accepted and execution.artifact is not None
    payload = (project / execution.artifact.output_relative_path).read_bytes()
    assert payload == snapshot.post_result.canonical_text.encode("utf-8")
    assert payload.endswith(b"\r\n") and not payload.startswith(b"\xef\xbb\xbf")
    manifest = service.store.load(project, request.project_id)
    assert loads(dumps(manifest)) == manifest
    assert manifest.entries[0].status is NCArtifactStatus.CURRENT


def test_default_overwrite_denied_same_artifact_and_explicit_replace(tmp_path) -> None:
    project = tmp_path / "Overwrite.HMS"
    request, snapshot = production_export_fixture(project)
    service = NCExportService()
    first = service.export(project, request, snapshot)
    assert first.accepted
    denied = service.export(project, replace(request, request_id=None), snapshot)
    assert not denied.accepted
    assert denied.diagnostics[0].code is NCExportDiagnosticCode.FILE_EXISTS
    assert service.current(request.project_id, request.operation_id) == first.result
    same = service.export(
        project,
        replace(
            request,
            request_id=None,
            overwrite_policy=ExportOverwritePolicy.REPLACE_IF_SAME_ARTIFACT,
        ),
        snapshot,
    )
    assert same.accepted
    explicit = service.export(
        project,
        replace(
            request,
            request_id=None,
            overwrite_policy=ExportOverwritePolicy.REPLACE_EXPLICIT,
        ),
        snapshot,
    )
    assert explicit.accepted


def test_external_export_failure_preserves_managed_artifact(tmp_path) -> None:
    project = tmp_path / "ExternalFailure.HMS"
    missing = tmp_path / "missing-server"
    request, snapshot = production_export_fixture(
        project,
        target=ExportTarget.DATA_SERVER_DIRECTORY,
        target_directory=missing,
    )
    execution = NCExportService().export(project, request, snapshot)
    assert not execution.accepted
    assert execution.status is NCExportStatus.EXTERNAL_FAILED
    assert execution.artifact is not None
    assert (project / execution.artifact.output_relative_path).is_file()
    assert not missing.exists()


def test_external_local_directory_bytes_and_explicit_overwrite(tmp_path) -> None:
    project = tmp_path / "External.HMS"
    target = tmp_path / "server"
    target.mkdir()
    request, snapshot = production_export_fixture(
        project,
        target=ExportTarget.FILESYSTEM_DIRECTORY,
        target_directory=target,
    )
    service = NCExportService()
    first = service.export(project, request, snapshot)
    assert first.accepted and first.status is NCExportStatus.PUBLISHED_EXTERNAL
    assert (target / "runtime_facing.fn").read_bytes() == snapshot.post_result.canonical_text.encode("utf-8")
    second = service.export(
        project,
        replace(
            request,
            request_id=None,
            overwrite_policy=ExportOverwritePolicy.REPLACE_EXPLICIT,
        ),
        snapshot,
    )
    assert second.accepted


def test_stale_callback_blocks_before_write(tmp_path) -> None:
    project = tmp_path / "Stale.HMS"
    request, snapshot = production_export_fixture(project)
    stale = replace(snapshot, project_generation=2)
    execution = NCExportService().export(
        project, request, snapshot, current_source=lambda: stale
    )
    assert not execution.accepted
    assert execution.status is NCExportStatus.STALE
    assert not (project / "post").exists()
    assert not (project / "nc").exists()


def test_profile_context_simulation_and_operation_changes_fail_closed(tmp_path) -> None:
    from hms_cadcam.cam.post import SimulationGateMode, SimulationGatePolicy

    project = tmp_path / "ChangedInputs.HMS"
    request, snapshot = production_export_fixture(project)
    changed_gate = replace(
        snapshot.post_request,
        simulation_gate_policy=SimulationGatePolicy(SimulationGateMode.REQUIRE_PASS),
    )
    gate_execution = NCExportService().export(
        project, request, replace(snapshot, post_request=changed_gate)
    )
    assert not gate_execution.accepted
    assert gate_execution.diagnostics[0].code is NCExportDiagnosticCode.POST_STALE

    context = replace(snapshot.post_request.program_context, file_name="OTHER.fn")
    changed_context = replace(snapshot.post_request, program_context=context)
    context_execution = NCExportService().export(
        project, request, replace(snapshot, post_request=changed_context)
    )
    assert not context_execution.accepted
    assert context_execution.diagnostics[0].code is NCExportDiagnosticCode.PROFILE_MISMATCH

    disabled_source = replace(
        snapshot.source,
        operation=replace(snapshot.source.operation, enabled=False),
    )
    disabled_execution = NCExportService().export(
        project, request, replace(snapshot, source=disabled_source)
    )
    assert not disabled_execution.accepted
    assert disabled_execution.diagnostics[0].code is NCExportDiagnosticCode.POST_STALE

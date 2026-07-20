"""Manual Windows smoke test for Stage 7D.2.2 NC export (no UI)."""

from __future__ import annotations

import argparse
import json
import logging
import socket
import sys
import tempfile
from datetime import timedelta
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from hms_cadcam.cam.post import (
    ExportOverwritePolicy,
    ExportTarget,
    NCArtifactStatus,
    NCArtifactStoreError,
    NCExportDiagnosticCode,
    NCExportStatus,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.project.constants import (
    APPLICATION_VERSION,
    SESSION_LOCK_FORMAT,
    SESSION_LOCK_FORMAT_VERSION,
)
from hms_cadcam.project.exceptions import RecoveryRequiredError
from hms_cadcam.project.session_lock import SessionLockMetadata
from tests.unit._export_fixtures import production_export_fixture


logger = logging.getLogger("manual_stage7d22_export")


def run_smoke(workspace: Path, data_server: Path | None = None) -> None:
    """Exercise managed/external bytes and project lifecycle without opening NC files."""
    workspace.mkdir(parents=True, exist_ok=True)
    service = ProjectService.create_default(workspace / "config")
    session = service.new_project(workspace, "Manual 7D22")
    request, snapshot = production_export_fixture(
        session.root_path,
        project_id=session.manifest.project_id,
        project_generation=service.cam_generation,
        post_runtime=service.post_service,
    )
    managed = service.export_nc(request, snapshot)
    if not managed.accepted or managed.artifact is None:
        raise RuntimeError(f"Managed export failed: {managed.diagnostics}")
    managed_path = session.root_path / managed.artifact.output_relative_path
    expected = snapshot.post_result.canonical_text.encode("utf-8")
    if managed_path.read_bytes() != expected or not expected.endswith(b"\r\n"):
        raise RuntimeError("Managed .fn bytes are not canonical CRLF bytes")
    logger.info("Managed artifact verified: %s", managed_path)

    denied = service.export_nc(replace(request, request_id=None), snapshot)
    if denied.accepted:
        raise RuntimeError("Default overwrite was not denied")
    explicit = service.export_nc(
        replace(
            request,
            request_id=None,
            overwrite_policy=ExportOverwritePolicy.REPLACE_EXPLICIT,
        ),
        snapshot,
    )
    if not explicit.accepted:
        raise RuntimeError("Explicit managed replacement failed")

    local_target = workspace / "local-nc-target"
    local_target.mkdir()
    local_request = replace(
        request,
        request_id=None,
        target=ExportTarget.FILESYSTEM_DIRECTORY,
        target_directory=local_target,
        overwrite_policy=ExportOverwritePolicy.REPLACE_EXPLICIT,
    )
    local = service.export_nc(local_request, snapshot)
    if not local.accepted or local.status is not NCExportStatus.PUBLISHED_EXTERNAL:
        raise RuntimeError(f"Local directory export failed: {local.diagnostics}")
    if (local_target / "runtime_facing.fn").read_bytes() != expected:
        raise RuntimeError("Local exported bytes differ from managed bytes")

    missing_request = replace(
        local_request,
        request_id=None,
        target=ExportTarget.DATA_SERVER_DIRECTORY,
        target_directory=workspace / "missing-data-server",
        overwrite_policy=ExportOverwritePolicy.REPLACE_EXPLICIT,
    )
    missing = service.export_nc(missing_request, snapshot)
    if missing.status is not NCExportStatus.EXTERNAL_FAILED or not managed_path.is_file():
        raise RuntimeError("Missing target did not preserve the managed artifact")

    with patch.object(
        service.nc_export_service.store,
        "export_external",
        side_effect=NCArtifactStoreError(
            NCExportDiagnosticCode.PERMISSION_DENIED,
            "simulated Windows permission denial",
            managed=False,
        ),
    ):
        denied_permission = service.export_nc(
            replace(missing_request, request_id=None, target_directory=local_target),
            snapshot,
        )
    if (
        denied_permission.status is not NCExportStatus.EXTERNAL_FAILED
        or managed_path.read_bytes() != expected
    ):
        raise RuntimeError("Permission failure did not preserve managed output")

    if data_server is not None:
        server_request = replace(
            local_request,
            request_id=None,
            target=ExportTarget.DATA_SERVER_DIRECTORY,
            target_directory=data_server,
        )
        server = service.export_nc(server_request, snapshot)
        if not server.accepted:
            raise RuntimeError(f"Mapped/UNC data-server export failed: {server.diagnostics}")
        logger.info("Data-server copy verified in caller-supplied directory")

    original_bytes = managed_path.read_bytes()
    managed_path.write_bytes(b"tampered")
    inspected = service.nc_export_service.store.inspect(
        session.root_path, session.manifest.project_id
    )
    if inspected.entries[0].status is not NCArtifactStatus.TAMPERED:
        raise RuntimeError("Tampered managed artifact was not classified")
    managed_path.write_bytes(original_bytes)

    session.is_dirty = True
    autosave = service.autosave()
    if autosave is None or not (autosave.path / "post" / "manifest.json").is_file():
        raise RuntimeError("Autosave did not isolate managed NC state")
    service.save()
    copied = service.save_as(workspace, "Manual 7D22 Copy")
    copied_manifest = service.nc_export_service.store.inspect(
        copied.root_path, copied.manifest.project_id
    )
    if copied_manifest.entries[0].status is not NCArtifactStatus.STALE:
        raise RuntimeError("Save As did not isolate/rebind NC artifact provenance")
    service.close_project()
    service.open_project(session.root_path)
    service.close_project()

    stale_lock = SessionLockMetadata(
        format=SESSION_LOCK_FORMAT,
        format_version=SESSION_LOCK_FORMAT_VERSION,
        project_id=autosave.metadata.project_id,
        session_id=autosave.metadata.session_id,
        pid=2_147_483_647,
        hostname=socket.gethostname(),
        created_at=autosave.metadata.created_at - timedelta(seconds=1),
        application_version=APPLICATION_VERSION,
    )
    (session.root_path / "session.lock").write_text(
        json.dumps(stale_lock.to_dict(), ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    managed_path.write_bytes(b"changed-after-autosave")
    recovery_service = ProjectService.create_default(workspace / "recovery-config")
    try:
        recovery_service.open_project(session.root_path)
    except RecoveryRequiredError as required:
        recovered = recovery_service.recover_project(required.assessment)
    else:
        raise RuntimeError("Manual recovery candidate was not detected")
    if managed_path.read_bytes() != expected or recovered.is_dirty:
        raise RuntimeError("Recovery did not restore isolated autosave NC bytes")
    recovery_service.close_project()
    logger.info(
        "Save/Open, Save As, Autosave/Recovery, permission failure, and switch/close passed"
    )
    logger.info("No file was opened, executed, streamed, or sent to a CNC machine")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path)
    parser.add_argument(
        "--data-server",
        type=Path,
        help="Optional existing local, mapped-drive, or UNC directory",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    if args.workspace is not None:
        run_smoke(args.workspace, args.data_server)
        return 0
    with tempfile.TemporaryDirectory(prefix="hms-stage7d22-") as directory:
        run_smoke(Path(directory), args.data_server)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

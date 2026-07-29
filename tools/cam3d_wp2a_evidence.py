"""Capture Stage 9A.8 WP2A production-widget selection evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
from pathlib import Path
import sys
from uuid import UUID, uuid4

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

if "--native" not in sys.argv:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDockWidget

from hms_cadcam.cad.models import BoundingBox, CadDocumentId, CadObjectId
from hms_cadcam.cad.unavailable import UnavailableCadKernel
from hms_cadcam.cam.application.cam3d_selection import (
    Cam3DSelectionApplicationService,
    Cam3DSelectionRole,
    Cam3DSelectionSource,
)
from hms_cadcam.cam.cam3d import (
    CamSurfaceOrientation,
    CamSurfaceReference,
    CamSurfaceRole,
)
from hms_cadcam.cam.domain import (
    GeometryFingerprint,
    GeometryReference,
    GeometryReferenceId,
    GeometryReferenceKind,
    GeometryRepresentationKind,
    Revision,
)
from hms_cadcam.project.service import ProjectService
from hms_cadcam.ui.cam3d_function_panel import Cam3DFunctionPanel
from hms_cadcam.ui.feature_flags import UiFeatureFlag, UiFeatureFlags
from hms_cadcam.ui.main_window import MainWindow
from hms_cadcam.viewer.models import SelectionMetadata, SelectionMode
from hms_cadcam.viewer.unavailable_backend import UnavailableCadViewportBackend

LOGGER = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selection(
    document_id: CadDocumentId,
    token: str,
    *,
    topology: SelectionMode = SelectionMode.FACE,
) -> SelectionMetadata:
    return SelectionMetadata(
        document_id,
        f"{document_id}:{topology.value}:{token}",
        topology,
        BoundingBox(0.0, 0.0, 0.0, 12.0, 8.0, 3.0),
        CadObjectId(f"object-{token}"),
    )


def _reference(
    project_id: UUID,
    source_id: UUID,
    selection: SelectionMetadata,
    role: CamSurfaceRole,
) -> CamSurfaceReference:
    selector = f"wp2a:{hashlib.sha256(selection.selection_id.encode('utf-8')).hexdigest()}"
    geometry = GeometryReference(
        GeometryReferenceId.new(),
        "hms_cam3d_surface",
        1,
        source_id,
        GeometryReferenceKind.FACE,
        GeometryRepresentationKind.BREP,
        GeometryFingerprint.from_payload({"selector": selector}),
        Revision(0),
        subshape_selector=selector,
        hint="CAD surface",
    )
    return CamSurfaceReference(
        project_id,
        geometry,
        CamSurfaceOrientation.FORWARD,
        role,
        body_identity="wp2a-body",
        face_identity=selector,
    )


def _capture(widget, output: Path, name: str) -> dict[str, object]:
    QApplication.processEvents()
    path = output / f"{name}.png"
    if not widget.grab().save(str(path)):
        raise RuntimeError(f"Could not save WP2A evidence image: {path}")
    return {
        "path": path.name,
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "width": widget.width(),
        "height": widget.height(),
    }


def _flags(enabled: bool) -> UiFeatureFlags:
    return UiFeatureFlags(
        {
            UiFeatureFlag.POST_ASSEMBLY_9A7: False,
            UiFeatureFlag.CAM_3D_9A8: enabled,
        }
    )


def build_evidence(output: Path, *, native: bool) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    project_id = uuid4()
    source_id = uuid4()
    document_id = CadDocumentId("wp2a-evidence-document")
    generation = 9
    current: dict[str, tuple[SelectionMetadata, ...]] = {"items": ()}

    def source_provider() -> Cam3DSelectionSource:
        return Cam3DSelectionSource(
            project_id,
            generation,
            document_id,
            source_id,
            False,
            current["items"],
        )

    service_box: dict[str, Cam3DSelectionApplicationService] = {}

    def new_service(*, read_only: bool = False) -> Cam3DSelectionApplicationService:
        service = Cam3DSelectionApplicationService(
            source_provider,
            lambda selection, role: _reference(
                project_id,
                source_id,
                selection,
                role,
            ),
        )
        service.bind_project(project_id, generation, read_only=read_only)
        service_box["service"] = service
        return service

    panel = Cam3DFunctionPanel(feature_enabled=True)
    panel.resize(560, 900)
    panel.selection_assign_requested.connect(
        lambda role: panel.set_selection_state(
            service_box["service"].assign_current(role)
        )
    )
    panel.selection_clear_requested.connect(
        lambda role: panel.set_selection_state(
            service_box["service"].clear_role(role)
        )
    )
    panel.show()

    images: dict[str, dict[str, object]] = {}
    service = new_service()
    panel.set_selection_state(service.state)
    images["empty_selection"] = _capture(panel, output, "01_empty_selection")

    current["items"] = (_selection(document_id, "part"),)
    dict(panel.role_editors)[Cam3DSelectionRole.PART].assign_button.click()
    images["part_selected"] = _capture(panel, output, "02_part_selected")

    current["items"] = (_selection(document_id, "check"),)
    dict(panel.role_editors)[Cam3DSelectionRole.CHECK].assign_button.click()
    images["part_check"] = _capture(panel, output, "03_part_check")

    current["items"] = (_selection(document_id, "fixture"),)
    dict(panel.role_editors)[Cam3DSelectionRole.FIXTURE].assign_button.click()
    images["part_check_fixture"] = _capture(
        panel,
        output,
        "04_part_check_fixture",
    )

    panel.set_selection_state(service.mark_stale())
    images["stale_selection"] = _capture(panel, output, "05_stale_selection")

    current["items"] = (
        _selection(document_id, "edge", topology=SelectionMode.EDGE),
    )
    invalid_service = new_service()
    panel.set_selection_state(invalid_service.state)
    dict(panel.role_editors)[Cam3DSelectionRole.PART].assign_button.click()
    images["invalid_selection"] = _capture(panel, output, "06_invalid_selection")

    read_only_service = new_service(read_only=True)
    panel.set_selection_state(read_only_service.state)
    images["read_only"] = _capture(panel, output, "07_read_only")

    switched_project = uuid4()
    switched = read_only_service.bind_project(switched_project, generation + 1)
    panel.set_selection_state(switched)
    images["project_switched_reset"] = _capture(
        panel,
        output,
        "08_project_switched_reset",
    )
    panel.close()
    panel.deleteLater()
    app.processEvents()

    runtime_root = output / "runtime"
    legacy_window = MainWindow(
        ProjectService.create_default(runtime_root / "config"),
        UnavailableCadKernel("WP2A evidence legacy rollback"),
        UnavailableCadViewportBackend("WP2A evidence legacy rollback"),
        ui_feature_flags=_flags(False),
    )
    legacy_window.resize(1280, 760)
    legacy_window.show()
    app.processEvents()
    legacy_docks = tuple(
        dock.objectName() for dock in legacy_window.findChildren(QDockWidget)
    )
    images["feature_disabled_legacy"] = _capture(
        legacy_window,
        output,
        "09_feature_disabled_legacy",
    )
    legacy_window.close()
    legacy_window.deleteLater()
    app.processEvents()

    return {
        "schema": "hms.stage9a8.wp2a.evidence-manifest.v1",
        "platform": "windows_native" if native else "qt_offscreen",
        "production_widget": "hms_cadcam.ui.cam3d_function_panel.Cam3DFunctionPanel",
        "application_service": "hms_cadcam.cam.application.cam3d_selection.Cam3DSelectionApplicationService",
        "binding": "production signals and typed application service with deterministic native-free selection port",
        "images": images,
        "legacy_dock_count": len(legacy_docks),
        "legacy_dock_object_names": legacy_docks,
        "cam3d_constructed_when_feature_disabled": hasattr(
            legacy_window,
            "cam3d_function_panel",
        ),
        "automatic_calculate": False,
        "database_writes": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--native", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = build_evidence(args.output.resolve(), native=args.native)
        manifest_path = args.output.resolve() / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest_hash = _sha256(manifest_path)
        manifest_path.with_suffix(".json.sha256").write_text(
            manifest_hash + "\n",
            encoding="utf-8",
        )
        LOGGER.info("WP2A evidence manifest: %s", manifest_path)
        LOGGER.info("WP2A evidence SHA-256: %s", manifest_hash)
        return 0
    except (OSError, RuntimeError, TypeError, ValueError):
        LOGGER.exception("WP2A evidence capture failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

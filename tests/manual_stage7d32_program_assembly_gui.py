"""Windows GUI smoke for the Stage 7D.3.2 Program Assembly panel.

This harness displays a real PySide6 panel backed by immutable 7D.3.1
snapshots.  It never opens or runs the generated ``.fn`` file.  Use a real
HMS project beside this smoke harness for the full Save/Open/Recovery checks.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hms_cadcam.cam.domain import CamJobId
from hms_cadcam.cam.post import NCExportService
from hms_cadcam.ui.program_assembly_ui import ProgramAssemblyPanel
from tests.unit.test_fanuc_robodrill_21i_runtime import _runtime_source
from tests.unit.test_post_service import _simulation
from tests.unit.test_program_assembly import _source_variant
from hms_cadcam.cam.simulation import SimulationStatus


logger = logging.getLogger("hms.manual.stage7d32")


class _Session:
    def __init__(self, project_id, root: Path) -> None:
        self.manifest = SimpleNamespace(project_id=project_id, project_name="GUI Smoke")
        self.root_path = root


class _Service:
    def __init__(self, sources: list, root: Path) -> None:
        self.sources = {source.operation.operation_id: source for source in sources}
        job_id = CamJobId.new()
        setup = SimpleNamespace(
            setup_id=sources[0].setup.setup_id,
            operation_tree=SimpleNamespace(
                operations=[source.operation for source in sources]
            ),
        )
        self.cam_snapshot = SimpleNamespace(
            jobs=(SimpleNamespace(job_id=job_id, setups=(setup,)),)
        )
        self.cam_generation = 1
        self.root = root
        self.nc_export_service = NCExportService()
        root.mkdir(parents=True, exist_ok=True)
        self.nc_export_service.bind_project(root, sources[0].project_id, 1)

    def capture_post_source(self, operation_id):
        source = self.sources.get(operation_id)
        if source is None:
            raise RuntimeError("manual source missing")
        return source

    def export_assembly_nc(self, request, snapshot, *, current_source=None):
        return self.nc_export_service.export_assembly(
            self.root,
            request,
            snapshot,
            current_source=current_source,
            current_project_generation=lambda: self.cam_generation,
        )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    first = _runtime_source()
    sources = [
        first,
        _source_variant(first, "pocket_2_5d"),
        _source_variant(first, "contour_2d"),
        _source_variant(first, "drilling_v1"),
    ]
    statuses = (SimulationStatus.PASS, SimulationStatus.WARN, SimulationStatus.FAIL, None)
    # PostSourceSnapshot uses slots/frozen fields; replace is intentionally
    # imported lazily so this script remains a smoke harness, not production UI.
    from dataclasses import replace

    prepared = []
    for source, status in zip(sources, statuses):
        if status is None:
            prepared.append(source)
        else:
            result, current_input = _simulation(source, status)
            prepared.append(
                replace(
                    source,
                    simulation_result=result,
                    expected_simulation_input_fingerprint=current_input,
                )
            )
    root = Path.cwd() / ".tmp_manual_stage7d32_gui"
    service = _Service(prepared, root)
    app = QApplication.instance() or QApplication([])
    panel = ProgramAssemblyPanel(service)
    panel.bind_project(_Session(prepared[0].project_id, root))
    names = ("Facing", "Pocket", "Contour G41", "Drilling")
    for source, name in zip(prepared, names):
        panel.set_selected_operation(source.operation.operation_id, operation_name=name)
        if not panel.add_selected_operation():
            logger.error("Could not add %s", name)
            return 1
    for row in range(panel.operation_table.rowCount()):
        panel.operation_table.selectRow(row)
        panel.tool_station_spin.setValue(row + 1)
        panel.length_offset_spin.setValue(row + 1)
        panel.diameter_offset_spin.setValue(row + 1)
        panel.safe_z_spin.setValue(10.0 + row)
        if row == 2:
            panel.compensation_combo.setCurrentText("LEGACY_WORKNC_LEFT (G41)")
        if not panel.apply_operation_draft():
            logger.error("Operation draft failed at row %d", row + 1)
            return 1
    panel.gate_combo.setCurrentText("ALLOW_WARN")
    panel.apply_shared_draft()
    panel.show()
    logger.info("Manual checklist: Add/Remove, Move Up/Down, explicit order, T/H/D, G41, PASS/WARN/FAIL gates, Preview/navigation, diagnostics, Save Managed, local/mapped/UNC export, overwrite denial/replace, stale recompute, Save/Open/Save As/Autosave/Recovery, project switch and resize.")
    logger.info("The initial FAIL and missing simulation rows intentionally block Generate; no automatic Generate/Export is performed.")
    logger.info("Review NOT MACHINE CERTIFIED / REVIEW REQUIRED and never open/run the .fn file.")
    auto_close = os.environ.get("HMS_MANUAL_AUTOCLOSE_MS")
    if auto_close:
        QTimer.singleShot(int(auto_close), app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

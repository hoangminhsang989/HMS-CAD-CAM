"""Stage 7D.3.2 Program Assembly panel/controller contract tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from hms_cadcam.cam.domain import CamJobId, OperationId
from hms_cadcam.cam.post import (
    ExportOverwritePolicy,
    NCArtifactStatus,
    NCExportService,
    ProgramAssemblyDiagnosticCode,
    ProgramAssemblyService,
)
from hms_cadcam.ui.program_assembly_ui import (
    ProgramAssemblyPanel,
    ProgramAssemblyUiStatus,
    parse_global_metadata,
)
from tests.unit.test_fanuc_robodrill_21i_runtime import _runtime_source
from tests.unit.test_program_assembly import _source_variant


class _Session:
    def __init__(self, project_id, root: Path) -> None:
        self.manifest = SimpleNamespace(project_id=project_id, project_name="UI Test")
        self.root_path = root


class _ExportProxy:
    def __init__(self) -> None:
        self._entries = ()
        self.stale_ids = []

    def artifacts(self):
        return self._entries

    def mark_operation_stale(self, operation_id):
        self.stale_ids.append(operation_id)


class _Service:
    def __init__(self, sources, root: Path | None = None) -> None:
        self.sources = {source.operation.operation_id: source for source in sources}
        self.sources_by_id = self.sources
        job_id = CamJobId.new()
        setups = {}
        for source in sources:
            setup = setups.setdefault(
                source.setup.setup_id,
                SimpleNamespace(
                    setup_id=source.setup.setup_id,
                    operation_tree=SimpleNamespace(operations=[]),
                ),
            )
            setup.operation_tree.operations.append(source.operation)
        self.job_id = job_id
        self.cam_snapshot = SimpleNamespace(
            jobs=(
                SimpleNamespace(job_id=job_id, setups=tuple(setups.values())),
            )
        )
        self.cam_generation = 1
        self.root = root
        self.nc_export_service = _ExportProxy()
        self._exporter = None

    def capture_post_source(self, operation_id):
        source = self.sources.get(operation_id)
        if source is None:
            raise RuntimeError("source missing")
        return source

    def export_assembly_nc(self, request, snapshot, *, current_source=None):
        assert self._exporter is not None and self.root is not None
        return self._exporter.export_assembly(
            self.root,
            request,
            snapshot,
            current_source=current_source,
            current_project_generation=lambda: self.cam_generation,
        )


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    QApplication.instance() or QApplication([])


def _panel(tmp_path: Path, *strategies: str):
    first = _runtime_source()
    sources = [first, *(_source_variant(first, strategy) for strategy in strategies)]
    service = _Service(sources, tmp_path / "UI Test.HMS")
    panel = ProgramAssemblyPanel(service)
    panel.bind_project(_Session(first.project_id, service.root))
    return panel, service, sources


def _add(panel: ProgramAssemblyPanel, source, name: str) -> None:
    panel.set_selected_operation(source.operation.operation_id, operation_name=name)
    assert panel.add_selected_operation()


def _apply_operation(panel: ProgramAssemblyPanel, safe_z: float = 10.0) -> None:
    panel.safe_z_spin.setValue(safe_z)
    assert panel.apply_operation_draft()


def _make_valid(panel: ProgramAssemblyPanel, count: int) -> None:
    for row in range(count):
        panel.operation_table.selectRow(row)
        _apply_operation(panel, 10.0 + row)
    panel.gate_combo.setCurrentText("OPTIONAL")
    assert panel.apply_shared_draft()
    diagnostics = panel.validate_assembly()
    assert not any(item.severity.value == "error" for item in diagnostics)


def test_initial_empty_and_typed_selection() -> None:
    panel, _, _ = _panel(Path("."))
    assert panel.ordered_operation_ids == ()
    assert panel.state.assembly_status is ProgramAssemblyUiStatus.MISSING
    assert not panel.add_button.isEnabled()
    panel.deleteLater()


def test_add_duplicate_remove_move_clear_and_explicit_identity(tmp_path: Path) -> None:
    panel, _, sources = _panel(tmp_path, "pocket_2_5d", "contour_2d")
    _add(panel, sources[0], "Facing")
    _add(panel, sources[1], "Pocket")
    _add(panel, sources[2], "Contour")
    expected = tuple(source.operation.operation_id for source in sources)
    assert panel.ordered_operation_ids == expected
    panel.set_selected_operation(sources[0].operation.operation_id)
    assert not panel.add_selected_operation()
    assert any(
        diagnostic.code is ProgramAssemblyDiagnosticCode.DUPLICATE_OPERATION
        for diagnostic in panel._diagnostic_values
    )
    panel.operation_table.selectRow(2)
    assert panel.move_selected_operation(-1)
    assert panel.ordered_operation_ids == (expected[0], expected[2], expected[1])
    assert panel.move_selected_operation(1)
    assert panel.ordered_operation_ids == expected
    panel.operation_table.selectRow(1)
    assert panel.remove_selected_operation()
    assert panel.ordered_operation_ids == (expected[0], expected[2])
    panel.clear_operation_list()
    assert panel.ordered_operation_ids == ()
    panel.deleteLater()


def test_add_rejects_different_job_without_mutating_order(tmp_path: Path) -> None:
    panel, service, sources = _panel(tmp_path, "pocket_2_5d")
    _add(panel, sources[0], "Facing")
    first_setup = service.cam_snapshot.jobs[0].setups[0]
    first_setup.operation_tree.operations = [sources[0].operation]
    second_setup = SimpleNamespace(
        setup_id=sources[1].setup.setup_id,
        operation_tree=SimpleNamespace(operations=[sources[1].operation]),
    )
    other_job = SimpleNamespace(
        job_id=CamJobId.new(),
        setups=(second_setup,),
    )
    service.cam_snapshot = SimpleNamespace(
        jobs=(service.cam_snapshot.jobs[0], other_job)
    )
    panel.set_selected_operation(sources[1].operation.operation_id, operation_name="Pocket")
    assert not panel.add_selected_operation()
    assert panel.ordered_operation_ids == (sources[0].operation.operation_id,)
    assert any(
        diagnostic.code is ProgramAssemblyDiagnosticCode.SETUP_MISMATCH
        for diagnostic in panel._diagnostic_values
    )
    panel.deleteLater()


def test_invalid_operation_draft_rolls_back_and_set_equal_offsets(tmp_path: Path) -> None:
    panel, _, sources = _panel(tmp_path)
    _add(panel, sources[0], "Facing")
    operation_id = sources[0].operation.operation_id
    panel.safe_z_spin.setValue(0.0)
    assert not panel.apply_operation_draft()
    assert panel._operation_drafts[operation_id].safe_z is None
    panel.tool_station_spin.setValue(7)
    panel.equalize_offsets()
    assert panel.tool_station_spin.value() == 7
    assert panel.length_offset_spin.value() == 7
    assert panel.diameter_offset_spin.value() == 7
    _apply_operation(panel)
    panel.deleteLater()


def test_tapping_is_visible_but_blocks_whole_assembly(tmp_path: Path) -> None:
    panel, _, sources = _panel(tmp_path, "tapping_v1")
    _add(panel, sources[0], "Facing")
    _add(panel, sources[1], "Tapping")
    for row in range(2):
        panel.operation_table.selectRow(row)
        _apply_operation(panel, 10.0 + row)
    panel.gate_combo.setCurrentText("OPTIONAL")
    assert panel.apply_shared_draft()
    panel.validate_assembly()
    assert any(
        diagnostic.code is ProgramAssemblyDiagnosticCode.UNSUPPORTED_TAPPING
        for diagnostic in panel._diagnostic_values
    )
    assert not panel.generate_button.isEnabled()
    panel.deleteLater()


def test_two_operation_generation_preview_and_reorder_checksum(tmp_path: Path) -> None:
    panel, _, sources = _panel(tmp_path, "pocket_2_5d")
    _add(panel, sources[0], "Facing")
    _add(panel, sources[1], "Pocket")
    _make_valid(panel, 2)
    first = panel.generate_sync()
    assert first is not None
    assert panel.state.assembly_status is ProgramAssemblyUiStatus.CURRENT
    assert panel.preview_source_text == first.canonical_text
    assert "NOT CERTIFIED / REVIEW REQUIRED" in panel.metadata_label.text()
    assert len(panel.navigation) == 2
    assert panel.jump_to_operation(sources[1].operation.operation_id)
    checksum = first.output_checksum
    panel.operation_table.selectRow(1)
    assert panel.move_selected_operation(-1)
    _make_valid(panel, 2)
    second = panel.generate_sync()
    assert second is not None
    assert second.output_checksum != checksum
    panel.deleteLater()


def test_source_recompute_marks_result_stale_without_auto_generate(tmp_path: Path) -> None:
    panel, service, sources = _panel(tmp_path, "pocket_2_5d")
    _add(panel, sources[0], "Facing")
    _make_valid(panel, 1)
    assert panel.generate_sync() is not None
    service.sources.pop(sources[0].operation.operation_id)
    panel.refresh_sources()
    assert panel.state.assembly_status is ProgramAssemblyUiStatus.STALE
    assert panel.preview_source_text
    panel.deleteLater()


def test_metadata_parser_is_deterministic_and_rejects_duplicates() -> None:
    assert parse_global_metadata("part=ABC; customer=HMS") == (
        ("customer", "HMS"),
        ("part", "ABC"),
    )
    with pytest.raises(ValueError):
        parse_global_metadata("part=ABC; part=DEF")


def test_managed_save_external_failure_retains_managed_and_explicit_clear(tmp_path: Path) -> None:
    panel, service, sources = _panel(tmp_path, "pocket_2_5d")
    service.root.mkdir(parents=True, exist_ok=True)
    service._exporter = NCExportService()
    service._exporter.bind_project(service.root, sources[0].project_id, 1)
    service.nc_export_service = service._exporter
    _add(panel, sources[0], "Facing")
    _make_valid(panel, 1)
    assert panel.generate_sync() is not None
    managed = panel.save_managed_artifact()
    assert managed is not None
    assert panel._managed_entry is not None
    managed_path = service.root / panel._managed_entry.output_relative_path
    assert managed_path.is_file()
    missing_destination = tmp_path / "missing-target"
    panel.target_edit.setText(str(missing_destination))
    panel.create_target_check.setChecked(False)
    assert panel.apply_shared_draft()
    assert panel.export_external(confirm=True) is None
    assert managed_path.is_file()
    panel.clear_managed_artifact(confirm=True)
    assert not managed_path.exists()
    assert not any(
        item.status is NCArtifactStatus.CURRENT
        for item in service._exporter.store.load(service.root, sources[0].project_id).entries
    )
    panel.deleteLater()

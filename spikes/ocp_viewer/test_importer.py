"""Headless STEP/BREP importer and worker checks for spike 4B3."""

from __future__ import annotations

import math
import threading
from dataclasses import fields
from pathlib import Path

from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer
from OCP.TopoDS import TopoDS_Shape

from geometry import create_demo_box
from importer import CadImporter
from model import ImportResult
from worker import ImportWorker


def _write_step(path: Path) -> None:
    writer = STEPControl_Writer()
    assert (
        writer.Transfer(
            create_demo_box(),
            STEPControl_StepModelType.STEPControl_AsIs,
        )
        == IFSelect_ReturnStatus.IFSelect_RetDone
    )
    assert writer.Write(str(path)) == IFSelect_ReturnStatus.IFSelect_RetDone


def _write_brep(path: Path) -> None:
    assert BRepTools.Write_s(create_demo_box(), str(path))


def _assert_valid_box_result(result: ImportResult, detected_format: str) -> None:
    assert result.success
    assert result.detected_format == detected_format
    assert result.shape_id
    assert result.topology_counts == {"solid": 1, "face": 6, "edge": 12}
    assert result.bounding_box is not None
    assert all(math.isfinite(value) for value in result.bounding_box)
    assert result.bounding_box[0] < result.bounding_box[3]
    assert result.bounding_box[1] < result.bounding_box[4]
    assert result.bounding_box[2] < result.bounding_box[5]
    assert not result.errors
    assert result.elapsed_seconds >= 0.0


def test_valid_step_import(tmp_path: Path) -> None:
    source = tmp_path / "box.step"
    _write_step(source)
    progress = []

    result = CadImporter().import_file(source, progress.append)

    _assert_valid_box_result(result, "step")
    assert progress == ["đang đọc", "đang chuyển đổi", "hoàn thành"]


def test_valid_brep_import(tmp_path: Path) -> None:
    source = tmp_path / "box.brep"
    _write_brep(source)

    result = CadImporter().import_file(source)

    _assert_valid_box_result(result, "brep")


def test_missing_file_is_reported(tmp_path: Path) -> None:
    result = CadImporter().import_file(tmp_path / "missing.step")
    assert not result.success
    assert "không tồn tại" in result.errors[0]


def test_corrupt_step_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "broken.step"
    source.write_text("not a STEP model", encoding="utf-8")
    result = CadImporter().import_file(source)
    assert not result.success
    assert "ReadFile status" in result.errors[0]


def test_empty_step_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "empty.step"
    source.touch()
    result = CadImporter().import_file(source)
    assert not result.success
    assert "STEP rỗng" in result.errors[0]


def test_corrupt_brep_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "broken.brep"
    source.write_text("not a BREP model", encoding="utf-8")
    result = CadImporter().import_file(source)
    assert not result.success
    assert "Không đọc được file BREP" in result.errors[0]


def test_null_shape_is_rejected(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "null.brep"
    source.write_text("non-empty", encoding="utf-8")
    importer = CadImporter()
    monkeypatch.setattr(
        importer,
        "_read_brep",
        lambda _path, _progress: (TopoDS_Shape(), ()),
    )

    result = importer.import_file(source)

    assert not result.success
    assert "shape rỗng" in result.errors[0]


def test_import_result_contains_no_topods(tmp_path: Path) -> None:
    source = tmp_path / "box.brep"
    _write_brep(source)
    result = CadImporter().import_file(source)

    assert all(
        not isinstance(getattr(result, field.name), TopoDS_Shape)
        for field in fields(result)
    )


def test_worker_runs_import_off_main_thread_without_viewer(tmp_path: Path) -> None:
    source = tmp_path / "box.brep"
    _write_brep(source)
    importer = CadImporter()
    worker = ImportWorker(importer, source)
    main_thread_id = threading.get_ident()
    worker_thread_ids = []
    worker_results = []
    original_import = importer.import_file

    def record_thread(path, progress=None):
        worker_thread_ids.append(threading.get_ident())
        result = original_import(path, progress)
        worker_results.append(result)
        return result

    importer.import_file = record_thread
    thread = threading.Thread(target=worker.run)
    thread.start()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert worker_thread_ids and worker_thread_ids[0] != main_thread_id
    assert not hasattr(worker, "viewer")
    assert worker_results and worker_results[0].success

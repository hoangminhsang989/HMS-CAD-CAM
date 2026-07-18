"""Headless STEP/BREP import service for the isolated OCP spike."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from OCP.BRep import BRep_Builder
from OCP.BRepTools import BRepTools
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopoDS import TopoDS_Shape

from geometry import shape_bounds, topology_counts
from model import ImportResult

ProgressCallback = Callable[[str], None]
ShapePresenter = Callable[[TopoDS_Shape], None]


class ImportFailure(RuntimeError):
    """Represent a controlled CAD input validation or translation failure."""


class CadImporter:
    """Import shapes off-thread while exposing only metadata results."""

    def __init__(self) -> None:
        self._shapes: dict[str, TopoDS_Shape] = {}
        self._shape_lock = threading.Lock()

    def import_file(
        self,
        source_path: str | Path,
        progress: ProgressCallback | None = None,
    ) -> ImportResult:
        """Read a STEP or BREP file and retain its OCCT shape behind an ID."""
        started = perf_counter()
        path = Path(source_path).resolve(strict=False)
        detected_format = self._detect_format(path)
        notify = progress or (lambda _status: None)
        warnings: tuple[str, ...] = ()
        try:
            notify("đang đọc")
            self._validate_source(path, detected_format)
            if detected_format == "step":
                shape, warnings = self._read_step(path, notify)
            else:
                shape, warnings = self._read_brep(path, notify)
            if shape.IsNull():
                raise ImportFailure("CAD reader trả về shape rỗng.")

            shape_id = f"import:{uuid4().hex}"
            result = ImportResult(
                success=True,
                source_path=str(path),
                detected_format=detected_format,
                shape_id=shape_id,
                topology_counts=topology_counts(shape),
                bounding_box=shape_bounds(shape),
                warnings=warnings,
                errors=(),
                elapsed_seconds=perf_counter() - started,
            )
            with self._shape_lock:
                self._shapes[shape_id] = shape
            notify("hoàn thành")
            return result
        except (ImportFailure, OSError) as error:
            notify("lỗi")
            return self._failure_result(
                path,
                detected_format,
                str(error),
                started,
            )
        except Exception as error:
            logging.getLogger(__name__).exception("Lỗi OCP không dự kiến khi import CAD")
            notify("lỗi")
            return self._failure_result(
                path,
                detected_format,
                f"Lỗi OCP khi import: {error}",
                started,
            )

    def present_shape(self, shape_id: str, presenter: ShapePresenter) -> None:
        """Resolve and present a retained shape without exposing it to UI results."""
        with self._shape_lock:
            shape = self._shapes.get(shape_id)
        if shape is None:
            raise KeyError(f"Không tìm thấy imported shape: {shape_id}")
        presenter(shape)
        with self._shape_lock:
            if self._shapes.get(shape_id) is shape:
                self._shapes.pop(shape_id)

    def discard_result(self, result: ImportResult) -> None:
        """Release a retained shape when its UI consumer is closing or gone."""
        if result.shape_id is None:
            return
        with self._shape_lock:
            self._shapes.pop(result.shape_id, None)

    @staticmethod
    def _detect_format(path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in {".step", ".stp"}:
            return "step"
        if suffix in {".brep", ".brp"}:
            return "brep"
        return "unknown"

    @staticmethod
    def _validate_source(path: Path, detected_format: str) -> None:
        if detected_format == "unknown":
            raise ImportFailure("Chỉ hỗ trợ STEP/STP và BREP trong spike 4B3.")
        if not path.is_file():
            raise ImportFailure(f"File không tồn tại: {path}")
        if path.stat().st_size == 0:
            raise ImportFailure(f"File {detected_format.upper()} rỗng.")

    @staticmethod
    def _read_step(
        path: Path,
        progress: ProgressCallback,
    ) -> tuple[TopoDS_Shape, tuple[str, ...]]:
        reader = STEPControl_Reader()
        status = reader.ReadFile(str(path))
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise ImportFailure(f"Không đọc được STEP; ReadFile status={status.name}.")
        roots = reader.NbRootsForTransfer()
        if roots <= 0:
            raise ImportFailure("STEP không có root hợp lệ để chuyển đổi.")
        progress("đang chuyển đổi")
        transferred = reader.TransferRoots()
        if transferred <= 0:
            raise ImportFailure("STEP không chuyển đổi được root nào.")
        shape = reader.OneShape()
        if shape.IsNull():
            raise ImportFailure("STEP chuyển đổi thành shape rỗng.")
        warnings = ()
        if transferred < roots:
            warnings = (f"Chỉ chuyển đổi {transferred}/{roots} STEP roots.",)
        return shape, warnings

    @staticmethod
    def _read_brep(
        path: Path,
        progress: ProgressCallback,
    ) -> tuple[TopoDS_Shape, tuple[str, ...]]:
        shape = TopoDS_Shape()
        builder = BRep_Builder()
        if not BRepTools.Read_s(shape, str(path), builder):
            raise ImportFailure("Không đọc được file BREP.")
        progress("đang chuyển đổi")
        if shape.IsNull():
            raise ImportFailure("BREP reader trả về shape rỗng.")
        return shape, ()

    @staticmethod
    def _failure_result(
        path: Path,
        detected_format: str,
        error: str,
        started: float,
    ) -> ImportResult:
        return ImportResult(
            success=False,
            source_path=str(path),
            detected_format=detected_format,
            shape_id=None,
            topology_counts={},
            bounding_box=None,
            warnings=(),
            errors=(error,),
            elapsed_seconds=perf_counter() - started,
        )

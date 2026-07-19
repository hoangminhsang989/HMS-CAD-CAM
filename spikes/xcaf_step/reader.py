"""All-or-nothing STEPCAF reader for the isolated 6A.1 spike."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Quantity import Quantity_Color
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (
    XCAFDoc_ColorTool,
    XCAFDoc_ColorType,
    XCAFDoc_DocumentTool,
    XCAFDoc_ShapeTool,
)

from spikes.xcaf_step.model import (
    XcafColor,
    XcafImportReport,
    XcafNodeRole,
    XcafOccurrenceRecord,
    XcafProductRecord,
    XcafSourceAppearance,
    XcafSubshapeAppearance,
    XcafTransform,
)


class XcafImportError(RuntimeError):
    """Controlled validation, STEP read or XCAF transfer failure."""


class _ReaderProtocol(Protocol):
    def SetColorMode(self, enabled: bool) -> None: ...
    def SetNameMode(self, enabled: bool) -> None: ...
    def SetSHUOMode(self, enabled: bool) -> None: ...
    def ReadFile(self, path: str) -> IFSelect_ReturnStatus: ...
    def Transfer(self, document: TDocStd_Document) -> bool: ...


ReaderFactory = Callable[[], _ReaderProtocol]


@dataclass(slots=True)
class _NativeXcafDocument:
    """Native state intentionally confined to this module."""

    document: TDocStd_Document
    product_labels: dict[str, TDF_Label]
    occurrence_labels: dict[str, TDF_Label]


class XcafStepSession:
    """Own one native XCAF document while exposing only pure spike records."""

    def __init__(self, reader_factory: ReaderFactory = STEPCAFControl_Reader) -> None:
        self._reader_factory = reader_factory
        self._native: _NativeXcafDocument | None = None
        self._last_report: XcafImportReport | None = None

    def __enter__(self) -> "XcafStepSession":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def has_native_document(self) -> bool:
        """Report whether this session currently retains an OCP document."""
        return self._native is not None

    @property
    def retained_label_count(self) -> int:
        """Return the number of internal label handles retained by the spike."""
        native = self._native
        return 0 if native is None else len(native.product_labels) + len(native.occurrence_labels)

    @property
    def last_report(self) -> XcafImportReport | None:
        """Return the last committed pure report, if any."""
        return self._last_report

    def import_file(self, source_path: str | Path) -> XcafImportReport:
        """Read and transfer STEP into a temporary XCAF document atomically."""
        path = Path(source_path).resolve(strict=False)
        self._validate_source(path)
        XCAFApp_Application.GetApplication_s()
        document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
        reader = self._reader_factory()
        reader.SetColorMode(True)
        reader.SetNameMode(True)
        reader.SetSHUOMode(True)
        status = reader.ReadFile(str(path))
        if status != IFSelect_ReturnStatus.IFSelect_RetDone:
            raise XcafImportError(f"Cannot read STEP; ReadFile status={status.name}")
        if not reader.Transfer(document):
            raise XcafImportError("STEPCAF transfer failed")

        shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        color_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
        report, product_labels, occurrence_labels = _build_report(
            path,
            shape_tool,
            color_tool,
        )
        self.close()
        self._native = _NativeXcafDocument(document, product_labels, occurrence_labels)
        self._last_report = report
        return report

    def close(self) -> None:
        """Release every native document and label retained by this spike."""
        native = self._native
        if native is not None:
            native.product_labels.clear()
            native.occurrence_labels.clear()
        self._native = None
        self._last_report = None

    @staticmethod
    def _validate_source(path: Path) -> None:
        if path.suffix.lower() not in {".step", ".stp"}:
            raise XcafImportError("XCAF spike only accepts STEP/STP")
        if not path.is_file():
            raise XcafImportError(f"STEP source does not exist: {path}")
        if path.stat().st_size == 0:
            raise XcafImportError("STEP source is empty")


def _build_report(
    path: Path,
    shape_tool: XCAFDoc_ShapeTool,
    color_tool: XCAFDoc_ColorTool,
) -> tuple[XcafImportReport, dict[str, TDF_Label], dict[str, TDF_Label]]:
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    if free_shapes.Length() <= 0:
        raise XcafImportError("Transferred XCAF document has no free shapes")

    products: dict[str, XcafProductRecord] = {}
    product_labels: dict[str, TDF_Label] = {}
    occurrences: list[XcafOccurrenceRecord] = []
    occurrence_labels: dict[str, TDF_Label] = {}
    root_ids: list[str] = []

    def ensure_product(label: TDF_Label) -> XcafProductRecord:
        internal_entry = _label_entry(label)
        product_id = _spike_id("product", internal_entry)
        existing = products.get(product_id)
        if existing is not None:
            return existing
        role = _role(shape_tool, label)
        product_name = _read_name(label)
        name, _source = _resolve_name(None, product_name, role, product_id)
        subshape_appearances = _read_subshape_appearances(
            shape_tool,
            color_tool,
            label,
            product_id,
        )
        record = XcafProductRecord(
            product_id=product_id,
            role=role,
            name=name,
            source_appearance=_read_source_appearance(color_tool, label),
            subshape_appearances=subshape_appearances,
        )
        products[product_id] = record
        product_labels[product_id] = label
        return record

    def visit(
        occurrence_label: TDF_Label,
        product_label: TDF_Label,
        parent_id: str | None,
        parent_absolute: XcafTransform,
        path_entries: tuple[str, ...],
        is_root: bool = False,
    ) -> str:
        product = ensure_product(product_label)
        entry = _label_entry(occurrence_label)
        occurrence_path = path_entries + (entry,)
        occurrence_id = _spike_id("occurrence", "/".join(occurrence_path))
        local = _location_transform(XCAFDoc_ShapeTool.GetLocation_s(occurrence_label))
        absolute = parent_absolute.compose(local)
        occurrence_name = None if is_root else _read_name(occurrence_label)
        name, name_source = _resolve_name(
            occurrence_name,
            _read_name(product_label),
            product.role,
            occurrence_id,
        )
        child_ids: list[str] = []
        if product.role is XcafNodeRole.ASSEMBLY:
            components = TDF_LabelSequence()
            if not XCAFDoc_ShapeTool.GetComponents_s(product_label, components, False):
                raise XcafImportError(
                    f"Assembly product has no components: {product.product_id}"
                )
            for index in range(1, components.Length() + 1):
                component_label = components.Value(index)
                referred_label = TDF_Label()
                if not XCAFDoc_ShapeTool.GetReferredShape_s(component_label, referred_label):
                    raise XcafImportError("XCAF component is not a valid product reference")
                child_ids.append(
                    visit(
                        component_label,
                        referred_label,
                        occurrence_id,
                        absolute,
                        occurrence_path,
                    )
                )
        occurrences.append(
            XcafOccurrenceRecord(
                occurrence_id=occurrence_id,
                product_id=product.product_id,
                parent_occurrence_id=parent_id,
                role=product.role,
                name=name,
                name_source=name_source,
                local_transform=local,
                absolute_transform=absolute,
                source_appearance=(
                    _read_source_appearance(color_tool, occurrence_label)
                    if is_root
                    else _read_occurrence_appearance(color_tool, occurrence_label)
                ),
                child_occurrence_ids=tuple(child_ids),
            )
        )
        occurrence_labels[occurrence_id] = occurrence_label
        return occurrence_id

    for index in range(1, free_shapes.Length() + 1):
        root_label = free_shapes.Value(index)
        root_ids.append(
            visit(
                root_label,
                root_label,
                None,
                XcafTransform.identity(),
                (),
                True,
            )
        )
    report = XcafImportReport(
        source_path=str(path),
        root_occurrence_ids=tuple(root_ids),
        products=tuple(products.values()),
        occurrences=tuple(occurrences),
    )
    return report, product_labels, occurrence_labels


def _role(shape_tool: XCAFDoc_ShapeTool, label: TDF_Label) -> XcafNodeRole:
    return XcafNodeRole.ASSEMBLY if shape_tool.IsAssembly_s(label) else XcafNodeRole.PART


def _read_name(label: TDF_Label) -> str | None:
    attribute = TDataStd_Name()
    if not label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return None
    value = attribute.Get().ToExtString().strip()
    return value or None


def _resolve_name(
    occurrence_name: str | None,
    product_name: str | None,
    role: XcafNodeRole,
    public_id: str,
) -> tuple[str, str]:
    semantic_occurrence_name = _semantic_occurrence_name(occurrence_name)
    if semantic_occurrence_name is not None:
        return semantic_occurrence_name, "occurrence"
    if product_name and product_name.strip():
        return product_name.strip(), "product"
    prefix = "Assembly" if role is XcafNodeRole.ASSEMBLY else "Part"
    return f"{prefix} {public_id.rsplit(':', 1)[-1][:8]}", "generated"


def _semantic_occurrence_name(value: str | None) -> str | None:
    """Discard empty and STEP-writer bookkeeping names before fallback."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped.isdecimal() or stripped.startswith("=>["):
        return None
    return stripped


def _read_source_appearance(
    color_tool: XCAFDoc_ColorTool,
    label: TDF_Label,
) -> XcafSourceAppearance:
    return XcafSourceAppearance(
        generic_color=_read_color(color_tool, label, XCAFDoc_ColorType.XCAFDoc_ColorGen),
        surface_color=_read_color(color_tool, label, XCAFDoc_ColorType.XCAFDoc_ColorSurf),
        curve_color=_read_color(color_tool, label, XCAFDoc_ColorType.XCAFDoc_ColorCurv),
    )


def _read_occurrence_appearance(
    color_tool: XCAFDoc_ColorTool,
    occurrence_label: TDF_Label,
) -> XcafSourceAppearance:
    """Read direct label colors and SHUO instance colors for one component."""
    direct = _read_source_appearance(color_tool, occurrence_label)
    shape = XCAFDoc_ShapeTool.GetShape_s(occurrence_label)
    return XcafSourceAppearance(
        generic_color=direct.generic_color
        or _read_instance_color(color_tool, shape, XCAFDoc_ColorType.XCAFDoc_ColorGen),
        surface_color=direct.surface_color
        or _read_instance_color(color_tool, shape, XCAFDoc_ColorType.XCAFDoc_ColorSurf),
        curve_color=direct.curve_color
        or _read_instance_color(color_tool, shape, XCAFDoc_ColorType.XCAFDoc_ColorCurv),
    )


def _read_instance_color(
    color_tool: XCAFDoc_ColorTool,
    shape: object,
    color_type: XCAFDoc_ColorType,
) -> XcafColor | None:
    native = Quantity_Color()
    if not color_tool.GetInstanceColor(shape, color_type, native):
        return None
    return XcafColor(native.Red(), native.Green(), native.Blue())


def _read_color(
    color_tool: XCAFDoc_ColorTool,
    label: TDF_Label,
    color_type: XCAFDoc_ColorType,
) -> XcafColor | None:
    native = Quantity_Color()
    if not color_tool.GetColor_s(label, color_type, native):
        return None
    return XcafColor(native.Red(), native.Green(), native.Blue())


def _read_subshape_appearances(
    shape_tool: XCAFDoc_ShapeTool,
    color_tool: XCAFDoc_ColorTool,
    product_label: TDF_Label,
    product_id: str,
) -> tuple[XcafSubshapeAppearance, ...]:
    labels = TDF_LabelSequence()
    if not shape_tool.GetSubShapes_s(product_label, labels):
        return ()
    result: list[XcafSubshapeAppearance] = []
    for index in range(1, labels.Length() + 1):
        label = labels.Value(index)
        appearance = _read_source_appearance(color_tool, label)
        if appearance != XcafSourceAppearance():
            result.append(
                XcafSubshapeAppearance(
                    subshape_id=_spike_id("subshape", f"{product_id}/{_label_entry(label)}"),
                    source_appearance=appearance,
                )
            )
    return tuple(result)


def _location_transform(location: TopLoc_Location) -> XcafTransform:
    native = location.Transformation()
    return XcafTransform(
        (
            native.Value(1, 1), native.Value(1, 2), native.Value(1, 3), native.Value(1, 4),
            native.Value(2, 1), native.Value(2, 2), native.Value(2, 3), native.Value(2, 4),
            native.Value(3, 1), native.Value(3, 2), native.Value(3, 3), native.Value(3, 4),
            0.0, 0.0, 0.0, 1.0,
        )
    )


def _label_entry(label: TDF_Label) -> str:
    value = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, value)
    return value.ToCString()


def _spike_id(kind: str, internal_value: str) -> str:
    digest = hashlib.sha256(internal_value.encode("ascii")).hexdigest()[:20]
    return f"xcaf-spike-{kind}:{digest}"

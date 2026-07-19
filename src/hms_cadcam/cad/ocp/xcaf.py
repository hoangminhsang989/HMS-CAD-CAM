"""Internal XCAF document conversion and native label ownership helpers."""

from __future__ import annotations

from dataclasses import dataclass

from OCP.Quantity import Quantity_Color
from OCP.TCollection import TCollection_AsciiString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label, TDF_LabelSequence, TDF_Tool
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Shape
from OCP.XCAFDoc import XCAFDoc_ColorTool, XCAFDoc_ColorType, XCAFDoc_ShapeTool

from hms_cadcam.cad.exceptions import CadImportError
from hms_cadcam.cad.models import (
    CadDocumentId,
    CadDocumentKind,
    XcafAssemblyMetadata,
    XcafColor,
    XcafNameSource,
    XcafNodeRole,
    XcafOccurrenceId,
    XcafOccurrenceMetadata,
    XcafProductId,
    XcafProductMetadata,
    XcafSourceAppearance,
    XcafSubshapeAppearance,
    XcafTransform,
)


@dataclass(slots=True)
class OcpXcafImportPayload:
    """Native STEPCAF result that never crosses the OCP adapter boundary."""

    document: TDocStd_Document
    shape_tool: XCAFDoc_ShapeTool
    color_tool: XCAFDoc_ColorTool
    shape: TopoDS_Shape
    warnings: tuple[str, ...] = ()


@dataclass(slots=True)
class OcpXcafDocumentData:
    """Native indexes plus public immutable metadata for one XCAF document."""

    document_kind: CadDocumentKind
    assembly_metadata: XcafAssemblyMetadata
    products: dict[XcafProductId, XcafProductMetadata]
    occurrences: dict[XcafOccurrenceId, XcafOccurrenceMetadata]
    product_labels: dict[XcafProductId, TDF_Label]
    occurrence_labels: dict[XcafOccurrenceId, TDF_Label]
    absolute_locations: dict[XcafOccurrenceId, TopLoc_Location]
    product_ids_by_label_entry: dict[str, XcafProductId]
    occurrence_ids_by_label_entry: dict[str, tuple[XcafOccurrenceId, ...]]

    def release(self) -> None:
        """Drop all retained native labels and locations before record release."""
        self.product_labels.clear()
        self.occurrence_labels.clear()
        self.absolute_locations.clear()
        self.product_ids_by_label_entry.clear()
        self.occurrence_ids_by_label_entry.clear()


def build_xcaf_document_data(
    document_id: CadDocumentId,
    payload: OcpXcafImportPayload,
) -> OcpXcafDocumentData:
    """Build runtime-scoped public metadata and private label indexes."""
    free_shapes = TDF_LabelSequence()
    payload.shape_tool.GetFreeShapes(free_shapes)
    if free_shapes.Length() <= 0:
        raise CadImportError("Transferred XCAF document has no free shapes")

    products: dict[XcafProductId, XcafProductMetadata] = {}
    occurrences: dict[XcafOccurrenceId, XcafOccurrenceMetadata] = {}
    product_labels: dict[XcafProductId, TDF_Label] = {}
    occurrence_labels: dict[XcafOccurrenceId, TDF_Label] = {}
    absolute_locations: dict[XcafOccurrenceId, TopLoc_Location] = {}
    product_ids_by_entry: dict[str, XcafProductId] = {}
    occurrence_ids_by_entry: dict[str, list[XcafOccurrenceId]] = {}
    root_ids: list[XcafOccurrenceId] = []
    product_counter = 0
    occurrence_counter = 0

    def ensure_product(label: TDF_Label) -> XcafProductMetadata:
        nonlocal product_counter
        entry = _label_entry(label)
        existing_id = product_ids_by_entry.get(entry)
        if existing_id is not None:
            return products[existing_id]
        product_counter += 1
        product_id = XcafProductId(f"{document_id}:xcaf-product:{product_counter}")
        role = _role(payload.shape_tool, label)
        name = _resolve_name(None, _read_name(label), role, product_id.value)[0]
        product = XcafProductMetadata(
            document_id=document_id,
            product_id=product_id,
            role=role,
            name=name,
            source_appearance=_read_source_appearance(payload.color_tool, label),
            subshape_appearances=_read_subshape_appearances(
                payload.shape_tool,
                payload.color_tool,
                label,
                product_id,
            ),
        )
        products[product_id] = product
        product_labels[product_id] = label
        product_ids_by_entry[entry] = product_id
        return product

    def visit(
        occurrence_label: TDF_Label,
        product_label: TDF_Label,
        parent_id: XcafOccurrenceId | None,
        parent_location: TopLoc_Location,
        is_root: bool = False,
    ) -> XcafOccurrenceId:
        nonlocal occurrence_counter
        product = ensure_product(product_label)
        occurrence_counter += 1
        occurrence_id = XcafOccurrenceId(
            f"{document_id}:xcaf-occurrence:{occurrence_counter}"
        )
        local_location = XCAFDoc_ShapeTool.GetLocation_s(occurrence_label)
        absolute_location = parent_location.Multiplied(local_location)
        occurrence_name = None if is_root else _read_name(occurrence_label)
        name, name_source = _resolve_name(
            occurrence_name,
            _read_name(product_label),
            product.role,
            occurrence_id.value,
        )
        child_ids: list[XcafOccurrenceId] = []
        if product.role is XcafNodeRole.ASSEMBLY:
            components = TDF_LabelSequence()
            if not XCAFDoc_ShapeTool.GetComponents_s(
                product_label,
                components,
                False,
            ):
                raise CadImportError(
                    f"XCAF assembly has no components: {product.product_id}"
                )
            for index in range(1, components.Length() + 1):
                component_label = components.Value(index)
                referred_label = TDF_Label()
                if not XCAFDoc_ShapeTool.GetReferredShape_s(
                    component_label,
                    referred_label,
                ):
                    raise CadImportError("XCAF component is not a valid reference")
                child_ids.append(
                    visit(
                        component_label,
                        referred_label,
                        occurrence_id,
                        absolute_location,
                    )
                )
        occurrence = XcafOccurrenceMetadata(
            document_id=document_id,
            occurrence_id=occurrence_id,
            product_id=product.product_id,
            parent_occurrence_id=parent_id,
            role=product.role,
            name=name,
            name_source=name_source,
            local_transform=_location_transform(local_location),
            absolute_transform=_location_transform(absolute_location),
            source_appearance=(
                _read_source_appearance(payload.color_tool, occurrence_label)
                if is_root
                else _read_occurrence_appearance(payload.color_tool, occurrence_label)
            ),
            child_occurrence_ids=tuple(child_ids),
        )
        occurrences[occurrence_id] = occurrence
        occurrence_labels[occurrence_id] = occurrence_label
        absolute_locations[occurrence_id] = absolute_location
        occurrence_ids_by_entry.setdefault(
            _label_entry(occurrence_label),
            [],
        ).append(occurrence_id)
        return occurrence_id

    identity = TopLoc_Location()
    for index in range(1, free_shapes.Length() + 1):
        root_label = free_shapes.Value(index)
        root_ids.append(visit(root_label, root_label, None, identity, True))

    document_kind = (
        CadDocumentKind.XCAF_ASSEMBLY
        if any(product.role is XcafNodeRole.ASSEMBLY for product in products.values())
        else CadDocumentKind.XCAF_PART
    )
    assembly_metadata = XcafAssemblyMetadata(
        document_id=document_id,
        root_occurrence_ids=tuple(root_ids),
        product_ids=tuple(products),
        occurrence_ids=tuple(occurrences),
    )
    return OcpXcafDocumentData(
        document_kind=document_kind,
        assembly_metadata=assembly_metadata,
        products=products,
        occurrences=occurrences,
        product_labels=product_labels,
        occurrence_labels=occurrence_labels,
        absolute_locations=absolute_locations,
        product_ids_by_label_entry=product_ids_by_entry,
        occurrence_ids_by_label_entry={
            entry: tuple(ids) for entry, ids in occurrence_ids_by_entry.items()
        },
    )


def resolve_occurrence_shape(
    payload: OcpXcafImportPayload,
    data: OcpXcafDocumentData,
    occurrence_id: XcafOccurrenceId,
) -> TopoDS_Shape:
    """Resolve one occurrence with its accumulated absolute location."""
    occurrence = data.occurrences.get(occurrence_id)
    if occurrence is None:
        raise KeyError(f"XCAF occurrence not found: {occurrence_id}")
    product_label = data.product_labels[occurrence.product_id]
    shape = XCAFDoc_ShapeTool.GetShape_s(product_label)
    if shape.IsNull():
        raise CadImportError(f"XCAF product has no shape: {occurrence.product_id}")
    return shape.Located(data.absolute_locations[occurrence_id], False)


def _role(shape_tool: XCAFDoc_ShapeTool, label: TDF_Label) -> XcafNodeRole:
    return (
        XcafNodeRole.ASSEMBLY
        if shape_tool.IsAssembly_s(label)
        else XcafNodeRole.PART
    )


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
) -> tuple[str, XcafNameSource]:
    semantic_name = _semantic_occurrence_name(occurrence_name)
    if semantic_name is not None:
        return semantic_name, XcafNameSource.OCCURRENCE
    if product_name and product_name.strip():
        return product_name.strip(), XcafNameSource.PRODUCT
    prefix = "Assembly" if role is XcafNodeRole.ASSEMBLY else "Part"
    return f"{prefix} {public_id.rsplit(':', 1)[-1]}", XcafNameSource.GENERATED


def _semantic_occurrence_name(value: str | None) -> str | None:
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
        generic_color=_read_color(
            color_tool,
            label,
            XCAFDoc_ColorType.XCAFDoc_ColorGen,
        ),
        surface_color=_read_color(
            color_tool,
            label,
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
        ),
        curve_color=_read_color(
            color_tool,
            label,
            XCAFDoc_ColorType.XCAFDoc_ColorCurv,
        ),
    )


def _read_occurrence_appearance(
    color_tool: XCAFDoc_ColorTool,
    occurrence_label: TDF_Label,
) -> XcafSourceAppearance:
    direct = _read_source_appearance(color_tool, occurrence_label)
    shape = XCAFDoc_ShapeTool.GetShape_s(occurrence_label)
    return XcafSourceAppearance(
        generic_color=direct.generic_color
        or _read_instance_color(
            color_tool,
            shape,
            XCAFDoc_ColorType.XCAFDoc_ColorGen,
        ),
        surface_color=direct.surface_color
        or _read_instance_color(
            color_tool,
            shape,
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
        ),
        curve_color=direct.curve_color
        or _read_instance_color(
            color_tool,
            shape,
            XCAFDoc_ColorType.XCAFDoc_ColorCurv,
        ),
    )


def _read_color(
    color_tool: XCAFDoc_ColorTool,
    label: TDF_Label,
    color_type: XCAFDoc_ColorType,
) -> XcafColor | None:
    native = Quantity_Color()
    if not color_tool.GetColor_s(label, color_type, native):
        return None
    return XcafColor(native.Red(), native.Green(), native.Blue())


def _read_instance_color(
    color_tool: XCAFDoc_ColorTool,
    shape: TopoDS_Shape,
    color_type: XCAFDoc_ColorType,
) -> XcafColor | None:
    native = Quantity_Color()
    if not color_tool.GetInstanceColor(shape, color_type, native):
        return None
    return XcafColor(native.Red(), native.Green(), native.Blue())


def _read_subshape_appearances(
    shape_tool: XCAFDoc_ShapeTool,
    color_tool: XCAFDoc_ColorTool,
    product_label: TDF_Label,
    product_id: XcafProductId,
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
                    subshape_id=f"{product_id}:subshape:{len(result) + 1}",
                    source_appearance=appearance,
                )
            )
    return tuple(result)


def _location_transform(location: TopLoc_Location) -> XcafTransform:
    native = location.Transformation()
    return XcafTransform(
        (
            native.Value(1, 1),
            native.Value(1, 2),
            native.Value(1, 3),
            native.Value(1, 4),
            native.Value(2, 1),
            native.Value(2, 2),
            native.Value(2, 3),
            native.Value(2, 4),
            native.Value(3, 1),
            native.Value(3, 2),
            native.Value(3, 3),
            native.Value(3, 4),
            0.0,
            0.0,
            0.0,
            1.0,
        )
    )


def _label_entry(label: TDF_Label) -> str:
    value = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, value)
    return value.ToCString()

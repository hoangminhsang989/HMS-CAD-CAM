"""Create a small real XCAF assembly STEP fixture in a caller-owned temp folder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from OCP.BRep import BRep_Builder
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_StepModelType
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Compound
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (
    XCAFDoc_ColorType,
    XCAFDoc_DocumentTool,
    XCAFDoc_ShapeTool,
)
from OCP.gp import gp_Trsf, gp_Vec


@dataclass(frozen=True, slots=True)
class XcafFixtureExpectations:
    """Pure values shared by the fixture and its round-trip tests."""

    root_product_name: str = "Fixture Root Assembly"
    repeated_product_name: str = "Repeated Product"
    first_occurrence_name: str = "Repeated occurrence A"
    nested_product_name: str = "Nested Assembly Product"
    nested_occurrence_name: str = "Nested assembly occurrence"
    nested_part_name: str = "Nested Part Product"
    repeated_product_surface_color: tuple[float, float, float] = (0.82, 0.12, 0.08)
    first_occurrence_color: tuple[float, float, float] = (0.15, 0.75, 0.25)
    nested_part_surface_color: tuple[float, float, float] = (0.10, 0.25, 0.85)
    subshape_surface_color: tuple[float, float, float] = (0.95, 0.80, 0.10)


def write_xcaf_step_fixture(path: Path) -> XcafFixtureExpectations:
    """Write a nested, colored assembly STEP file only at ``path``."""
    destination = Path(path)
    if destination.suffix.lower() not in {".step", ".stp"}:
        raise ValueError("XCAF fixture path must use .step or .stp")
    destination.parent.mkdir(parents=True, exist_ok=True)
    expected = XcafFixtureExpectations()

    XCAFApp_Application.GetApplication_s()
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(document.Main())
    previous_auto_naming = XCAFDoc_ShapeTool.AutoNaming_s()
    XCAFDoc_ShapeTool.SetAutoNaming_s(False)
    try:
        root_label = shape_tool.AddShape(_empty_compound(), True)
        repeated_shape = BRepPrimAPI_MakeBox(10.0, 8.0, 6.0).Shape()
        repeated_label = shape_tool.AddShape(repeated_shape, False)
        nested_label = shape_tool.AddShape(_empty_compound(), True)
        nested_part_shape = BRepPrimAPI_MakeBox(4.0, 5.0, 7.0).Shape()
        nested_part_label = shape_tool.AddShape(nested_part_shape, False)

        _set_name(root_label, expected.root_product_name)
        _set_name(repeated_label, expected.repeated_product_name)
        _set_name(nested_label, expected.nested_product_name)
        _set_name(nested_part_label, expected.nested_part_name)

        first_occurrence = shape_tool.AddComponent(
            root_label,
            repeated_label,
            _translation(10.0, 0.0, 0.0),
        )
        shape_tool.AddComponent(
            root_label,
            repeated_label,
            _translation(40.0, 0.0, 0.0),
        )
        nested_occurrence = shape_tool.AddComponent(
            root_label,
            nested_label,
            _translation(0.0, 50.0, 0.0),
        )
        shape_tool.AddComponent(
            nested_label,
            nested_part_label,
            _translation(0.0, 0.0, 20.0),
        )
        _set_name(first_occurrence, expected.first_occurrence_name)
        _set_name(nested_occurrence, expected.nested_occurrence_name)

        color_tool.SetColor(
            repeated_label,
            _color(expected.repeated_product_surface_color),
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
        )
        color_tool.SetColor(
            first_occurrence,
            _color(expected.first_occurrence_color),
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
        )
        color_tool.SetColor(
            nested_part_label,
            _color(expected.nested_part_surface_color),
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
        )
        face = TopExp_Explorer(repeated_shape, TopAbs_ShapeEnum.TopAbs_FACE)
        if not face.More():
            raise RuntimeError("Fixture repeated product has no face")
        face_label = shape_tool.AddSubShape(repeated_label, face.Current())
        if face_label.IsNull():
            raise RuntimeError("Cannot label fixture product face")
        color_tool.SetColor(
            face_label,
            _color(expected.subshape_surface_color),
            XCAFDoc_ColorType.XCAFDoc_ColorSurf,
        )
    finally:
        XCAFDoc_ShapeTool.SetAutoNaming_s(previous_auto_naming)

    shape_tool.UpdateAssemblies()
    if not color_tool.SetInstanceColor(
        shape_tool.GetShape_s(first_occurrence),
        XCAFDoc_ColorType.XCAFDoc_ColorSurf,
        _color(expected.first_occurrence_color),
        True,
    ):
        raise RuntimeError("Cannot assign fixture occurrence color")
    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    writer.SetSHUOMode(True)
    if not writer.Transfer(document, STEPControl_StepModelType.STEPControl_AsIs):
        raise RuntimeError("Cannot transfer temporary XCAF fixture")
    status = writer.Write(str(destination))
    if status != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise RuntimeError(f"Cannot write temporary XCAF fixture: {status.name}")
    return expected


def _empty_compound() -> TopoDS_Compound:
    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    return compound


def _translation(x_value: float, y_value: float, z_value: float) -> TopLoc_Location:
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(x_value, y_value, z_value))
    return TopLoc_Location(transform)


def _set_name(label: TDF_Label, value: str) -> None:
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(value))


def _color(values: tuple[float, float, float]) -> Quantity_Color:
    return Quantity_Color(*values, Quantity_TOC_RGB)

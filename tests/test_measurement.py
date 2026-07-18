"""Direct BREP measurement and vertex-pair selection tests for Stage 5B."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from math import pi, sqrt

import pytest
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

from hms_cadcam.cad.measurement import (
    AreaMeasurement,
    BoundingDimensions,
    CircularEdgeMeasurement,
    DistanceMeasurement,
    EdgeLengthMeasurement,
    MeasurementResult,
    PointCoordinates,
    VolumeMeasurement,
)
from hms_cadcam.cad.models import CadDocumentId, CadFormat
from hms_cadcam.cad.ocp import OcpCadKernel
from hms_cadcam.cad.ocp.measurement import OcpMeasurementService
from hms_cadcam.viewer.models import SelectionMode
from hms_cadcam.viewer.ocp.selection import OcpSelectionController

MEASUREMENT_TOLERANCE = 1.0e-9


def _selection_id(document_id: CadDocumentId, topology: str, index: int) -> str:
    return f"{document_id}:{topology}:{index}"


def _value(result: MeasurementResult, value_type):
    return next(value for value in result.values if isinstance(value, value_type))


def _assert_no_ocp_object(value: object) -> None:
    assert not type(value).__module__.startswith("OCP.")
    if is_dataclass(value) and not isinstance(value, type):
        for field in fields(value):
            _assert_no_ocp_object(getattr(value, field.name))
    elif isinstance(value, (tuple, list, dict)):
        children = value.values() if isinstance(value, dict) else value
        for child in children:
            _assert_no_ocp_object(child)


@pytest.fixture
def box_measurement() -> tuple[OcpCadKernel, OcpMeasurementService, CadDocumentId]:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(2.0, 3.0, 4.0)
    return kernel, OcpMeasurementService(kernel), document_id


def test_box_vertex_coordinates_and_point_distance(box_measurement) -> None:
    _kernel, service, document_id = box_measurement
    vertices = {
        _value(
            service.measure_selection(
                document_id, _selection_id(document_id, "vertex", index)
            ),
            PointCoordinates,
        ): index
        for index in range(1, 9)
    }
    expected = {
        PointCoordinates(x, y, z)
        for x in (0.0, 2.0)
        for y in (0.0, 3.0)
        for z in (0.0, 4.0)
    }
    assert set(vertices) == expected
    first = vertices[PointCoordinates(0.0, 0.0, 0.0)]
    second = vertices[PointCoordinates(2.0, 3.0, 4.0)]
    distance = service.measure_distance(
        document_id,
        _selection_id(document_id, "vertex", first),
        _selection_id(document_id, "vertex", second),
    )
    assert _value(distance, DistanceMeasurement).distance == pytest.approx(
        sqrt(29.0), abs=MEASUREMENT_TOLERANCE
    )


def test_box_edge_face_solid_and_bounding_measurements(box_measurement) -> None:
    _kernel, service, document_id = box_measurement
    edge = service.measure_selection(document_id, _selection_id(document_id, "edge", 1))
    edge_length = _value(edge, EdgeLengthMeasurement).length
    assert any(
        edge_length == pytest.approx(expected, abs=MEASUREMENT_TOLERANCE)
        for expected in (2.0, 3.0, 4.0)
    )
    assert not any(isinstance(value, CircularEdgeMeasurement) for value in edge.values)
    face_areas = {
        _value(
            service.measure_selection(
                document_id, _selection_id(document_id, "face", index)
            ),
            AreaMeasurement,
        ).area
        for index in range(1, 7)
    }
    assert sorted(face_areas) == pytest.approx(
        [6.0, 8.0, 12.0], abs=MEASUREMENT_TOLERANCE
    )
    solid = service.measure_selection(document_id, _selection_id(document_id, "solid", 1))
    assert _value(solid, VolumeMeasurement).volume == pytest.approx(
        24.0, abs=MEASUREMENT_TOLERANCE
    )
    bounds = _value(service.measure_document(document_id), BoundingDimensions)
    assert (bounds.x, bounds.y, bounds.z) == pytest.approx(
        (2.0, 3.0, 4.0), abs=MEASUREMENT_TOLERANCE
    )
    assert all(
        value.area >= 0.0 if isinstance(value, AreaMeasurement) else True
        for index in range(1, 7)
        for value in service.measure_selection(
            document_id, _selection_id(document_id, "face", index)
        ).values
    )
    assert _value(solid, VolumeMeasurement).volume >= 0.0


def test_circle_arc_and_cylinder_measurements() -> None:
    kernel = OcpCadKernel()
    cylinder = BRepPrimAPI_MakeCylinder(5.0, 7.0).Shape()
    cylinder_id = kernel._documents.add_brep(
        cylinder, CadFormat.GENERATED
    ).document_id
    service = OcpMeasurementService(kernel)
    circular = []
    edge_count = kernel.get_topology_counts(cylinder_id).edges
    for index in range(1, edge_count + 1):
        result = service.measure_selection(
            cylinder_id, _selection_id(cylinder_id, "edge", index)
        )
        circular.extend(
            value for value in result.values if isinstance(value, CircularEdgeMeasurement)
        )
    assert circular
    assert all(
        value.radius == pytest.approx(5.0, abs=MEASUREMENT_TOLERANCE)
        for value in circular
    )
    assert all(
        value.diameter == pytest.approx(10.0, abs=MEASUREMENT_TOLERANCE)
        for value in circular
    )
    assert any(value.is_full_circle for value in circular)
    volume = _value(
        service.measure_selection(
            cylinder_id, _selection_id(cylinder_id, "solid", 1)
        ),
        VolumeMeasurement,
    )
    assert volume.volume == pytest.approx(175.0 * pi, abs=MEASUREMENT_TOLERANCE)
    cylinder_bounds = _value(
        service.measure_document(cylinder_id), BoundingDimensions
    )
    assert (cylinder_bounds.x, cylinder_bounds.y, cylinder_bounds.z) == pytest.approx(
        (10.0, 10.0, 7.0), abs=MEASUREMENT_TOLERANCE
    )

    circle = gp_Circ(gp_Ax2(gp_Pnt(), gp_Dir(0.0, 0.0, 1.0)), 9.0)
    arc = BRepBuilderAPI_MakeEdge(circle, 0.0, pi / 2.0).Edge()
    arc_id = kernel._documents.add_brep(arc, CadFormat.GENERATED).document_id
    arc_value = _value(
        service.measure_selection(arc_id, _selection_id(arc_id, "edge", 1)),
        CircularEdgeMeasurement,
    )
    assert arc_value.radius == pytest.approx(9.0, abs=MEASUREMENT_TOLERANCE)
    assert not arc_value.is_full_circle


def test_public_measurement_result_contains_no_ocp_object(box_measurement) -> None:
    _kernel, service, document_id = box_measurement
    result = service.measure_selection(
        document_id, _selection_id(document_id, "face", 1)
    )
    _assert_no_ocp_object(result)


class _PickView:
    def Redraw(self) -> None:  # noqa: N802 - OCP-compatible fake
        return None


class _PickContext:
    def __init__(self) -> None:
        self.detected = None
        self.selected = []
        self.position = 0

    def MoveTo(self, x, y, view, update) -> None:  # noqa: N802
        del x, y, view, update

    def SelectDetected(self) -> None:  # noqa: N802
        self.selected = [] if self.detected is None else [self.detected]

    def ShiftSelect(self, update) -> None:  # noqa: N802
        del update
        if self.detected in self.selected:
            self.selected.remove(self.detected)
        elif self.detected is not None:
            self.selected.append(self.detected)

    def ClearSelected(self, update) -> None:  # noqa: N802
        del update
        self.selected = []

    def InitSelected(self) -> None:  # noqa: N802
        self.position = 0

    def MoreSelected(self) -> bool:  # noqa: N802
        return self.position < len(self.selected)

    def SelectedShape(self):  # noqa: N802
        return self.selected[self.position]

    def NextSelected(self) -> None:  # noqa: N802
        self.position += 1

    def Deactivate(self, presentation) -> None:  # noqa: N802
        del presentation

    def Activate(self, presentation, mode) -> None:  # noqa: N802
        del presentation, mode


def test_ctrl_vertex_pair_regular_click_and_third_ctrl_restart() -> None:
    kernel = OcpCadKernel()
    document_id = kernel.create_box(2.0, 3.0, 4.0)
    shape = kernel._resolve_shape(document_id)
    context = _PickContext()
    selection = OcpSelectionController(context)
    selection.bind_document(document_id, shape, object())
    selection.set_mode(SelectionMode.VERTEX)
    service = OcpMeasurementService(kernel)

    vertex_shapes = []
    for index in range(1, 4):
        _topology, vertex = service._resolve_selection(
            shape, document_id, _selection_id(document_id, "vertex", index)
        )
        vertex_shapes.append(vertex)
    context.detected = vertex_shapes[0]
    assert len(selection.pick(_PickView(), 0, 0, True)) == 1
    context.detected = vertex_shapes[1]
    pair = selection.pick(_PickView(), 0, 0, True)
    assert len(pair) == 2
    context.detected = vertex_shapes[2]
    restarted = selection.pick(_PickView(), 0, 0, True)
    assert len(restarted) == 1
    context.detected = vertex_shapes[0]
    regular = selection.pick(_PickView(), 0, 0, False)
    assert len(regular) == 1

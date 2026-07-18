"""Headless checks for the OCP technical spike."""

from __future__ import annotations

import math
from dataclasses import fields

import OCP
from OCP.TopoDS import TopoDS_Shape

from geometry import create_demo_box, selection_metadata, shape_bounds, topology_counts
from model import SelectionKind, SelectionMetadata, SelectionModeState


def test_ocp_import_and_box_geometry() -> None:
    """OCP must import and create a bounded, non-null OCCT shape."""
    assert OCP.__file__
    shape = create_demo_box()
    assert not shape.IsNull()

    bounds = shape_bounds(shape)
    assert all(math.isfinite(value) for value in bounds)
    assert bounds[0] < bounds[3]
    assert bounds[1] < bounds[4]
    assert bounds[2] < bounds[5]


def test_box_topology_counts() -> None:
    """The demo box must expose one solid, six faces and twelve edges."""
    assert topology_counts(create_demo_box()) == {"solid": 1, "face": 6, "edge": 12}


def test_selection_mode_state_switches_supported_topology() -> None:
    """Selection state must switch explicitly between solid, face and edge."""
    state = SelectionModeState()
    assert state.kind is SelectionKind.SOLID
    state.set_kind(SelectionKind.FACE)
    assert state.kind is SelectionKind.FACE
    state.set_kind(SelectionKind.EDGE)
    assert state.kind is SelectionKind.EDGE


def test_selection_metadata_contains_no_topods_object() -> None:
    """UI-facing selection metadata must contain only simple Python values."""
    metadata = selection_metadata(create_demo_box())
    assert isinstance(metadata, SelectionMetadata)
    assert metadata.topology == "solid"
    assert all(
        not isinstance(getattr(metadata, field.name), TopoDS_Shape)
        for field in fields(metadata)
    )
    assert isinstance(metadata.shape_id, str)
    assert isinstance(metadata.bounds, tuple)


def test_selection_metadata_is_stable_for_same_document_shape() -> None:
    """Repeated metadata reads of one document shape must keep the same ID."""
    shape = create_demo_box()
    first = selection_metadata(shape)
    second = selection_metadata(shape)
    assert first == second

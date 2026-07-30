"""Strategy, family, capability, and canonical-regression tests for Stage 12."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from hms_cadcam.cam.domain.machine import OperationCapability
from hms_cadcam.cam.domain.operation import OperationFamily
from hms_cadcam.cam.domain.setup import CylinderStock, SetupKind
from hms_cadcam.cam.domain.tooling import TurningInsertGeometry
from hms_cadcam.cam.lathe.strategies import (
    LATHE_STRATEGY_REGISTRY,
    lathe_strategy_definition,
)
from hms_cadcam.cam.lathe.types import (
    LatheGeometryKind,
    LatheStrategyFamily,
    LatheStrategyId,
    LatheToolCapability,
)


EXPECTED_STRATEGIES = (
    ("FACE", "lathe.face.v1"),
    ("OD_ROUGH", "lathe.od_rough.v1"),
    ("OD_FINISH", "lathe.od_finish.v1"),
    ("ID_ROUGH", "lathe.id_rough.v1"),
    ("ID_FINISH", "lathe.id_finish.v1"),
    ("OD_GROOVE", "lathe.od_groove.v1"),
    ("ID_GROOVE", "lathe.id_groove.v1"),
    ("PART_OFF", "lathe.part_off.v1"),
    ("OD_THREAD", "lathe.od_thread.v1"),
    ("ID_THREAD", "lathe.id_thread.v1"),
    ("AXIAL_DRILL", "lathe.axial_drill.v1"),
)

EXPECTED_CAPABILITIES = (
    "FACE_TURNING",
    "OD_TURNING",
    "ID_TURNING",
    "OD_GROOVING",
    "ID_GROOVING",
    "PARTING",
    "OD_THREADING",
    "ID_THREADING",
    "AXIAL_DRILLING",
)


def test_exact_strategy_enum_and_registry_order() -> None:
    assert tuple((item.name, item.value) for item in LatheStrategyId) == EXPECTED_STRATEGIES
    assert tuple(item.strategy_id for item in LATHE_STRATEGY_REGISTRY) == tuple(
        LatheStrategyId
    )
    assert len(LATHE_STRATEGY_REGISTRY) == 11
    assert len({item.strategy_id for item in LATHE_STRATEGY_REGISTRY}) == 11
    assert not hasattr(LatheStrategyId, "UNKNOWN")
    assert not hasattr(LatheStrategyId, "CUSTOM")


def test_exact_family_ids_and_assignments() -> None:
    assert tuple((item.name, item.value) for item in LatheStrategyFamily) == (
        ("TURNING", "lathe.family.turning.v1"),
        ("GROOVING", "lathe.family.grooving.v1"),
        ("THREADING", "lathe.family.threading.v1"),
        ("HOLE_MAKING", "lathe.family.hole_making.v1"),
    )
    assert tuple(item.family_id for item in LATHE_STRATEGY_REGISTRY) == (
        *(LatheStrategyFamily.TURNING for _ in range(5)),
        *(LatheStrategyFamily.GROOVING for _ in range(3)),
        *(LatheStrategyFamily.THREADING for _ in range(2)),
        LatheStrategyFamily.HOLE_MAKING,
    )


def test_exact_capability_enum_and_strategy_mapping() -> None:
    assert tuple(item.name for item in LatheToolCapability) == EXPECTED_CAPABILITIES
    assert tuple(item.value for item in LatheToolCapability) == EXPECTED_CAPABILITIES
    assert tuple(
        next(iter(item.required_tool_capabilities))
        for item in LATHE_STRATEGY_REGISTRY
    ) == (
        LatheToolCapability.FACE_TURNING,
        LatheToolCapability.OD_TURNING,
        LatheToolCapability.OD_TURNING,
        LatheToolCapability.ID_TURNING,
        LatheToolCapability.ID_TURNING,
        LatheToolCapability.OD_GROOVING,
        LatheToolCapability.ID_GROOVING,
        LatheToolCapability.PARTING,
        LatheToolCapability.OD_THREADING,
        LatheToolCapability.ID_THREADING,
        LatheToolCapability.AXIAL_DRILLING,
    )


def test_exact_geometry_kind_ids_and_compatibility_matrix() -> None:
    assert tuple((item.name, item.value) for item in LatheGeometryKind) == (
        ("AXIS", "lathe.geometry.axis.v1"),
        ("PROFILE", "lathe.geometry.profile.v1"),
        ("FACE", "lathe.geometry.face.v1"),
        ("EDGE", "lathe.geometry.edge.v1"),
        ("CYLINDER", "lathe.geometry.cylinder.v1"),
        ("POINT", "lathe.geometry.point.v1"),
    )
    expected = {
        LatheStrategyId.FACE: (LatheGeometryKind.FACE, LatheGeometryKind.EDGE, LatheGeometryKind.PROFILE),
        LatheStrategyId.OD_ROUGH: (LatheGeometryKind.PROFILE, LatheGeometryKind.EDGE, LatheGeometryKind.CYLINDER),
        LatheStrategyId.OD_FINISH: (LatheGeometryKind.PROFILE, LatheGeometryKind.EDGE, LatheGeometryKind.CYLINDER),
        LatheStrategyId.ID_ROUGH: (LatheGeometryKind.PROFILE, LatheGeometryKind.EDGE, LatheGeometryKind.CYLINDER),
        LatheStrategyId.ID_FINISH: (LatheGeometryKind.PROFILE, LatheGeometryKind.EDGE, LatheGeometryKind.CYLINDER),
        LatheStrategyId.OD_GROOVE: (LatheGeometryKind.PROFILE, LatheGeometryKind.EDGE, LatheGeometryKind.FACE),
        LatheStrategyId.ID_GROOVE: (LatheGeometryKind.PROFILE, LatheGeometryKind.EDGE, LatheGeometryKind.FACE),
        LatheStrategyId.PART_OFF: (LatheGeometryKind.EDGE, LatheGeometryKind.FACE, LatheGeometryKind.PROFILE),
        LatheStrategyId.OD_THREAD: (LatheGeometryKind.CYLINDER, LatheGeometryKind.EDGE, LatheGeometryKind.PROFILE),
        LatheStrategyId.ID_THREAD: (LatheGeometryKind.CYLINDER, LatheGeometryKind.EDGE, LatheGeometryKind.PROFILE),
        LatheStrategyId.AXIAL_DRILL: (LatheGeometryKind.POINT, LatheGeometryKind.AXIS, LatheGeometryKind.CYLINDER),
    }
    assert {
        item.strategy_id: item.allowed_geometry_kinds
        for item in LATHE_STRATEGY_REGISTRY
    } == expected


def test_registry_is_immutable_and_unknown_strategy_fails_closed() -> None:
    definition = LATHE_STRATEGY_REGISTRY[0]
    with pytest.raises(FrozenInstanceError):
        definition.family_id = LatheStrategyFamily.GROOVING  # type: ignore[misc]
    with pytest.raises(TypeError, match="LatheStrategyId"):
        lathe_strategy_definition("lathe.unknown.v1")  # type: ignore[arg-type]


def test_existing_turning_foundation_contracts_are_unchanged() -> None:
    assert OperationFamily.TURNING.value == "turning"
    assert SetupKind.TURN.value == "turn"
    assert OperationCapability.TURNING.value == "turning"
    assert OperationCapability.THREADING.value == "threading"
    assert CylinderStock.kind.value == "cylinder"
    assert TurningInsertGeometry.kind.value == "turning_insert"

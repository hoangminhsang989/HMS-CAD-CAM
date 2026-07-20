"""Tests for immutable CAM tooling and assembly stale contracts."""

import dataclasses
import json

import pytest

from hms_cadcam.cam.domain import (
    Angle,
    AngleUnit,
    BallEndGeometry,
    BoringBarGeometry,
    BullNoseGeometry,
    CamInvariantError,
    CamUnitError,
    CamValidationError,
    ChamferGeometry,
    CustomCuttingGeometry,
    CylindricalGeometry,
    DrillGeometry,
    HolderDefinition,
    HolderDefinitionId,
    HolderSection,
    Length,
    LengthUnit,
    Revision,
    ShankGeometry,
    TapGeometry,
    ToolAssembly,
    ToolAssemblyEvidence,
    ToolAssemblyId,
    ToolAssemblyStatus,
    ToolDefinition,
    ToolDefinitionId,
    ToolFamily,
    ToolHand,
    TurningInsertGeometry,
    UnsupportedCamSchemaError,
    assess_tool_assembly,
)


def _mm(value: float) -> Length:
    return Length(value, LengthUnit.MM)


def _inch(value: float) -> Length:
    return Length(value, LengthUnit.INCH)


def _geometry_cases():
    cylinder = CylindricalGeometry(_mm(10.0), _mm(20.0))
    drill = DrillGeometry(_mm(8.0), _mm(30.0), Angle(118.0, AngleUnit.DEGREE))
    return (
        (ToolFamily.END_MILL, cylinder),
        (ToolFamily.BALL_END_MILL, BallEndGeometry(_mm(10.0), _mm(20.0))),
        (
            ToolFamily.BULL_NOSE_END_MILL,
            BullNoseGeometry(_mm(10.0), _mm(20.0), _mm(1.0)),
        ),
        (ToolFamily.DRILL, drill),
        (ToolFamily.CENTER_DRILL, drill),
        (
            ToolFamily.CHAMFER_MILL,
            ChamferGeometry(
                _mm(12.0),
                _mm(10.0),
                Angle(90.0, AngleUnit.DEGREE),
                _mm(0.0),
            ),
        ),
        (ToolFamily.FACE_MILL, cylinder),
        (ToolFamily.REAMER, cylinder),
        (ToolFamily.TAP, TapGeometry(_mm(8.0), _mm(20.0), _mm(1.25), ToolHand.RIGHT)),
        (
            ToolFamily.BORING_BAR,
            BoringBarGeometry(_mm(12.0), _mm(30.0), _mm(20.0), ToolHand.RIGHT),
        ),
        (
            ToolFamily.TURNING_INSERT,
            TurningInsertGeometry(_mm(12.7), _mm(4.0), _mm(0.8)),
        ),
        (ToolFamily.CUSTOM, CustomCuttingGeometry(_mm(6.0), _mm(15.0), "Form tool")),
    )


def _tool(family=ToolFamily.END_MILL, geometry=None, unit=LengthUnit.MM) -> ToolDefinition:
    selected = geometry or CylindricalGeometry(Length(10.0, unit), Length(20.0, unit))
    return ToolDefinition(
        ToolDefinitionId.new(),
        f"Tool {family.value}",
        family,
        unit,
        selected,
        Length(100.0, unit),
        Length(30.0, unit),
        ShankGeometry(Length(10.0, unit), Length(70.0, unit)),
        Revision(2),
    )


def _holder(unit=LengthUnit.MM) -> HolderDefinition:
    length = _mm if unit is LengthUnit.MM else _inch
    return HolderDefinition(
        HolderDefinitionId.new(),
        "Holder",
        unit,
        (
            HolderSection(length(0.0), length(20.0), length(32.0), length(32.0)),
            HolderSection(length(20.0), length(50.0), length(32.0), length(50.0)),
        ),
        length(0.0),
        Revision(3),
        "generic_taper",
    )


@pytest.mark.parametrize(("family", "geometry"), _geometry_cases())
def test_each_foundational_tool_family_round_trips(family, geometry) -> None:
    tool = _tool(family, geometry)

    restored = ToolDefinition.from_dict(tool.to_dict())

    assert restored == tool
    assert json.dumps(restored.to_dict(), sort_keys=True) == json.dumps(
        tool.to_dict(), sort_keys=True
    )


@pytest.mark.parametrize("invalid", (0.0, -1.0, float("nan"), float("inf")))
def test_invalid_tool_diameter_is_rejected(invalid: float) -> None:
    with pytest.raises((CamUnitError, CamValidationError)):
        CylindricalGeometry(_mm(invalid), _mm(10.0))


def test_bull_nose_corner_radius_cannot_exceed_tool_radius() -> None:
    with pytest.raises(CamInvariantError):
        BullNoseGeometry(_mm(10.0), _mm(20.0), _mm(6.0))


def test_tool_family_and_geometry_must_match() -> None:
    with pytest.raises(CamInvariantError):
        _tool(ToolFamily.BALL_END_MILL, CylindricalGeometry(_mm(10.0), _mm(20.0)))


def test_tool_unknown_unit_is_rejected_without_inference() -> None:
    with pytest.raises(CamUnitError):
        _tool(unit=LengthUnit.UNKNOWN)


def test_cutting_length_cannot_exceed_usable_length() -> None:
    with pytest.raises(CamInvariantError):
        ToolDefinition(
            ToolDefinitionId.new(),
            "Too long",
            ToolFamily.END_MILL,
            LengthUnit.MM,
            CylindricalGeometry(_mm(10.0), _mm(40.0)),
            _mm(100.0),
            _mm(30.0),
            ShankGeometry(_mm(10.0), _mm(60.0)),
        )


def test_future_tool_version_is_rejected() -> None:
    payload = _tool().to_dict()
    payload["format_version"] = 2

    with pytest.raises(UnsupportedCamSchemaError):
        ToolDefinition.from_dict(payload)


@pytest.mark.parametrize("unit", (LengthUnit.MM, LengthUnit.INCH))
def test_boring_bar_geometry_is_versioned_and_round_trips(unit) -> None:
    length = lambda value: Length(value, unit)
    geometry = BoringBarGeometry(
        length(0.5), length(1.25), length(2.0), ToolHand.LEFT
    )
    tool = _tool(ToolFamily.BORING_BAR, geometry, unit)

    restored = ToolDefinition.from_dict(tool.to_dict())

    assert restored == tool
    assert restored.content_fingerprint == tool.content_fingerprint
    assert geometry.to_dict()["geometry_version"] == 1


@pytest.mark.parametrize(
    "geometry",
    (
        lambda: BoringBarGeometry(_mm(0), _mm(20), _mm(10), ToolHand.RIGHT),
        lambda: BoringBarGeometry(_mm(12), _mm(10), _mm(10), ToolHand.RIGHT),
        lambda: BoringBarGeometry(_mm(10), _inch(1), _mm(10), ToolHand.RIGHT),
        lambda: BoringBarGeometry(_mm(10), _mm(20), _mm(0), ToolHand.RIGHT),
        lambda: BoringBarGeometry(_mm(10), _mm(20), _mm(10), "right"),
    ),
)
def test_invalid_boring_bar_geometry_fails_closed(geometry) -> None:
    with pytest.raises((CamInvariantError, CamUnitError, CamValidationError)):
        geometry()


def test_boring_bar_future_geometry_version_and_wrong_family_are_rejected() -> None:
    geometry = BoringBarGeometry(
        _mm(12), _mm(30), _mm(20), ToolHand.RIGHT
    )
    payload = geometry.to_dict()
    payload["geometry_version"] = 2
    with pytest.raises(UnsupportedCamSchemaError):
        BoringBarGeometry.from_dict(payload)
    with pytest.raises(CamInvariantError):
        _tool(ToolFamily.END_MILL, geometry)
    with pytest.raises(CamInvariantError):
        _tool(
            ToolFamily.BORING_BAR,
            CylindricalGeometry(_mm(12), _mm(20)),
        )


def test_existing_tool_payload_shape_is_unchanged_by_boring_variant() -> None:
    tool = _tool()
    payload = tool.to_dict()

    assert set(payload["cutting_geometry"]) == {
        "kind", "diameter", "flute_length",
    }
    assert ToolDefinition.from_dict(payload).content_fingerprint == tool.content_fingerprint


def test_legacy_end_mill_payload_and_fingerprint_remain_stable() -> None:
    payload = {
        "format": "HMS_CAM_TOOL_DEFINITION",
        "format_version": 1,
        "tool_id": (
            "tool_definition:12345678-1234-4234-8234-123456789abc"
        ),
        "name": "Legacy end mill",
        "family": "end_mill",
        "unit": "mm",
        "cutting_geometry": {
            "kind": "cylindrical",
            "diameter": {"value": 10.0, "unit": "mm"},
            "flute_length": {"value": 20.0, "unit": "mm"},
        },
        "overall_length": {"value": 100.0, "unit": "mm"},
        "usable_length": {"value": 30.0, "unit": "mm"},
        "shank": {
            "diameter": {"value": 10.0, "unit": "mm"},
            "length": {"value": 70.0, "unit": "mm"},
        },
        "revision": {"value": 2},
        "coolant_capabilities": [],
        "manufacturer": None,
        "model": None,
    }

    restored = ToolDefinition.from_dict(payload)

    assert restored.to_dict() == payload
    assert restored.content_fingerprint.digest == (
        "e541cce77c6e39e760ef022aa6aace97a9248c016e69274a2648770791509183"
    )


def test_holder_stepped_profile_is_continuous_and_round_trips() -> None:
    holder = _holder()

    restored = HolderDefinition.from_dict(holder.to_dict())

    assert restored == holder
    assert tuple(item.axial_start.value for item in restored.sections) == (0.0, 20.0)


@pytest.mark.parametrize(
    "section",
    (
        lambda: HolderSection(_mm(0.0), _mm(0.0), _mm(20.0), _mm(20.0)),
        lambda: HolderSection(_mm(0.0), _mm(10.0), _mm(0.0), _mm(20.0)),
        lambda: HolderSection(_mm(-1.0), _mm(10.0), _mm(20.0), _mm(20.0)),
    ),
)
def test_holder_section_invalid_dimensions_are_rejected(section) -> None:
    with pytest.raises((CamInvariantError, CamValidationError)):
        section()


@pytest.mark.parametrize("second_start", (19.0, 21.0))
def test_holder_overlap_or_gap_is_rejected(second_start: float) -> None:
    first = HolderSection(_mm(0.0), _mm(20.0), _mm(30.0), _mm(30.0))
    second = HolderSection(
        _mm(second_start),
        _mm(40.0),
        _mm(30.0),
        _mm(40.0),
    )

    with pytest.raises(CamInvariantError):
        dataclasses.replace(_holder(), sections=(first, second))


def test_holder_sections_are_publicly_immutable() -> None:
    holder = _holder()

    assert isinstance(holder.sections, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        holder.sections = ()


def test_same_tool_supports_multiple_independent_assemblies() -> None:
    tool = _tool()
    holder = _holder()
    first = ToolAssembly.create(
        ToolAssemblyId.new(), "Short", tool, _mm(30.0), _mm(80.0), holder
    )
    second = ToolAssembly.create(
        ToolAssemblyId.new(), "Long", tool, _mm(45.0), _mm(95.0), holder
    )

    assert first.tool_id == second.tool_id
    assert first.assembly_id != second.assembly_id
    assert first.stickout != second.stickout


def test_holder_is_optional_in_assembly() -> None:
    tool = _tool()
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "No holder", tool, _mm(30.0), _mm(80.0)
    )

    assert assembly.holder_id is None
    assert ToolAssembly.from_dict(assembly.to_dict()) == assembly


def test_assembly_rejects_invalid_stickout_and_unit_mismatch() -> None:
    tool = _tool()

    with pytest.raises(CamValidationError):
        ToolAssembly.create(
            ToolAssemblyId.new(), "Invalid", tool, _mm(0.0), _mm(80.0)
        )
    with pytest.raises(CamInvariantError):
        ToolAssembly.create(
            ToolAssemblyId.new(), "Short gauge", tool, _mm(80.0), _mm(30.0)
        )
    with pytest.raises(CamUnitError):
        ToolAssembly.create(
            ToolAssemblyId.new(),
            "Mismatch",
            tool,
            _mm(30.0),
            _mm(80.0),
            _holder(LengthUnit.INCH),
        )


def test_tool_and_holder_stale_revisions_are_distinguished() -> None:
    tool = _tool()
    holder = _holder()
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Assembly", tool, _mm(30.0), _mm(80.0), holder
    )
    current = ToolAssemblyEvidence(
        True,
        tool.revision,
        tool.content_fingerprint,
        tool.unit,
        True,
        holder.revision,
        holder.content_fingerprint,
        holder.unit,
    )

    assert assess_tool_assembly(assembly, current) is ToolAssemblyStatus.VALID
    assert assess_tool_assembly(
        assembly, dataclasses.replace(current, tool_revision=Revision(99))
    ) is ToolAssemblyStatus.TOOL_REVISION_MISMATCH
    assert assess_tool_assembly(
        assembly, dataclasses.replace(current, holder_revision=Revision(99))
    ) is ToolAssemblyStatus.HOLDER_REVISION_MISMATCH


def test_assembly_missing_and_unit_mismatch_states_are_distinct() -> None:
    tool = _tool()
    holder = _holder()
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Assembly", tool, _mm(30.0), _mm(80.0), holder
    )
    current = ToolAssemblyEvidence(
        True,
        tool.revision,
        tool.content_fingerprint,
        tool.unit,
        False,
    )

    assert assess_tool_assembly(
        assembly, ToolAssemblyEvidence(False)
    ) is ToolAssemblyStatus.MISSING_TOOL
    assert assess_tool_assembly(assembly, current) is ToolAssemblyStatus.MISSING_HOLDER
    assert assess_tool_assembly(
        assembly,
        dataclasses.replace(current, tool_unit=LengthUnit.INCH),
    ) is ToolAssemblyStatus.INCOMPATIBLE_UNIT


def test_assembly_fingerprint_is_deterministic_without_python_hash(monkeypatch) -> None:
    tool = _tool()
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Assembly", tool, _mm(30.0), _mm(80.0)
    )

    monkeypatch.setattr(
        "builtins.hash",
        lambda _value: (_ for _ in ()).throw(AssertionError("hash() used")),
    )

    assert assembly.content_fingerprint == assembly.content_fingerprint


def test_malformed_tool_child_payload_is_rejected() -> None:
    payload = _tool().to_dict()
    payload["cutting_geometry"]["diameter"]["value"] = 0.0

    with pytest.raises(CamValidationError):
        ToolDefinition.from_dict(payload)


@pytest.mark.parametrize(
    ("model", "decoder"),
    (
        (_holder(), HolderDefinition.from_dict),
        (
            ToolAssembly.create(
                ToolAssemblyId.new(),
                "Assembly",
                _tool(),
                _mm(30.0),
                _mm(80.0),
            ),
            ToolAssembly.from_dict,
        ),
    ),
)
def test_holder_and_assembly_future_versions_are_rejected(model, decoder) -> None:
    payload = model.to_dict()
    payload["format_version"] = 2

    with pytest.raises(UnsupportedCamSchemaError):
        decoder(payload)


def test_public_tooling_graph_contains_no_native_types() -> None:
    tool = _tool()
    holder = _holder()
    assembly = ToolAssembly.create(
        ToolAssemblyId.new(), "Assembly", tool, _mm(30.0), _mm(80.0), holder
    )

    def walk(value):
        yield value
        if dataclasses.is_dataclass(value):
            for field in dataclasses.fields(value):
                yield from walk(getattr(value, field.name))
        elif isinstance(value, tuple):
            for item in value:
                yield from walk(item)

    assert all(
        not type(value).__module__.startswith(("OCP", "PySide6"))
        for root in (tool, holder, assembly)
        for value in walk(root)
    )

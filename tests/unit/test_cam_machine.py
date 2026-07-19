"""Tests for controller-neutral machines and kinematic contracts."""

import dataclasses
import json

import pytest

from hms_cadcam.cam.domain import (
    AffineTransform,
    Angle,
    AngleUnit,
    CamInvariantError,
    CamUnitError,
    CamValidationError,
    DuplicateCamIdError,
    FeedRate,
    FeedUnit,
    KinematicChain,
    KinematicMount,
    KinematicNode,
    KinematicSide,
    Length,
    LengthUnit,
    MachineAxis,
    MachineAxisType,
    MachineCapabilities,
    MachineCompatibilityStatus,
    MachineCoolantCapability,
    MachineDefinition,
    MachineDefinitionId,
    MachineEvidence,
    MachineKind,
    MachineRequirement,
    OperationCapability,
    Revision,
    SpindleCapability,
    SpindleSpeed,
    UnsupportedCamSchemaError,
    Vector3,
    WorkEnvelope,
    assess_machine_compatibility,
)


def _mm(value: float) -> Length:
    return Length(value, LengthUnit.MM)


def _linear(name: str, semantic: str, direction: Vector3) -> MachineAxis:
    return MachineAxis(
        name,
        semantic,
        MachineAxisType.LINEAR,
        direction,
        _mm(-500.0),
        _mm(500.0),
        _mm(0.0),
    )


def _rotary(name: str, semantic: str) -> MachineAxis:
    return MachineAxis(
        name,
        semantic,
        MachineAxisType.ROTARY,
        Vector3(0.0, 0.0, 1.0),
        Angle(-360.0, AngleUnit.DEGREE),
        Angle(360.0, AngleUnit.DEGREE),
        Angle(0.0, AngleUnit.DEGREE),
    )


def _capabilities(kind: MachineKind) -> MachineCapabilities:
    milling = kind in {MachineKind.MILL, MachineKind.MILL_TURN}
    turning = kind in {MachineKind.TURN, MachineKind.MILL_TURN}
    operations = [OperationCapability.DRILLING]
    if milling:
        operations.append(OperationCapability.MILLING)
    if turning:
        operations.append(OperationCapability.TURNING)
    return MachineCapabilities(
        milling=milling,
        turning=turning,
        live_tooling=kind is MachineKind.MILL_TURN,
        probing=True,
        tapping=True,
        threading=turning,
        spindle_count=1,
        maximum_feed=FeedRate(10000.0, FeedUnit.MM_PER_MINUTE),
        maximum_rapid=FeedRate(30000.0, FeedUnit.MM_PER_MINUTE),
        tool_capacity=24 if milling else 12,
        coolant=(MachineCoolantCapability.FLOOD,),
        operations=tuple(operations),
    )


def _machine(kind: MachineKind) -> MachineDefinition:
    axes = (
        _linear("axis_longitudinal", "longitudinal_motion", Vector3(1.0, 0.0, 0.0)),
        _linear("axis_vertical", "vertical_motion", Vector3(0.0, 0.0, 1.0)),
        _rotary("axis_table", "workpiece_rotation"),
    )
    nodes = (
        KinematicNode(
            "base",
            None,
            None,
            KinematicSide.FIXED,
            KinematicMount.NONE,
            AffineTransform.identity(LengthUnit.MM),
        ),
        KinematicNode(
            "longitudinal",
            "base",
            axes[0].name,
            KinematicSide.TOOL,
            KinematicMount.NONE,
            AffineTransform.identity(LengthUnit.MM),
        ),
        KinematicNode(
            "vertical",
            "longitudinal",
            axes[1].name,
            KinematicSide.TOOL,
            KinematicMount.TOOL,
            AffineTransform.identity(LengthUnit.MM),
        ),
        KinematicNode(
            "table",
            "base",
            axes[2].name,
            KinematicSide.WORKPIECE,
            KinematicMount.WORKPIECE,
            AffineTransform.identity(LengthUnit.MM),
        ),
    )
    return MachineDefinition(
        MachineDefinitionId.new(),
        f"Machine {kind.value}",
        kind,
        LengthUnit.MM,
        axes,
        (SpindleCapability("main_spindle", SpindleSpeed(100.0), SpindleSpeed(12000.0)),),
        _capabilities(kind),
        KinematicChain(nodes),
        WorkEnvelope(_mm(1000.0), _mm(600.0), _mm(500.0)),
        Revision(4),
    )


@pytest.mark.parametrize("kind", tuple(MachineKind))
def test_mill_turn_and_turn_machine_round_trip(kind: MachineKind) -> None:
    machine = _machine(kind)

    restored = MachineDefinition.from_dict(machine.to_dict())

    assert restored == machine
    assert json.dumps(restored.to_dict(), sort_keys=True) == json.dumps(
        machine.to_dict(), sort_keys=True
    )


def test_duplicate_axis_name_is_rejected() -> None:
    machine = _machine(MachineKind.MILL)
    duplicate = dataclasses.replace(
        machine.axes[1],
        name=machine.axes[0].name.upper(),
        semantic="different_semantic",
    )

    with pytest.raises(DuplicateCamIdError):
        dataclasses.replace(machine, axes=(machine.axes[0], duplicate, machine.axes[2]))


@pytest.mark.parametrize(
    ("minimum", "maximum", "home"),
    ((0.0, 0.0, 0.0), (10.0, 0.0, 5.0), (0.0, 10.0, 20.0)),
)
def test_invalid_linear_travel_limits_are_rejected(minimum, maximum, home) -> None:
    with pytest.raises(CamInvariantError):
        MachineAxis(
            "linear_axis",
            "linear_motion",
            MachineAxisType.LINEAR,
            Vector3(1.0, 0.0, 0.0),
            _mm(minimum),
            _mm(maximum),
            _mm(home),
        )


def test_linear_and_rotary_axis_quantity_types_cannot_mix() -> None:
    with pytest.raises(CamUnitError):
        MachineAxis(
            "rotary_axis",
            "table_rotation",
            MachineAxisType.ROTARY,
            Vector3(0.0, 0.0, 1.0),
            _mm(-10.0),
            _mm(10.0),
            _mm(0.0),
        )


def test_machine_rejects_linear_axis_with_different_length_unit() -> None:
    machine = _machine(MachineKind.MILL)
    inch_axis = MachineAxis(
        "inch_axis",
        "inch_motion",
        MachineAxisType.LINEAR,
        Vector3(1.0, 0.0, 0.0),
        Length(-10.0, LengthUnit.INCH),
        Length(10.0, LengthUnit.INCH),
        Length(0.0, LengthUnit.INCH),
    )
    chain = KinematicChain(
        (
            KinematicNode(
                "root",
                None,
                inch_axis.name,
                KinematicSide.TOOL,
                KinematicMount.TOOL,
                AffineTransform.identity(LengthUnit.MM),
            ),
        )
    )

    with pytest.raises(CamUnitError):
        dataclasses.replace(machine, axes=(inch_axis,), kinematic_chain=chain)


def test_capabilities_round_trip_and_public_sets_are_immutable() -> None:
    capabilities = _capabilities(MachineKind.MILL_TURN)

    restored = MachineCapabilities.from_dict(capabilities.to_dict())

    assert restored == capabilities
    assert isinstance(restored.operations, tuple)
    with pytest.raises(dataclasses.FrozenInstanceError):
        restored.operations = ()


def test_kinematic_chain_rejects_unknown_or_late_parent() -> None:
    child = KinematicNode(
        "child",
        "missing_parent",
        None,
        KinematicSide.TOOL,
        KinematicMount.NONE,
        AffineTransform.identity(LengthUnit.MM),
    )

    with pytest.raises(CamInvariantError):
        KinematicChain((child,))


def test_kinematic_chain_must_reference_every_axis_once() -> None:
    machine = _machine(MachineKind.MILL)
    incomplete = KinematicChain((machine.kinematic_chain.nodes[0],))

    with pytest.raises(CamInvariantError):
        dataclasses.replace(machine, kinematic_chain=incomplete)


def test_machine_future_version_is_rejected() -> None:
    payload = _machine(MachineKind.MILL).to_dict()
    payload["format_version"] = 2

    with pytest.raises(UnsupportedCamSchemaError):
        MachineDefinition.from_dict(payload)


@pytest.mark.parametrize(
    ("model", "decoder"),
    (
        (_linear("test_axis", "test_motion", Vector3(1.0, 0.0, 0.0)), MachineAxis.from_dict),
        (
            KinematicChain(
                (
                    KinematicNode(
                        "root",
                        None,
                        None,
                        KinematicSide.FIXED,
                        KinematicMount.NONE,
                        AffineTransform.identity(LengthUnit.MM),
                    ),
                )
            ),
            KinematicChain.from_dict,
        ),
    ),
)
def test_axis_and_kinematic_future_versions_are_rejected(model, decoder) -> None:
    payload = model.to_dict()
    payload["format_version"] = 2

    with pytest.raises(UnsupportedCamSchemaError):
        decoder(payload)


def test_machine_compatibility_distinguishes_stale_and_capability_mismatch() -> None:
    machine = _machine(MachineKind.MILL_TURN)
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        (OperationCapability.MILLING, OperationCapability.TURNING),
    )
    evidence = MachineEvidence(
        True,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
        machine.capabilities.operations,
    )

    assert assess_machine_compatibility(
        requirement, evidence
    ) is MachineCompatibilityStatus.COMPATIBLE
    assert assess_machine_compatibility(
        requirement, dataclasses.replace(evidence, revision=Revision(99))
    ) is MachineCompatibilityStatus.REVISION_MISMATCH
    assert assess_machine_compatibility(
        requirement,
        dataclasses.replace(evidence, capabilities=(OperationCapability.MILLING,)),
    ) is MachineCompatibilityStatus.CAPABILITY_MISMATCH


def test_machine_compatibility_distinguishes_missing_and_unit_mismatch() -> None:
    machine = _machine(MachineKind.MILL)
    requirement = MachineRequirement(
        machine.machine_id,
        machine.revision,
        machine.content_fingerprint,
        machine.unit,
    )
    evidence = MachineEvidence(
        True,
        machine.revision,
        machine.content_fingerprint,
        LengthUnit.INCH,
    )

    assert assess_machine_compatibility(
        requirement, MachineEvidence(False)
    ) is MachineCompatibilityStatus.MISSING_MACHINE
    assert assess_machine_compatibility(
        requirement, evidence
    ) is MachineCompatibilityStatus.INCOMPATIBLE_UNIT


def test_malformed_nested_axis_payload_is_rejected() -> None:
    payload = _machine(MachineKind.MILL).to_dict()
    payload["axes"][0]["minimum"]["value"] = 1000.0

    with pytest.raises(CamInvariantError):
        MachineDefinition.from_dict(payload)


def test_public_machine_graph_contains_no_native_types() -> None:
    machine = _machine(MachineKind.MILL_TURN)

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
        for value in walk(machine)
    )

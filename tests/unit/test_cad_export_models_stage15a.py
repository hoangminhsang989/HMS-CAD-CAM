"""Stage15A export registry and versioned-profile contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hms_cadcam.cad.export_models import (
    EXPORT_CAPABILITIES,
    EXPORT_PROFILE_FORMAT,
    EXPORT_PROFILE_SCHEMA_VERSION,
    ExportCapabilityClass,
    ExportFormatId,
    ExportOverwritePolicy,
    ExportProfile,
    StlEncoding,
    StlMeshOptions,
    capability_for_path,
    export_file_filter,
)


def test_registry_covers_all_targets_with_honest_audit_classification() -> None:
    assert tuple(EXPORT_CAPABILITIES) == tuple(ExportFormatId)
    assert {
        item.format_id: item.classification for item in EXPORT_CAPABILITIES.values()
    } == {
        ExportFormatId.STEP: ExportCapabilityClass.NATIVE_SUPPORTED_NOW,
        ExportFormatId.IGES: ExportCapabilityClass.NATIVE_SUPPORTED_NOW,
        ExportFormatId.STL: ExportCapabilityClass.NATIVE_SUPPORTED_NOW,
        ExportFormatId.BREP: ExportCapabilityClass.NATIVE_SUPPORTED_NOW,
        ExportFormatId.PARASOLID: (
            ExportCapabilityClass.ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE
        ),
        ExportFormatId.ACIS: (
            ExportCapabilityClass.ARCHITECTURE_SUPPORTED_BUT_BACKEND_UNAVAILABLE
        ),
        ExportFormatId.DWG: ExportCapabilityClass.NOT_IMPLEMENTED,
        ExportFormatId.DXF: ExportCapabilityClass.DECLARED_UI_ONLY,
    }
    assert all(
        item.available
        for item in EXPORT_CAPABILITIES.values()
        if item.classification is ExportCapabilityClass.NATIVE_SUPPORTED_NOW
    )
    assert all(
        item.unavailable_reason
        for item in EXPORT_CAPABILITIES.values()
        if not item.available
    )


@pytest.mark.parametrize(
    ("path", "format_id"),
    [
        ("part.STEP", ExportFormatId.STEP),
        ("part.stp", ExportFormatId.STEP),
        ("part.IGS", ExportFormatId.IGES),
        ("part.stl", ExportFormatId.STL),
        ("part.brp", ExportFormatId.BREP),
        ("part.x_t", ExportFormatId.PARASOLID),
        ("part.sat", ExportFormatId.ACIS),
        ("part.dwg", ExportFormatId.DWG),
        ("part.dxf", ExportFormatId.DXF),
    ],
)
def test_extension_routing_is_case_insensitive_and_deterministic(
    path: str, format_id: ExportFormatId
) -> None:
    assert capability_for_path(Path(path)).format_id is format_id
    assert capability_for_path(Path("part.unknown")) is None
    assert export_file_filter().count(";;") == len(ExportFormatId) - 1


@pytest.mark.parametrize("format_id", tuple(ExportFormatId))
def test_default_profile_is_typed_and_deterministic(format_id: ExportFormatId) -> None:
    profile = ExportProfile.default_for(format_id)
    encoded = profile.to_json()
    payload = json.loads(encoded)
    assert payload["format"] == EXPORT_PROFILE_FORMAT
    assert payload["format_version"] == EXPORT_PROFILE_SCHEMA_VERSION
    assert payload["format_id"] == format_id.value
    decoded = ExportProfile.from_json(encoded)
    assert decoded == profile
    assert decoded.to_json() == encoded
    assert ExportProfile.from_dict(profile.to_dict()) == profile
    assert tuple(payload) == tuple(sorted(payload))


def test_step_and_brep_versions_are_bounded_to_writer_supported_values() -> None:
    for standard in ("AP203", "AP214", "AP242"):
        assert ExportProfile(ExportFormatId.STEP, standard=standard).standard == standard
    for version in ("1", "2", "3"):
        assert ExportProfile(ExportFormatId.BREP, standard=version).standard == version
    with pytest.raises(ValueError, match="version/standard"):
        ExportProfile(ExportFormatId.STEP, standard="AP999")
    with pytest.raises(ValueError, match="version/standard"):
        ExportProfile(ExportFormatId.BREP, standard="4")


def test_stl_options_are_validated_and_rejected_for_other_formats() -> None:
    mesh = StlMeshOptions(0.05, 0.25, True)
    profile = ExportProfile(
        ExportFormatId.STL,
        tolerance=0.05,
        stl_encoding=StlEncoding.ASCII,
        mesh_options=mesh,
    )
    assert profile.mesh_options == mesh
    with pytest.raises(ValueError, match="finite and positive"):
        StlMeshOptions(0.0, 0.25)
    with pytest.raises(ValueError, match="must match"):
        ExportProfile(
            ExportFormatId.STL,
            tolerance=0.1,
            stl_encoding=StlEncoding.BINARY,
            mesh_options=mesh,
        )
    with pytest.raises(ValueError, match="STL-only"):
        ExportProfile(
            ExportFormatId.IGES,
            tolerance=0.1,
            stl_encoding=StlEncoding.BINARY,
            mesh_options=StlMeshOptions(),
        )


def test_profile_rejects_unconsumed_compatibility_option() -> None:
    with pytest.raises(ValueError, match="compatibility override"):
        ExportProfile(ExportFormatId.STEP, standard="AP242", compatibility="legacy")


@pytest.mark.parametrize("standard", ("AP203", "AP214", "AP242"))
def test_every_step_standard_round_trips_with_overwrite_policy(standard: str) -> None:
    profile = ExportProfile(
        ExportFormatId.STEP,
        standard=standard,
        overwrite_policy=ExportOverwritePolicy.REPLACE_EXISTING,
    )
    assert ExportProfile.from_json(profile.to_json()) == profile


@pytest.mark.parametrize("version", ("1", "2", "3"))
def test_every_brep_version_round_trips(version: str) -> None:
    profile = ExportProfile(ExportFormatId.BREP, standard=version)
    assert ExportProfile.from_json(profile.to_json()).to_json() == profile.to_json()


@pytest.mark.parametrize("encoding", tuple(StlEncoding))
@pytest.mark.parametrize("tessellation_applicable", (True, False))
def test_stl_brep_and_existing_mesh_profiles_round_trip(
    encoding: StlEncoding, tessellation_applicable: bool
) -> None:
    if tessellation_applicable:
        mesh = StlMeshOptions(0.025, 0.35, True)
        profile = ExportProfile(
            ExportFormatId.STL,
            tolerance=mesh.linear_deflection,
            stl_encoding=encoding,
            mesh_options=mesh,
        )
    else:
        profile = ExportProfile(ExportFormatId.STL, stl_encoding=encoding)
    assert ExportProfile.from_json(profile.to_json()) == profile


@pytest.mark.parametrize("payload", ("{", "[]", "null", "true", "1"))
def test_profile_decoder_rejects_invalid_or_non_object_json(payload: str) -> None:
    error = ValueError if payload == "{" else TypeError
    with pytest.raises(error):
        ExportProfile.from_json(payload)


def test_profile_decoder_rejects_unknown_missing_and_invalid_schema_fields() -> None:
    payload = ExportProfile.default_for(ExportFormatId.STEP).to_dict()
    invalid = dict(payload)
    invalid["unknown"] = True
    with pytest.raises(ValueError, match="unknown"):
        ExportProfile.from_dict(invalid)
    missing = dict(payload)
    del missing["unit_policy"]
    with pytest.raises(ValueError, match="missing"):
        ExportProfile.from_dict(missing)
    wrong_version = dict(payload)
    wrong_version["format_version"] = 2
    with pytest.raises(ValueError, match="Unsupported"):
        ExportProfile.from_dict(wrong_version)
    bool_version = dict(payload)
    bool_version["format_version"] = True
    with pytest.raises(TypeError, match="must be int"):
        ExportProfile.from_dict(bool_version)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("format_id", "unknown", ValueError),
        ("unit_policy", "inch_conversion", ValueError),
        ("overwrite_policy", "overwrite_anyway", ValueError),
        ("stl_encoding", 1, TypeError),
        ("standard", True, TypeError),
    ],
)
def test_profile_decoder_rejects_invalid_enums_and_primitive_types(
    field: str, value: object, error: type[Exception]
) -> None:
    payload = ExportProfile.default_for(ExportFormatId.STEP).to_dict()
    payload[field] = value
    with pytest.raises(error):
        ExportProfile.from_dict(payload)


@pytest.mark.parametrize("value", (True, False, float("nan"), float("inf"), -1.0))
def test_profile_decoder_rejects_bool_nonfinite_and_negative_stl_numbers(
    value: object,
) -> None:
    payload = ExportProfile.default_for(ExportFormatId.STL).to_dict()
    options = dict(payload["mesh_options"])
    options["linear_deflection"] = value
    payload["mesh_options"] = options
    with pytest.raises((TypeError, ValueError)):
        ExportProfile.from_dict(payload)


def test_repeated_profile_encode_decode_is_byte_stable() -> None:
    profile = ExportProfile(
        ExportFormatId.STL,
        tolerance=0.05,
        stl_encoding=StlEncoding.ASCII,
        mesh_options=StlMeshOptions(0.05, 0.25, True),
        overwrite_policy=ExportOverwritePolicy.REPLACE_EXISTING,
    )
    encoded = profile.to_json()
    for _ in range(10):
        profile = ExportProfile.from_json(encoded)
        assert profile.to_json() == encoded

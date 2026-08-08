"""Stage15A export registry and versioned-profile contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hms_cadcam.cad.export_models import (
    EXPORT_CAPABILITIES,
    EXPORT_PROFILE_FORMAT,
    ExportCapabilityClass,
    ExportFormatId,
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
    payload = json.loads(profile.to_json())
    assert payload["format"] == EXPORT_PROFILE_FORMAT
    assert payload["format_id"] == format_id.value
    assert profile.to_json() == profile.to_json()
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

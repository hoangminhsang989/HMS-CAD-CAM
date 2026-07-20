import pytest

from hms_cadcam.cam.post.export_model import NCExportDiagnosticCode
from hms_cadcam.cam.post.export_security import (
    NCExportSecurityError,
    sanitize_export_filename,
)


@pytest.mark.parametrize(
    "filename",
    ["../PART.fn", "A/B.fn", "A\\B.fn", 'A:B.fn', "A?.fn", "A\x01.fn"],
)
def test_filename_rejects_traversal_forbidden_and_control_characters(filename) -> None:
    with pytest.raises(NCExportSecurityError) as caught:
        sanitize_export_filename(filename, (".fn",))
    assert caught.value.code is NCExportDiagnosticCode.FILENAME_INVALID


@pytest.mark.parametrize("filename", ["CON.fn", "nul.FN", "COM1.fn", "LPT9.fn"])
def test_filename_rejects_windows_device_names(filename) -> None:
    with pytest.raises(NCExportSecurityError):
        sanitize_export_filename(filename, (".fn",))


@pytest.mark.parametrize("filename", ["PART. ", "PART.fn ", "PART..fn"])
def test_filename_rejects_trailing_space_dot_and_double_extension(filename) -> None:
    with pytest.raises(NCExportSecurityError):
        sanitize_export_filename(filename, (".fn",))


def test_filename_applies_profile_extension_deterministically() -> None:
    assert sanitize_export_filename("CHI_TIẾT", (".fn",)) == "CHI_TIẾT.fn"
    assert sanitize_export_filename("PART.FN", (".fn",)) == "PART.fn"


def test_filename_rejects_wrong_extension_empty_and_too_long() -> None:
    for filename in ("", "PART.nc", "A" * 121):
        with pytest.raises(NCExportSecurityError):
            sanitize_export_filename(filename, (".fn",))

"""Exact R222 counterfactual and no-CNC-control boundary for R223."""

import subprocess
from pathlib import Path

from hms_cadcam.cam.qualification import NO_CNC_CONTROL_MARKER


BASELINE = "fe8dade90a0ef7d58d51a316088ff19ce08968c7"
PRODUCT_PATHS = (
    "src/hms_cadcam/cam/qualification/offline_model.py",
    "src/hms_cadcam/cam/qualification/offline_analyzer.py",
    "src/hms_cadcam/cam/qualification/offline_package.py",
    "src/hms_cadcam/ui/nc_release_center.py",
)


def test_r222_baseline_lacks_tranche3_release_workflow():
    root = Path(__file__).parents[2]
    for path in PRODUCT_PATHS:
        assert (root / path).is_file()
        result = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{BASELINE}:{path}"],
            check=False, capture_output=True, text=True,
        )
        assert result.returncode != 0


def test_offline_modules_have_no_cnc_transport_surface():
    root = Path(__file__).parents[2]
    combined = "\n".join(
        (root / path).read_text(encoding="utf-8") for path in PRODUCT_PATHS[:3]
    ).casefold()
    assert NO_CNC_CONTROL_MARKER == "STAGE18A_TRANCHE3_NO_CNC_CONTROL_BOUNDARY_PRESERVED"
    for forbidden in ("import socket", "import requests", "focas", "upload_nc", "start_cycle"):
        assert forbidden not in combined

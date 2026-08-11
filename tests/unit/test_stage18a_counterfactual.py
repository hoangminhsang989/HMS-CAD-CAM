"""Exact-baseline counterfactual for the Stage18A product delta."""

import subprocess
from pathlib import Path


BASELINE = "0bbaa5d8fab6424d059cb13adffa901b8a15a20c"
PRODUCT_PATHS = (
    "src/hms_cadcam/cam/qualification/model.py",
    "src/hms_cadcam/cam/qualification/profile.py",
    "src/hms_cadcam/cam/qualification/validation.py",
    "src/hms_cadcam/cam/qualification/store.py",
    "src/hms_cadcam/ui/machine_qualification_panel.py",
)


def test_stage17a_closure_baseline_lacks_stage18a_machine_qualification_delta():
    root = Path(__file__).parents[2]

    for path in PRODUCT_PATHS:
        candidate = root / path
        counterfactual = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{BASELINE}:{path}"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert candidate.is_file()
        assert counterfactual.returncode != 0

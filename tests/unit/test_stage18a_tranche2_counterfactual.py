"""Exact R220 counterfactual for the Stage18A Tranche2 product delta."""

import subprocess
from pathlib import Path


BASELINE = "661ba163d7b99272ce50252352daf5f3e7358bee"
PRODUCT_PATHS = (
    "src/hms_cadcam/cam/qualification/physical_model.py",
    "src/hms_cadcam/cam/qualification/evidence_model.py",
    "src/hms_cadcam/cam/qualification/tranche2_store.py",
    "src/hms_cadcam/ui/physical_qualification_wizard.py",
)


def test_r220_baseline_lacks_tranche2_setup_and_dry_run_workflow():
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

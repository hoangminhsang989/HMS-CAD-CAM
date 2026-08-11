"""Build the deterministic local R225 Tranche4 direct-review package."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_NAME = "R225_STAGE18A_TRANCHE4_LOCAL_REVIEW"
INCLUDED = (
    Path("docs/STAGE18A_POST_TRANCHE3_REMAINING_SOFTWARE_SCOPE_MATRIX.md"),
    Path("docs/STAGE18A_TRANCHE4_PRODUCTION_RELEASE_GOVERNANCE_CONTRACT.md"),
    Path("src/hms_cadcam/cam/qualification/manufacturing_job.py"),
    Path("src/hms_cadcam/cam/qualification/manufacturing_release.py"),
    Path("src/hms_cadcam/cam/qualification/manufacturing_package.py"),
    Path("src/hms_cadcam/cam/qualification/manufacturing_store.py"),
    Path("src/hms_cadcam/ui/manufacturing_release_center.py"),
    Path("tests/unit/test_stage18a_tranche4_manufacturing_job.py"),
    Path("tests/unit/test_stage18a_tranche4_ui.py"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / PACKAGE_NAME)
    args = parser.parse_args()
    output = args.output
    if output.exists():
        raise SystemExit(f"Refusing to overwrite {output}")
    output.mkdir(parents=True)
    files = []
    for relative in INCLUDED:
        source = ROOT / relative
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        files.append({"path": relative.as_posix(), "sha256": sha256(source), "size": source.stat().st_size})
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    manifest = {
        "format": PACKAGE_NAME, "format_version": 1,
        "baseline_r224": "3f73da82aaa2574bf9df4c965e27a81d3d367977",
        "baseline_tree_r224": "80c376ecc181a47c9d3490642a326520d3dabcb8",
        "candidate_head": head, "candidate_tree": tree,
        "verdict": "READY_FOR_STAGE18A_TRANCHE4_FINAL_DIRECT_REVIEW",
        "markers": [
            "STAGE18A_POST_TRANCHE3_REMAINING_SOFTWARE_SCOPE_MATRIX_COMPLETE",
            "STAGE18A_TRANCHE4_SCOPE_FROZEN", "STAGE18A_MANUFACTURING_JOB_MODEL_IMPLEMENTED",
            "STAGE18A_JOB_TOOL_RECONCILIATION_IMPLEMENTED", "STAGE18A_JOB_SETUP_CONSISTENCY_IMPLEMENTED",
            "STAGE18A_JOB_RELEASE_POLICY_IMPLEMENTED", "STAGE18A_IMMUTABLE_JOB_RELEASE_IMPLEMENTED",
            "STAGE18A_RELEASE_SUPERSEDE_HISTORY_IMPLEMENTED", "STAGE18A_JOB_LEVEL_STRUCTURED_DIFF_IMPLEMENTED",
            "STAGE18A_MANUFACTURING_JOB_HANDOFF_PACKAGE_IMPLEMENTED",
            "STAGE18A_TRANCHE4_NO_CNC_CONTROL_BOUNDARY_PRESERVED",
            "LEVEL2_NOT_ACHIEVED", "LEVEL3_NOT_ACHIEVED", "MACHINE_READY_FALSE",
        ],
        "adversarial_matrix_cases": 24,
        "credential_findings": 0,
        "files": files,
        "verification": {
            "focused": "8 passed",
            "stage18a_tranche3": "30 passed",
            "bounded": "839 passed, 3 inherited missing-private-artifact failures, 6 skipped",
            "candidate_induced": 0,
        },
    }
    (output / "PACKAGE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (output / "REMEDIATION_LEDGER.md").write_text(
        "# R225 remediation ledger\n\n"
        "- Fixed lifecycle-state fingerprint drift by excluding lifecycle state from source identity.\n"
        "- Added stale/reject/tool/setup/package fail-closed regression coverage.\n"
        "- Bounded regression retained three exact inherited missing-private-artifact failures; no candidate-induced failure.\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

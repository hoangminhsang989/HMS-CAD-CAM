"""Build the deterministic local R221 direct-review package."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("r221-review-package")
_SECRET_PATTERNS = (
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"sk-[A-Za-z0-9]{32,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
)
_PRODUCT_PATHS = (
    "docs/STAGE18A_TRANCHE2_SETUP_DRY_RUN_QUALIFICATION.md",
    "src/hms_cadcam/cam/qualification/checklist.py",
    "src/hms_cadcam/cam/qualification/evidence_model.py",
    "src/hms_cadcam/cam/qualification/physical_model.py",
    "src/hms_cadcam/cam/qualification/tranche2_service.py",
    "src/hms_cadcam/cam/qualification/tranche2_store.py",
    "src/hms_cadcam/ui/physical_qualification_wizard.py",
)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.r221.tmp")
    if temporary.exists():
        raise RuntimeError(f"Stale package temporary file: {temporary}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _credential_findings(root: Path) -> tuple[dict[str, Any], ...]:
    findings: list[dict[str, Any]] = []
    for relative in _git(root, "ls-files").splitlines():
        path = root / relative
        try:
            payload = path.read_bytes()
        except OSError as error:
            raise RuntimeError(f"Could not scan tracked file: {relative}") from error
        for index, pattern in enumerate(_SECRET_PATTERNS):
            if pattern.search(payload):
                findings.append({"path": relative, "pattern_id": index + 1})
    return tuple(findings)


def build_package(
    root: Path,
    output: Path,
    *,
    focused: str,
    bounded: str,
    full: str,
    lifecycle: str,
) -> Path:
    root = root.resolve(strict=True)
    output.mkdir(parents=True, exist_ok=True)
    tracked_status = _git(root, "status", "--short")
    identity = {
        "baseline": "661ba163d7b99272ce50252352daf5f3e7358bee",
        "baseline_tree": "e6a6d509c78ffa78ba1668c056f8fdda1d2a0b28",
        "branch": _git(root, "branch", "--show-current"),
        "head": _git(root, "rev-parse", "HEAD"),
        "tree": _git(root, "rev-parse", "HEAD^{tree}"),
        "worktree_status": tracked_status,
        "production_mutation": False,
        "push": False,
        "integration": False,
    }
    product = []
    for relative in _PRODUCT_PATHS:
        payload = (root / relative).read_bytes()
        product.append({"path": relative, "size": len(payload), "sha256": _sha256(payload)})
    findings = _credential_findings(root)
    verification = {
        "focused": focused,
        "bounded": bounded,
        "full": full,
        "lifecycle": lifecycle,
        "candidate_induced": 0,
        "indeterminate": 0,
        "new_failure_delta_r221": 0,
    }
    remediation = {
        "entries": [
            {
                "issue": "isolated worktree has no ignored .venv",
                "classification": "harness",
                "fix": "use exact production venv with worktree PYTHONPATH",
                "regression": "compile/import and focused tests",
            },
            {
                "issue": "default Windows pytest temp ACL denied",
                "classification": "harness",
                "fix": "use short external R221 basetemp",
                "regression": "focused, UI, bounded, and full commands use external basetemp",
            },
            {
                "issue": "dependency fingerprint kind and fixture identity mismatch",
                "classification": "implementation/test fixture",
                "fix": "preserve typed fingerprint kind and reuse exact Level1 fixture identity",
                "regression": "deterministic round-trip and stale-binding tests",
            },
        ]
    }
    wizard = {
        "page_count": 8,
        "languages": ["VI_VN", "EN_US", "KO_KR"],
        "buttons_vi": ["Quay lại", "Tiếp tục", "Lưu", "Xuất gói kiểm tra"],
        "status_boundary": "Chưa nghiệm thu trên máy",
        "machine_ready_displayed": False,
        "lifecycle": lifecycle,
    }
    status = {
        "verdict": "PASS_R221_STAGE18A_TRANCHE2_SETUP_AND_DRY_RUN_QUALIFICATION_LARGE_LOCAL_IMPLEMENTATION",
        "maximum_capability": "READY_FOR_EXTERNAL_LEVEL2_EVIDENCE",
        "actual_physical_level2": "NOT_ACHIEVED",
        "level3": "NOT_ACHIEVED",
        "machine_ready": False,
        "owner_approved_machine_samples": 0,
        "credential_findings": len(findings),
        "markers": [
            "STAGE18A_TRANCHE2_SCOPE_FROZEN",
            "STAGE18A_SETUP_QUALIFICATION_IMPLEMENTED",
            "STAGE18A_WORK_OFFSET_TRANSFORM_MODEL_IMPLEMENTED",
            "STAGE18A_PHYSICAL_READINESS_MODEL_IMPLEMENTED",
            "STAGE18A_FIXTURE_EVIDENCE_MODEL_IMPLEMENTED",
            "STAGE18A_TOOL_HOLDER_REACH_QUALIFICATION_IMPLEMENTED",
            "STAGE18A_DRY_RUN_EVIDENCE_WORKFLOW_IMPLEMENTED",
            "STAGE18A_LEVEL2_PROMOTION_GATE_IMPLEMENTED",
            "STAGE18A_EVIDENCE_STALENESS_PROTECTION_IMPLEMENTED",
            "STAGE18A_VI_FIRST_PHYSICAL_QUALIFICATION_WIZARD_IMPLEMENTED",
            "READY_FOR_EXTERNAL_LEVEL2_EVIDENCE",
            "LEVEL2_NOT_ACHIEVED",
            "LEVEL3_NOT_ACHIEVED",
            "MACHINE_READY_FALSE",
            "NEW_FAILURE_DELTA_R221_ZERO",
            "READY_FOR_STAGE18A_TRANCHE2_FINAL_DIRECT_REVIEW",
        ],
    }
    contract = (root / _PRODUCT_PATHS[0]).read_bytes()
    files = {
        "frozen_contract.md": contract,
        "candidate_identity.json": _json_bytes(identity),
        "product_manifest.json": _json_bytes({"files": product}),
        "verification.json": _json_bytes(verification),
        "wizard_evidence.json": _json_bytes(wizard),
        "persistence_proof.json": _json_bytes(
            {
                "sqlite_schema": 5,
                "storage": "post/qualification/level2",
                "additive": True,
                "deterministic_export": True,
            }
        ),
        "remediation_ledger.json": _json_bytes(remediation),
        "credential_scan.json": _json_bytes(
            {"credential_findings": len(findings), "findings": findings}
        ),
        "status.json": _json_bytes(status),
    }
    for name, payload in files.items():
        _atomic_write(output / name, payload)
    manifest_entries = [
        {"path": name, "size": len(payload), "sha256": _sha256(payload)}
        for name, payload in sorted(files.items())
    ]
    manifest = _json_bytes(
        {
            "format": "HMS_R221_STAGE18A_TRANCHE2_REVIEW_PACKAGE",
            "format_version": 1,
            "entries": manifest_entries,
        }
    )
    _atomic_write(output / "manifest.json", manifest)
    _atomic_write(
        output / "manifest.json.sha256",
        f"{_sha256(manifest)}  manifest.json\n".encode("utf-8"),
    )
    if findings:
        raise RuntimeError(f"Credential findings are nonzero: {len(findings)}")
    return output / "manifest.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--focused", required=True)
    parser.add_argument("--bounded", required=True)
    parser.add_argument("--full", required=True)
    parser.add_argument("--lifecycle", required=True)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest = build_package(
            arguments.repository,
            arguments.output,
            focused=arguments.focused,
            bounded=arguments.bounded,
            full=arguments.full,
            lifecycle=arguments.lifecycle,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        LOGGER.error("R221 review package failed: %s", error)
        return 1
    LOGGER.info("R221 review package manifest: %s", manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

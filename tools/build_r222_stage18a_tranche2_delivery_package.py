"""Build and independently verify the deterministic R222 delivery package."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger("r222-delivery-package")
BASELINE = "661ba163d7b99272ce50252352daf5f3e7358bee"
PACKAGE_FORMAT = "R222_STAGE18A_TRANCHE2_FULL_SOFTWARE_DELIVERY"
EVIDENCE_PATHS = (
    "review/direct_review.json",
    "review/remediation_ledger.json",
    "review/candidate_identity.json",
    "review/focused.txt",
    "review/adversarial.txt",
    "review/bounded.txt",
    "review/full.txt",
    "review/lifecycle.txt",
    "integration/preflight.json",
    "integration/replacement_proof.json",
    "integration/ff.json",
    "production/targeted.txt",
    "production/full.txt",
    "delivery/push_a.json",
    "delivery/state_commit.json",
    "delivery/push_b.json",
    "delivery/ai_sync.json",
    "delivery/housekeeping.json",
    "delivery/final_state.json",
)
SECRET_PATTERNS = (
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"sk-[A-Za-z0-9]{32,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
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
    temporary = path.with_name(f".{path.name}.r222.tmp")
    if temporary.exists():
        raise RuntimeError(f"Stale R222 temporary file: {temporary}")
    try:
        with temporary.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _credential_findings(entries: dict[str, bytes]) -> tuple[dict[str, Any], ...]:
    findings: list[dict[str, Any]] = []
    for name, payload in sorted(entries.items()):
        for index, pattern in enumerate(SECRET_PATTERNS, start=1):
            if pattern.search(payload):
                findings.append({"path": name, "pattern_id": index})
    return tuple(findings)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, payload)
    return buffer.getvalue()


def _verify_zip(payload: bytes, expected: dict[str, bytes]) -> dict[str, int]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("R222 delivery ZIP contains duplicate paths")
        if set(names) != set(expected):
            raise RuntimeError("R222 delivery ZIP path set mismatch")
        if archive.testzip() is not None:
            raise RuntimeError("R222 delivery ZIP CRC failure")
        for name, expected_payload in expected.items():
            actual = archive.read(name)
            if len(actual) != len(expected_payload):
                raise RuntimeError(f"R222 delivery ZIP size mismatch: {name}")
            if _sha256(actual) != _sha256(expected_payload):
                raise RuntimeError(f"R222 delivery ZIP hash mismatch: {name}")
    return {
        "missing": 0,
        "unexpected": 0,
        "duplicate": 0,
        "crc_failures": 0,
        "hash_mismatch": 0,
        "size_mismatch": 0,
    }


def build_package(
    repository: Path,
    evidence_root: Path,
    output: Path,
    *,
    product_commit: str,
    state_commit: str,
) -> tuple[Path, Path]:
    repository = repository.resolve(strict=True)
    evidence_root = evidence_root.resolve(strict=True)
    if output.exists():
        raise RuntimeError("R222 output directory must not already exist")
    current_head = _git(repository, "rev-parse", "HEAD")
    if current_head != state_commit:
        raise RuntimeError("R222 state commit is not current production HEAD")
    if _git(repository, "status", "--porcelain"):
        raise RuntimeError("R222 production worktree is not clean")
    entries: dict[str, bytes] = {}
    for relative in EVIDENCE_PATHS:
        source = evidence_root / relative
        try:
            entries[f"evidence/{relative}"] = source.read_bytes()
        except OSError as error:
            raise RuntimeError(f"Missing required R222 evidence: {relative}") from error
    changed_paths = tuple(
        line for line in _git(repository, "diff", "--name-only", f"{BASELINE}..{state_commit}").splitlines()
        if line
    )
    product_paths = tuple(
        line for line in _git(repository, "diff", "--name-only", f"{BASELINE}..{product_commit}").splitlines()
        if line
    )
    file_manifest: list[dict[str, Any]] = []
    for relative in changed_paths:
        path = repository / relative
        if not path.is_file():
            raise RuntimeError(f"Delivered tracked path is unavailable: {relative}")
        payload = path.read_bytes()
        entry_name = f"delivered/{relative.replace(os.sep, '/')}"
        entries[entry_name] = payload
        file_manifest.append(
            {"path": relative, "size": len(payload), "sha256": _sha256(payload)}
        )
    identity = {
        "format": PACKAGE_FORMAT,
        "format_version": 1,
        "baseline": BASELINE,
        "product_commit": product_commit,
        "product_tree": _git(repository, "rev-parse", f"{product_commit}^{{tree}}"),
        "state_commit": state_commit,
        "state_tree": _git(repository, "rev-parse", f"{state_commit}^{{tree}}"),
        "origin_main": _git(repository, "rev-parse", "origin/main"),
        "product_paths": list(product_paths),
        "delivered_paths": list(changed_paths),
        "delivered_files": file_manifest,
        "level2": "NOT_ACHIEVED",
        "level3": "NOT_ACHIEVED",
        "machine_ready": False,
        "physical_evidence": "NOT_SUPPLIED",
        "owner_approved_machine_samples": 0,
        "no_cnc_control_path_introduced": True,
    }
    entries["identity.json"] = _canonical_json(identity)
    findings = _credential_findings(entries)
    entries["credential_scan.json"] = _canonical_json(
        {"credential_findings": len(findings), "findings": findings}
    )
    if findings:
        raise RuntimeError(f"R222 credential findings are nonzero: {len(findings)}")
    manifest_entries = [
        {"path": name, "size": len(payload), "sha256": _sha256(payload)}
        for name, payload in sorted(entries.items())
    ]
    manifest = _canonical_json(
        {
            "format": PACKAGE_FORMAT,
            "format_version": 1,
            "entries": manifest_entries,
        }
    )
    entries["manifest.json"] = manifest
    entries["manifest.json.sha256"] = (
        f"{_sha256(manifest)}  manifest.json\n".encode("utf-8")
    )
    first_zip = _zip_bytes(entries)
    second_zip = _zip_bytes(entries)
    if first_zip != second_zip:
        raise RuntimeError("R222 deterministic ZIP rebuild differs")
    verification = _verify_zip(first_zip, entries)
    entries["package_verification.json"] = _canonical_json(
        {
            **verification,
            "credential_findings": 0,
            "deterministic_rebuild_identical": True,
        }
    )
    manifest_entries = [
        {"path": name, "size": len(payload), "sha256": _sha256(payload)}
        for name, payload in sorted(entries.items())
        if name not in {"manifest.json", "manifest.json.sha256"}
    ]
    manifest = _canonical_json(
        {"format": PACKAGE_FORMAT, "format_version": 1, "entries": manifest_entries}
    )
    entries["manifest.json"] = manifest
    entries["manifest.json.sha256"] = (
        f"{_sha256(manifest)}  manifest.json\n".encode("utf-8")
    )
    final_zip = _zip_bytes(entries)
    if final_zip != _zip_bytes(entries):
        raise RuntimeError("R222 final deterministic ZIP rebuild differs")
    _verify_zip(final_zip, entries)
    for name, payload in entries.items():
        _atomic_write(output / name, payload)
    actual_files = {
        path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()
    }
    if actual_files != set(entries):
        raise RuntimeError("R222 output directory contains missing or unexpected paths")
    zip_path = output.with_suffix(".zip")
    _atomic_write(zip_path, final_zip)
    return output / "manifest.json", zip_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--product-commit", required=True)
    parser.add_argument("--state-commit", required=True)
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        manifest, zip_path = build_package(
            arguments.repository,
            arguments.evidence_root,
            arguments.output,
            product_commit=arguments.product_commit,
            state_commit=arguments.state_commit,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        LOGGER.error("R222 delivery package failed: %s", error)
        return 1
    LOGGER.info("R222 manifest: %s", manifest)
    LOGGER.info("R222 ZIP: %s", zip_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

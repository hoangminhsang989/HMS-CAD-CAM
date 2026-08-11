"""Deterministic, offline manufacturing-job handoff package builder."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
from typing import Mapping

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint
from hms_cadcam.cam.qualification.manufacturing_job import (
    ManufacturingJobRelease,
    PACKAGE_FORMAT,
    TRANCHE4_NO_CNC_MARKER,
)

FORBIDDEN_SUFFIXES = {".exe", ".dll", ".ps1", ".bat", ".cmd", ".sh"}
FORBIDDEN_NAMES = {"fo cas", "focas", "credentials", "secrets", "upload", "controller"}


class ManufacturingPackageError(RuntimeError):
    """Raised when a handoff package cannot be created or verified."""


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _safe_relative(name: str) -> str:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not name.strip():
        raise ManufacturingPackageError(f"Unsafe package path: {name!r}")
    lowered = name.casefold()
    if path.suffix.casefold() in FORBIDDEN_SUFFIXES or any(token in lowered for token in FORBIDDEN_NAMES):
        raise ManufacturingPackageError(f"Forbidden package content: {name!r}")
    return str(path)


@dataclass(frozen=True, slots=True)
class HandoffPackage:
    root: Path
    manifest: Mapping[str, object]
    package_fingerprint: ContentFingerprint


class ManufacturingHandoffPackageBuilder:
    """Build and verify a package containing only data and review artifacts."""

    def build(self, release: ManufacturingJobRelease, output_root: Path, *,
              nc_files: Mapping[str, bytes], setup_sheets: Mapping[str, bytes] | None = None,
              verification_reports: Mapping[str, bytes] | None = None) -> HandoffPackage:
        output_root = Path(output_root)
        package_dir = output_root / release.release_id
        if package_dir.exists():
            raise ManufacturingPackageError("Refusing to overwrite an existing immutable package")
        files: dict[str, bytes] = {}
        for name, payload in nc_files.items():
            relative = _safe_relative(f"nc/{name}")
            if not relative.casefold().endswith((".nc", ".ngc", ".tap")):
                raise ManufacturingPackageError("NC package files must use a recognised NC extension")
            if not isinstance(payload, bytes):
                raise ManufacturingPackageError("NC payload must be bytes")
            files[relative] = payload
        for prefix, values in (("setup", setup_sheets or {}), ("verification", verification_reports or {})):
            for name, payload in values.items():
                relative = _safe_relative(f"{prefix}/{name}")
                if not isinstance(payload, bytes):
                    raise ManufacturingPackageError("Package artifact payload must be bytes")
                files[relative] = payload
        expected_names = {f"nc/{program.program_id}.nc" for program in release.job.programs}
        if not expected_names.issubset(files):
            raise ManufacturingPackageError("Handoff package is missing one or more exact NC programs")
        for program in release.job.programs:
            payload = files[f"nc/{program.program_id}.nc"]
            if _sha(payload) != program.nc_sha256:
                raise ManufacturingPackageError("NC package bytes do not match the released SHA-256")
            files[f"nc/{program.program_id}.nc.sha256"] = f"{program.nc_sha256}  {program.program_id}.nc\n".encode("ascii")
        files["job/job_manifest.json"] = json.dumps(release.job.to_dict(), ensure_ascii=False,
                                                      sort_keys=True, separators=(",", ":")).encode("utf-8")
        files["release/release_record.json"] = json.dumps(release.to_dict(), ensure_ascii=False,
                                                            sort_keys=True, separators=(",", ":")).encode("utf-8")
        files["release/review_record.json"] = json.dumps(release.review.to_dict(), ensure_ascii=False,
                                                           sort_keys=True, separators=(",", ":")).encode("utf-8")
        files["machine/machine_summary.json"] = self._json_bytes({
            "machine_profile_id": release.job.machine_profile_id,
            "machine_profile_fingerprint": release.job.machine_profile_fingerprint.to_dict(),
            "controller_contract": release.job.controller_contract,
            "physical_qualification": "LEVEL2_NOT_ACHIEVED; LEVEL3_NOT_ACHIEVED; MACHINE_READY_FALSE",
        })
        files["setup/setup_pack.json"] = self._json_bytes({"setups": [item.to_dict() for item in release.job.setups]})
        files["tools/master_tool_list.json"] = self._json_bytes({"tools": [item.to_dict() for item in release.job.tools],
                                                                    "reconciliation": release.tool_report.to_dict()})
        files["reports/job_risk_summary.json"] = self._json_bytes({
            "programs": len(release.job.programs), "setups": len(release.job.setups), "tools": len(release.job.tools),
            "warnings": 0, "blockers": len(release.tool_report.issues), "stale_releases": 0,
        })
        files["history/revision_history.json"] = self._json_bytes({"release_id": release.release_id,
                                                                     "supersedes_release_id": release.supersedes_release_id,
                                                                     "state": release.state.value})
        files["release/dry_run_checklist.txt"] = self._checklist(release).encode("utf-8")
        files["level2/returned_evidence_intake.txt"] = (
            "Attach attributable physical evidence to the exact release, NC SHA-256, setup and machine.\n"
            "No Level2 PASS is created by this template.\n"
        ).encode("utf-8")
        inventory = [{"path": name, "sha256": _sha(payload), "size": len(payload)} for name, payload in sorted(files.items())]
        manifest_identity = {
            "format": PACKAGE_FORMAT, "format_version": 1, "release_id": release.release_id,
            "release_fingerprint": release.release_fingerprint.to_dict(), "files": inventory,
            "no_cnc_control": TRANCHE4_NO_CNC_MARKER, "credential_findings": 0,
        }
        package_fp = ContentFingerprint.from_payload(manifest_identity)
        manifest = {**manifest_identity, "package_fingerprint": package_fp.to_dict()}
        files["package_manifest.json"] = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                                                     separators=(",", ":")).encode("utf-8")
        package_dir.mkdir(parents=True)
        try:
            for name, payload in files.items():
                target = package_dir / Path(name)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
        except OSError as error:
            shutil.rmtree(package_dir, ignore_errors=True)
            raise ManufacturingPackageError("Unable to write handoff package") from error
        return HandoffPackage(package_dir, manifest, package_fp)

    @staticmethod
    def _json_bytes(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _checklist(release: ManufacturingJobRelease) -> str:
        lines = ["MANUFACTURING_JOB_HANDOFF_PACKAGE", f"Release: {release.release_id}",
                 "Physical machine acceptance: UNKNOWN / NOT CLAIMED", "CNC control: NOT INCLUDED", ""]
        for program in release.job.programs:
            lines.append(f"[ ] Verify {program.program_id}.nc SHA-256={program.nc_sha256} at the machine")
        lines.extend(("[ ] Confirm setup G54 and physical fixture/stock against approved instructions",
                      "[ ] Perform separately authorized dry-run and return attributable evidence"))
        return "\n".join(lines) + "\n"

    def verify(self, package: HandoffPackage) -> None:
        manifest_path = package.root / "package_manifest.json"
        if not manifest_path.is_file():
            raise ManufacturingPackageError("Package manifest is missing")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ManufacturingPackageError("Package manifest is unreadable") from error
        if manifest.get("no_cnc_control") != TRANCHE4_NO_CNC_MARKER or manifest.get("credential_findings") != 0:
            raise ManufacturingPackageError("Package boundary/security marker is invalid")
        required = {"format", "format_version", "release_id", "release_fingerprint", "files", "no_cnc_control", "credential_findings", "package_fingerprint"}
        if set(manifest) != required or not isinstance(manifest.get("files"), list):
            raise ManufacturingPackageError("Package manifest structure is invalid")
        paths = [item.get("path") for item in manifest["files"] if isinstance(item, dict)]
        if len(paths) != len(manifest["files"]) or len(paths) != len(set(paths)):
            raise ManufacturingPackageError("Package manifest contains duplicate or malformed paths")
        identity = {key: value for key, value in manifest.items() if key != "package_fingerprint"}
        if ContentFingerprint.from_payload(identity).to_dict() != manifest["package_fingerprint"]:
            raise ManufacturingPackageError("Package manifest fingerprint mismatch")
        if manifest["package_fingerprint"] != package.package_fingerprint.to_dict():
            raise ManufacturingPackageError("Package handle fingerprint is stale")
        for item in manifest["files"]:
            try:
                relative = _safe_relative(item["path"])
                expected_sha = item["sha256"]
                expected_size = item["size"]
            except (KeyError, TypeError, ManufacturingPackageError) as error:
                raise ManufacturingPackageError("Package manifest inventory is malformed") from error
            if type(expected_size) is not int or expected_size < 0:
                raise ManufacturingPackageError("Package manifest size is invalid")
            path = package.root / relative
            if not path.is_file() or path.stat().st_size != expected_size or _sha(path.read_bytes()) != expected_sha:
                raise ManufacturingPackageError(f"Package tamper detected: {relative}")
        unexpected = [p for p in package.root.rglob("*") if p.is_file() and p.name != "package_manifest.json"
                      and p.relative_to(package.root).as_posix() not in {item["path"] for item in manifest["files"]}]
        if unexpected:
            raise ManufacturingPackageError("Unexpected package file detected")


__all__ = ["HandoffPackage", "ManufacturingHandoffPackageBuilder", "ManufacturingPackageError"]

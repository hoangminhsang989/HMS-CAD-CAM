"""Provider model for isolated Post sandbox generation.

No provider in this module can target a global Post or a production NC path.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkdtemp

from hms_cadcam.cam.domain.errors import CamValidationError
from hms_cadcam.cam.domain.revision import ContentFingerprint


@dataclass(frozen=True, slots=True)
class SandboxProviderStatus:
    provider_id: str
    provider_version: str
    available: bool
    missing_dependencies: tuple[str, ...]
    fingerprint: ContentFingerprint


@dataclass(frozen=True, slots=True)
class SandboxRunResult:
    provider: SandboxProviderStatus
    workspace: str
    manifest: dict[str, object]
    output_sha256: str | None
    state: str


class WorkNC2021PostProvider:
    """Configuration-only boundary for the recovered WorkNC 2021 chain."""

    provider_id = "worknc_2021_post_provider"
    provider_version = "1"
    REQUIRED_EXECUTABLES = ("wncmain.exe", "ppadd", "ppadd.prc", "wnc_tpfilecheck", "caldega22", "rescor_batch", "machine8_932.exe")

    def __init__(self, executable_root: Path | None = None) -> None:
        self._root = executable_root

    def status(self) -> SandboxProviderStatus:
        missing = self.REQUIRED_EXECUTABLES if self._root is None else tuple(item for item in self.REQUIRED_EXECUTABLES if not (self._root / item).is_file())
        payload = {"provider_id": self.provider_id, "provider_version": self.provider_version,
                   "root_configured": self._root is not None, "missing_dependencies": list(missing)}
        return SandboxProviderStatus(self.provider_id, self.provider_version, not missing, missing, ContentFingerprint.from_payload(payload))

    def prepare(self, sandbox_root: Path, *, post_source: bytes, input_manifest: dict[str, object]) -> SandboxRunResult:
        status = self.status()
        workspace = Path(mkdtemp(prefix="hms-post-studio-", dir=sandbox_root))
        source = workspace / "candidate-post.dat"; source.write_bytes(post_source)
        manifest = {"format": "HMS_POST_STUDIO_SANDBOX", "format_version": 1,
                    "provider": {"id": status.provider_id, "version": status.provider_version, "fingerprint": status.fingerprint.to_dict()},
                    "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "input_manifest": input_manifest, "candidate_post_sha256": hashlib.sha256(post_source).hexdigest(),
                    "workspace_isolated": True, "global_post_write": False, "production_nc_write": False,
                    "cleanup_policy": "DELETE_WORKSPACE_AFTER_VERIFIED_COMPLETION_PRESERVE_AUDIT_MANIFEST"}
        (workspace / "sandbox-manifest.json").write_text(__import__("json").dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        state = "READY" if status.available else "UNAVAILABLE"
        return SandboxRunResult(status, str(workspace), manifest, None, state)

    @staticmethod
    def cleanup(result: SandboxRunResult) -> None:
        workspace = Path(result.workspace)
        if workspace.name.startswith("hms-post-studio-"):
            shutil.rmtree(workspace, ignore_errors=True)


__all__ = ["SandboxProviderStatus", "SandboxRunResult", "WorkNC2021PostProvider"]

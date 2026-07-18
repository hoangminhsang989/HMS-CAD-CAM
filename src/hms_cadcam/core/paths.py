"""Windows application paths without machine-specific constants."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AppPaths:
    """Resolved per-user paths used by HMS CAD/CAM."""

    data_dir: Path
    config_dir: Path
    log_dir: Path

    @classmethod
    def for_current_user(cls) -> "AppPaths":
        """Resolve application paths below the current Windows profile."""
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_root = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        data_dir = base_root / "HMS CADCAM"
        return cls(
            data_dir=data_dir,
            config_dir=data_dir / "config",
            log_dir=data_dir / "logs",
        )

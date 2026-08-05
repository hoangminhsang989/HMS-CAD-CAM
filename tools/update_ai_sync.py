"""Executable entry point for HMS AI Sync Engine V1.1."""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from ai_sync.cli import main


if __name__ == "__main__":
    raise SystemExit(main())

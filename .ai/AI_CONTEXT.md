# AI CONTEXT

## Purpose

This directory synchronizes project context between Codex and ChatGPT Web.

## Project

HMS CAD/CAM is a Python and PySide6 CAD/CAM application with 2D and 3D CAD/CAM functionality.

## Operating rules

- Preserve existing functionality unless an approved task explicitly changes it.
- Do not silently remove features, tests, compatibility behavior, or historical data.
- Use bounded work packages and verified checkpoints.
- Record exact test counts and commit hashes.
- Clearly distinguish completed, in-progress, blocked, and planned work.
- Never overwrite uncommitted user work without explicit approval.
- Keep generated packages, caches, environments, secrets, and large private reference files out of Git.

## State files

- `CURRENT_STATUS.md`: verified present state
- `NEXT_TASK.md`: next bounded objective
- `SESSION.json`: machine-readable current session
- `METRICS.json`: progress and test metrics
- `HISTORY/`: immutable checkpoint summaries
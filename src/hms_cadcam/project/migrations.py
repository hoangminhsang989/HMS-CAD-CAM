"""Ordered SQLite schema migrations for HMS projects."""

from __future__ import annotations

from collections.abc import Sequence

MIGRATIONS: dict[int, Sequence[str]] = {
    1: (
        """
        CREATE TABLE schema_migrations (
            version INTEGER PRIMARY KEY CHECK (version > 0),
            applied_at TEXT NOT NULL
        )
        """,
    ),
}

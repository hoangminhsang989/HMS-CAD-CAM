"""Stage 12.5A schema, migration-backup, rollback, and read-only gates."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import ProjectDatabaseError
from hms_cadcam.project.migrations import MIGRATIONS


_LATHE_TABLES = (
    "lathe_derived_snapshots",
    "lathe_tool_bindings",
    "lathe_operations",
    "lathe_programs",
)


def _downgrade_to_v4(path) -> None:  # type: ignore[no-untyped-def]
    ProjectDatabase().initialize(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        for table in _LATHE_TABLES:
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version = ?", (5,))
        connection.execute("PRAGMA user_version = 4")


def test_v4_to_v5_creates_verified_atomic_backup_and_exact_schema(tmp_path) -> None:
    root = tmp_path / "Legacy.HMS"
    root.mkdir()
    (root / "backups").mkdir()
    database_path = root / "project.db"
    _downgrade_to_v4(database_path)

    ProjectDatabase().open_and_migrate(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )
        }
    assert set(_LATHE_TABLES).issubset(objects)
    backups = tuple((root / "backups").iterdir())
    assert len(backups) == 1 and backups[0].is_dir()
    assert not backups[0].name.startswith(".")
    backup_database = backups[0] / "project.db"
    metadata = json.loads(
        (backups[0] / "migration-backup.json").read_text(encoding="utf-8")
    )
    backup_bytes = backup_database.read_bytes()
    assert metadata == {
        "database": "project.db",
        "format": "HMS_SQLITE_MIGRATION_BACKUP",
        "format_version": 1,
        "sha256": hashlib.sha256(backup_bytes).hexdigest(),
        "size_bytes": len(backup_bytes),
        "source_schema": 4,
        "target_schema": 5,
    }
    with closing(sqlite3.connect(backup_database)) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 4
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_new_database_initialization_does_not_create_migration_backup(tmp_path) -> None:
    root = tmp_path / "New.HMS"
    root.mkdir()
    ProjectDatabase().initialize(root / "project.db")
    assert not (root / "backups").exists()


def test_v5_failure_rolls_back_active_database_and_retains_backup(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "Rollback.HMS"
    root.mkdir()
    (root / "backups").mkdir()
    database_path = root / "project.db"
    _downgrade_to_v4(database_path)
    monkeypatch.setitem(
        MIGRATIONS,
        5,
        (
            "CREATE TABLE lathe_failure_probe(value INTEGER)",
            "THIS IS NOT SQL",
        ),
    )

    with pytest.raises(ProjectDatabaseError):
        ProjectDatabase().open_and_migrate(database_path)

    with closing(sqlite3.connect(database_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = ?",
            ("lathe_failure_probe",),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?", (5,)
        ).fetchone() is None
    assert len(tuple((root / "backups").iterdir())) == 1


def test_read_only_v4_fails_closed_without_backup_or_write(tmp_path) -> None:
    root = tmp_path / "Readonly.HMS"
    root.mkdir()
    database_path = root / "project.db"
    _downgrade_to_v4(database_path)
    before = database_path.read_bytes()

    with pytest.raises(ProjectDatabaseError, match="Read-only"):
        ProjectDatabase().open_and_migrate(database_path, read_only=True)

    assert database_path.read_bytes() == before
    assert not (root / "backups").exists()


def test_v5_revision_generation_checks_are_nonnegative_and_profile_is_nullable(
    tmp_path,
) -> None:
    path = tmp_path / "project.db"
    ProjectDatabase().initialize(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "INSERT INTO lathe_programs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "program",
                "project",
                "document",
                "source",
                "setup",
                0,
                0,
                "program",
                1,
                None,
                "{}",
                1,
            ),
        )
        connection.execute(
            "INSERT INTO lathe_operations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("operation", "program", 0, "lathe.face.v1", 0, 1, "{}", 1),
        )
        connection.execute(
            "INSERT INTO lathe_tool_bindings VALUES (?, ?, ?, ?, ?, ?)",
            ("operation", "tool", None, None, None, 0),
        )
        assert connection.execute(
            "SELECT profile_id, binding_revision FROM lathe_tool_bindings"
        ).fetchone() == (None, 0)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE lathe_programs SET revision = ? WHERE program_id = ?",
                (-1, "program"),
            )

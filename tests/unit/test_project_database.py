"""Unit tests for versioned SQLite project schemas."""

import sqlite3

import pytest

from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import ProjectDatabaseError, UnsupportedFormatVersionError


def test_database_initialization_is_idempotent_and_has_no_cam_tables(tmp_path) -> None:
    path = tmp_path / "project.db"
    database = ProjectDatabase()
    database.initialize(path)
    database.open_and_migrate(path)
    database.validate(path)

    assert database.current_schema_version(path) == 2
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables == {
        "schema_migrations",
        "cad_view_state",
        "cad_object_appearance",
    }


def test_database_migrates_schema_v1_to_v2_transactionally(tmp_path) -> None:
    path = tmp_path / "legacy-v1.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (1, 'legacy')")
        connection.execute("PRAGMA user_version = 1")

    database = ProjectDatabase()
    database.open_and_migrate(path)

    assert database.current_schema_version(path) == 2
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert {"cad_view_state", "cad_object_appearance"}.issubset(tables)


def test_future_and_corrupt_databases_are_rejected(tmp_path) -> None:
    database = ProjectDatabase()
    future = tmp_path / "future.db"
    database.initialize(future)
    with sqlite3.connect(future) as connection:
        connection.execute("INSERT INTO schema_migrations VALUES (99, 'now')")
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(UnsupportedFormatVersionError):
        database.open_and_migrate(future)

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"not sqlite")
    with pytest.raises(ProjectDatabaseError):
        database.validate(corrupt)


def test_future_pragma_without_migration_table_is_not_downgraded(tmp_path) -> None:
    path = tmp_path / "future-pragma.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    database = ProjectDatabase()
    with pytest.raises(ProjectDatabaseError):
        database.open_and_migrate(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone()
    assert table is None

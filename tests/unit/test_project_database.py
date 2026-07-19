"""Unit tests for versioned SQLite project schemas."""

import sqlite3

import pytest

from hms_cadcam.project.database import ProjectDatabase
from hms_cadcam.project.exceptions import ProjectDatabaseError, UnsupportedFormatVersionError
from hms_cadcam.project.service import ProjectService


def test_database_initialization_is_idempotent_and_has_cam_v4_tables(tmp_path) -> None:
    path = tmp_path / "project.db"
    database = ProjectDatabase()
    database.initialize(path)
    database.open_and_migrate(path)
    database.validate(path)

    assert database.current_schema_version(path) == 4
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "schema_migrations",
        "cad_view_state",
        "cad_object_appearance",
        "cad_xcaf_occurrence_appearance",
        "cam_project_state",
        "cam_jobs",
        "cam_setups",
        "cam_nodes",
        "cam_operations",
        "cam_dependencies",
        "cam_tool_definitions",
        "cam_holder_definitions",
        "cam_tool_assemblies",
        "cam_machine_definitions",
        "toolpath_artifacts",
    }.issubset(tables)


def test_database_migrates_schema_v1_to_latest_transactionally(tmp_path) -> None:
    path = tmp_path / "legacy-v1.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (1, 'legacy')")
        connection.execute("PRAGMA user_version = 1")

    database = ProjectDatabase()
    database.open_and_migrate(path)

    assert database.current_schema_version(path) == 4
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert {
        "cad_view_state",
        "cad_object_appearance",
        "cad_xcaf_occurrence_appearance",
    }.issubset(tables)


def test_database_migrates_v2_to_v3_without_changing_v2_rows(tmp_path) -> None:
    path = tmp_path / "legacy-v2.db"
    database = ProjectDatabase()
    database.initialize(path)
    with sqlite3.connect(path) as connection:
        for table in (
            "toolpath_artifacts", "cam_dependencies", "cam_operations", "cam_nodes",
            "cam_setups", "cam_jobs", "cam_project_state", "cam_tool_definitions",
            "cam_holder_definitions", "cam_tool_assemblies", "cam_machine_definitions",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DROP TABLE cad_xcaf_occurrence_appearance")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 3")
        connection.execute("PRAGMA user_version = 2")
        connection.execute(
            "INSERT INTO cad_view_state VALUES (?, ?, ?, ?, ?)",
            ("legacy-source", 1, "shaded", "top", "legacy"),
        )

    database.open_and_migrate(path)

    assert database.current_schema_version(path) == 4
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT source_id FROM cad_view_state"
        ).fetchone() == ("legacy-source",)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='cad_xcaf_occurrence_appearance'"
        ).fetchone() == (1,)


def test_project_database_migration_does_not_modify_manifest_or_source(tmp_path) -> None:
    source = tmp_path / "legacy-source.brep"
    source.write_bytes(b"immutable legacy source")
    service = ProjectService.create_default(tmp_path / "config")
    session = service.create_project_from_source(tmp_path, "Legacy Migration", source)
    project_root = session.root_path
    stored_source = project_root / session.manifest.source_files[0].stored_path
    manifest_before = (project_root / "project.hms.json").read_bytes()
    source_before = stored_source.read_bytes()
    service.close_project()
    with sqlite3.connect(project_root / "project.db") as connection:
        connection.execute("DROP TABLE cad_object_appearance")
        connection.execute("DROP TABLE cad_view_state")
        connection.execute("DROP TABLE cad_xcaf_occurrence_appearance")
        for table in (
            "toolpath_artifacts", "cam_dependencies", "cam_operations", "cam_nodes",
            "cam_setups", "cam_jobs", "cam_project_state", "cam_tool_definitions",
            "cam_holder_definitions", "cam_tool_assemblies", "cam_machine_definitions",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 2")
        connection.execute("PRAGMA user_version = 1")

    opener = ProjectService.create_default(tmp_path / "opener-config")
    opener.open_project(project_root)

    assert (project_root / "project.hms.json").read_bytes() == manifest_before
    assert stored_source.read_bytes() == source_before
    assert ProjectDatabase().current_schema_version(project_root / "project.db") == 4


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

"""SQLite initialization, validation, migration, and backup."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from hms_cadcam.project.constants import DATABASE_SCHEMA_VERSION
from hms_cadcam.project.exceptions import (
    DatabaseMissingError,
    ProjectDatabaseError,
    UnsupportedFormatVersionError,
)
from hms_cadcam.project.migrations import MIGRATIONS
from hms_cadcam.project.models import datetime_to_json, utc_now


class ProjectDatabase:
    """Own all direct SQLite access for a project."""

    def initialize(self, database_path: Path) -> None:
        """Create a new database and apply all known migrations."""
        try:
            with closing(self._connect(database_path)) as connection:
                self._migrate_connection(connection)
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error

    def open_and_migrate(self, database_path: Path) -> None:
        """Validate an existing database and apply supported migrations."""
        if not database_path.is_file():
            raise DatabaseMissingError(f"Missing database: {database_path}")
        try:
            with closing(self._connect(database_path)) as connection:
                self._ensure_integrity(connection)
                self._migrate_connection(connection)
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error

    def validate(self, database_path: Path) -> None:
        """Check database integrity and supported schema version."""
        if not database_path.is_file():
            raise DatabaseMissingError(f"Missing database: {database_path}")
        try:
            with closing(self._connect(database_path)) as connection:
                self._ensure_integrity(connection)
                version = self._schema_version(connection)
                pragma_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version != pragma_version:
                    raise ProjectDatabaseError("Database schema versions do not match")
                if version > DATABASE_SCHEMA_VERSION:
                    raise UnsupportedFormatVersionError(str(version))
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error

    def backup(self, source_path: Path, destination_path: Path) -> None:
        """Copy SQLite safely using the online backup API."""
        if not source_path.is_file():
            raise DatabaseMissingError(f"Missing database: {source_path}")
        try:
            with closing(self._connect(source_path)) as source:
                with closing(self._connect(destination_path)) as target:
                    source.backup(target)
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error

    def current_schema_version(self, database_path: Path) -> int:
        """Return the latest applied migration version."""
        if not database_path.is_file():
            raise DatabaseMissingError(f"Missing database: {database_path}")
        try:
            with closing(self._connect(database_path)) as connection:
                return self._schema_version(connection)
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error

    @staticmethod
    def _connect(database_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(database_path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _ensure_integrity(connection: sqlite3.Connection) -> None:
        result = connection.execute("PRAGMA quick_check").fetchone()
        if result is None or result[0] != "ok":
            raise ProjectDatabaseError("SQLite quick_check failed")

    @staticmethod
    def _schema_version(connection: sqlite3.Connection) -> int:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if table is None:
            return 0
        row = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
        return int(row[0]) if row else 0

    def _migrate_connection(self, connection: sqlite3.Connection) -> None:
        current = self._schema_version(connection)
        pragma_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current != pragma_version:
            raise ProjectDatabaseError("Database schema versions do not match")
        if current > DATABASE_SCHEMA_VERSION:
            raise UnsupportedFormatVersionError(str(current))
        for version in range(current + 1, DATABASE_SCHEMA_VERSION + 1):
            statements = MIGRATIONS.get(version)
            if statements is None:
                raise ProjectDatabaseError(f"Missing database migration {version}")
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                    (version, datetime_to_json(utc_now())),
                )
                connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            except Exception:
                connection.rollback()
                raise

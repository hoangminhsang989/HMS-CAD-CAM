"""SQLite initialization, validation, migration, and backup."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from uuid import UUID, uuid4

from hms_cadcam.project.constants import (
    BACKUPS_DIRECTORY,
    DATABASE_FILENAME,
    DATABASE_SCHEMA_VERSION,
)
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

    def open_and_migrate(
        self,
        database_path: Path,
        *,
        read_only: bool = False,
    ) -> None:
        """Validate an existing database and apply supported migrations."""
        if not database_path.is_file():
            raise DatabaseMissingError(f"Missing database: {database_path}")
        try:
            connector = self._connect_read_only if read_only else self._connect
            with closing(connector(database_path)) as connection:
                self._ensure_integrity(connection)
                current = self._validated_schema_version(connection)
                if read_only and current < DATABASE_SCHEMA_VERSION:
                    raise ProjectDatabaseError(
                        "Read-only project requires a database migration"
                    )
                if (
                    not read_only
                    and current < DATABASE_SCHEMA_VERSION
                    and current > 0
                    and database_path.name == DATABASE_FILENAME
                ):
                    self._create_migration_backup(
                        connection,
                        database_path,
                        current,
                        DATABASE_SCHEMA_VERSION,
                    )
                self._migrate_connection(connection)
                self._verify_schema(connection, DATABASE_SCHEMA_VERSION)
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error

    def validate(self, database_path: Path) -> None:
        """Check database integrity and supported schema version."""
        if not database_path.is_file():
            raise DatabaseMissingError(f"Missing database: {database_path}")
        try:
            with closing(self._connect(database_path)) as connection:
                self._ensure_integrity(connection)
                version = self._validated_schema_version(connection)
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

    def bind_project_identity(
        self,
        database_path: Path,
        project_id: UUID,
    ) -> int:
        """Bind a v4 database to one Project ID without changing its schema."""
        if not isinstance(project_id, UUID) or project_id.int == 0:
            raise ProjectDatabaseError("Project identity is invalid")
        tag = self.identity_tag(project_id)
        try:
            with closing(self._connect(database_path)) as connection:
                connection.execute(f"PRAGMA application_id = {tag}")
                connection.commit()
                stored = int(
                    connection.execute("PRAGMA application_id").fetchone()[0]
                )
                if stored != tag:
                    raise ProjectDatabaseError(
                        "Database project identity could not be persisted"
                    )
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error
        return tag

    def validate_project_identity(
        self,
        database_path: Path,
        project_id: UUID,
        *,
        require_bound: bool = True,
    ) -> None:
        """Verify the SQLite header identity tag against the manifest UUID."""
        expected = self.identity_tag(project_id)
        try:
            with closing(self._connect(database_path)) as connection:
                actual = int(
                    connection.execute("PRAGMA application_id").fetchone()[0]
                )
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error
        if actual == 0 and not require_bound:
            return
        if actual != expected:
            raise ProjectDatabaseError(
                "Database project identity does not match the manifest"
            )

    @staticmethod
    def identity_tag(project_id: UUID) -> int:
        """Return a stable non-zero signed-safe SQLite application ID."""
        if not isinstance(project_id, UUID) or project_id.int == 0:
            raise ProjectDatabaseError("Project identity is invalid")
        value = int.from_bytes(
            hashlib.sha256(project_id.bytes).digest()[:4],
            "big",
        ) & 0x7FFFFFFF
        return value or 1

    @staticmethod
    def _connect(database_path: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(database_path, timeout=5.0)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @staticmethod
    def _connect_read_only(database_path: Path) -> sqlite3.Connection:
        uri = f"{database_path.resolve().as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
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

    def _validated_schema_version(self, connection: sqlite3.Connection) -> int:
        current = self._schema_version(connection)
        pragma_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current != pragma_version:
            raise ProjectDatabaseError("Database schema versions do not match")
        if current > DATABASE_SCHEMA_VERSION:
            raise UnsupportedFormatVersionError(str(current))
        return current

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection, expected: int) -> None:
        quick = connection.execute("PRAGMA quick_check").fetchone()
        if quick is None or quick[0] != "ok":
            raise ProjectDatabaseError("SQLite quick_check failed")
        foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_rows:
            raise ProjectDatabaseError("SQLite foreign_key_check failed")
        ledger = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        if tuple(int(row[0]) for row in ledger) != tuple(range(1, expected + 1)):
            raise ProjectDatabaseError("Database migration ledger is incomplete")
        pragma_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if pragma_version != expected:
            raise ProjectDatabaseError("Database user_version is invalid")

    def _create_migration_backup(
        self,
        source: sqlite3.Connection,
        database_path: Path,
        source_schema: int,
        target_schema: int,
    ) -> Path:
        backups_root = database_path.parent / BACKUPS_DIRECTORY
        backups_root.mkdir(exist_ok=True)
        token = uuid4().hex
        staging = backups_root / (
            f".migration-v{source_schema}-to-v{target_schema}-{token}.staging"
        )
        final = backups_root / (
            f"migration-v{source_schema}-to-v{target_schema}-{token}"
        )
        staging.mkdir()
        backup_path = staging / DATABASE_FILENAME
        try:
            with closing(self._connect(backup_path)) as target:
                source.backup(target)
            with closing(self._connect_read_only(backup_path)) as verified:
                self._ensure_integrity(verified)
                if self._validated_backup_version(verified) != source_schema:
                    raise ProjectDatabaseError(
                        "Migration backup schema does not match the source"
                    )
                if verified.execute("PRAGMA foreign_key_check").fetchall():
                    raise ProjectDatabaseError(
                        "Migration backup foreign_key_check failed"
                    )
            size_bytes = backup_path.stat().st_size
            digest = self._sha256_file(backup_path)
            metadata = {
                "database": DATABASE_FILENAME,
                "format": "HMS_SQLITE_MIGRATION_BACKUP",
                "format_version": 1,
                "sha256": digest,
                "size_bytes": size_bytes,
                "source_schema": source_schema,
                "target_schema": target_schema,
            }
            metadata_path = staging / "migration-backup.json"
            temporary = staging / f".migration-backup-{token}.tmp"
            temporary.write_text(
                json.dumps(
                    metadata,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.replace(temporary, metadata_path)
            if backup_path.stat().st_size != size_bytes or self._sha256_file(
                backup_path
            ) != digest:
                raise ProjectDatabaseError("Migration backup verification failed")
            os.replace(staging, final)
        except Exception:
            if staging.exists():
                for child in staging.iterdir():
                    child.unlink(missing_ok=True)
                staging.rmdir()
            raise
        return final

    @staticmethod
    def _validated_backup_version(connection: sqlite3.Connection) -> int:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type = ? AND name = ?",
            ("table", "schema_migrations"),
        ).fetchone()
        if table is None:
            return 0
        row = connection.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()
        ledger = int(row[0]) if row else 0
        pragma = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if ledger != pragma:
            raise ProjectDatabaseError("Migration backup schema versions differ")
        return ledger

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _migrate_connection(self, connection: sqlite3.Connection) -> None:
        current = self._validated_schema_version(connection)
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
                self._verify_schema(connection, version)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

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
    2: (
        """
        CREATE TABLE cad_view_state (
            source_id TEXT PRIMARY KEY NOT NULL,
            state_version INTEGER NOT NULL CHECK (state_version > 0),
            display_mode TEXT NOT NULL,
            view_direction TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE cad_object_appearance (
            source_id TEXT NOT NULL,
            topology_path_version INTEGER NOT NULL CHECK (topology_path_version > 0),
            topology_path TEXT NOT NULL,
            geometry_kind TEXT NOT NULL,
            visible INTEGER NOT NULL CHECK (visible IN (0, 1)),
            color_r REAL NOT NULL CHECK (color_r >= 0.0 AND color_r <= 1.0),
            color_g REAL NOT NULL CHECK (color_g >= 0.0 AND color_g <= 1.0),
            color_b REAL NOT NULL CHECK (color_b >= 0.0 AND color_b <= 1.0),
            transparency REAL NOT NULL CHECK (
                transparency >= 0.0 AND transparency <= 1.0
            ),
            updated_at TEXT NOT NULL,
            PRIMARY KEY (
                source_id,
                topology_path_version,
                topology_path,
                geometry_kind
            )
        )
        """,
        """
        CREATE INDEX idx_cad_object_appearance_source_kind
        ON cad_object_appearance(source_id, geometry_kind)
        """,
    ),
    3: (
        """
        CREATE TABLE cad_xcaf_occurrence_appearance (
            source_id TEXT NOT NULL,
            geometry_kind TEXT NOT NULL,
            key_scheme TEXT NOT NULL,
            key_version INTEGER NOT NULL CHECK (key_version > 0),
            occurrence_path TEXT NOT NULL,
            product_identity TEXT NOT NULL,
            occurrence_role TEXT NOT NULL,
            visible INTEGER CHECK (visible IS NULL OR visible IN (0, 1)),
            color_r REAL CHECK (color_r IS NULL OR (color_r >= 0.0 AND color_r <= 1.0)),
            color_g REAL CHECK (color_g IS NULL OR (color_g >= 0.0 AND color_g <= 1.0)),
            color_b REAL CHECK (color_b IS NULL OR (color_b >= 0.0 AND color_b <= 1.0)),
            transparency REAL CHECK (
                transparency IS NULL OR (
                    transparency >= 0.0 AND transparency <= 1.0
                )
            ),
            updated_at TEXT NOT NULL,
            CHECK (
                (color_r IS NULL AND color_g IS NULL AND color_b IS NULL)
                OR
                (color_r IS NOT NULL AND color_g IS NOT NULL AND color_b IS NOT NULL)
            ),
            CHECK (
                visible IS NOT NULL
                OR color_r IS NOT NULL
                OR transparency IS NOT NULL
            ),
            PRIMARY KEY (
                source_id,
                geometry_kind,
                key_scheme,
                key_version,
                occurrence_path,
                product_identity,
                occurrence_role
            )
        )
        """,
        """
        CREATE INDEX idx_cad_xcaf_occurrence_source
        ON cad_xcaf_occurrence_appearance(source_id, key_scheme, key_version)
        """,
    ),
}

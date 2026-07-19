"""SQLite adapter for versioned CAD display state."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from hms_cadcam.cad.models import CadGeometryKind
from hms_cadcam.cad.persistent_keys import (
    PersistentCadObjectKey,
    TopologyPath,
    TopologyPathVersion,
)
from hms_cadcam.project.cad_state import (
    CAD_VIEW_STATE_VERSION,
    DEFAULT_DISPLAY_MODE,
    DEFAULT_VIEW_DIRECTION,
    CadViewState,
    PersistentObjectAppearance,
    default_cad_view_state,
)
from hms_cadcam.project.exceptions import ProjectDatabaseError
from hms_cadcam.project.models import datetime_to_json, utc_now
from hms_cadcam.viewer.models import DisplayMode, ObjectAppearance, ObjectColor, ViewDirection

logger = logging.getLogger(__name__)


class CadViewStateStore:
    """Read and replace CAD display rows without exposing SQLite to the UI."""

    def load(
        self,
        database_path: Path,
        valid_source_ids: Iterable[UUID],
    ) -> dict[UUID, CadViewState]:
        """Load valid state rows and safely ignore stale or malformed records."""
        valid_sources = set(valid_source_ids)
        states = {source_id: default_cad_view_state(source_id) for source_id in valid_sources}
        appearances: dict[UUID, list[PersistentObjectAppearance]] = {
            source_id: [] for source_id in valid_sources
        }
        try:
            with sqlite3.connect(database_path, timeout=5.0) as connection:
                connection.row_factory = sqlite3.Row
                for row in connection.execute("SELECT * FROM cad_view_state"):
                    try:
                        source_id = UUID(row["source_id"])
                        if source_id not in valid_sources:
                            logger.warning("Bỏ qua CAD view state có source_id không hợp lệ: %s", source_id)
                            continue
                        states[source_id] = CadViewState(
                            source_id=source_id,
                            state_version=row["state_version"],
                            display_mode=DisplayMode(row["display_mode"]),
                            view_direction=ViewDirection(row["view_direction"]),
                        )
                    except (KeyError, TypeError, ValueError):
                        logger.warning("Bỏ qua CAD view state không hợp lệ", exc_info=True)
                for row in connection.execute("SELECT * FROM cad_object_appearance"):
                    try:
                        source_id = UUID(row["source_id"])
                        if source_id not in valid_sources:
                            logger.warning(
                                "Bỏ qua CAD appearance có source_id không hợp lệ: %s",
                                source_id,
                            )
                            continue
                        key = PersistentCadObjectKey(
                            source_id=source_id,
                            geometry_kind=CadGeometryKind(row["geometry_kind"]),
                            topology_path_version=TopologyPathVersion(
                                row["topology_path_version"]
                            ),
                            topology_path=TopologyPath(row["topology_path"]),
                        )
                        appearance = ObjectAppearance(
                            visible=bool(row["visible"]),
                            color=ObjectColor(
                                row["color_r"], row["color_g"], row["color_b"]
                            ),
                            transparency=row["transparency"],
                        )
                        if appearance != ObjectAppearance():
                            appearances[source_id].append(
                                PersistentObjectAppearance(key, appearance)
                            )
                    except (KeyError, TypeError, ValueError):
                        logger.warning("Bỏ qua CAD appearance không hợp lệ", exc_info=True)
        except sqlite3.Error as error:
            raise ProjectDatabaseError(str(error)) from error
        result: dict[UUID, CadViewState] = {}
        for source_id, state in states.items():
            merged = CadViewState(
                source_id=source_id,
                state_version=state.state_version,
                display_mode=state.display_mode,
                view_direction=state.view_direction,
                object_appearances=tuple(appearances[source_id]),
            ).normalized()
            if not merged.is_default:
                result[source_id] = merged
        return result

    @contextmanager
    def transaction(self, database_path: Path) -> Iterator[sqlite3.Connection]:
        """Open one immediate transaction and rollback every failed save."""
        connection = sqlite3.connect(database_path, timeout=5.0)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise ProjectDatabaseError(str(error)) from error
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def replace_all(
        self,
        connection: sqlite3.Connection,
        states: Iterable[CadViewState],
        valid_source_ids: Iterable[UUID],
    ) -> None:
        """Replace all rows, writing only values different from defaults."""
        valid_sources = set(valid_source_ids)
        normalized = {state.source_id: state.normalized() for state in states}
        if not set(normalized).issubset(valid_sources):
            raise ValueError("CAD view state references an unknown project source")
        connection.execute("DELETE FROM cad_object_appearance")
        connection.execute("DELETE FROM cad_view_state")
        timestamp = datetime_to_json(utc_now())
        for state in normalized.values():
            if state.is_default:
                continue
            if (
                state.display_mode is not DEFAULT_DISPLAY_MODE
                or state.view_direction is not DEFAULT_VIEW_DIRECTION
            ):
                connection.execute(
                    """
                    INSERT INTO cad_view_state(
                        source_id, state_version, display_mode, view_direction, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        str(state.source_id),
                        CAD_VIEW_STATE_VERSION,
                        state.display_mode.value,
                        state.view_direction.value,
                        timestamp,
                    ),
                )
            for item in state.object_appearances:
                key = item.key
                appearance = item.appearance
                connection.execute(
                    """
                    INSERT INTO cad_object_appearance(
                        source_id, topology_path_version, topology_path, geometry_kind,
                        visible, color_r, color_g, color_b, transparency, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(state.source_id),
                        int(key.topology_path_version),
                        key.topology_path.value,
                        key.geometry_kind.value,
                        int(appearance.visible),
                        appearance.color.red,
                        appearance.color.green,
                        appearance.color.blue,
                        appearance.transparency,
                        timestamp,
                    ),
                )

"""Stage 12.5A strict codec and normalized repository round-trip gates."""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from hms_cadcam.cam.lathe.domain import lathe_operation_to_canonical_mapping
from hms_cadcam.cam.lathe.lathe_post.identity import LatheProgramIdentity
from hms_cadcam.cam.lathe.persistence import (
    LatheAuthoringCorruptError,
    LatheCodecError,
    LatheSqliteRepository,
    canonical_json_dumps,
    decode_operation,
    encode_operation,
    strict_json_loads,
)
from hms_cadcam.cam.lathe.types import LatheStrategyId
from hms_cadcam.project.database import ProjectDatabase
from tests.unit._lathe_persistence_fixtures import persistence_snapshot


def _store(path, snapshot):  # type: ignore[no-untyped-def]
    ProjectDatabase().initialize(path)
    repository = LatheSqliteRepository()
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        repository.replace_all(connection, snapshot)
    return repository


def test_all_11_strategies_basic_advanced_geometry_tool_and_post_round_trip(
    tmp_path,
) -> None:
    snapshot = persistence_snapshot(with_profiles=True)
    assert tuple(
        operation.strategy_id
        for operation in snapshot.programs[0].operations
    ) == tuple(LatheStrategyId)
    assert snapshot.programs[0].identity.revision == 0
    assert snapshot.programs[0].identity.source_generation == 0
    path = tmp_path / "project.db"
    repository = _store(path, snapshot)

    for _cycle in range(3):
        loaded = repository.load(
            path,
            expected_project_id=snapshot.programs[0].operations[0].ownership.project_id,
        )
        assert loaded.diagnostics == ()
        assert loaded.snapshot == snapshot
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute("PRAGMA foreign_keys = ON")
            repository.replace_all(connection, loaded.snapshot)

    program = snapshot.programs[0]
    assert len(program.post_config.tool_mappings) == 11
    assert all(item.geometry_binding is not None for item in program.operations)
    assert all(item.tool_binding is not None for item in program.operations)
    assert all(item.tool_binding.profile_id is not None for item in program.operations)  # type: ignore[union-attr]
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT position FROM lathe_operations ORDER BY position"
        ).fetchall() == [(index,) for index in range(11)]


def test_nullable_profile_restores_sql_null_as_python_none(tmp_path) -> None:
    snapshot = persistence_snapshot(strategies=(LatheStrategyId.FACE,))
    path = tmp_path / "project.db"
    repository = _store(path, snapshot)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT profile_id FROM lathe_tool_bindings"
        ).fetchone() == (None,)
    restored = repository.load(
        path,
        expected_project_id=snapshot.programs[0].operations[0].ownership.project_id,
    ).snapshot.programs[0].operations[0]
    assert restored.tool_binding is not None
    assert restored.tool_binding.profile_id is None
    assert restored.tool_binding.profile_revision is None


@pytest.mark.parametrize("field", ["generation", "revision"])
def test_zero_is_valid_but_bool_numeric_is_rejected(field: str) -> None:
    operation = persistence_snapshot(
        strategies=(LatheStrategyId.FACE,)
    ).programs[0].operations[0]
    mapping = lathe_operation_to_canonical_mapping(operation)
    if field == "generation":
        mapping["ownership"]["generation"] = True  # type: ignore[index]
    else:
        mapping["revision"] = True
    payload = canonical_json_dumps(mapping, max_bytes=1024 * 1024)
    with pytest.raises(LatheCodecError):
        decode_operation(payload)
    with pytest.raises(ValueError):
        LatheProgramIdentity(
            "project",
            "document",
            "source",
            True if field == "generation" else 0,  # type: ignore[arg-type]
            "setup",
            "program",
            True if field == "revision" else 0,  # type: ignore[arg-type]
        )


def test_operation_codec_is_canonical_duplicate_strict_and_bounded() -> None:
    operation = persistence_snapshot(
        strategies=(LatheStrategyId.FACE,)
    ).programs[0].operations[0]
    payload = encode_operation(operation)
    assert decode_operation(payload) == operation
    with pytest.raises(LatheCodecError, match="Duplicate"):
        strict_json_loads('{"a":1,"a":2}', max_bytes=100)
    with pytest.raises(LatheCodecError, match="canonical"):
        strict_json_loads('{"b": 1, "a": 2}', max_bytes=100)
    with pytest.raises(LatheCodecError, match="Control"):
        canonical_json_dumps({"value": "bad\u0001"}, max_bytes=100)
    with pytest.raises(LatheCodecError, match="Non-finite"):
        canonical_json_dumps({"value": float("nan")}, max_bytes=100)
    nested: object = {}
    for _ in range(33):
        nested = {"next": nested}
    with pytest.raises(LatheCodecError, match="depth"):
        canonical_json_dumps(nested, max_bytes=10000)


def test_missing_binding_and_corrupt_order_reject_complete_program_without_rewrite(
    tmp_path,
) -> None:
    snapshot = persistence_snapshot(
        strategies=(LatheStrategyId.FACE, LatheStrategyId.OD_ROUGH)
    )
    path = tmp_path / "project.db"
    repository = _store(path, snapshot)
    before_program = snapshot.programs[0]
    first_id = str(before_program.operations[0].ownership.operation_id)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            "DELETE FROM lathe_tool_bindings WHERE operation_id = ?", (first_id,)
        )
    with pytest.raises(LatheAuthoringCorruptError, match="tool identity"):
        repository.load(
            path,
            expected_project_id=before_program.operations[0].ownership.project_id,
        )
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM lathe_operations"
        ).fetchone() == (2,)

    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        repository.replace_all(connection, snapshot)
        connection.execute(
            "UPDATE lathe_operations SET position = ? WHERE operation_id = ?",
            (9, first_id),
        )
    with pytest.raises(LatheAuthoringCorruptError, match="order"):
        repository.load(
            path,
            expected_project_id=before_program.operations[0].ownership.project_id,
        )

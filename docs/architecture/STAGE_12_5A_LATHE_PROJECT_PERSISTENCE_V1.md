# Stage 12.5A — Lathe Project Persistence V1

Status: `BASELINE_AND_PERSISTENCE_ARCHITECTURE_LOCKED`

This specification is authoritative for the Stage 12.5A implementation. It
defines project persistence and restore only. It does not change the eleven
Lathe strategies, toolpath generators, Post output bytes, simulation, or
machine verification.

## 1. Ownership and database boundary

The only database is `<project>.HMS/project.db`. There is no Lathe sidecar
database or additional project format. UI code never opens SQLite; it uses
`ProjectService`, which composes the Qt-free
`LatheProjectPersistenceService` and its parameterized SQLite repository.

`ProjectSession` has optional `lathe_snapshot` and
`persisted_lathe_snapshot` values. `None` means the feature did not hydrate or
stage Lathe state. Save and autosave then leave existing Lathe rows untouched.
The separate `lathe_persistence_loaded` flag distinguishes an enabled empty
snapshot from feature-off opaque preservation.

One authored program owns an exact project/document/source/setup tuple. The
program carries source generation, program revision, a stable UUID, an authored
display name, selected Post profile, safe Basic Post configuration, typed tool
and offset mappings, and an exact ordered operation tuple. Each operation keeps
its existing `lathe.foundation.snapshot.v1` payload and typed identity.

Revision, source generation, binding revision, and derived owner revision are
non-negative. Zero is valid. Python validators use exact integer type checks,
so `bool` is rejected even though it subclasses `int`.

## 2. Schema v5

`DATABASE_SCHEMA_VERSION` is 5. The only new migration is additive v4 to v5.
It creates four normalized tables and deterministic indexes.

### `lathe_programs`

- `program_id TEXT PRIMARY KEY NOT NULL`
- `project_id`, `document_id`, `source_id`, `setup_id TEXT NOT NULL`
- `source_generation INTEGER NOT NULL CHECK (source_generation >= 0)`
- `revision INTEGER NOT NULL CHECK (revision >= 0)`
- `display_name TEXT NOT NULL`
- `operation_count INTEGER NOT NULL CHECK (0 <= operation_count <= 1000)`
- `selected_post_profile_id TEXT NULL`
- `post_config_json TEXT NOT NULL`
- `persistence_schema_version INTEGER NOT NULL CHECK (= 1)`

Indexes cover project/document lookup and the unique exact owner tuple.

### `lathe_operations`

- `operation_id TEXT PRIMARY KEY NOT NULL`
- `program_id TEXT NOT NULL` with `ON DELETE CASCADE`
- `position INTEGER NOT NULL CHECK (position >= 0)`
- `strategy_id TEXT NOT NULL`
- `revision INTEGER NOT NULL CHECK (revision >= 0)`
- `enabled INTEGER NOT NULL CHECK (enabled IN (0,1))`
- `payload_json TEXT NOT NULL`
- `parameters_schema_version INTEGER NOT NULL CHECK (= 1)`

`(program_id, position)` and `(program_id, operation_id)` are unique. Position
must restore as the contiguous range `0..operation_count-1`. The normalized
operation ID, strategy ID, revision, and enabled flag must exactly equal the
strict operation payload or the complete program is rejected.

### `lathe_tool_bindings`

- `operation_id TEXT PRIMARY KEY NOT NULL` with `ON DELETE CASCADE`
- `tool_id TEXT NOT NULL`
- `profile_id TEXT NULL`
- `assembly_id TEXT NULL`
- `capability_id TEXT NULL`
- `binding_revision INTEGER NOT NULL CHECK (binding_revision >= 0)`

The current foundation contract provides an assembly and one required strategy
capability whenever a binding exists. A tool binding payload requires its
normalized row and mandatory tool identity. SQL `NULL` profile restores only
as Python `None`; it is never converted to empty text or an invented profile.
An operation with no authored tool binding remains an incomplete authored
operation. A payload that claims a binding while its normalized identity row is
missing is corrupt and fails closed.

### `lathe_derived_snapshots`

The only kinds are:

1. `accepted_toolpath` — operation owner;
2. `accepted_program_ir` — program owner;
3. `neutral_listing` — program owner;
4. `basic_nc_preview` — program owner;
5. `conformance_review` — program owner.

Each row has a stable snapshot ID, exactly one permitted owner FK, non-negative
owner revision, positive schema version, algorithm version, SHA-256 dependency
fingerprint, SHA-256 content hash, and canonical payload. CHECK constraints
enforce owner kind. Partial unique indexes allow at most one cache per exact
owner/kind, and ordinary indexes cover both owner FKs.

## 3. Migration and backup

New database initialization applies migrations 1 through 5 and creates no
migration backup. Before a canonical existing `project.db` with supported
schema 1–4 is migrated, `ProjectDatabase`:

1. runs `quick_check` and verifies ledger/user-version agreement;
2. creates a private staged directory below project `backups/`;
3. copies SQLite with the online backup API;
4. verifies the copied schema, `quick_check`, foreign keys, size, and SHA-256;
5. writes sorted compact UTF-8 `migration-backup.json` containing format,
   format version, database name, source/target schemas, size, and digest;
6. atomically renames the staged directory to its final backup name;
7. retains that published backup even if the later migration fails.

Migration uses the existing per-version `BEGIN IMMEDIATE` transaction. DDL,
the `schema_migrations` row, `PRAGMA user_version`, `quick_check`,
`foreign_key_check`, complete ledger, and user-version verification all occur
before commit. Any exception rolls the v5 transaction back to v4. Newer,
mismatched, corrupt, and read-only databases requiring migration fail without
write or backup publication.

## 4. Canonical serialization and bounds

JSON is UTF-8, sorted by key, compact (`(',', ':')`), and encoded with
`allow_nan=False`. Decode requires those exact canonical bytes. It rejects:

- duplicate, missing, and unknown keys;
- malformed or non-object roots where an object is required;
- bool-as-number for typed revisions and generations;
- non-finite numbers and integers outside SQLite signed 64-bit range;
- forbidden controls, unsafe semantic strings, and path-bearing Post stems;
- unsupported value types, executable type tags, and transient runtime states;
- nesting deeper than 32 and all declared size/count violations.

Limits are 1,000 operations per program, 1 MiB per operation payload, 64
geometry references per operation, 200,000 motions/blocks, 16 MiB per derived
payload, 4 MiB Basic NC text, 10,000 conformance findings, depth 32, and 512
characters for semantic strings unless an existing domain type is narrower.

No pickle, marshal, compression, arbitrary constructor, Qt/OCP object,
callback, lock, worker/job/process/thread state, or raw executable NC object is
decoded. SQL values are parameters; table and column names are fixed source
constants.

## 5. Authoring restore

Feature-on loading validates all program rows and operation payloads before
loading derived rows. Ownership, exact order, all eleven canonical strategy
IDs, parameter state, geometry identifiers, Tool/Profile/Assembly identifiers,
revisions, enabled state, selected Post profile, and typed Basic Post mappings
round-trip without localized labels or runtime objects.

Any corrupt ownership/order/strategy/payload/binding rejects the complete
program and performs no rewrite. Missing external geometry or catalog objects
do not trigger name-based replacement. Their authored identifiers remain. The
runtime service resolves the exact stored Tool/Profile/Assembly tuple against
the current catalog and reports missing/incompatible readiness. Restored
geometry is conservatively incomplete until explicitly rebound/revalidated,
because persistence has no CAD-kernel resolver.

`LatheOperationService.restore_operations()` accepts only a complete immutable
tuple. It validates type, uniqueness, project/document/source/generation/setup
ownership, closed state, and one-time hydration before replacing the internal
map. No partial tuple is published on failure.

## 6. Derived restore

Only stable successful toolpaths are accepted. Program IR requires a complete
block collection. Neutral listing is bounded text. Basic NC requires
`BASIC_NC_PREVIEW_READY_UNVERIFIED`; machine-output-ready values are rejected.
Conformance findings are bounded. Active, running, queued, pending, computing,
or cancelling values are not persisted.

Repository load verifies canonical payload, content hash, and authored owner.
Corrupt derived rows produce a diagnostic and are omitted from the in-memory
cache set without rejecting or rewriting authoring. The public facade then
requires exact kind, owner, owner revision, schema version, algorithm version,
dependency fingerprint, and content hash before restore. Stale, ownership, or
version mismatch returns no cache and a typed diagnostic. Nothing regenerates
automatically.

## 7. Project lifecycle

- **Create:** initializes schema v5; enabled persistence hydrates an empty
  snapshot and does not create a migration backup.
- **Open:** feature-on validates authoring before derived. The UI controller
  creates one runtime service for the current project/document/source/setup and
  hydrates once. Feature-off does not instantiate the persistence-connected
  Lathe facade.
- **Presenter edit:** an accepted `command_completed` event stages the complete
  operation tuple through `ProjectService`; UI performs no SQLite access.
- **Save:** a staged Lathe snapshot is replaced inside the same SQLite
  transaction as CAD and CAM. Failure rolls the database transaction back.
  `None` preserves opaque rows. This feature creates no NC file.
- **Save As:** the staged SQLite online backup is rebound from old to new
  project UUID. Authoring, order, and bindings remain; operation payload
  ownership is rewritten canonically. Derived caches are dropped because the
  ownership fingerprint changed. Feature-off performs the same narrow
  fail-closed rebind without exposing a UI facade.
- **Autosave:** a staged Lathe snapshot is replaced inside the autosave
  database transaction. `None` preserves copied rows.
- **Recovery:** existing checksummed manifest/database recovery restores Lathe
  rows naturally and retains its rollback backup; feature-on loader then runs
  the same validation.
- **Read-only:** schema v5 may be inspected and hydrated through SQLite
  read-only mode. Stage, Save, Save As, import, and autosave are rejected.
  A schema requiring migration fails without writing.
- **Feature-off:** normal Save leaves rows untouched; online database copies
  preserve rows. Save As performs only required project-identity rebind and
  derived invalidation.

Reopen does not start a worker, publish a viewport actor, regenerate a preview,
run simulation, render Post output, or export NC.

## 8. Diagnostics

Semantic diagnostic IDs are stable and independent of translation:

- `lathe.persistence.derived_corrupt`
- `lathe.persistence.derived_stale`
- `lathe.persistence.derived_ownership_mismatch`
- `lathe.persistence.derived_version_mismatch`
- `lathe.persistence.authoring_incompatible`

VI, EN, and KO catalogs contain the same keys and `{subject}` placeholder.
Feature-on UI reports each restore diagnostic visibly once per subject.

## 9. Exclusions

Stage 12.5A does not implement or change:

- automatic NC export or a separate Lathe Save button;
- simulation, machine-specific tuning, machine verification, or a
  machine-ready claim;
- the eleven strategy algorithms, toolpath semantics, Basic Post renderer,
  profile bytes, controller output, or tool catalog topology;
- packaging, pushing, networking, or a Lathe sidecar format;
- previous Stage specifications or the six historical documents excluded by
  the work package.

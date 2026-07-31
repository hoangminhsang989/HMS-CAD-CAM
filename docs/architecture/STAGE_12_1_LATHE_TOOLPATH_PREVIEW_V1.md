# Stage 12.1 — Lathe Toolpath Preview V1

Status: owner-approved authoritative additive specification

## 1. Scope

Stage 12.1 adds deterministic, offline, controller-neutral Lathe toolpath
calculation and viewport preview for exactly three Stage 12 strategies:

1. `lathe.od_rough.v1`;
2. `lathe.od_finish.v1`;
3. `lathe.axial_drill.v1`.

The remaining exact Stage 12 strategies (`FACE`, `ID_ROUGH`, `ID_FINISH`,
`OD_GROOVE`, `ID_GROOVE`, `PART_OFF`, `OD_THREAD`, and `ID_THREAD`) have no V1
generator. Preview fails closed as `UNSUPPORTED_STRATEGY` with diagnostic
`toolpath_not_implemented_v1`; it never returns an empty successful path.

This stage does not provide Post, G-code, machine commands, cutter compensation,
simulation, stock removal, collision certification, persistence, database or
`.HMS` schema changes. A successful result means offline preview only, never a
machine-safe or production-ready program.

## 2. Coordinate and numerical contract

The pure domain is two-dimensional XZ in millimetres. `LatheXZPoint` stores
`x_diameter_mm` and `z_mm`; X is always diameter and there is no domain Y.
Values are immutable, finite and deterministic; `bool`, NaN and infinity are
rejected. No machine-axis inversion or implicit unit conversion is allowed.

The viewport adapter alone maps `(X, Z)` to `(X / 2, 0, Z)` on the fixed XZ
display plane. A single absolute tolerance of `1e-9 mm` is used for deterministic
zero-length, bounds and final-target comparisons.

## 3. Motion and result contract

Motion classes are exactly `RAPID`, `CUTTING`, `LEAD_IN`, and `LEAD_OUT`.
Every immutable segment has a stable sequence index, start/end XZ points,
semantic source/pass identity, immutable JSON-compatible metadata, and a
positive `feed_mm_per_rev` for fed motions. Zero-length segments are forbidden.

AXIAL_DRILL may add a positive-duration typed `DWELL` point event at final
depth. A dwell is never encoded as a zero-length line.

The immutable result records request/job identity, exact Lathe ownership,
strategy and algorithm versions, ordered motions, bounds, pass count, cutting
and rapid lengths, typed diagnostics/warnings, semantic fingerprint/cache key,
source (`worker` or `cache`) and deterministic generation metadata. Terminal
states are `SUCCESS`, `CANCELLED`, `INVALID_REQUEST`, `UNSUPPORTED_STRATEGY`,
and `GENERATION_FAILED`. Partial generation is never published.

Viewport colors are fixed: cutting yellow, rapid red, lead-in white and
lead-out green. Colors and other rendering state do not participate in semantic
identity.

## 4. Normalized stock

`LatheStockSnapshotV1` contains stock identity, source identity, generation,
outer/inner diameter, and front/back Z. OD must be positive; ID must be
non-negative and below OD; front and back must differ; all numbers are finite.

The `CylinderStock` adapter copies immutable values and does not change
`CylinderStock`. Its setup-local turning envelope is front `Z=0`, back
`Z=-length_mm`, OD equal to cylinder diameter and ID `0`. The deterministic
stock identity includes canonical CylinderStock, setup and source semantics so
stock/WCS changes invalidate cached results. No raw stock or OCP object crosses
the request boundary.

OD targets must be at or below stock OD and above stock ID. AXIAL_DRILL still
requires a live stock/source snapshot and follows the centerline at X=0.

## 5. Request, fingerprint and validation

`LatheToolpathRequestV1` copies the immutable Stage 12 operation snapshot,
exact ownership/revision, ordered parameters, geometry and Tool/Profile/Assembly
bindings with resolved capabilities, normalized stock, stable algorithm version,
and explicit sequence/job identity. No Qt/OCP/native object is permitted.

The builder fails closed for stale ownership/generation/setup, revision
mismatch, non-READY or disabled operation, read-only/closed session, missing or
incompatible geometry/tool/capability, invalid parameters/stock, and unsupported
strategy. It never clamps or rewrites user values.

Semantic SHA-256 fingerprint includes algorithm/strategy versions, operation
semantics, ownership, revision, parameters, geometry, Tool/Profile/Assembly
identity/revision/capabilities and stock/source generation. Cache identity is a
versioned derivation of that fingerprint. Job UUID, request sequence, time,
language, theme, UI scale, actor handles and localized strings are excluded.

Global algorithm version is `lathe.toolpath.preview.v1`. Strategy versions are
`lathe.od_rough.toolpath.v1`, `lathe.od_finish.toolpath.v1`, and
`lathe.axial_drill.toolpath.v1`.

## 6. OD_ROUGH algorithm V1

V1 roughs only an axisymmetric cylindrical envelope; it does not follow an
arbitrary free-form profile. Inputs are the Stage 12 OD_ROUGH fields plus stock.
`max_depth_of_cut_mm` is radial, therefore each nominal diameter decrement is
`2 * max_depth_of_cut_mm`. The final rough diameter is
`target_diameter_mm + 2 * radial_stock_to_leave_mm`.

Cut direction is `sign(end_z_mm - start_z_mm)`. The effective end is
`end_z_mm - direction * axial_stock_to_leave_mm` and must remain beyond the
start in the cutting direction. Clearance diameter is at least stock OD plus
twice clearance. Each deterministic pass approaches at safe clearance, leads
radially to its exact pass diameter, cuts axially to the effective end, leads
out to radial clearance and rapids back. The last pass reaches the exact rough
target, no duplicate/zero motion is emitted, and cancellation is checked
between passes and motion construction steps.

No contour roughing, undercut detection, insert compensation, stock-removal
simulation or collision checking is implied.

## 7. OD_FINISH algorithm V1

OD_FINISH creates exactly `finish_passes + spring_passes` deterministic passes
at the same nominal target diameter. `finish_passes >= 1` and
`spring_passes >= 0`. Every pass approaches from safe clearance, leads to target,
cuts from start Z to end Z, leads out/retracts and returns safely. Both Z
directions are supported and cancellation is checked between passes.

The result carries warning `nominal_centerline_preview`; no tool-nose/cutter
compensation or hidden stock mutation is inferred.

## 8. AXIAL_DRILL algorithm V1

AXIAL_DRILL is X=0, Z-only motion with POINT, AXIS or CYLINDER geometry and
`AXIAL_DRILLING` capability. Direction is derived from normalized stock front
toward stock back, never inferred from a localized UI value. Depth must remain
inside the stock envelope and the retract plane must be at/outside the front.

Without peck, the path moves from a safe centerline position to retract plane,
feeds to exact target, optionally dwells at final depth, rapids to retract plane
and returns safe. With peck, positive deterministic increments reach exact
target, including one final partial peck where needed; every peck retracts to
the approved plane and no zero-length final peck is emitted. Optional dwell is
at final depth only. Cancellation is checked between pecks and build steps.

No drill-tip compensation, chip-break timing, canned cycle, spindle sync or
coolant command is generated.

## 9. Registry, worker and cache

The injected Qt-free registry contains three executable entries and eight
explicit unsupported entries with no duplicate or fallback. Generator
exceptions become structured failures.

The application coordinator uses a bounded executor, cooperative cancellation,
latest-wins per exact Lathe operation ownership, operation/project/source/setup
isolation, late-result rejection, callback-outside-lock delivery, bounded
observable records and idempotent clean shutdown. Close or ownership transition
cancels work. Threads are never terminated unsafely.

The bounded deterministic FIFO cache is memory-only. It stores immutable
successful semantic results, writes no file/database/pickle, invalidates on any
semantic change, and routes cache hits through the same latest-wins/publication
gate as fresh results.

## 10. Qt and viewport publication

The narrow Qt bridge queues immutable results onto its owner thread, weakly
binds receivers, is idempotent, suppresses late signals after close, and never
lets a worker call the viewport.

The native-free Lathe publication carries exact ownership and grouped mapped
segments. The OCP backend builds candidate `BRepBuilderAPI_MakeEdge` compounds
and `AIS_Shape` groups, then atomically swaps them. Replacement and clear have
rollback; clear applies only to the exact active owner. Publication never clears
the scene, mutates CAD source/selection actors, changes current selection,
duplicates the active preview, or calls fit-all.

## 11. UI, lifecycle and feature topology

The independent `LATHE_TOOLPATH_12_1` feature is additive to `LATHE_9A9` and is
off by default. Feature-off creates no toolpath coordinator, bridge, sink,
adapter or Preview/Cancel actions. Feature-on creates exactly one of each per
live owner context and repeated initialization is idempotent.

The Lathe UI adds explicit accessible `Preview Toolpath` and
`Cancel Calculation` actions only when the capability is enabled. Parameter,
tool, geometry or strategy edits never auto-submit. READY executable operations
submit; invalid/incomplete/read-only/closed operations do not; unsupported
operations remain editable and show a localized V1 explanation. Cancel targets
the exact active job. Project/document/source/setup transition and close cancel
work and clear the exact owned preview. Fresh/cache results use one acceptance
and publication path.

UI states cover ready, calculating, cancelling, preview ready, cache hit,
cancelled, unsupported, invalid request, generation failed, publication failed
and stale result dropped. Visible success is explicitly an offline nominal
preview and not machine-ready. Vietnamese, English and Korean catalogs remain
in parity; runtime retranslation changes no fingerprint, result, operation,
revision or active calculation.

## 12. Acceptance and completion

Acceptance requires contract/numeric/immutability tests; stock and request
guards; deterministic generator and unsupported-strategy tests; cache,
cancellation, latest-wins, isolation and leak tests; Qt queued/deleted receiver
tests; atomic viewport replacement/clear/rollback and source/selection isolation;
explicit UI/lifecycle/feature/I18N/accessibility tests; Stage 12/9A.9/affected
9A.8 regression; compile/import/cycle/write/scope probes; deterministic visual
evidence; focused `pytest -W error`; and exactly one fresh-clone full repository
`pytest -W error` run.

Only after every gate passes may the exact allowlisted candidate be staged and
committed. Completion marks OD_ROUGH, OD_FINISH and AXIAL_DRILL preview
`COMPLETE`; the other eight strategies, Lathe Post, G-code, simulation and
persistence remain `NOT_STARTED`.

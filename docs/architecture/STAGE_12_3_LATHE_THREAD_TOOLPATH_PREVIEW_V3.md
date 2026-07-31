# Stage 12.3 — Lathe Thread Toolpath Preview V3

Status: owner-approved authoritative additive specification

## 1. Scope

Stage 12.3 adds executable offline previews for exactly
`lathe.od_thread.v1` and `lathe.id_thread.v1`. Together with Stage 12.1 and
12.2, the single generator registry contains exactly 11 executable strategies
and zero unsupported strategies.

A successful result is a
`PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW`: an immutable nominal XZ
centerline with pitch-derived feed semantics. It is not a true 3D helix and
does not model spindle encoder position, spindle phase, C-axis position,
thread-start phase alignment or a controller threading cycle.

Post, G-code, G32/G33/G76, machine synchronization, cutter/insert
compensation, collision certification, stock-removal simulation, persistence,
database/schema changes and machine-ready claims are excluded.

## 2. Coordinate, versions and identity

The existing Stage 12.1 contract remains authoritative: X is diameter in
millimetres, Z is setup-local axial position and the viewport alone maps X to
radius X/2. The numerical tolerance remains `1e-9 mm`.

The exact new strategy algorithm versions are:

- `lathe.od_thread.toolpath.v3`;
- `lathe.id_thread.toolpath.v3`.

The stage capability identifier is `lathe.thread.toolpath.preview.v3`.
Existing V1/V2 strategy versions are unchanged. The exact strategy version,
all thread parameters, ownership/revision, geometry, Tool/Profile/Assembly
evidence, stock and source generation participate in the existing semantic
fingerprint and cache key. Language, theme, UI scale, job ID, timestamp and
actor handles do not.

## 3. Request and validation

`LatheToolpathRequestV1` remains the only request. The existing builder
requires an enabled READY operation, exact live ownership and revision,
compatible Foundation geometry and tool capability, live normalized stock and
an open mutable session. It never clamps, rewrites or mutates operation
parameters.

Both strategies require unequal start/end Z, positive pitch, at least one
cutting pass, non-negative spring passes, `0 <= infeed_angle_deg < 90`,
major diameter greater than minor diameter and start/end inside the normalized
stock axial envelope.

OD_THREAD additionally requires major diameter no greater than stock OD, minor
diameter positive and no lower than stock ID. Its safe X is
`max(stock OD, major diameter) + 2 * clearance`.

ID_THREAD additionally requires an explicit positive stock bore, minor
diameter no lower than that bore and major diameter strictly below stock OD.
Its safe X is `max(0, stock ID - 2 * clearance)`. A zero stock ID returns
`missing_internal_bore`; no bore is inferred.

Thread-specific failures use stable diagnostics including
`invalid_thread_diameter_order`, `thread_major_exceeds_stock`,
`thread_minor_below_bore`, `invalid_pitch`, `invalid_pass_count`,
`invalid_spring_passes`, `invalid_infeed_angle`,
`thread_range_outside_stock`, `incompatible_thread_tool` and
`incompatible_thread_geometry`.

## 4. Deterministic pass schedule

Let:

`total_radial_depth_mm = (major_diameter_mm - minor_diameter_mm) / 2`.

For cutting pass `i = 1..pass_count`, cumulative radial depth is
`total_radial_depth_mm * i / pass_count`.

- OD cutting diameter is
  `major_diameter_mm - 2 * cumulative_radial_depth_i`.
- ID cutting diameter is
  `minor_diameter_mm + 2 * cumulative_radial_depth_i`.

The final OD cutting pass is assigned the exact minor diameter and the final ID
cutting pass the exact major diameter. The schedule is linear, finite and
deterministic, has no overshoot or duplicate cutting diameter, and is explicitly
a preview schedule rather than a production load-optimized schedule.

Each spring pass repeats the exact final cutting diameter, has a distinct
`spring_pass_index` and does not change final geometry. Result-level immutable
typed pass metadata and each motion's JSON-scalar metadata carry pass index,
cutting-pass count, optional spring-pass index, cumulative radial depth,
cutting diameter, pitch, hand, infeed angle, phase-neutral state, synchronized
feed and exact strategy algorithm version.

## 5. Pitch, hand and infeed semantics

`pitch_mm` is the authoritative synchronized cutting feed. Every thread
CUTTING motion has `feed_mm_per_rev == pitch_mm`; the nominal LEAD_IN and
LEAD_OUT use the same feed. The common operation `feed_mm_per_rev` remains a
positive Foundation parameter but is neither rewritten nor used as thread
cutting feed. No feed-mode or synchronization command is emitted.

RIGHT and LEFT remain exact metadata, differ in fingerprint/cache identity and
do not reverse the explicitly supplied start/end path. With otherwise identical
inputs their phase-neutral XZ points may be identical.

`infeed_angle_deg` is authoritative metadata only. Stage 12.3 does not model
compound-rest offset, insert-tip geometry, flank engagement or flank loading.

Successful thread results contain all four non-failure diagnostics:
`PHASE_NEUTRAL_SYNCHRONIZED_CENTERLINE_PREVIEW`,
`THREAD_FEED_DERIVED_FROM_PITCH`,
`NOMINAL_INFEED_ANGLE_METADATA_ONLY` and `NOT_MACHINE_READY`. Their stable
serialized codes use the corresponding lower-case values.

## 6. Lead-in, cutting and lead-out

Cut direction is `sign(end_z_mm - start_z_mm)`. Lead distance is exactly one
pitch:

- pre-start Z = `start_z_mm - direction * pitch_mm`;
- post-end Z = `end_z_mm + direction * pitch_mm`.

Each cutting or spring pass rapids to safe X, rapids axially to pre-start,
LEAD_INs to its cutting diameter at exact start Z, CUTTINGs from exact start to
exact end at pitch feed, LEAD_OUTs toward safe X at post-end and rapids safely
for the next pass. No zero-length or non-finite motion, hidden runout model,
pull-out cycle, crest/root profile, taper, variable pitch or multi-start thread
is generated.

## 7. Registry, worker and cache

Registry order is exactly FACE, OD_ROUGH, OD_FINISH, ID_ROUGH, ID_FINISH,
OD_GROOVE, ID_GROOVE, PART_OFF, OD_THREAD, ID_THREAD and AXIAL_DRILL. There is
no duplicate, unsupported entry, fallback or second registry.

Stage 12.3 reuses the single bounded coordinator/executor, cooperative
cancellation, latest-wins ownership isolation, callback-outside-lock behavior,
late-result rejection and idempotent shutdown. Cancellation is checked before
generation, between all cutting and spring passes, during motion construction
and before result finalization. Partial paths are never returned or cached.

The existing bounded FIFO memory cache stores only successful immutable
semantic results. Cache hits pass through the same latest-wins and publication
gate as worker results.

## 8. Viewport, UI and I18N

The existing publication adapter maps diameter X to radius X/2 and retains the
four motion colors: rapid red, cutting yellow, lead-in white and lead-out green.
Publication stays on the GUI thread and uses the existing atomic grouped actor
replacement, rollback and exact-owner clear. CAD source actors and selection
are preserved; no scene-wide clear, duplicate actor system or helical OCP curve
is introduced.

The existing explicit Preview and Cancel controls and
`LATHE_TOOLPATH_12_1` feature flag are reused. Edits never auto-submit.
READY OD_THREAD and ID_THREAD operations submit, calculate, publish, use cache
and cancel through the common flow. The visible status localizes all thread
limitations in Vietnamese, English and Korean and never claims G-code,
verified spindle phase, collision safety, production readiness or machine
execution.

Feature-off creates no coordinator, bridge, sink or Preview/Cancel controls.
Feature-on creates exactly one of each per live context; repeated
initialization is idempotent. Runtime language changes do not alter operation
state, request identity, cache identity or the accepted preview.

## 9. Acceptance and completion

Acceptance requires exact 11/0 registry/version tests; deterministic one/multi
pass and spring schedules; both Z directions; OD/ID stock and bore guards;
pitch-feed, hand/infeed and fingerprint tests; request/cache/cancellation/
latest-wins isolation; viewport atomicity and X/2 mapping; explicit UI,
feature/I18N and lifecycle tests; Stage 12.1/12.2/Foundation/9A.9 regression;
static/import/numerical/resource/leak/scope and deterministic visual evidence;
focused `pytest -W error`; exactly one short-path fresh-clone full repository
`pytest -W error`; exact staging/commit; and post-commit light verification.

Completion marks Stage 12.3, OD_THREAD, ID_THREAD and Lathe Toolpath Preview
11/11 COMPLETE. Lathe Post, G-code, simulation and persistence remain
NOT_STARTED.

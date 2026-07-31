# Stage 12.4A — Lathe Post Foundation V1

Status: owner-approved authoritative additive specification

## Scope

Stage 12.4A creates the controller-neutral intermediate program foundation for
the eleven accepted Lathe toolpath strategies. It assembles immutable semantic
blocks, validates ownership and motion intent, exposes one neutral preview
profile, renders a deterministic human-readable listing, and integrates a
read-only Program Preview surface into Lathe UI behind a default-off feature
flag.

It does not create a machine Post, executable G-code, NC/TAP/CNC files,
simulation, persistence, database/schema changes, machine offsets, spindle
phase/encoder synchronization, controller cycles, or packaging/push behavior.

## Version and identity

The exact identifiers are `lathe.program.ir.v1`,
`lathe.program.assembler.v1`, `lathe.neutral.listing.v1`, and
`hms.lathe.neutral_program_preview.v1`. `LatheProgramIdentity` contains
project_id, document_id, source_id, source_generation, setup_id, program_id,
and revision. IDs are non-blank; generation/revision are non-negative integers
and reject bool; instances are frozen and deterministic.

## Neutral Program IR

`LatheProgramIRV1` is immutable and contains the identity, ordered blocks,
semantic versions, selected profile ID, and SHA-256 semantic fingerprint.
The exact emitted block kinds are:

`PROGRAM_BEGIN`, `SET_UNITS`, `SET_PLANE`, `TOOL_INTENT`,
`SPINDLE_INTENT`, `RAPID_MOTION`, `LINEAR_CUT_MOTION`, `LEAD_IN_MOTION`,
`LEAD_OUT_MOTION`, `THREAD_CUT_INTENT`, `DWELL_INTENT`, `OPERATION_BEGIN`,
`OPERATION_END`, and `PROGRAM_END`.

`COOLANT_INTENT` is reserved by the contract but is `NOT_APPLICABLE` in V1
because no canonical Lathe coolant enum exists. No raw/controller/custom-text
block is permitted. Blocks contain contiguous sequence indexes, frozen typed
payloads, operation ownership when applicable, and semantic source identity;
they contain no localized text, Qt object, or OCP object.

Only MILLIMETRES and `LATHE_XZ_DIAMETER` are supported. X remains a diameter
coordinate and Z remains the setup-local axial coordinate.

## Intent and mapping

Tool intent preserves canonical Tool/Profile/Assembly identities, revisions,
resolved Lathe capabilities, and operation ownership. No controller tool or
offset number is invented. Spindle intent is semantic START/STOP with CW/CCW
and positive finite RPM for START; it never emits S/M words, CSS, ranges,
clamps, or synchronization. Feed remains on motion payloads.

RAPID, CUTTING, LEAD_IN, LEAD_OUT, and typed DWELL map to their exact semantic
blocks. Thread CUTTING motions from OD_THREAD and ID_THREAD map to
`THREAD_CUT_INTENT` and preserve start/end diameter/Z, pitch, pitch-equal
cutting feed, hand, pass/spring metadata, cumulative radial depth, cutting
diameter, infeed angle, `phase_neutral=True`, and algorithm version. They are
not G32/G33/G76 equivalents and remain non-executable.

## Assembly lifecycle and validation

`LatheProgramAssemblerV1` emits PROGRAM_BEGIN, units, plane, then one
OPERATION_BEGIN/TOOL_INTENT/SPINDLE START/motion/SPINDLE STOP/OPERATION_END
section per input operation, ending in PROGRAM_END. Input order is semantic;
localized names never reorder operations and no tool optimization occurs.

Assembly rejects stale program or operation ownership, revision mismatch,
missing/failed/cancelled/stale toolpaths, missing Tool/Profile binding,
duplicates/empty operations, unsupported units/plane, bad sequence indexes,
non-finite coordinates/feed, thread feed different from pitch, and any
machine-specific block. A failed assembly never returns a partial successful
program.

## Fingerprint and profile registry

Fingerprint input includes both schema versions, ownership, ordered operation
identities/revisions, tool bindings, result fingerprints, strategy/algorithm
versions, spindle intent, and neutral profile version. Language, theme, UI
scale, timestamps, actor handles, widget state, and display labels are
excluded. Identical semantic input is byte-deterministic.

The registry has exactly one built-in profile:
`hms.lathe.neutral_program_preview.v1`, controller family
`CONTROLLER_NEUTRAL`, machine model `NONE`, preview-only true, and machine
output false. Production profile count is zero; production Post requests fail
closed with `PRODUCTION_POST_UNAVAILABLE`.

## Listing and readiness

`lathe.neutral.listing.v1` renders a deterministic read-only listing with the
warnings “PREVIEW ONLY”, “NOT MACHINE-READY”, “NO CONTROLLER POST PROFILE”,
and “DO NOT RUN ON A CNC MACHINE”. It does not resemble controller output,
does not contain G/M/T words, and never writes automatically to disk.

Readiness states are `INVALID`, `INCOMPLETE`, `NEUTRAL_PREVIEW_READY`,
`PRODUCTION_POST_UNAVAILABLE`, and `MACHINE_OUTPUT_READY`. Stage 12.4A may
return the first three and production-unavailable state; it must never return
`MACHINE_OUTPUT_READY`.

## UI, lifecycle, feature topology, and I18N

The explicit action is “Program Preview” / “Xem chương trình trung gian”. It
assembles only on user action, shows identity/status, profile, operation
summary, diagnostics, read-only listing, and a persistent warning footer.
Refresh and Close are provided; Save NC, Send to Machine, raw G-code editing,
and automatic assembly are absent. Repeated open reuses the owned preview
surface and close is idempotent. Project/document/source/setup/revision/result
changes clear the exact owned preview; no cross-session leakage is allowed.

`LATHE_POST_FOUNDATION_12_4A` defaults OFF. Feature-off creates no service,
action, or panel; feature-on creates one service and one owned preview panel.
Vietnamese, English, and Korean catalogs contain parity keys for all preview,
status, diagnostic, warning, Refresh, and Close text. Stable IDs and block
names are not localized and language changes do not alter fingerprint/state.

## Acceptance and completion

Completion requires immutable IR, all eleven strategy mappings, semantic
validation, deterministic fingerprint/listing, exactly one neutral profile,
fail-closed production readiness, read-only UI/lifecycle behavior, catalog
parity, focused/static/resource/visual gates, and a fresh-clone full
`pytest -W error` gate. Stage 12.4A is COMPLETE only after commit verification.

Production machine Post, machine G-code, simulation, and persistence remain
NOT_STARTED. The exact blocker for any later production Post is an undefined
Lathe machine/controller/Post contract (controller family, coordinate rules,
tool mapping, spindle/thread semantics, safety policy, and output contract).

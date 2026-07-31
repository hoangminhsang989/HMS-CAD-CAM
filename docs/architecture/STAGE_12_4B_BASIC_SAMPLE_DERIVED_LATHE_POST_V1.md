# Stage 12.4B — Basic Sample-Derived Fanuc-Style Lathe Post V1

## Status and source contract

This specification defines the one owner-approved basic Post derived from:

* `260516---CTS26079-M001-24--25X489_9-L2.NC`
* `260516---CTS26079-M001-40--20X8-L1.NC`
* `260516---CTS26079-M001-24--25X489_9-L1.NC`

The profile is deliberately unverified. Its exact identity is
`hms.lathe.fanuc_basic_sample_v1`, controller family is
`FANUC_STYLE_UNVERIFIED`, and both machine and controller models are
`UNSPECIFIED`. It can produce a reviewable `.NC` file, but it is not a
certified production Post, machine-ready output, simulation, transfer, or CNC
execution path.

## Scope and topology

The Post consumes the existing immutable Stage 12.4A `LatheProgramIRV1`; it
does not create a second IR, alter toolpath generation, or persist data. The
feature is `LATHE_BASIC_POST_12_4B`, default OFF, and depends on
`LATHE_POST_FOUNDATION_12_4A`. Feature-off leaves the neutral Program Preview
unchanged and registers no basic profile, renderer, exporter, or action.

The immutable editable configuration is schema `lathe.basic.fanuc.profile.v1`.
Defaults are safe sample-derived values: metric `G21`, feed-per-revolution
`G99`, `G54`, fixed RPM `G97`, `M03/M04/M05`, `M08/M09`, `G28 U0 W0`, optional
stop `M01`, CRLF, ASCII, no line numbers, warning enabled, and no machine
verification or production approval. The output extension is `.NC` and the
default program number is `O0000`.

## Program format

Each program is deterministic and uses `%`, a four-digit `O` number, stable
ASCII English comments, `G21`, and `G99`. The unverified warning remains in
the header while either verification or production approval is false. The
program ends with `M05`, `M09`, `G28 U0 W0`, configurable final safe tool
`T0303`, `M30`, and `%`, without duplicate consecutive shutdown lines.

An operation is emitted in existing IR order as its operation comment, an
optional sanitized tool description, `G0 TNNNN`, coolant on, fixed-RPM spindle
start, work offset/first rapid, mapped motions, configured spindle stop,
coolant off, reference return, and `M01` between operations. No automatic tool
optimization or parameter-edit regeneration is performed.

## Semantic mapping

`RAPID_MOTION` maps to `G0`; `LINEAR_CUT_MOTION`, `LEAD_IN_MOTION`, and
`LEAD_OUT_MOTION` map to `G1`. X remains the lathe diameter and Z remains the
axial coordinate; no viewport transform or sign inversion is applied. Feed is
emitted on the first cutting move and whenever it changes, never on rapid.
There is no invented arc output.

`THREAD_CUT_INTENT` for both OD and ID strategies maps to basic single-point
`G32 X... Z... F<pitch>`. Feed equals the semantic pitch exactly. Pass and
spring-pass ordering is preserved. Before the first thread operation the file
contains `(THREAD OUTPUT USES BASIC G32 - SPINDLE PHASE NOT VERIFIED)`.
There is no `G76`, multi-start, taper, encoder, or spindle-phase guarantee.

Nonzero `DWELL_INTENT` fails closed with diagnostic
`BASIC_POST_DWELL_SYNTAX_UNDEFINED`; no guessed `G04` syntax is emitted.
Unknown or unsupported IR blocks, missing operation tools, missing typed
mapping, duplicate mapping, non-finite numbers, and invalid controller words
also fail closed with no partial NC output.

## Typed mappings and sample extensions

Every operation must have an explicit immutable mapping from canonical
`tool_id` to positive tool and geometry-offset numbers. The renderer emits
`G0 TTTTOO` (for example `T0101` and `T1010`) and never derives a turret number
from a UUID or silently assigns tool 1. Optional wear offsets are retained for
future profiles; they are not emitted in V1.

The sample-specific `M73`, `M74`, secondary `G55`, initial tool call/manual
stop, and setup sequence are typed options and default OFF. They are emitted
only when individually enabled. Unrestricted raw setup text is rejected and
cannot be enabled by the normal UI. Their meaning is not inferred.

## Numeric and comment rules

Coordinates use at most three decimals, feeds and thread pitch at most four;
trailing zeroes and a leading zero may be suppressed, decimal points are used,
scientific notation is forbidden, and negative zero is normalized to zero.
NaN, infinity, booleans, invalid RPM, and fractional RPM without the documented
half-away-from-zero integer policy are rejected. Comments are parenthesized,
uppercase, ASCII-safe, control-character-free, and sanitize embedded
parentheses; arbitrary raw comments are not accepted.

## Preview, validation, export, and readiness

The Qt-free renderer returns an immutable output snapshot containing lines,
text, SHA-256, diagnostics, readiness, and a suggested filename. Validation
requires `%` envelope, one O-number, `G21`, `G99`, exactly one `M30`, balanced
comments, final newline, deterministic CRLF, no control characters or
unsupported tokens, complete operation boundaries, and thread feed/pitch
agreement. Valid text is `BASIC_NC_PREVIEW_READY_UNVERIFIED`.

Explicit export is user-selected only, uses `.NC`, ASCII/CRLF deterministic
bytes, a sibling temporary file with flush/fsync and atomic replace, overwrite
confirmation, cleanup on failure, and post-write SHA verification. The export
service never writes a database or project. Before every export, an unchecked
per-session acknowledgement states that the basic Post is unverified and the
program must be checked before machine use. Preview requires no acknowledgement.
After a successful export readiness is
`BASIC_NC_EXPORT_READY_UNVERIFIED`; `MACHINE_OUTPUT_READY` is never returned.

## UI and language contract

The existing neutral Program Preview remains available as a separate tab. The
feature-on UI adds a read-only profile selector, unverified badge, explicit
Generate Basic NC Preview and Export `.NC` actions, NC text tab, diagnostics,
SHA, suggested filename, and an accessible acknowledgement dialog. There is no
Send to Machine, Run, DNC, FTP, network, machine-ready, or production-approved
action. NC bytes, SHA, and readiness are invariant across VI/EN/KO switching.

## Acceptance and completion

Completion requires focused profile/format/mapping/motion/thread/dwell/output/
export/UI/topology/I18N tests, static and resource gates, deterministic and
lifecycle probes, a fresh detached clone with the locked R3 provisioning, one
full repository `python -m pytest -q -W error`, diff-check, exact allowlist
staging, and the commit message `hoan thanh Post Tien co ban kieu Fanuc va xuat
file NC V1`. Packaging and pushing are excluded. The final state is complete
only for this basic unverified Post; machine-specific tuning, verification,
simulation, persistence, package, and push remain not started.

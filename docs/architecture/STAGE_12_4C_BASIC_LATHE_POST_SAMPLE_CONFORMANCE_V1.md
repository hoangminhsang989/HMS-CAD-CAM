# Stage 12.4C — Basic Lathe Post Sample Conformance V1

## Status, purpose, and safety boundary

This specification is authoritative for format-and-structure review of the
unverified basic Lathe Post `hms.lathe.fanuc_basic_sample_v1`. It compares HMS
output with three owner-provided NC sample contracts. Completion means only
`FORMAT_AND_STRUCTURE_REVIEW_COMPLETE`; it never means machine verified,
machine ready, certified, controller approved, or safe to run.

The existing readiness values remain `BASIC_NC_PREVIEW_READY_UNVERIFIED` and
`BASIC_NC_EXPORT_READY_UNVERIFIED`. `MACHINE_OUTPUT_READY` is forbidden. No
machine-specific tuning, simulation, persistence, automatic transfer, DNC,
network output, packaging, or pushing belongs to this stage.

## Owner samples and privacy contract

| Alias | Approved filename | Bytes | Lines | SHA-256 |
|---|---|---:|---:|---|
| SAMPLE_A | `260516---CTS26079-M001-24--25X489_9-L2.NC` | 905 | 60 | `942741ac0e02aacbd1f9a8a966ed2204b74b9e12b51ffd8d8785473ef10ccf32` |
| SAMPLE_B | `260516---CTS26079-M001-40--20X8-L1.NC` | 2779 | 211 | `805d9d97c247bfb318a1a67c87ada1d3eca9d2d671c40fcb91d314ed9107a92e` |
| SAMPLE_C | `260516---CTS26079-M001-24--25X489_9-L1.NC` | 989 | 88 | `cd99df3a8a941e6417b7ef04e02af3e74f1229df3eb6a18bdc6d8811ecb01488` |

The original NC bodies are proprietary and must not be committed, copied into
fixtures, staged, or reproduced in reports. The repository stores only the
approved filenames, hashes, byte/line counts, sanitized structural signatures,
and high-level token evidence. Synthetic HMS-owned fixtures use generic names,
dimensions, and tool comments.

External discovery is optional and read-only. It accepts only an explicitly
supplied `HMS_LATHE_SAMPLE_NC_DIR`, reads only the three exact filenames, and
records encoding, newline, size, line count, and hash. It never infers a path or
scans a drive/profile. Absence gives `EXTERNAL_SAMPLE_NOT_AVAILABLE`; mismatch
gives `SAMPLE_HASH_MISMATCH`. Either state retains this derived contract.

## Sample-derived lexical and numeric contract

Controller words are uppercase; comments occupy balanced parenthesized lines;
line numbers default OFF; units are metric `G21`. Numbers are deterministic
decimal notation with no scientific notation, NaN, infinity, or negative zero.
Leading-zero suppression and trailing-zero trimming are supported. Coordinates
use at most three decimals and feed/thread pitch at most four. Observed forms
include `Z.315`, `F.25`, `X0.`, `U0. W0.`, `X-.6`, and `K-.5`; the basic profile's
locked reference-return spelling remains `G28 U0 W0`.

Comments are normalized to ASCII, control characters are rejected, and embedded
parentheses become brackets. Expressions, variables, macros, and unrestricted
raw controller text are outside the analyzer and renderer contract.

## Program and operation structure

The common program envelope is:

1. `%`;
2. `O0000` (typed four-digit O-number);
3. `(TEN FILE = ...)` and `(SHL_TECH)` comments, with HMS identity/safety
   comments allowed;
4. `G21`;
5. operation blocks in Program IR order;
6. `T0303`;
7. `M30`;
8. `%` and final CRLF.

The sample-backed operation order is:

1. typed operation/tool-offset comment and optional sanitized tool description;
2. `G0 TNNNN`;
3. `M8`;
4. `G97 S... M03` or typed reverse direction `M04`;
5. first rapid approach `G0 G54 X... Z...`;
6. `G99` on, or immediately before, the first cutting activation;
7. deterministic `G0`, `G1`, or basic `G32` motions;
8. intentional `M05` when enabled, then `M9` and `G28 U0 W0`;
9. `M01` between operations only by default.

Stage 12.4C behavior revision 1 uses renderer algorithm
`lathe.basic_fanuc.renderer.v1.1`. It moves `G99` from the unrelated global
header to the first `G1`/`G32` activation in each operation. The stable profile
ID remains unchanged. The behavior revision is rendered in a comment so output
SHA identity changes with renderer behavior; the immutable Program IR
fingerprint does not change.

## Modal conformance and operation boundaries

The analyzer checks the bounded modal vocabulary `%`, O-number, comments,
`G21`, `G54/G55`, `G99`, `G0/G1/G2/G3/G32`, `I/K`, T/S words,
`M03/M04/M05/M8/M9/M01/M30/M73/M74`, `G28 U/W`, and `X/Z/F` numeric words.
It verifies tool/coolant/spindle/approach/cut/shutdown ordering per operation,
the single program end, no default line numbers, and final `T0303/M30/%`.
It is deliberately not a complete G-code parser and executes nothing.

## Strategy coverage

Synthetic representative output is divided into:

* Scenario A — `OD_ROUGH`, `OD_FINISH`, two typed tools, and `M01` boundary;
* Scenario B — `FACE`, `AXIAL_DRILL`, `ID_ROUGH`, `ID_FINISH`, `OD_GROOVE`,
  `ID_GROOVE`, and `PART_OFF`;
* Scenario C — `OD_THREAD`, `ID_THREAD`, spring passes, deterministic `G32`,
  exact pitch feed, and the phase-not-verified warning.

All eleven strategies must render deterministic ASCII/CRLF bytes. Scenarios A
and B are sample-backed. Scenario C is
`CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE`; missing sample thread syntax is not
a failure and does not authorize `G76`.

## Intentional safety deviations

The following are visible, non-failing deviations:

* `INTENTIONAL_SAFE_DEVIATION_WARNING_HEADER` — the HMS unverified warning;
* `INTENTIONAL_SAFE_DEVIATION_SPINDLE_STOP` — explicit `M05` even though the
  samples generally omit it between operations;
* stronger fail-closed output validation and explicit export acknowledgement.

Similarity must never remove these safety mechanisms or elevate readiness.

## Optional machine/setup tokens

`M73`, `M74`, `G55`, initial `G0 T0303 (CU)`-style setup intent, and `M0` are
`SAMPLE_OPTIONAL_MACHINE_SPECIFIC`. Every option defaults OFF, requires a typed
profile field, has no inferred meaning, and cannot be enabled by filename or
strategy. Unrestricted raw setup sequences remain rejected.

## Unsupported and uncovered sample features

The samples contain `G2/G3` with `I/K`, while Program IR V1 has no arc block.
The report therefore includes
`SAMPLE_FEATURE_NOT_REPRESENTABLE_CURRENT_IR_ARC_IK`. No line-to-arc fitting or
fake arc output is permitted. This is non-failing for linear HMS toolpaths.

No authoritative dwell spelling exists, so nonzero dwell remains fail-closed as
`BASIC_POST_DWELL_SYNTAX_UNDEFINED`; `G04` is not guessed. No authoritative
thread example exists, so OD/ID thread output keeps Stage 12.4B `G32`, exact
pitch, and phase warning under `CONTRACT_DERIVED_NO_OWNER_SAMPLE_COVERAGE`.

## Immutable model and decision rules

The Qt-free public model comprises `LatheSampleContractV1`,
`LatheSampleSignature`, `LatheNcLineClassification`,
`LatheNcConformanceFinding`, `LatheNcConformanceReport`,
`LatheNcConformanceStatus`, and `LatheNcConformanceAnalyzerV1`.

Exact statuses are:

* `CONFORMANT`;
* `CONFORMANT_WITH_INTENTIONAL_SAFE_DEVIATIONS`;
* `PARTIALLY_CONFORMANT`;
* `NONCONFORMANT`;
* `NO_SAMPLE_COVERAGE`;
* `INVALID_INPUT`.

Each finding has exactly one category from `PROGRAM_ENVELOPE`, `COMMENTS`,
`UNITS`, `TOOL_CALL`, `SPINDLE`, `COOLANT`, `WORK_OFFSET`, `FEED_MODE`,
`MOTION`, `THREAD`, `ARC`, `REFERENCE_RETURN`, `OPTIONAL_STOP`, `PROGRAM_END`,
`NUMERIC_FORMAT`, `LINE_NUMBERING`, `OPTIONAL_MACHINE_EXTENSION`,
`SAFETY_DEVIATION`, `UNSUPPORTED_SAMPLE_FEATURE`, or `PRIVACY`, and one severity
from `INFO`, `PASS`, `NOTICE`, `WARNING`, or `ERROR`.

Mandatory failures include invalid envelope/O-number, missing `G21`, invalid or
missing tool call, missing/duplicate program end, malformed comments, default
line numbers, nondeterministic numbers, raw injection, `G76`, invalid operation
order, false readiness claims, or default-on optional machine tokens. There is
no weighted machine-compatibility score.

## Read-only NC Preview behavior

With `LATHE_BASIC_POST_12_4B` OFF, neither basic Post nor conformance UI exists;
the Stage 12.4A neutral preview is unchanged. With it ON, the existing singleton
Basic NC Preview contains one read-only **Sample Conformance Review** section and
one explicit **Run Conformance Review** action. The action analyzes only the
current in-memory NC text. It performs no scan, export, mutation, transfer,
persistence, or automatic review.

The section shows profile ID, behavior revision, status, mandatory findings,
intentional deviations, unsupported features, 11/11 representative strategy
coverage, owner-sample coverage classification, external sample state,
“structural review only,” and “not machine verification.” It never shows a
compatibility percentage or a verified/certified/safe/controller-approved label.

VI/EN/KO catalogs have parity for every new label. Switching language may
change UI labels only; NC bytes/SHA, finding codes, analyzer status, and report
semantics remain identical. Vietnamese remains the fallback.

## Acceptance matrix

| Area | Acceptance |
|---|---|
| Privacy | Three exact metadata contracts; no proprietary NC body or tool text committed |
| Lexical/numeric | ASCII comments, deterministic decimals, precision limits, no control/macro injection |
| Structural/modal | Exact envelope, operation order, first-cut `G99`, shutdown and final sequence |
| Safety | Warning and `M05` visible as intentional deviations; readiness unverified |
| Optional tokens | Typed and default OFF; no raw setup path |
| Arc/dwell/thread | Arc unsupported, dwell fail-closed, thread no-owner-sample coverage, no `G76` |
| Scenarios | A/B/C deterministic, synthetic, CRLF, all 11 strategies |
| UI/topology | Explicit read-only singleton review; feature-off unchanged |
| I18N/accessibility | VI/EN/KO parity, stable codes/bytes, accessible action and tab order |
| Regression | Stage 12.4B/A, 12.3/12.2/12.1/Foundation and related lifecycle gates pass |
| Repository | Static/resource/focused gates, fresh clone, one full `pytest -W error`, exact staged candidate |

## Completion definition and remaining limitations

Completion requires executable implementation, automated tests, deterministic
scenario evidence, static/resource/focused gates, a detached fresh-clone full
gate, exact candidate staging, commit verification, and no proprietary sample
file in Git. Evidence is runtime-only under `.pytest_tmp`.

After completion the basic review state is
`FORMAT_AND_STRUCTURE_REVIEW_COMPLETE`, while machine-specific Post,
machine verification, simulation, and persistence remain `NOT_STARTED`. The
machine-specific blocker remains `EXACT_LATHE_MACHINE_AND_CONTROLLER_UNDEFINED`.

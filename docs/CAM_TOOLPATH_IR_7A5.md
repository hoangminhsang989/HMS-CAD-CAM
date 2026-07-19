# CAM Toolpath Intermediate Representation 7A.5

## Coordinate-space and pose policy

Toolpath IR v1 stores every movement in `SETUP_WCS` with one explicit known length
unit. It does not store a controller work-offset word, controller axis spelling or
machine-coordinate command. `MACHINE`, `TOOL_LOCAL` and `MODEL_SOURCE` are explicit
coordinate-space vocabulary for later adapters, but published v1 movement artifacts
reject them. Machine/post conversion belongs outside this domain.

A `Pose` contains a finite `Point3` and a deterministically normalized non-zero tool
axis. IR v1 does not model IK or a complete rotary-axis/orientation solution. Linear
and rapid motion may interpolate pose for future rendering, while arc motion requires
a constant tool axis from start to end.

## Event model

The ordered immutable stream supports rapid, linear and arc movement plus semantic
dwell, spindle state, coolant state, feed mode, tool context and marker events. These
events contain no G/M code, controller block number or raw post syntax. Motion is
classified as cutting, non-cutting, link or retract. Zero-length movement is rejected;
a semantic marker must represent a zero-displacement occurrence. Continuity is
validated exactly and is never repaired by inserting an unrequested move.

Each event has a stable `ToolpathEventId`, contiguous sequence index, source operation
and semantic provenance. The builder derives UUID5 IDs from operation identity, input
fingerprint, sequence and provenance. Repeated occurrences at different sequence
positions therefore remain distinct. Event ID stability is not promised across
different algorithm/strategy versions.

## Arc representation

An arc is explicit start/end pose, center, normalized plane normal and signed sweep in
radians. Positive and negative sweep represent the two semantic directions and sweeps
larger than 180 degrees are supported. Radius equality, coplanarity and the expected
endpoint are checked with a fixed IR geometry tolerance. Full circles and multi-turn
arcs are rejected in v1 and must later use a separately specified representation.
Arc bounds evaluate analytical extrema on every world axis, not just endpoints.

## Builder and process state

`ToolpathBuilder` is single-use. It requires provenance header data, then one initial
pose. It tracks current pose and process state, rejects redundant state transitions,
uses a mutable event list internally, and converts that list once to a tuple at
finalize. A finalized or aborted builder cannot be reused. An error before finalize
does not create or publish a partial artifact and the builder never adds safety moves
implicitly.

Spindle values are OFF/CLOCKWISE/COUNTERCLOCKWISE with explicit RPM when running.
Coolant values are OFF/FLOOD/MIST/THROUGH_TOOL. Feed mode is controller-neutral units
per minute, units per revolution or inverse-time vocabulary. No capability mapping or
controller code is performed in this stage.

## Fingerprint and publish policy

Artifact content fingerprint is canonical SHA-256 over schema, coordinate space,
units, operation/setup/WCS provenance, input fingerprint, expected tool/machine
fingerprints, initial pose, ordered semantic events, completion and diagnostics.
Artifact ID, computation-token UUID and optional timestamp metadata are excluded;
computation generation remains included. Loading verifies bounds, statistics,
continuity and the stored fingerprint, so tampered payloads fail atomically.

The pure-domain publish contract accepts a candidate only when the 7A.4 computation
token is current, input fingerprint matches, the operation still exists and is
enabled, operation revision/provenance matches, and artifact fingerprint verifies.
A stale token cannot change newer state; other stale provenance leaves the operation
dirty and never returns an artifact for storage.

## Bounds, statistics and size policy

Statistics include rapid, cutting, link, retract and arc lengths, dwell-inclusive
basic duration and deterministic per-kind counts. Duration is marked partial when a
rapid lacks an explicit rate. It is an estimate, not controller cycle time. Tool
orientation and cutter envelope do not enlarge positional bounds in v1.

Artifacts store an immutable tuple in v1; the builder avoids repeated full-stream
copies. Decode supports an optional event-count validation limit. Iterator/chunk and
binary persistence contracts are deferred rather than optimized prematurely.

## Not implemented

7A.5 includes no machining algorithm, compensation, stock removal, collision,
kinematics/IK, renderer, worker, SQLite/file persistence, project lifecycle, Post
Processor or G-code generation.

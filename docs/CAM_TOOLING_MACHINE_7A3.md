# CAM Tooling and Machine 7A.3

## Tool geometry variants

`ToolDefinition` is an immutable, revisioned definition with one concrete cutting
geometry variant selected by `ToolFamily`. Cylindrical, ball-end, bull-nose, drill,
chamfer, tap, turning-insert and custom envelopes have typed fields rather than
parameter dictionaries. All physical dimensions use one known definition unit.
Ball tools use diameter consistently; bull-nose corner radius cannot exceed cutter
radius. The turning-insert model is only a conservative envelope, not full ISO data.

## Holder profile policy

`HolderDefinition` stores an ordered immutable tuple of cylindrical/conical sections.
The profile starts at axial zero. Each section has positive diameters and an end
greater than its start; the next section must begin exactly at the previous end.
Diameter steps are allowed, but axial gaps and overlaps are rejected. This profile is
collision-envelope metadata only and owns no CAD geometry.

## Tool assembly revision and stale policy

`ToolAssembly` uses policy A: it stores tool/holder IDs together with expected
revision, content fingerprint and unit. It does not embed mutable definitions.
Changing a library definition never changes an existing assembly silently. Native-
free assessment reports missing tool, stale tool, missing holder, stale holder or
incompatible unit. Assemblies have independent IDs, so one tool may have multiple
holder/stickout configurations. `ToolLibraryPort` defines CRUD with expected-revision
conflict handling but has no persistence adapter or singleton.

## Machine axis and capability model

`MachineDefinition` supports mill, turn and mill-turn kinds. Linear axes carry
`Length`; rotary axes carry `Angle`; axis names and semantic roles are distinct from
controller axis words. Capabilities describe milling, turning, live tooling,
probing, tapping, threading, coolant, feed/rapid, spindle count and supported future
operation families. Arbitrary semantic axis names represent table/head/C/Y/B-like
capabilities without hard-coding G-code spelling.

## Kinematic limits

The kinematic chain is a parent-before-child hierarchy of fixed transforms and
optional axis references on tool, workpiece or fixed sides. Every machine axis is
referenced exactly once. The model preserves enough structure for later mill-turn
extension, but implements no IK, TCP/RTCP, singularity handling, collision checking
or machine simulation.

## Not implemented

This stage does not connect tools or machines to Setup/operations and does not add
operation trees, toolpaths, stock removal, UI, SQLite/project lifecycle integration,
Post Processor, G-code or milling/turning algorithms.

# CAM Core 7A.1

## GeometryReference invariants

CAM editable state is authoritative, but it never owns CAD geometry. Geometry is
addressed only through a versioned `GeometryReference` containing a project source
UUID, persistent selectors and expected revision/fingerprint. Runtime
`CadDocumentId`, `CadObjectId`, OCP, AIS and Python object identity are forbidden.
Ambiguous or foreign-source matches fail closed. Topology changes never silently
rebind a reference; rebind will be an explicit user operation in a later stage.

## Unit policy

Lengths carry `mm`, `inch` or `unknown`. An unknown length unit is never interpreted
as millimetres and blocks conversion or any future operation requiring physical
dimensions. Angle, feed and spindle values carry explicit semantic units. All
numeric values must be finite. The future calculation layer must declare its own
tolerance as a `Length` in a known unit; there is no implicit global tolerance.
Quantization is allowed only at an explicit export boundary. Domain storage and
fingerprint comparison keep validated values and never apply hidden rounding.

## Stale and rebind policy

Resolvers compare source identity, scheme/version, cardinality, source revision and
geometry fingerprint. A mismatch returns a specific non-success status. A
fingerprint mismatch is `TOPOLOGY_CHANGED`; a revision-only mismatch is `STALE`.
Neither result mutates the reference. Public resolution results contain diagnostics,
not native geometry objects.

## Not implemented in 7A.1

This stage does not add CAM jobs/setups, operations, toolpaths, machining algorithms,
SQLite schema, project lifecycle integration, UI, simulation, G-code or Post
Processor support. It defines only the value objects, serialization rules and
resolution contract needed by those later stages.

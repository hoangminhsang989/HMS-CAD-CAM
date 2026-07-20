# Production Post UI workflow — 7D.2.3

The CAM workspace now exposes a Post Processor panel for the selected
operation.  The panel is a UI/application boundary over the existing
controller-neutral Post 7D.1, production profile 7D.2.1 and NC export
service 7D.2.2 contracts.

## Workflow

1. Select one CAM operation.  The panel captures an immutable
   `PostSourceSnapshot`; CAD-only nodes do not expose Post state.
2. Edit a draft for the production profile, controller tool binding, filename,
   safe Z, work offset, cutter-compensation policy and simulation gate.
3. Apply validates all fields atomically.  An invalid draft does not mutate the
   project or mark it dirty.  Applying a valid draft invalidates an older
   PostResult/NC artifact and requires an explicit Generate.
4. Validate reports deterministic production diagnostics.  Generate runs the
   pure-Python Post service with a latest-wins stale guard; it never writes a
   file.  Tapping is fail-closed for the v1 production profile.
5. Preview displays the exact production text read-only, with line count,
   bytes, CRLF/UTF-8 metadata and SHA-256.  UI-only gutters must not alter the
   bytes.  The profile is marked `NOT CERTIFIED / REVIEW REQUIRED`.
6. Save Managed Artifact calls `NCExportService` with a project-managed `.fn`
   target.  Export is a separate explicit action and accepts local, mapped and
   UNC filesystem directories.  Credentials, auto-export and direct CNC/DNC
   communication are not supported.

Managed-artifact clear is explicit and only removes the project-managed NC
file and sidecar.  External files and source Toolpath/Post data are retained.
Save/Open, Save As, Autosave and Recovery do not generate or externally export
NC programs; lifecycle changes invalidate active requests and stale callbacks.

The implementation remains single-operation.  Multi-operation assembly,
production Tapping, machine certification, stock removal, machine kinematics
and direct CNC transfer are outside this stage.

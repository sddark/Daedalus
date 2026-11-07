# Integrated checkpoint verification

Date: 2026-11-07. Local Mac ARM64, CPython 3.12.16, Apple Git 2.50.1,
cadquery-ocp-novtk 8.0.1.1.0.

## Implemented

- A persistent workspace selected by the CLI, defaulting to
  `~/.local/share/daedalus/projects`; imports preserve separate projects.
- App-owned bare Git checkpoints containing immutable STEP source and complete
  placement state, with no remote or publication.
- Protected checkpoint, history, compare, restore and project-open routes.
- Checkpoint UI, saved/dirty status, comparison deltas, independent ghost
  transforms, project reopening and save/discard/cancel replacement flows.
- Restore loads historical placement into working state. A subsequent save creates
  a descendant checkpoint; no history rewind or source replacement occurs.
- Revision and session-base guards prevent stale tabs/processes silently
  overwriting newer saved placements. Exact durable request receipts can be
  reconciled after an uncertain response without permitting a new conflicting save.
- Damaged-project errors preserve the visible model and allow explicit recovery
  by opening a healthy project or reimporting.
- Existing toolbar, mesh shading, opaque Normal, complete translucent Diff and
  stable camera behavior were preserved. History panels scroll independently.

## Completed automated checks

- Full integrated Python suite: **87 tests passed**.
  Log: `.runtime/checkpoint-backend-final.log`.
- Final frontend/renderer suite: **50 tests passed**.
  Log: `.runtime/checkpoint-ui-final.log`.
- Exact geometry proof: **13/13 passed**, including source preservation,
  shared-instance identity and STEP export/reimport.
  Evidence: `.runtime/checkpoint-integrated-proof/proof.json`.
- Dependency check, Python source compilation and `git diff --check` passed.
- The project virtual environment was updated to an editable install of the
  local source, without changing runtime dependencies. The regular
  `.venv/bin/daedalus serve --help` exposes `--workspace`.

The Python suite covers checkpoint save/no-op/replay, source integrity, stale
revisions and heads, two independent sessions editing different components,
committed-then-uncertain receipt reconciliation, malformed IDs and input,
cross-site/API protections, native rollback, candidate-load atomicity, damaged
project escape, project source retention and exact export round trips.

## Real-browser checks on this Mac

- Demo import, movement, checkpoint save and a subsequent movement.
- A 10 mm checkpoint compared with 25 mm working state reported a 15 mm delta.
- Cancel/Escape, save-before-restore, explicit discard, and restored-state save
  as a new descendant.
- Saved project reopening after a server restart and switching between two projects.
- Cancelled dirty project switching kept the working state; explicit discard
  switched projects.
- Opaque Normal, complete translucent solid Diff, no dashed outlines and stable
  camera when changing comparisons/view modes.
- Desktop layout and a 390 x 844 narrow viewport, including replacement-dialog
  bounds and no horizontal document overflow.
- No warning/error entries in the browser console during the final checks.
- Export UI completed and wrote a 17,488-byte STEP file in Downloads. Native
  reimport verified Fixed at [0, 0, 0] mm and Movable at [40, 0, 0] mm, one shared
  definition and both exact 6000 mm³ volumes.

## Boundaries and remaining validation

- A fresh curved-sphere upload through browser automation could not be completed
  because the browser extension did not have file-URL access. No permission was
  changed. Existing curved-geometry/renderer regressions and the earlier Mac
  sphere verification remain distinct evidence, not a new upload test.
- No physical Raspberry Pi execution, Pi RAM/performance measurement, hostile
  native-import sandboxing, or power-loss durability testing is claimed.
- Unsaved working movements remain transient. Explicit checkpoints persist.
- This remains a trusted-local, loopback-only application with direct occurrence
  translations; it is not arbitrary geometry diff or multi-user CAD.

## Local work and preservation

At this verification checkpoint, all changes were local and uncommitted; no GitHub
push had been made. The subsequent saved-pair milestone is recorded in
[SAVED_COMPARISON_VERIFICATION.md](SAVED_COMPARISON_VERIFICATION.md).
Pre-integration UI and source backups are in `.runtime/checkpoint-backup`.
The browser verification server uses the isolated
`.runtime/checkpoint-browser-projects` workspace on port 8000. To reopen those
test projects, start with the same workspace option. A regular `daedalus serve`
uses the default persistent workspace instead.

The older CHECKPOINT_IMPLEMENTATION.md and CHECKPOINT_VERIFICATION.md describe
the earlier backend-only preparation. This integration record supersedes their
statements that HTTP/UI integration and macOS testing were still outstanding.

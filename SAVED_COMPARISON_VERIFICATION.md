# Saved checkpoint comparison verification

Date: 2026-11-07. macOS ARM64, CPython 3.12.16, OCP 8.0.1.1.0.

## Milestone

Review two saved checkpoints without restoring either or replacing working edits.
The same-source placement report includes complete endpoint matrices, signed
per-occurrence deltas, movement distance and unchanged count. Choose either
saved checkpoint as From or To; working positions remain the default To endpoint.

The browser shows read-only review explicitly. Move, reset, save, restore and
export are disabled during review to prevent an action on a different assembly
than the one displayed. Return to working positions restores the original display
and editing controls. Comparison and Normal/Diff changes do not reset the camera.
A dirty project replacement can cancel safely or save the preserved working state
before continuing. Selecting a movement in the change list selects that occurrence.

The GET comparison route retains checkpoint-vs-working compatibility and adds an
optional `after` full checkpoint ID. It reads the same project history only, keeps
the editing revision guard, and never changes the native CAD assembly or saved state.

## Automated checks

- Full Python suite: **89 passed**. Two new HTTP regression tests cover saved pairs,
  reversed/equal endpoints, complete unchanged matrices, working fallback, invalid,
  blank, duplicate and foreign IDs, stale revision rejection, and no changes to
  native assembly, document state, revision or stored files.
- Frontend and Three.js renderer suite: **55 passed**. New checks cover read-only
  controls and handler guards, working-state preservation, repeat selection,
  failed/mismatched responses, cancel/save-before-replacement, endpoint transforms,
  unchanged geometry sharing, equal pairs, camera preservation and source changes.
- Exact geometry proof: **13/13 passed**. Source integrity, shared definitions,
  placement and STEP export/reimport remained correct.
- Dependency integrity, Python compilation and whitespace checks passed.

Local test logs and generated fixtures are ignored under `.runtime/` and are not
published. Tests use synthetic assemblies rather than private user CAD files.

## Chrome checks on the Mac

Using a separate workspace on port 8001:

1. Saved movements at 10 mm and 25 mm, then left a 40 mm movement unsaved.
2. Compared the 10 mm and 25 mm saved checkpoints in Diff: **15 mm** delta,
   independent translucent saved-position surfaces, and read-only controls.
3. Returned to working: **40 mm** was preserved and the comparison delta became
   **30 mm**, with editing and export enabled again.
4. Cancelled a dirty project reload while reviewing; review and working data stayed.
5. Compared a checkpoint with itself: **0 moved, 2 unchanged**.
6. Reversed the pair: **-15 mm X**, with the correct From and To labels.
7. Checked 390 x 844 layout: document width and scroll width were both 390 px.
   Restored the browser's original viewport afterward.
8. No warning/error console entries were reported during the final check.

## Scope still open

This completes the saved-to-saved placement comparison slice, not the entire
comparison roadmap. It does not infer added/removed/renamed parts or changed shapes
across different STEP sources; occurrence identity remains source-scoped. The UI
lists the latest 50 checkpoints. Exact geometric differences, physical Raspberry
Pi performance and unsaved working-session autosave remain separate work.

The earlier checkpoint, toolbar, smooth-shading and overlay changes were preserved.
Publication contains source, tests and documentation only, not private CAD files,
runtime projects, test downloads, virtual environments or browser state.

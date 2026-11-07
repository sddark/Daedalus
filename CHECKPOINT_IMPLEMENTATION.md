# Local checkpoint implementation

Historical backend-only record from 2026-11-06. The Mac integration is described
in [INTEGRATION_VERIFICATION.md](INTEGRATION_VERIFICATION.md); its status supersedes
remaining-integration statements below.

Prepared against public Daedalus commit `9063b7aef6f7e40b43014e6e9b77609e6d765ef8` in an isolated directory. Nothing was published and the offline Mac checkout was not accessed. This is an additive backend slice, not completion of the project/history UI.

## Implemented

- `placements.py`: strict source-scoped manifests, complete absolute-offset state, canonical serialization and pure O(n) movement comparisons. Shared definitions and duplicate names remain distinct occurrences. Source, units, identity, matrix and kernel-version mismatches fail closed.
- `history.py`: a dedicated app-owned bare Git repository per project; original STEP plus canonical placement state; linear checkpoint history; source integrity checks; cross-process locking; stale-head detection; no-op suppression; durable request receipts including no-op saves.
- `checkpoints.py`: adapter saving the authoritative `ViewerSession` under its existing lock and editing-revision guard, plus candidate reconstruction from a checkpoint without swapping the active model.
- `cad.py`: a checked movement operation that restores the exact prior native placement if movement/snapshot fails.
- `viewer.py`: uses checked movement, blocks further editing/export if native rollback fails, and cleans only session-owned temporary import directories. Adopting a durable checkpoint source no longer exposes that project's directory to deletion on a later import.

All static UI files are unchanged from the public baseline. In particular this patch does not replace the Mac's unpublished transparent solid overlays, camera fixes, no-dashes styling or toolbar.

## Checkpoint transaction

The durable authority is Git itself. A checkpoint tree has only `source.step`, `project.json`, `placements.json` and `request.json`. Original source and manifest blobs are reused rather than exporting or remeshing on save.

A save creates immutable objects, then uses one `git update-ref --stdin` transaction to:

1. Compare-and-swap `refs/heads/main` against the expected head, or verify it for a no-op.
2. Create `refs/requests/<uuid>` pointing to a receipt blob describing the original request and its result.

The transaction records both outcomes together. No second mutable metadata file or journal must agree with the ref update. A lost response is reconciled by reading the head and receipt. If that read also fails, `UncertainCheckpoint` explicitly instructs the caller to inspect state before retrying. Reusing a request UUID with different data is rejected.

Git commands use fixed argv, a controlled environment, disabled hooks/signing/content filters and no worktree. No remote, shell, checkout, reset, clean or publication operation is used. The store does not adopt an existing directory. The original STEP is read-only and hash-checked, and historical reads accept full known/reachable checkpoint IDs rather than Git revision expressions.

Uncheckpointed `ViewerSession` state is still transient. This implementation deliberately does not claim working-session autosave, power-loss durability on every filesystem, untrusted-repository import, arbitrary STEP geometry matching or Internet-safe upload handling.

## Minimal use

Run from the repository with `PYTHONPATH=src` and the pinned OCCT environment. Import/meshing must remain under the existing shared kernel lock.

```python
from pathlib import Path
from uuid import uuid4
from engineering_workbench.cad import StepAssembly
from engineering_workbench.history import CheckpointStore
from engineering_workbench.placements import compare, make_state

source = Path("assembly.step")
assembly = StepAssembly.load(source)
# Use the actual pinned binding version from the application, not a guessed value.
store = CheckpointStore.create(
    Path("new-project-directory"),
    source.read_bytes(),
    assembly.snapshot(),
    "cadquery-ocp-novtk==8.0.1.1.0",
)
old_head = store.head()
before = store.read()
after = make_state(store.manifest, before["offsets"])
occurrence_id = next(iter(after["offsets"]))
after["offsets"][occurrence_id] = [10, 0, 0]
result = store.save(
    after,
    expected_head=old_head,
    message="Move one component 10 mm",
    request_id=str(uuid4()),
)
report = compare(store.manifest, before, store.read(result.checkpoint_id))
```

For the application use `save_session_checkpoint(...)`, not client-supplied placements. This checks both the displayed editing revision and Git head. `load_checkpoint_assembly(...)` returns a fully reconstructed candidate plus snapshot; tessellate and validate it before replacing the active session. It does not silently switch the session or discard current edits.

Restore should load historical placements into working state and save a new descendant checkpoint. Do not rewind the Git branch, replace the immutable original source or use exported STEP IDs as native identities.

## Remaining integration points

1. Configure a persistent workspace root through the CLI, not an arbitrary browser path. Create a new project for each source import and preserve old projects.
2. Add protected checkpoint/history/compare/restore routes. Keep existing Host/Origin/CSRF checks. Save through `save_session_checkpoint` with both `revision` and `expected_head`. New routes are not included here.
3. Handle `StaleCheckpoint`, `UncertainCheckpoint` and `recovery_required` explicitly in UI. A restore candidate must not overwrite unsaved work without a clear save/cancel flow. A failed native recovery requires reimport or a validated checkpoint reload.
4. Add a small history list and checkpoint action on top of the Mac changes. Keep import-relative offset, dirty-since-checkpoint state and selected comparison delta separate. Comparison matrices should not replace `baseline_matrix_3x4` or change camera state.
5. Decide working-session autosave separately. This patch saves only explicit checkpoints. The backend still uses temporary sessions until lifecycle integration is added.
6. Test the integrated branch on the actual Pi. These tests ran on Linux x86_64, not ARM64 hardware or macOS.

The store uses POSIX `fcntl` locking, suitable for the intended Linux/Mac environment; Windows support is not implemented. It assumes an application-owned local repository, not one malicious local software can concurrently rewrite. Ref transactions protect ordinary process/concurrency failures, not every storage/controller/power-loss condition.

## Apply safely to the Mac checkout

Use the accompanying patch on a separate branch after inspecting and preserving current uncommitted changes. Do not replace the whole checkout with this directory. Most files are new; the only existing production files modified are `cad.py` and `viewer.py`. Review those small changes against any local backend edits. There are no frontend hunks.

The isolated repository contains reconstructed baseline commits, not the real public repository's full ancestry. Apply/cherry-pick only the implementation patch, not its synthetic baseline commits.

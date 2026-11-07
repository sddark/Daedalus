# Checkpoint verification

Historical backend-only record from 2026-11-06. The Mac integration is described
in [INTEGRATION_VERIFICATION.md](INTEGRATION_VERIFICATION.md); its status supersedes
remaining-integration statements below.

Date: 2026-11-06.

Environment: Linux x86_64, CPython 3.12.14, Git 2.52.0. A fresh `.runtime/ocp8` virtual environment installed `cadquery-ocp-novtk==8.0.1.1.0` and `cadquery-ocp-proxy==8.0.1.1.0`. `pip check` passed. No global dependency or OS changes were made.

Final checks:

- Full Python backend suite: 70 tests passed with the exact pinned OCCT binding.
- Existing UI/renderer regression suite: 30 tests passed. These are Node/jsdom/Three.js tests, not real-browser visual QA.
- Existing exact geometry demonstration: 13 of 13 assertions passed.
- `git diff --check` and Python compilation passed.

The new tests include canonical serialization, invalid state, source/kernel lineage, shared definitions, rotated matrices, tolerance behavior, immutable source and source-blob reuse, save/reopen/restore history, stale editing revisions, concurrent stale heads, durable no-op request receipts, retries after lost responses, ref transaction failure, object-write failure, explicitly uncertain outcomes, disabled hooks/environment injection, malformed IDs, source tampering/symlinks, out-of-history commits, real STEP export/reimport and native movement rollback.

Independent read-only review found a project-cleanup integration hazard and missing no-op retry receipts. Both were fixed and covered with regression tests; focused re-review found no remaining actionable issue in those fixes.

Reproduce:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .runtime/ocp8/bin/python -m unittest discover -s tests -v
cd tests/ui
npm ci --ignore-scripts
npm test
```

The original compatibility binding (`cadquery-ocp==7.8.1.1.post1`) also passed an earlier 67-test backend run before the final adapter and review fixes. The release claim is the final pinned-binding run above, not that earlier compatibility run.

Not run: actual Pi or macOS execution, physical power-loss tests, full browser visual interaction, or tests after merging unpublished Mac UI changes. No frontend was changed and no new history HTTP routes or checkpoint UI are claimed.

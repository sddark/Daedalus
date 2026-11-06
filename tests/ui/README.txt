Focus View frontend tests

These tests use Node's built-in test runner and a pinned development-only jsdom.
Node >=22.7 is required for ESM syntax detection in the static .js modules. The
application itself has no Node/npm runtime requirement and no build step.
Verified with Node v24.22.1 and npm 11.9.0.

From the repository root:

  npm ci --prefix tests/ui --ignore-scripts
  npm test --prefix tests/ui

The committed package-lock.json pins the development dependency tree. Do not
ship tests/ui/node_modules. The validation run in this environment reused an
already-installed temporary jsdom@26.1.0, without installing anything into the
application:

  NODE_PATH=/tmp/daedalus-dom-tests/node_modules npm test --prefix tests/ui

ui.test.cjs runs the real app controller against a jsdom document, a small API
fixture and a viewer test double. It covers pending actions, cancellation,
repeated moves, upload errors/retry, revision conflicts, network reconciliation,
input limits, label escaping, accessible controls and WebGL-unavailable behavior.
viewer.test.mjs runs the real Three.js geometry/transform code without a GPU. It
checks shared mesh buffers, matrix conventions, movement outlines, reset, fitting,
selection and disposal. These tests do not prove browser pixel rendering, pointer
orbit/pan/zoom behavior, actual browser layout or performance on Raspberry Pi.

For manual browser QA, start `daedalus serve`, open its loopback URL and verify:
- Empty workspace at desktop and phone widths, keyboard focus and readable labels.
- Sample or STEP import, list selection and click selection of both rendered parts.
- Drag orbit, right-drag pan, wheel/pinch zoom, F/Fit and keyboard arrow controls.
- Movable X=10 shows a shaded new position and dashed original outline in one view.
- Repeating X=10 does nothing; X=20 is 20 mm from import, not 30 mm accumulated.
- Reset clears the outline. Export downloads a new STEP, preserving the source.
- After a move, replacement import Cancel keeps the current model; Accept replaces.
- Bad STEP leaves the prior view intact and the same filename can be retried.
- While importing/moving, repeat submissions, selection and other mutations lock.
- A second tab's edit causes a stale first-tab move or replacement to reload state.
- WebGL unavailable/context-loss messaging is helpful and numeric controls work.

The bundled vendor README and THREE-LICENSE.txt document upstream provenance.

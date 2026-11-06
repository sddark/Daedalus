# Verification record

Verified on **2026-11-06**, on Linux x86_64, CPython 3.12.14 and glibc 2.41.
This is an experimental local viewer, not a validated Raspberry Pi release.

## Passed

- Clean isolated installation of `cadquery-ocp-novtk==8.0.1.1.0` and its sole
  Python runtime dependency, `cadquery-ocp-proxy==8.0.1.1.0`, from PyPI.
- ARM64 CPython 3.11/3.12 binary-only dependency downloads, wheel metadata,
  checksums and native-library inspection. See [ARM64_VERIFICATION.md](ARM64_VERIFICATION.md).
- **41 Python tests** against both the final source and the installed application:
  12 original CAD/demo tests, 3 mesh/transform/budget tests and 26 HTTP/lifecycle tests.
- **13/13 headless demo checks** against the installed application.
- Both `daedalus` and compatibility `engineering-workbench` console commands.
- Final application wheel construction, isolated installation and `pip check`.
  All 14 application source/static files matched their installed bytes, including
  browser JavaScript, styles, local Three.js/OrbitControls and the MIT notice.
- **30 frontend logic tests**: 23 jsdom controller flows and 7 tests exercising
  real Three.js geometry/transforms without a graphics context.
- JavaScript syntax checks and `git diff --check`.

The final application wheel was built offline with the available packaging tools
and installed without dependency downloads into the clean no-VTK environment.
This checks package contents and installed execution. It is not a fresh Pi install.
Frontend tests used Node 24.22.1, npm 11.9.0 and an already-installed matching
jsdom 26.1.0. The test manifest and lockfile are included for reproducibility;
Node is not required to run the app.

## What the tests cover

- Shared STEP definitions, independent occurrence movement and source integrity.
- Box and curved-cylinder surface tessellation, rotated baseline preservation,
  parent-axis offsets and exported STEP reimport checks.
- Original-position outlines, current-placement matrices, shared display buffers,
  selection, fit bounds and resource disposal in the JavaScript geometry code.
- Absolute movement semantics, repeated/stale requests and concurrent revisions.
- Stale-tab import/demo replacement protection and revision-bound STEP export.
- Failed import/tessellation retaining the previous model and temporary-file cleanup.
- Input types, non-finite/oversized offsets, body lengths, file limits, mesh budgets,
  malformed revisions, Host/Origin/CSRF enforcement and static path traversal.
- DOM flows for pending actions, repeated clicks, replacement cancellation,
  same-file retry, revision conflicts, network-outcome reconciliation, export,
  safe label rendering and WebGL-unavailable messaging.

## Not yet verified

- **Actual browser rendering or layout.** Chromium could not start in the test
  environment because socket creation was unavailable. The separate cloud browser
  could not reach the isolated local test server. DOM/geometry tests do not verify
  pixels, real pointer controls, browser downloads, GPU behavior or responsive layout.
- **Physical Raspberry Pi execution.** Pi installation, CPython 3.11 execution,
  ARM64 runtime behavior, peak RAM, import/export time, thermals and usable model
  sizes have not been measured.
- Arbitrary real-world STEP exporter compatibility, inch-unit fixtures, independent
  nested-part editing, other operating systems and multi-user/network deployment.
- Sandboxed native parsing or hard import/meshing timeouts. Use trusted files only.

## Reproduce

After following [INSTALL.md](INSTALL.md), run from the repository root:

```sh
python -m unittest discover -s tests -v
daedalus demo --output .runtime/verification-01
```

For optional development-only frontend tests, use Node 22.7 or newer:

```sh
npm ci --prefix tests/ui --ignore-scripts
npm test --prefix tests/ui
```

The manual browser checklist is in [tests/ui/README.txt](tests/ui/README.txt).
Run it on the actual Pi/client browser before calling the UI hardware-validated.
No CI service has been configured by this change.

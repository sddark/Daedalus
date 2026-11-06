# Daedalus: component-movement proof

This experimental starter implements a **real, headless Open CASCADE STEP round
trip**. It does not implement a web UI, Git checkpoint service or KiCad adapter.
Product and UI details remain undecided. Raspberry Pi compatibility has not been
verified, and the current dependency pin is not directly installable from a Linux
ARM64 wheel.

For installation on another computer, see [INSTALL.md](INSTALL.md).

## What works

1. Generate a STEP assembly containing two instances of the same 10 × 20 × 30 mm box.
2. Import the assembly with XCAF, preserving assembly references and names.
3. Move only the `Movable` occurrence 10 mm along its parent's X axis.
4. Export a new STEP and reimport it.
5. Check placements, bounds, dimensions, volume, instance sharing and original-file integrity.

`Fixed` remains at `[0, 0, 0]`; `Movable` changes from `[30, 0, 0]` to `[40, 0, 0]`.
The source STEP is never rewritten. Each part remains 10 × 20 × 30 mm with volume
6000 mm³. The exported STEP is a geometric round trip, not a byte-identical copy.

## Run from source

After installing the compatible dependencies described in [INSTALL.md](INSTALL.md),
run these commands from the directory containing `pyproject.toml`:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m engineering_workbench demo --output .runtime/demo-new
```

The demo intentionally refuses an existing output directory. Choose a new path for every run.
OCCT prints its own STEP writer diagnostics before the summary. A successful
demo reports `passed` with 13 of 13 checks passed.

Outputs:

- `original.step`: immutable generated input.
- `moved.step`: new exact-geometry STEP export.
- `placements.json`: readable parent-local placement snapshot with source hash,
  occurrence IDs and kernel version, ready for future Git checkpoint integration.
- `proof.json`: before/after/reimported snapshots and explicit verification checks.

## Architecture and scope

`src/engineering_workbench/cad.py` is the native-kernel adapter.
`src/engineering_workbench/__main__.py` runs the self-contained demonstration.
`tests/test_cad.py` and `tests/test_demo.py` contain 12 regression tests using
Python's standard `unittest`; pytest is not required.

The adapter accepts exactly one top-level XCAF assembly and edits only its
immediate component occurrences, including whole subassemblies. Nested parts are
not independently editable. A solitary solid or multiple free roots is rejected
instead of silently flattening the model. Shared definitions remain untouched.

Occurrence IDs derive from the source file hash and imported occurrence path.
They are stable for repeated imports of the same bytes with the tested importer,
not across a replacement or re-exported source. Names are display labels, not IDs.
The placement JSON records final absolute transforms; the proof's movement API
accepts a parent-local translation delta. Rotation editing and replaying placement
JSON are not implemented yet.

Internal and export units are millimetres. This initial proof uses millimetre
fixtures; inch-unit and complex real-world imports still need dedicated tests.
Process-global OCCT STEP settings mean kernel jobs must run serially. This is a
trusted local proof, not a hardened upload service or a thread-safe public API.

## Raspberry Pi deployment status

This proof is tested on **x86_64**, not a Raspberry Pi. Its OCP 7.8.1.1.post1 pin
matches the available environment but has no listed Linux ARM64 wheel. Current
OCP versions publish ARM64 wheels; choose and test one common supported version
before deployment. Do not promise that this exact dependency pin installs on a Pi.

The pinned OCP package also requires `vtk==9.3.1`. A deployment plan must validate
the complete dependency set, not just OCP. No Pi performance, memory limits or
installation procedure has been verified. See [installation limits](INSTALL.md#raspberry-pi-status).

## Verification and remaining work

The synthetic proof passes 12 regression tests and 13 demo checks on Linux
x86_64 with Python 3.12.14 and OCP 7.8.1.1.post1. This is not evidence of support
for every STEP exporter, arbitrary assemblies, other operating systems or a Pi.

Installation smoke testing and its limits are recorded in [INSTALL.md](INSTALL.md).
Real-world samples, additional unit/transform fixtures and ARM64 dependencies
remain to be validated. Further product implementation is paused pending scope.

## Third-party notices

OCP is the Python binding to Open CASCADE; the binding and bundled components have
their own licenses. Open CASCADE is LGPL 2.1 with the OCCT exception. Preserve
upstream notices and review distribution obligations before shipping an installer.
No license for this starter's original application code has been chosen yet.

- [Open CASCADE license](https://occt3d.com/dev/doc/overview/html/occt_public_license.html)
- [OCP package and current distributions](https://pypi.org/project/cadquery-ocp/)
- [Pinned OCP distributions](https://pypi.org/project/cadquery-ocp/7.8.1.1.post1/)

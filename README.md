# Daedalus

An experimental, local **STEP component viewer and mover** built on Open CASCADE.
Import an assembly, select a component, enter its X/Y/Z offset, compare its old
outline with its new position in one view, and export a new STEP file.

The viewer uses exact CAD geometry for editing and export. Tessellated meshes
are only for display. The imported source is preserved.

## Start here

Follow [INSTALL.md](INSTALL.md), then run:

```sh
daedalus serve
```

Open **http://127.0.0.1:8000** in a WebGL-capable browser. Choose a STEP file or
load the built-in two-component example. The browser assets are bundled locally;
there is no CDN or external service requirement at runtime.

## Focus View

- One 3D viewport with orbit, pan, zoom and fit controls.
- Select a component in the model or component list.
- Apply numeric X/Y/Z offsets in millimetres, relative to its imported placement
  and measured along the parent assembly's axes.
- Changed components retain an outline at their imported position. Their current
  geometry is shaded in the same viewport.
- Export the current assembly as a new STEP download.

This is a **single, temporary editing session**. Reloading the page reconnects to
the running session. Stopping the server discards it, so export changes first.
There is no project persistence, Git history UI, parametric feature editing,
rotation editing or KiCad workflow yet. The diff shows placement changes since
import, not arbitrary geometry changes between unrelated STEP files.

## Supported scope

The current adapter requires exactly one top-level STEP assembly and moves its
immediate component occurrences. A whole direct subassembly can move; its nested
parts cannot be selected and edited independently. A solitary solid or multiple
free roots is rejected rather than silently flattened. Repeated components share
one definition, so moving one occurrence does not alter its siblings.

Units are converted to millimetres. Box and curved-cylinder fixtures, including
rotated placements, are tested. Broader exporter compatibility and inch-unit
fixtures still need validation. Occurrence IDs are tied to the imported source
bytes and occurrence paths, not guaranteed stable across re-exported files.

The viewer accepts STEP files up to 20 MiB and at most 500 direct components.
Display output, counting repeated instances, is capped at 200,000 triangles and 200,000 edge segments, with
0.2 mm / 0.35 rad tessellation settings. These are payload limits, **not a native
memory guarantee** or a promise that a model will fit every Pi.

## Raspberry Pi status

The new `cadquery-ocp-novtk==8.0.1.1.0` dependency has verified Linux ARM64 wheels
and removes the previous VTK dependency. The complete Python dependency graph
has been resolved for ARM64 CPython 3.11 and 3.12. The application has been
installed and tested on **Linux x86_64**, not a physical Pi.

The intended Pi 4B target is **64-bit Raspberry Pi OS Bookworm with Python 3.11**.
32-bit Pi OS is unsupported. Native shared libraries are still required despite
the no-VTK package. See [the Pi installation steps](INSTALL.md#raspberry-pi-4b-target)
and [the dependency verification report](ARM64_VERIFICATION.md).

Actual Pi installation, browser graphics, peak RAM, import/export performance
and usable model size remain to be measured. Do not treat ARM64 wheel availability
as hardware validation.

## Verification

```sh
python -m unittest discover -s tests -v
daedalus demo --output .runtime/proof-01
```

The headless demo generates two instances of one box, moves only one, exports
and reimports STEP, and checks all 13 geometry/source-preservation assertions.
Choose a new output directory for each run. Tests cover CAD round trips, curved
meshes, rotated placements, HTTP input validation and local-server boundaries.
See [VERIFICATION.md](VERIFICATION.md) for the final verification record and limits.

## Local use and safety

The server binds to loopback only. It validates Host/Origin and request tokens,
serializes kernel operations, stages uploads in its own temporary directory,
and keeps the previous model when a replacement import fails. It does not accept
arbitrary filesystem paths or overwrite an existing input/export.

This is not a hardened public upload service. Use trusted STEP files. Native
OCCT import/meshing has no subprocess sandbox or hard timeout; a large or hostile
file can exhaust memory or crash the process. Do not expose the server directly
to the internet or use it as a multi-user CAD service. Remote access can use
[SSH forwarding](INSTALL.md#opening-the-viewer-from-another-computer).

## Dependencies and licenses

- Open CASCADE: LGPL 2.1 with the OCCT exception.
- OCP Python binding/proxy: upstream Apache-2.0 metadata; bundled native libraries
  have additional licenses. See [ARM64_VERIFICATION.md](ARM64_VERIFICATION.md).
- Three.js and OrbitControls: MIT; version and license are included with the
  bundled browser assets.

No license for Daedalus's original application code has been selected. Public
repository visibility alone does not provide an open-source reuse license.
Resolve all redistribution obligations before shipping an installer or bundled
native binaries.

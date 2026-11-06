# Install and run the Daedalus STEP proof

## Scope and prerequisites

This package is a command-line experiment, not an installable CAD desktop app or
web viewer. It creates a synthetic two-instance assembly, moves one occurrence,
exports STEP and verifies the result. It has no web deployment, login, server,
Git checkpoint UI or KiCad workflow.

Clone the public repository, then work in the directory containing `pyproject.toml`:

```sh
git clone https://github.com/sddark/Daedalus.git
cd Daedalus
```

You can also download and extract the source ZIP from the repository's Code menu.
Git is only needed for the clone route; the proof itself does not yet create Git
checkpoints. The experimental Python package and CLI still use the
`engineering-workbench` name; the project and repository are named Daedalus.

- **Tested environment:** Linux x86_64, CPython 3.12.14, glibc 2.41,
  `cadquery-ocp==7.8.1.1.post1`, `vtk==9.3.1`.
- **Declared Python range:** CPython 3.11–3.12. Only 3.12 has been tested here.
  Python 3.13 is excluded because the pinned VTK 9.3.1 dependency publishes no
  CPython 3.13 wheels, even though the pinned OCP release itself does.
- **Published pinned Linux wheels:** x86_64 with glibc 2.31 or newer. Wheel
  availability is a prerequisite, not a full application compatibility test.
- Python must include `venv` and pip. If creating a venv fails, install the
  appropriate Python venv/pip support through your operating system's normal
  package manager before continuing.
- The normal installation below needs access to PyPI for build dependencies,
  OCP and VTK. These include sizeable native binary packages.
- No C++ compiler, CMake, KiCad or separately installed system OCCT is needed for
  the wheel-based path. Do not mix an arbitrary system OCCT library into the
  pinned binding environment.

macOS and Windows have published upstream wheels but have not been tested for
this application. Raspberry Pi instructions are deliberately not offered below.

## Linux x86_64 installation

The following commands assume `python3.12` is available. Use a compatible Python
interpreter explicitly rather than relying on an unknown system default.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --only-binary=cadquery-ocp,vtk .
```

The binary-only restriction makes pip fail clearly when the required native
wheels are unavailable instead of attempting an unexpected source build.
Installation must finish successfully before running the following checks.

```sh
python -c "from importlib.metadata import version; from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox; print('OCP', version('cadquery-ocp')); print('Native kernel loaded:', not BRepPrimAPI_MakeBox(1, 2, 3).Shape().IsNull())"
engineering-workbench --help
python -m unittest discover -s tests -v
engineering-workbench demo --output .runtime/demo-installed-01
```

Expected results:

- Native kernel check prints `True`.
- Help lists the `demo` command. There is no viewer/serve command.
- The tests report **12 tests, OK**.
- The demo exits successfully and reports **13/13 checks passed**.

`python -m engineering_workbench demo --output ...` is equivalent to the
installed `engineering-workbench demo` command.

The demo refuses an existing output directory. For another run, choose a new
path such as `.runtime/demo-installed-02`; do not delete previous results just
to make the command succeed.

## Inspect the output

The chosen output directory contains:

- `original.step`: generated immutable input.
- `moved.step`: exported assembly after moving one instance.
- `placements.json`: source hash, occurrence IDs and final parent-local transforms.
- `proof.json`: explicit checks plus before/after/reimported geometry snapshots.

Expected positions in millimetres are `Fixed=[0,0,0]` and `Movable=[40,0,0]`.
Both boxes remain 10 × 20 × 30 mm, each with a volume of 6000 mm³. The proof checks
the original source hash and preserves a shared part definition. This does not
promise a lossless round trip for every STEP feature or file.

## Run without installation in an already provisioned environment

If the compatible OCP dependency is already installed, run from the source root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python -m engineering_workbench demo --output .runtime/demo-source-01
```

This bypasses package installation; it does not install or validate missing
dependencies. No pytest dependency is needed.

## What installation testing actually covers

The normal new-computer instructions above are the recommended package workflow,
but a clean internet-based install has **not** been performed in this restricted
execution environment.

Installation smoke testing passed in a separate local virtual environment with access
to the already-installed system packages, then installs the project with
`--no-index --no-deps --no-build-isolation`. This verifies package construction,
imports, the console entry point, regression tests and the demo against the
existing native dependencies. It does **not** independently validate downloading
dependencies, fresh native library resolution or another machine's setup.

Recorded result: package build/install, installed imports, console help, all
12 tests and all 13 demo checks passed on the tested Linux x86_64/Python 3.12
environment. No network package download or Pi execution was performed.

## Raspberry Pi status

**Do not use the current pin as a Pi installation recipe.** The pinned OCP
7.8.1.1.post1 release lists no Linux ARM64 wheel. Its VTK 9.3.1 dependency also
needs a compatible target build. Current OCP releases publish ARM64 wheels, but
changing the version requires API and round-trip regression testing and a review
of the complete dependency graph.

Before supporting a Pi 4B, select a compatible 64-bit OS/Python/dependency set,
run the same tests on real ARM64 hardware and measure import/export time and
peak RAM. No Pi performance claim, RAM requirement or maximum model size has
been established. The x86_64 result is not a Pi benchmark.

## Troubleshooting

- **`No matching distribution found`:** check interpreter version, CPU
  architecture and libc compatibility against the pinned wheels. Do not remove
  the binary-only restriction and expect a Pi source build to work automatically.
- **`ModuleNotFoundError: OCP`:** confirm the intended venv is active and the
  dependency installation succeeded. Installing this project with `--no-deps`
  is only appropriate when compatible dependencies are already present.
- **Native shared-library import error:** retain the exact missing-library error
  and resolve it for the target OS; the current smoke test does not establish a
  universal list of system packages.
- **`FileExistsError` for demo output:** choose a new directory.
- **Unsupported assembly error:** the API currently requires exactly one
  top-level assembly and edits only its immediate component occurrences.
- **Source changed since import:** import it as a new source. The application
  intentionally refuses to replay edits against changed input bytes.

## Upstream references

- [Pinned OCP files and platform tags](https://pypi.org/project/cadquery-ocp/7.8.1.1.post1/)
- [Pinned VTK files and Python tags](https://pypi.org/project/vtk/9.3.1/)
- [Current OCP releases](https://pypi.org/project/cadquery-ocp/)
- [Open CASCADE license](https://occt3d.com/dev/doc/overview/html/occt_public_license.html)

Preserve upstream notices before redistributing binaries. An application-code
license for this starter has not yet been selected.

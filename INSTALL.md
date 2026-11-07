# Install and run Daedalus

Daedalus is an experimental STEP assembly viewer and component mover. It runs a
local Python server and opens in your browser. It is not a full CAD modeller,
a public hosted service or a KiCad integration.

## Get the source

```sh
git clone https://github.com/sddark/Daedalus.git
cd Daedalus
```

You may instead download and extract the repository's source ZIP. Run the
commands below from the directory containing `pyproject.toml`.

The Python distribution remains named `engineering-workbench`; the primary CLI
is now `daedalus`. The older `engineering-workbench` command remains an alias.

## Supported dependency target

- 64-bit Linux x86_64 or ARM64/aarch64, with **glibc 2.28 or newer**.
- **CPython 3.11 or 3.12**. Only 3.12 has been executed in the current test host.
- `cadquery-ocp-novtk==8.0.1.1.0` and its pinned `cadquery-ocp-proxy` dependency.
- Git for local persistent checkpoints (`git --version`).
- A WebGL-capable browser for the UI. No Node.js, VTK, KiCad, compiler or
  standalone OCCT installation is required for this wheel-based path.

The app is tested on Linux x86_64 and macOS ARM64 with Python 3.12. Linux ARM64
wheels and their dependency resolution are verified, but actual Pi execution is
still pending. The checkpoint store uses POSIX file locking; Windows is unsupported. 32-bit Pi OS and Alpine/musl are unsupported.

**Create a fresh virtual environment.** Do not install the old `cadquery-ocp`
package alongside `cadquery-ocp-novtk`; they both provide the `OCP` namespace.
Do not silently reuse an environment from the earlier VTK-based proof.

## Raspberry Pi 4B target

Use **64-bit Raspberry Pi OS Bookworm / Debian 12 with Python 3.11**, or a
64-bit glibc-based OS with an explicitly supported Python 3.11/3.12 interpreter.
Do not assume a newer OS's default Python is in the supported range.

```sh
uname -m
python3 --version
getconf GNU_LIBC_VERSION
```

On the Pi, expect `aarch64`, Python 3.11.x/3.12.x and glibc 2.28 or newer.
For Bookworm, install these prerequisites if missing:

```sh
sudo apt update
sudo apt install git python3-venv python3-pip libgl1 libx11-6 libexpat1 zlib1g libstdc++6 libgcc-s1
```

The no-VTK OCP wheel still links graphics libraries such as libGL and libX11.
A desktop/display-server session is not needed just to serve the app, but the
shared libraries must load. See [ARM64_VERIFICATION.md](ARM64_VERIFICATION.md)
for the dependency audit and what has and has not been tested.

## Install in a fresh environment

The commands use `python3.11` for the Pi Bookworm target. On the tested x86_64
host, `python3.12` is used instead. Select the compatible interpreter explicitly.
If `.venv` already exists from an older installation, use a new name instead of
overwriting or deleting it.

```sh
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --only-binary=cadquery-ocp-novtk,cadquery-ocp-proxy .
python -m pip check
```

Initial installation needs PyPI network access and disk space for the native
packages. The binary-only restriction makes unsupported architectures fail
clearly instead of starting an untested source build. Once installed, the app
and browser assets run without a CDN or external web service.

## Verify the kernel and run the tests

```sh
python -c "from importlib.metadata import version; from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox; print('OCP', version('cadquery-ocp-novtk')); print('Native kernel loaded:', not BRepPrimAPI_MakeBox(1, 2, 3).Shape().IsNull())"
daedalus --help
python -m unittest discover -s tests -v
daedalus demo --output .runtime/proof-01
```

The native kernel check should print `True`, the tests should end with `OK`,
and the demo should report **13/13 checks passed**. OCCT also prints STEP writer
diagnostics. Choose a new output directory for each demo run, such as
`.runtime/proof-02`; existing directories are intentionally refused.

The demo produces `original.step`, `moved.step`, `placements.json` and `proof.json`.
Its original input stays unchanged. Both boxes retain their 6000 mm³ volumes;
only `Movable` changes position, from `[30,0,0]` to `[40,0,0]` mm.

## Open the local viewer

Git is required for the persistent project workspace. Imports and explicit
checkpoints are kept locally at `~/.local/share/daedalus/projects` unless you pass
`--workspace /absolute/path/to/projects`. Reopen saved projects in the UI after a
restart. Save a checkpoint before stopping: working movements are not autosaved.
The app does not publish, push, or configure a Git remote. Keep the workspace on
local storage and back up its complete project directories.


```sh
daedalus serve
```

Open **http://127.0.0.1:8000** in the browser on the same computer.
A different port can be selected with `daedalus serve --port 8001`.

1. Load the built-in example or choose a `.step`/`.stp` assembly, up to 20 MiB.
2. Select a direct component in the viewport or list.
3. Enter X/Y/Z offsets from its imported position, in millimetres along the
   parent assembly's axes, then apply the move.
4. Choose Diff to compare translucent imported and current surfaces. Choose Normal
   to see only the opaque current assembly.
5. Save a local checkpoint with a short message. Choose a checkpoint to compare
   its placement with current work, or restore it into working state.
6. Export a new STEP download when needed. Save a checkpoint before stopping.

Working movements are kept in memory while the server runs. Browser reloads
reconnect to that session. Saved projects and Git checkpoints survive a restart;
unsaved movements do not. The original input file is never edited.

Only one top-level assembly and its direct component occurrences are supported.
A solitary solid and independent selection of nested children are not supported.
The viewer rejects excessive display geometry instead of guaranteeing a maximum
RAM budget. Native import can still consume substantial memory.

## Opening the viewer from another computer

The server intentionally binds only to loopback. For a Pi running the server,
use SSH forwarding from your other computer, with your actual SSH account/host:

```sh
ssh -N -L 8000:127.0.0.1:8000 pi-user@pi-host
```

Then open **http://127.0.0.1:8000** on that computer. Keep the tunnel open. Use the
same port at both ends because the viewer validates the Host header; if port
8000 is occupied, use `--port 8001` on the Pi and 8001 at both tunnel endpoints.
SSH access must already be configured by you. These instructions do not enable
SSH, change firewall settings or expose the app publicly.

Direct phone/LAN access, HTTPS, authentication and multi-user deployment are not
implemented. Choose the access model before opening broader network access.

## Verification limits

A clean isolated Linux x86_64/Python 3.12 environment installed the new no-VTK
runtime from PyPI. ARM64 Python 3.11/3.12 wheels and their complete runtime graph
were downloaded and inspected. The original geometry proof passes with OCCT8.
Final package, API and browser results are recorded in [VERIFICATION.md](VERIFICATION.md).

No physical Pi installation, browser rendering, performance, peak-memory,
thermal or usable-model-size measurement has been performed. Validate those on
the actual Pi before relying on this for large or important work.

## Troubleshooting

- **No matching distribution:** check 64-bit architecture, Python 3.11/3.12 and
  glibc compatibility. Do not remove the binary-only restriction to force a build.
- **Missing libGL/libX11/native library:** install the OS prerequisites above and
  retain the exact error if another dependency remains missing.
- **OCP import/API error:** ensure a fresh environment contains only the pinned
  no-VTK OCP distribution and proxy, not a mixture of old/new bindings.
- **Blank viewport/WebGL error:** enable browser graphics support or try a
  supported browser. Actual Pi GPU behavior has not been validated here.
- **HTTP 403:** use the printed loopback URL. For SSH forwarding, keep port
  numbers identical. Reload for a fresh request token.
- **HTTP 409 after moving:** another page changed the model; refresh its state
  before applying another movement.
- **Import rejected:** retain your source, check that it is a supported assembly,
  or try a smaller model. Failed imports keep the previous session model.
- **Existing demo directory:** choose a new output path.

Use trusted STEP files only. This local native parser is not a hardened upload
service and has no process-isolation or hard native-operation timeout.

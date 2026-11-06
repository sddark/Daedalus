# ARM64 dependency verification

Verified on 2026-11-06. **Raspberry Pi hardware execution is still unverified.**
This report distinguishes downloadable ARM64 binaries from tests actually run.

## Selected runtime

- `cadquery-ocp-novtk==8.0.1.1.0` provides Open CASCADE 8.0.1 bindings without VTK.
- Its only Python runtime dependency is `cadquery-ocp-proxy==8.0.1.1.0`.
  The proxy is a universal `py3-none-any` wheel with no further dependencies.
- Daedalus keeps its declared CPython range at **3.11–3.12**. Upstream supports
  more versions, but this application has not validated them.
- Both native architectures use `manylinux_2_28`: **64-bit Linux with glibc 2.28
  or newer**. A Pi running 32-bit Raspberry Pi OS (`armv7l`) or Alpine/musl does
  not match these wheels. Use a fresh venv; do not mix `cadquery-ocp` and
  `cadquery-ocp-novtk`, because both supply the `OCP` import namespace.

## Wheel verification

Upstream PyPI metadata and downloaded wheel METADATA/WHEEL records confirm:

| CPython | Linux x86_64 | Linux ARM64/aarch64 |
| --- | --- | --- |
| 3.11 | Binary dependency download passed | Binary dependency download passed |
| 3.12 | Clean venv installation passed | Binary dependency download passed |

The ARM64 downloads resolved the entire Python runtime graph, not just the OCP
wheel. Their SHA-256 digests match the upstream PyPI release records:

- CPython 3.11 ARM64: `b29d3b11b2798cdcc7ab6cb5ddd0ecb741885f36626044c54814e4879b9e9729`
- CPython 3.12 ARM64: `dd5e239d0ba0863e3a2ca8de71c563d3375f1c075e493eb7650609de22d85240`

Neither ARM64 binary has been executed here. Python 3.11 has not been executed
here either. Cross-platform pip downloads establish availability and dependency
resolution, not runtime compatibility or performance.

## Native libraries

Inspection of all 77 ELF files in the CPython 3.11 ARM64 wheel found a highest
required glibc symbol version of 2.28, consistent with the wheel tag. Apart from
the bundled libraries, these native libraries remain operating-system supplied:

- `libGL.so.1`, `libX11.so.6`, `libexpat.so.1`, `libz.so.1`.
- `libstdc++.so.6`, `libgcc_s.so.1`, and the usual glibc/loader libraries
  (`libc`, `libm`, `libdl`, `libpthread`, `librt`, `ld-linux-aarch64`).

The no-VTK build is not completely independent of graphics libraries. It can
serve a browser without a desktop session, but those native shared libraries
must still load. On the x86_64 test host, `ldd` resolved all dependencies and
native geometry loading succeeded. This is not a fresh Raspberry Pi OS audit.

## Pi 4B target recipe, awaiting hardware validation

Use **64-bit Raspberry Pi OS Bookworm / Debian 12 with CPython 3.11**, or another
64-bit glibc-based OS with an explicit supported Python 3.11/3.12 interpreter.
Do not assume a newer OS's default `python3` is supported. Check first:

```sh
uname -m                         # must be aarch64 on the Pi
python3 --version                # must be 3.11.x or 3.12.x
getconf GNU_LIBC_VERSION         # glibc 2.28 or newer
```

For the Bookworm target, install ordinary OS prerequisites if missing, then
create a fresh environment in the source checkout:

```sh
sudo apt update
sudo apt install python3-venv python3-pip libgl1 libx11-6 libexpat1 zlib1g libstdc++6 libgcc-s1
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --only-binary=cadquery-ocp-novtk,cadquery-ocp-proxy .
python -m pip check
python -c "from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox; print(not BRepPrimAPI_MakeBox(1, 2, 3).Shape().IsNull())"
python -m unittest discover -s tests -v
daedalus demo --output .runtime/pi-proof-01
```

The native check should print `True`; all tests must pass and the demo must
report 13/13 checks. Choose a new demo output directory for each run. Do not
remove the binary-only restriction to force an untested source build.
See [INSTALL.md](INSTALL.md) for the viewer launch and local-access instructions.
No Pi timing, peak RAM, thermal, model-size, or GPU claims are established.

## Local verification commands

The isolated environment is `.runtime/ocp8-venv`; its configuration explicitly
has `include-system-site-packages = false`. It uses Linux x86_64, CPython
3.12.14 and glibc 2.41. No global Python packages or OS settings were changed.

```sh
python -m venv .runtime/ocp8-venv
.runtime/ocp8-venv/bin/python -m pip install --no-cache-dir --only-binary=:all: cadquery-ocp-novtk==8.0.1.1.0
.runtime/ocp8-venv/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .runtime/ocp8-venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .runtime/ocp8-venv/bin/python -m engineering_workbench demo --output .runtime/demo-ocp8-final
```

The original 12 regression tests and all 13 demo checks passed directly against
the migrated source. The source migration replaces `TDF_LabelSequence` with
`OCP.collections.Sequence_TDF_Label`, reads bounding-box corner coordinates
instead of the newly unregistered `Bnd_Box::Limits` return type, and detects the
new distribution name for version reporting. See the final project verification
summary for the separate viewer test results.

Reproduce a target-only dependency check from another architecture:

```sh
.runtime/ocp8-venv/bin/python -m pip download --only-binary=:all: \
  --platform manylinux_2_28_aarch64 --python-version 311 \
  --implementation cp --abi cp311 --dest .runtime/wheels-arm64-311 \
  cadquery-ocp-novtk==8.0.1.1.0
```

For CPython 3.12, change both `311`/`cp311` to `312`/`cp312` and select a new
output directory. For x86_64, change the platform suffix to `x86_64`.

## Licensing and redistribution limits

PyPI metadata declares Apache-2.0 for the OCP binding and proxy; the upstream
OCP repository carries the Apache-2.0 license. **Bundled Open CASCADE itself is
LGPL 2.1 with the OCCT exception**, and bundled third-party native libraries have
their own licenses. The inspected wheel archives did not contain filenames
identifying separate license/notice files. Package metadata is not a complete
binary-redistribution license inventory. Preserve applicable notices and resolve
source/redistribution obligations before shipping bundled binaries or an
installer. No application-code license has been chosen by this change.

## Sources

- [Pinned no-VTK package and files](https://pypi.org/project/cadquery-ocp-novtk/8.0.1.1.0/)
- [Machine-readable release metadata](https://pypi.org/pypi/cadquery-ocp-novtk/8.0.1.1.0/json)
- [Pinned proxy metadata](https://pypi.org/pypi/cadquery-ocp-proxy/8.0.1.1.0/json)
- [OCP source license](https://github.com/CadQuery/OCP/blob/master/LICENSE)
- [Open CASCADE 8.0.1 license](https://occt3d.com/dev/doc/overview/html/occt_public_license.html)

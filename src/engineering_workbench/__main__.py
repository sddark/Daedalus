"""Daedalus STEP component movement proof and local Focus View."""

from __future__ import annotations

import argparse
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import platform
import sys

from .cad import StepAssembly, create_two_instance_fixture, file_sha256


def kernel_binding() -> dict:
    for package in ("cadquery-ocp-novtk", "cadquery-ocp"):
        try:
            return {"package": package, "version": version(package)}
        except PackageNotFoundError:
            continue
    raise RuntimeError("No supported OCP distribution is installed")


def _close(actual: list[float], expected: list[float], tolerance: float = 1e-6) -> bool:
    return len(actual) == len(expected) and all(
        math.isclose(left, right, rel_tol=0.0, abs_tol=tolerance)
        for left, right in zip(actual, expected)
    )


def run_demo(output: Path) -> dict:
    """Produce source, edited STEP and machine-readable evidence in a new folder."""
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = output / "original.step"
    exported = output / "moved.step"
    create_two_instance_fixture(source)
    original_hash = file_sha256(source)
    assembly = StepAssembly.load(source)
    before = assembly.snapshot()
    movable = next(item for item in before["occurrences"] if item["name"] == "Movable")
    assembly.translate_occurrence(movable["id"], [10.0, 0.0, 0.0])
    after = assembly.snapshot()
    assembly.export_step(exported)
    reimported = StepAssembly.load(exported).snapshot()
    after_by_name = {item["name"]: item for item in after["occurrences"]}
    roundtrip_by_name = {item["name"]: item for item in reimported["occurrences"]}
    expected_positions = {"Fixed": [0.0, 0.0, 0.0], "Movable": [40.0, 0.0, 0.0]}
    checks = {
        "source_bytes_unchanged": file_sha256(source) == original_hash,
        "two_occurrences_preserved": len(reimported["occurrences"]) == 2,
        "one_shared_definition_preserved": len({item["definition_id"] for item in reimported["occurrences"]}) == 1,
        "one_root_preserved": reimported["root_count"] == 1,
        "occurrence_names_preserved": set(roundtrip_by_name) == set(expected_positions),
    }
    for name, expected_position in expected_positions.items():
        item = roundtrip_by_name.get(name)
        checks[f"{name.lower()}_placement_correct"] = item is not None and _close(item["translation_mm"], expected_position)
        checks[f"{name.lower()}_dimensions_preserved"] = item is not None and _close(item["dimensions_mm"], [10.0, 20.0, 30.0])
        checks[f"{name.lower()}_volume_preserved"] = item is not None and math.isclose(item["volume_mm3"], 6000.0, rel_tol=0.0, abs_tol=1e-6)
        checks[f"{name.lower()}_roundtrip_bounds_correct"] = item is not None and _close(item["bounds_mm"], after_by_name[name]["bounds_mm"])
    report = {
        "status": "passed" if all(checks.values()) else "failed",
        "scope": "synthetic two-instance STEP assembly, one parent-local translation",
        "kernel_binding": kernel_binding(),
        "platform": {"machine": platform.machine(), "python": platform.python_version()},
        "translation_mm": [10.0, 0.0, 0.0],
        "absolute_tolerance_mm": 1e-6,
        "source_file": source.name,
        "export_file": exported.name,
        "source_sha256": original_hash,
        "export_sha256": file_sha256(exported),
        "checks": checks,
        "before": before,
        "after": after,
        "reimported": reimported,
    }
    (output / "proof.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    (output / "placements.json").write_text(json.dumps({
        "schema_version": 1,
        "source_sha256": original_hash,
        "kernel_binding_version": kernel_binding()["version"],
        "units": "mm",
        "coordinate_convention": "right-handed; parent-local; column-vector 3x4 transforms",
        "occurrences": [{"id": item["id"], "path": item["path"], "name": item["name"], "matrix_3x4": item["matrix_3x4"]} for item in after["occurrences"]],
    }, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if report["status"] != "passed":
        raise RuntimeError(f"Proof checks failed; inspect {output / 'proof.json'}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Generate and verify a two-instance STEP movement")
    demo.add_argument("--output", type=Path, required=True, help="A new output directory; existing directories are refused")
    viewer = commands.add_parser("serve", help="Open a local STEP Focus View in your browser")
    viewer.add_argument("--port", type=int, default=8000, help="Loopback HTTP port (default: 8000)")
    arguments = parser.parse_args()
    try:
        if arguments.command == "serve":
            from .viewer import serve
            if not 0 <= arguments.port <= 65535:
                raise ValueError("Port must be between 0 and 65535")
            serve(arguments.port)
            return 0
        report = run_demo(arguments.output)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": report["status"], "checks_passed": sum(report["checks"].values()), "checks_total": len(report["checks"]), "output": str(arguments.output.resolve())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

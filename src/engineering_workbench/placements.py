"""Canonical, source-scoped placement state and pure semantic movement diffs."""
from __future__ import annotations

import hashlib
import json
import math
import re
from uuid import UUID

MAX_COMPONENTS = 500
MAX_OFFSET_MM = 1_000_000
SCHEMA_VERSION = 1


class StateError(ValueError):
    pass


def canonical(data: dict) -> bytes:
    return (json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                       allow_nan=False) + "\n").encode("utf-8")


def digest(data: dict) -> str:
    return hashlib.sha256(canonical(data)).hexdigest()


def identifier(value):
    if not isinstance(value, str):
        raise StateError("Expected a canonical UUID")
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError) as error:
        raise StateError("Expected a canonical UUID") from error
    return value


def _number(value, limit=None):
    if type(value) not in (int, float):
        raise StateError("Placement coordinates must be finite numbers")
    try:
        value = float(value)
    except (ValueError, OverflowError) as error:
        raise StateError("Placement coordinates must be finite numbers") from error
    if not math.isfinite(value) or (limit is not None and abs(value) > limit):
        raise StateError("Placement coordinate is nonfinite or outside the supported range")
    return 0.0 if value == 0 else value


def _vector(value):
    if not isinstance(value, list) or len(value) != 3:
        raise StateError("Offsets require three coordinates")
    return [_number(v, MAX_OFFSET_MM) for v in value]


def _matrix(value):
    if not isinstance(value, list) or len(value) != 3 or any(
        not isinstance(row, list) or len(row) != 4 for row in value
    ):
        raise StateError("Expected a 3x4 placement matrix")
    return [[_number(v) for v in row] for row in value]


def make_manifest(project_id: str, snapshot: dict, kernel_version: str) -> dict:
    """Accept a validated import snapshot, not a re-exported or edited baseline."""
    identifier(project_id)
    source_hash = snapshot.get("source_sha256")
    if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise StateError("Expected source SHA-256")
    if snapshot.get("units") != "mm":
        raise StateError("Only millimetre projects are supported")
    if not isinstance(kernel_version, str) or not 0 < len(kernel_version) <= 100:
        raise StateError("Expected a kernel version")
    occurrences = snapshot.get("occurrences")
    if not isinstance(occurrences, list) or not 0 < len(occurrences) <= MAX_COMPONENTS:
        raise StateError("Unsupported occurrence count")
    result = {}
    paths = set()
    for item in occurrences:
        if not isinstance(item, dict):
            raise StateError("Malformed occurrence")
        occurrence_id = identifier(item.get("id"))
        path = item.get("path")
        name = item.get("name", "")
        definition = item.get("definition_id")
        if (occurrence_id in result or not isinstance(path, str) or not 0 < len(path) <= 1024
            or path in paths or not isinstance(name, str) or len(name) > 4096
            or not isinstance(definition, str) or not 0 < len(definition) <= 1024):
            raise StateError("Duplicate or malformed occurrence identity")
        paths.add(path)
        result[occurrence_id] = {"path": path, "name": name, "definition_id": definition,
                                 "matrix_3x4": _matrix(item.get("matrix_3x4"))}
    return {"schema_version": SCHEMA_VERSION, "project_id": project_id,
            "source_sha256": source_hash, "units": "mm", "kernel_version": kernel_version,
            "coordinate_convention": "right-handed; parent-local; column-vector 3x4 transforms",
            "occurrences": result}


def validate_manifest(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "project_id", "source_sha256", "units", "kernel_version",
        "coordinate_convention", "occurrences"
    } or type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise StateError("Unsupported project schema")
    if not isinstance(value["occurrences"], dict):
        raise StateError("Malformed occurrence map")
    snapshot = {"source_sha256": value["source_sha256"], "units": value["units"],
                "occurrences": [{"id": key, **item} for key, item in value["occurrences"].items()
                                if isinstance(item, dict)]}
    if len(snapshot["occurrences"]) != len(value["occurrences"]):
        raise StateError("Malformed occurrence")
    result = make_manifest(value["project_id"], snapshot, value["kernel_version"])
    if result != value:
        raise StateError("Unsupported or malformed project manifest")
    return result


def make_state(manifest: dict, offsets: dict | None = None) -> dict:
    manifest = validate_manifest(manifest)
    if offsets is None:
        offsets = {key: [0.0, 0.0, 0.0] for key in manifest["occurrences"]}
    if not isinstance(offsets, dict) or set(offsets) != set(manifest["occurrences"]):
        raise StateError("Placement IDs must exactly match the imported occurrences")
    return {"schema_version": SCHEMA_VERSION, "project_id": manifest["project_id"],
            "source_sha256": manifest["source_sha256"], "units": "mm",
            "offsets": {key: _vector(value) for key, value in sorted(offsets.items())}}


def validate_state(manifest: dict, state: dict) -> dict:
    if not isinstance(state, dict) or set(state) != {
        "schema_version", "project_id", "source_sha256", "units", "offsets"
    } or type(state.get("schema_version")) is not int or state["schema_version"] != SCHEMA_VERSION:
        raise StateError("Unsupported placement schema")
    result = make_state(manifest, state["offsets"])
    if any(result[key] != state[key] for key in ("project_id", "source_sha256", "units")):
        raise StateError("Placement state belongs to another project/source lineage")
    return result


def validate_import(manifest: dict, snapshot: dict, kernel_version: str) -> None:
    """Fail closed on changed IDs, transforms, geometry lineage or kernel version."""
    candidate = make_manifest(manifest["project_id"], snapshot, kernel_version)
    if canonical(candidate) != canonical(validate_manifest(manifest)):
        raise StateError("Reimport differs from the saved baseline; migration is required")


def compare(manifest: dict, before: dict, after: dict, tolerance_mm: float = 1e-9) -> dict:
    tolerance = _number(tolerance_mm)
    if tolerance < 0:
        raise StateError("Comparison tolerance must be nonnegative")
    manifest = validate_manifest(manifest)
    before, after = validate_state(manifest, before), validate_state(manifest, after)
    changes = []
    for occurrence_id, left in before["offsets"].items():
        right = after["offsets"][occurrence_id]
        delta = [b - a for a, b in zip(left, right)]
        if not any(abs(value) > tolerance for value in delta):
            continue
        occurrence = manifest["occurrences"][occurrence_id]
        matrices = []
        for offset in (left, right):
            matrix = [row[:] for row in occurrence["matrix_3x4"]]
            for axis in range(3):
                matrix[axis][3] += offset[axis]
            matrices.append(matrix)
        changes.append({"id": occurrence_id, "name": occurrence["name"], "path": occurrence["path"],
                        "before_offset_mm": left, "after_offset_mm": right,
                        "delta_mm": delta, "distance_mm": math.hypot(*delta),
                        "before_matrix_3x4": matrices[0], "after_matrix_3x4": matrices[1]})
    return {"kind": "same-source-placement", "project_id": manifest["project_id"],
            "source_sha256": manifest["source_sha256"], "units": "mm",
            "coordinate_frame": "parent", "tolerance_mm": tolerance,
            "before_digest": digest(before), "after_digest": digest(after),
            "changes": changes, "unchanged_count": len(before["offsets"]) - len(changes)}

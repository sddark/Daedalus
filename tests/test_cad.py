"""Independent end-to-end tests for the two-occurrence STEP move proof.

Run with ``PYTHONPATH=src python -m unittest discover -s tests -v``.
All CAD inputs and outputs are isolated in temporary directories.
"""

import hashlib
from pathlib import Path
import tempfile
import unittest

from engineering_workbench.cad import StepAssembly, create_two_instance_fixture


class StepOccurrenceMoveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="engineering-cad-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source.step"
        create_two_instance_fixture(self.source)
        self.source_bytes = self.source.read_bytes()
        self.source_sha256 = hashlib.sha256(self.source_bytes).hexdigest()
        self.assembly = StepAssembly.load(self.source)

    def by_name(self, snapshot):
        occurrences = snapshot["occurrences"]
        self.assertEqual(len(occurrences), 2)
        result = {item["name"]: item for item in occurrences}
        self.assertEqual(set(result), {"Fixed", "Movable"})
        self.assertEqual(len({item["id"] for item in occurrences}), 2)
        self.assertEqual(len({item["definition_id"] for item in occurrences}), 1)
        return result

    def assert_vector(self, actual, expected):
        self.assertEqual(len(actual), len(expected))
        for got, wanted in zip(actual, expected):
            self.assertAlmostEqual(got, wanted, places=6)

    def assert_box(self, occurrence, x):
        self.assert_vector(occurrence["translation_mm"], [x, 0, 0])
        self.assert_vector(occurrence["bounds_mm"], [x, 0, 0, x + 10, 20, 30])
        self.assert_vector(occurrence["dimensions_mm"], [10, 20, 30])
        self.assertAlmostEqual(occurrence["volume_mm3"], 6000, places=6)
        bounds = occurrence["bounds_mm"]
        centroid = [(bounds[index] + bounds[index + 3]) / 2 for index in range(3)]
        self.assert_vector(centroid, [x + 5, 10, 15])

    def assert_source_unchanged(self):
        self.assertEqual(self.source.read_bytes(), self.source_bytes)
        self.assertEqual(self.assembly.snapshot()["source_sha256"], self.source_sha256)

    def test_fixture_import_geometry_and_stable_ids(self):
        snapshot = self.assembly.snapshot()
        self.assertEqual(snapshot["source_sha256"], self.source_sha256)
        self.assertEqual(snapshot["root_count"], 1)
        occurrences = self.by_name(snapshot)
        self.assert_box(occurrences["Fixed"], 0)
        self.assert_box(occurrences["Movable"], 30)
        reloaded = self.by_name(StepAssembly.load(self.source).snapshot())
        for name in occurrences:
            self.assertEqual(occurrences[name]["id"], reloaded[name]["id"])
            self.assertEqual(occurrences[name]["definition_id"], reloaded[name]["definition_id"])
        self.assert_source_unchanged()

    def test_move_only_selected_occurrence_and_roundtrip(self):
        before = self.assembly.snapshot()
        original = self.by_name(before)
        self.assembly.translate_occurrence(original["Movable"]["id"], [10, 0, 0])
        after = self.assembly.snapshot()
        moved = self.by_name(after)
        self.assertEqual(after["root_count"], before["root_count"])
        self.assertEqual(moved["Fixed"], original["Fixed"])
        self.assertEqual(moved["Movable"]["id"], original["Movable"]["id"])
        self.assertEqual(moved["Movable"]["definition_id"], original["Movable"]["definition_id"])
        self.assert_box(moved["Movable"], 40)
        self.assert_source_unchanged()

        exported = self.directory / "moved.step"
        self.assembly.export_step(exported)
        self.assertTrue(exported.is_file())
        self.assertGreater(exported.stat().st_size, 0)
        roundtrip = StepAssembly.load(exported).snapshot()
        self.assertEqual(roundtrip["root_count"], 1)
        result = self.by_name(roundtrip)
        self.assert_box(result["Fixed"], 0)
        self.assert_box(result["Movable"], 40)
        self.assertEqual(roundtrip["source_sha256"], hashlib.sha256(exported.read_bytes()).hexdigest())
        self.assert_source_unchanged()
        # Reopening the original also proves that the move was not persisted there.
        self.assert_box(self.by_name(StepAssembly.load(self.source).snapshot())["Movable"], 30)

    def test_move_is_delta_not_absolute_position(self):
        movable_id = self.by_name(self.assembly.snapshot())["Movable"]["id"]
        self.assembly.translate_occurrence(movable_id, [10, 0, 0])
        self.assembly.translate_occurrence(movable_id, [-10, 0, 0])
        occurrences = self.by_name(self.assembly.snapshot())
        self.assert_box(occurrences["Fixed"], 0)
        self.assert_box(occurrences["Movable"], 30)
        self.assert_source_unchanged()

    def test_invalid_translations_are_atomic(self):
        movable_id = self.by_name(self.assembly.snapshot())["Movable"]["id"]
        invalid = [[], [1], [1, 2], [1, 2, 3, 4], None, ["bad", 0, 0],
                   [True, 0, 0], "123", b"123"]
        for bad_number in (float("nan"), float("inf"), float("-inf")):
            for axis in range(3):
                delta = [0, 0, 0]
                delta[axis] = bad_number
                invalid.append(delta)
        for delta in invalid:
            with self.subTest(delta=delta):
                before = self.assembly.snapshot()
                with self.assertRaises((ValueError, TypeError)):
                    self.assembly.translate_occurrence(movable_id, delta)
                self.assertEqual(self.assembly.snapshot(), before)
                self.assert_source_unchanged()

    def test_export_cannot_overwrite_source_through_links(self):
        before = self.assembly.snapshot()
        symlink = self.directory / "source-symlink.step"
        hardlink = self.directory / "source-hardlink.step"
        symlink.symlink_to(self.source)
        hardlink.hardlink_to(self.source)
        for target in (symlink, hardlink):
            with self.subTest(target=target.name):
                self.assertTrue(target.samefile(self.source))
                with self.assertRaises((ValueError, PermissionError, FileExistsError)):
                    self.assembly.export_step(target)
                self.assertTrue(target.samefile(self.source))
                self.assertEqual(self.assembly.snapshot(), before)
                self.assert_source_unchanged()

    def test_changed_source_rejects_read_move_and_export(self):
        before = self.assembly.snapshot()
        movable_id = self.by_name(before)["Movable"]["id"]
        target = self.directory / "must-not-be-created.step"
        modified = self.source_bytes + b"\n/* source changed externally */\n"
        operations = (
            ("snapshot", self.assembly.snapshot),
            ("move", lambda: self.assembly.translate_occurrence(movable_id, [10, 0, 0])),
            ("export", lambda: self.assembly.export_step(target)),
        )
        for name, operation in operations:
            with self.subTest(operation=name):
                self.source.write_bytes(modified)
                try:
                    with self.assertRaisesRegex(RuntimeError, "Source.*changed"):
                        operation()
                    self.assertEqual(self.source.read_bytes(), modified)
                    self.assertFalse(target.exists())
                finally:
                    self.source.write_bytes(self.source_bytes)
                # Restoring the external input lets us inspect that the failed
                # operation did not alter the in-memory occurrence placements.
                self.assertEqual(self.assembly.snapshot(), before)

    def test_existing_export_is_not_overwritten(self):
        target = self.directory / "existing.step"
        existing = b"pre-existing output must survive"
        target.write_bytes(existing)
        before = self.assembly.snapshot()
        with self.assertRaises(FileExistsError):
            self.assembly.export_step(target)
        self.assertEqual(target.read_bytes(), existing)
        self.assertEqual(self.assembly.snapshot(), before)
        self.assert_source_unchanged()

    def test_unknown_occurrence_is_rejected_atomically(self):
        before = self.assembly.snapshot()
        unknown = "missing-occurrence-not-in-fixture"
        self.assertNotIn(unknown, {item["id"] for item in before["occurrences"]})
        with self.assertRaises((KeyError, ValueError)):
            self.assembly.translate_occurrence(unknown, [10, 0, 0])
        self.assertEqual(self.assembly.snapshot(), before)
        self.assert_source_unchanged()

    def test_export_cannot_overwrite_source(self):
        movable_id = self.by_name(self.assembly.snapshot())["Movable"]["id"]
        self.assembly.translate_occurrence(movable_id, [10, 0, 0])
        before = self.assembly.snapshot()
        # The alias must be normalized, not compared as a raw path string.
        alias_directory = self.directory / "alias"
        alias_directory.mkdir()
        for target in (self.source, alias_directory / ".." / self.source.name):
            with self.subTest(target=str(target)):
                with self.assertRaises((ValueError, PermissionError, FileExistsError)):
                    self.assembly.export_step(target)
                self.assertEqual(self.assembly.snapshot(), before)
                self.assert_source_unchanged()


if __name__ == "__main__":
    unittest.main()

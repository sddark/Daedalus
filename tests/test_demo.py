"""End-to-end demonstration and atomic publication regressions."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engineering_workbench.__main__ import run_demo
from engineering_workbench.cad import StepAssembly, create_two_instance_fixture


class DemoTests(unittest.TestCase):
    def test_demo_report_and_placement_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="engineering-demo-test-") as temporary:
            output = Path(temporary) / "proof"
            report = run_demo(output)
            self.assertEqual(report["status"], "passed")
            self.assertEqual(len(report["checks"]), 13)
            self.assertTrue(all(report["checks"].values()))
            self.assertEqual(json.loads((output / "proof.json").read_text()), report)
            placements = json.loads((output / "placements.json").read_text())
            self.assertEqual(placements["source_sha256"], report["source_sha256"])
            self.assertEqual(placements["units"], "mm")
            self.assertEqual(len(placements["occurrences"]), 2)
            matrices = {item["name"]: item["matrix_3x4"] for item in placements["occurrences"]}
            self.assertEqual(matrices["Fixed"][0][3], 0.0)
            self.assertEqual(matrices["Movable"][0][3], 40.0)

    def test_demo_refuses_existing_directory_without_changing_contents(self):
        with tempfile.TemporaryDirectory(prefix="engineering-demo-test-") as temporary:
            output = Path(temporary)
            sentinel = output / "keep.txt"
            sentinel.write_text("preserve me")
            with self.assertRaises(FileExistsError):
                run_demo(output)
            self.assertEqual(sentinel.read_text(), "preserve me")
            self.assertEqual(list(output.iterdir()), [sentinel])

    def test_export_does_not_overwrite_concurrent_destination(self):
        with tempfile.TemporaryDirectory(prefix="engineering-race-test-") as temporary:
            directory = Path(temporary)
            source = directory / "original.step"
            destination = directory / "moved.step"
            create_two_instance_fixture(source)
            assembly = StepAssembly.load(source)
            real_link = os.link

            def competing_writer(temporary_path, final_path):
                Path(final_path).write_bytes(b"another writer's file")
                return real_link(temporary_path, final_path)

            with patch("engineering_workbench.cad.os.link", side_effect=competing_writer):
                with self.assertRaises(FileExistsError):
                    assembly.export_step(destination)
            self.assertEqual(destination.read_bytes(), b"another writer's file")
            self.assertEqual(list(directory.glob(".step-export-*")), [])


if __name__ == "__main__":
    unittest.main()

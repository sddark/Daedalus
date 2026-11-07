"""Actual native surface tessellation and placement-coordinate regressions."""

import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from OCP.BRep import BRep_Builder
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeSphere
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Compound
from OCP.XCAFDoc import XCAFDoc_DocumentTool

from engineering_workbench.cad import StepAssembly, _document, _set_name, _write_document, create_two_instance_fixture, file_sha256
from engineering_workbench.mesh import display_meshes
from engineering_workbench.viewer import ViewerSession


class CurvedMeshTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "cylinders.step"
        document = _document()
        shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
        compound = TopoDS_Compound()
        BRep_Builder().MakeCompound(compound)
        root = shapes.AddShape(compound, True)
        definition = shapes.AddShape(BRepPrimAPI_MakeCylinder(2, 10).Shape(), False)
        for name, x in (("First", 0), ("Second", 20)):
            transform = gp_Trsf()
            transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)), math.pi / 4)
            transform.SetTranslationPart(gp_Vec(x, 0, 0))
            _set_name(shapes.AddComponent(root, definition, TopLoc_Location(transform)), name)
        shapes.UpdateAssemblies()
        _write_document(document, self.source)

    def test_curved_shared_geometry_is_meshed_in_definition_frame(self):
        source_hash = file_sha256(self.source)
        assembly = StepAssembly.load(self.source)
        before = assembly.snapshot()
        meshes = display_meshes(assembly)
        self.assertEqual(len(meshes), 1)
        mesh = next(iter(meshes.values()))
        self.assertGreater(len(mesh["positions"]), 108)
        self.assertEqual(len(mesh["positions"]) % 9, 0)
        self.assertGreater(len(mesh["edges"]), 24)
        for axis, (low, high) in enumerate(((-2, 2), (-2, 2), (0, 10))):
            values = mesh["positions"][axis::3]
            self.assertGreaterEqual(min(values), low - 1e-6)
            self.assertLessEqual(max(values), high + 1e-6)
        self.assertEqual(file_sha256(self.source), source_hash)
        after = assembly.snapshot()
        for original, meshed in zip(before["occurrences"], after["occurrences"]):
            self.assertEqual(original["matrix_3x4"], meshed["matrix_3x4"])
            self.assertAlmostEqual(original["volume_mm3"], meshed["volume_mm3"], places=6)

    def test_numeric_offsets_preserve_rotation_and_export(self):
        with __import__("contextlib").closing(ViewerSession()) as session:
            before = session.import_step(self.source.read_bytes(), self.source.name)
            first, second = before["model"]["occurrences"]
            moved = session.move({"revision": before["revision"], "occurrence_id": first["id"], "offset_mm": [4, -3, 2]})
            new_first, new_second = moved["model"]["occurrences"]
            self.assertEqual(second["matrix_3x4"], new_second["matrix_3x4"])
            self.assertEqual(first["matrix_3x4"], new_first["baseline_matrix_3x4"])
            for axis, offset in enumerate((4, -3, 2)):
                self.assertEqual(first["matrix_3x4"][axis][:3], new_first["matrix_3x4"][axis][:3])
                self.assertAlmostEqual(first["matrix_3x4"][axis][3] + offset, new_first["matrix_3x4"][axis][3])
            output = Path(self.temporary.name) / "moved.step"
            output.write_bytes(session.export_step())
            exported = StepAssembly.load(output).snapshot()
            by_name = {item["name"]: item for item in exported["occurrences"]}
            self.assertAlmostEqual(by_name[first["name"]]["volume_mm3"], math.pi * 4 * 10, places=5)
            for axis in range(3):
                self.assertAlmostEqual(by_name[first["name"]]["matrix_3x4"][axis][3], new_first["matrix_3x4"][axis][3], places=6)

    def test_display_budget_counts_every_shared_occurrence(self):
        boxes = Path(self.temporary.name) / "boxes.step"
        create_two_instance_fixture(boxes)
        source_hash = file_sha256(boxes)
        for name, limit in (("MAX_TRIANGLES", 20), ("MAX_EDGE_SEGMENTS", 30)):
            with self.subTest(name=name), patch("engineering_workbench.mesh." + name, limit):
                with self.assertRaisesRegex(ValueError, "Instanced display"):
                    display_meshes(StepAssembly.load(boxes))
        self.assertEqual(file_sha256(boxes), source_hash)


class SmoothNormalsTests(unittest.TestCase):
    def test_sphere_normals_are_radial_and_boxes_keep_sharp_corners(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "sphere.step"
            document = _document()
            shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
            compound = TopoDS_Compound()
            BRep_Builder().MakeCompound(compound)
            root = shapes.AddShape(compound, True)
            definition = shapes.AddShape(BRepPrimAPI_MakeSphere(10).Shape(), False)
            shapes.AddComponent(root, definition, TopLoc_Location())
            shapes.UpdateAssemblies()
            _write_document(document, source)
            mesh = next(iter(display_meshes(StepAssembly.load(source)).values()))
            self.assertEqual(mesh['edges'], [], 'A sphere has no physical boundary edges')
            self.assertEqual(len(mesh['normals']), len(mesh['positions']))
            for i in range(0, len(mesh['positions']), 3):
                point = mesh['positions'][i:i+3]
                normal = mesh['normals'][i:i+3]
                self.assertAlmostEqual(math.hypot(*normal), 1)
                self.assertGreater(sum(p*n for p,n in zip(point, normal))/10, .98)
            boxes = Path(directory) / "boxes.step"
            create_two_instance_fixture(boxes)
            mesh = next(iter(display_meshes(StepAssembly.load(boxes)).values()))
            self.assertGreater(len(mesh['edges']), 0, 'Box edges must remain visible')
            for i in range(0, len(mesh['normals']), 3):
                self.assertEqual(sorted(abs(v) for v in mesh['normals'][i:i+3]), [0,0,1])


    def test_cylinder_keeps_rims_but_removes_periodic_seam(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cylinder.step'
            doc = _document()
            shapes = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
            compound = TopoDS_Compound()
            BRep_Builder().MakeCompound(compound)
            root = shapes.AddShape(compound, True)
            definition = shapes.AddShape(BRepPrimAPI_MakeCylinder(2, 10).Shape(), False)
            shapes.AddComponent(root, definition, TopLoc_Location())
            shapes.UpdateAssemblies()
            _write_document(doc, path)
            mesh = next(iter(display_meshes(StepAssembly.load(path)).values()))
            self.assertGreater(len(mesh['edges']), 0)
            for i in range(0, len(mesh['edges']), 6):
                segment = mesh['edges'][i:i+6]
                self.assertAlmostEqual(segment[2], segment[5])
                self.assertTrue(abs(segment[2]) < 1e-6 or abs(segment[2]-10) < 1e-6)

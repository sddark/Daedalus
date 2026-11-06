"""Black-box HTTP regressions for the local STEP viewer.

Uses only unittest/urllib and the existing OCCT dependency. All files are temporary
and the server binds an ephemeral loopback port. Run with the other regressions:
``PYTHONPATH=src python -m unittest discover -s tests -v``.
"""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from html.parser import HTMLParser
from http.client import HTTPConnection
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, ProxyHandler

from engineering_workbench.cad import StepAssembly, create_two_instance_fixture
from engineering_workbench.viewer import make_server


class _LocalAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []
        self.external = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        value = attributes.get("src") if tag == "script" else attributes.get("href") if tag == "link" else None
        if value and urlsplit(value).scheme != "data":
            if urlsplit(value).netloc or urlsplit(value).scheme:
                self.external.append(value)
            else:
                self.paths.append("/" + value.lstrip("/"))


def _create_cylinder_fixture(path, *, rotated=False):
    """Use a curved shared part to distinguish real tessellation from box proxies."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS_Compound
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from engineering_workbench.cad import _document, _set_name, _write_document

    document = _document()
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    compound = TopoDS_Compound()
    BRep_Builder().MakeCompound(compound)
    root = shape_tool.AddShape(compound, True)
    _set_name(root, "TwoCylinderAssembly")
    definition = shape_tool.AddShape(BRepPrimAPI_MakeCylinder(5.0, 20.0).Shape(), False)
    _set_name(definition, "SharedCylinder")
    for name, x in (("Fixed", 0), ("Movable", 30)):
        transform = gp_Trsf()
        if rotated and name == "Movable":
            transform.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 1, 0)), math.pi / 2)
        transform.SetTranslationPart(gp_Vec(x, 0, 0))
        occurrence = shape_tool.AddComponent(root, definition, TopLoc_Location(transform))
        _set_name(occurrence, name)
    shape_tool.UpdateAssemblies()
    _write_document(document, path)


class ViewerHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(prefix="viewer-fixtures-") as directory:
            directory = Path(directory)
            box = directory / "boxes.step"
            cylinder = directory / "cylinders.step"
            rotated = directory / "rotated.step"
            create_two_instance_fixture(box)
            _create_cylinder_fixture(cylinder)
            _create_cylinder_fixture(rotated, rotated=True)
            cls.box_bytes = box.read_bytes()
            cls.cylinder_bytes = cylinder.read_bytes()
            cls.rotated_bytes = rotated.read_bytes()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="viewer-http-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.source = self.directory / "source.step"
        self.source.write_bytes(self.box_bytes)
        self.server = make_server(host="127.0.0.1", port=0)
        self.addCleanup(self.close_server)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 10)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%s" % self.server.server_address[1]
        # Explicitly avoid an ambient HTTP proxy for these loopback-only tests.
        self.opener = build_opener(ProxyHandler({}))
        self.token = None
        initial = self.state()
        self.token = initial["csrf_token"]

    def close_server(self):
        staging = self.server.session.directory
        self.server.server_close()
        self.assertFalse(staging.exists(), "Closing the server must remove session staging")

    def request(self, path, *, method="GET", data=None, headers=None, csrf=True, with_revision=True):
        if path == "/api/export" and with_revision:
            path += "?revision=" + str(self.state()["revision"])
        headers = dict(headers or {})
        if method == "POST" and csrf and self.token is not None:
            headers.setdefault("X-CSRF-Token", self.token)
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            response = self.opener.open(request, timeout=30)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    def json_response(self, response, expected=200):
        status, headers, body = response
        self.assertEqual(status, expected, body[:500])
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        result = json.loads(body)
        if expected >= 400:
            self.assertIsInstance(result.get("error"), str)
            self.assertTrue(result["error"])
        return result

    def state(self):
        return self.json_response(self.request("/api/state"))

    def post(self, path, payload, *, with_revision=True, **kwargs):
        if path == "/api/demo" and isinstance(payload, dict) and with_revision and "revision" not in payload:
            payload = dict(payload, revision=self.state()["revision"])
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Content-Type", "application/json")
        return self.request(path, method="POST", data=json.dumps(payload).encode(), headers=headers, **kwargs)

    def import_step(self, body=None, filename="source #1.step", *, with_revision=True, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if with_revision and "X-Revision" not in headers:
            headers["X-Revision"] = str(self.state()["revision"])
        headers.setdefault("Content-Type", "application/octet-stream")
        headers.setdefault("X-Filename", quote(filename, safe=""))
        return self.request("/api/import", method="POST", data=self.box_bytes if body is None else body,
                            headers=headers, **kwargs)

    def import_boxes(self):
        return self.json_response(self.import_step())

    def by_name(self, state):
        occurrences = state["model"]["occurrences"]
        self.assertEqual(len(occurrences), 2)
        self.assertEqual(len({item["id"] for item in occurrences}), 2)
        self.assertEqual(len({item["definition_id"] for item in occurrences}), 1)
        result = {item["name"]: item for item in occurrences}
        self.assertEqual(set(result), {"Fixed", "Movable"})
        return result

    def assert_vector(self, actual, expected, places=6):
        self.assertEqual(len(actual), len(expected))
        for got, wanted in zip(actual, expected):
            self.assertAlmostEqual(got, wanted, places=places)

    def assert_matrix(self, actual, expected):
        self.assertEqual(len(actual), 3)
        for actual_row, expected_row in zip(actual, expected):
            self.assert_vector(actual_row, expected_row)

    def assert_finite_flat(self, values, divisor):
        self.assertIsInstance(values, list)
        self.assertGreater(len(values), 0)
        self.assertEqual(len(values) % divisor, 0)
        self.assertTrue(all(isinstance(value, (float, int)) and not isinstance(value, bool)
                            and math.isfinite(value) for value in values))

    def move(self, state, offset, name="Movable"):
        occurrence = self.by_name(state)[name]
        return self.post("/api/move", {"occurrence_id": occurrence["id"],
                                      "offset_mm": offset, "revision": state["revision"]})

    def test_empty_session_and_missing_model_errors(self):
        state = self.state()
        self.assertIsNone(state["model"])
        self.assertIsInstance(state["csrf_token"], str)
        self.assertGreaterEqual(len(state["csrf_token"]), 24)
        self.assertIsInstance(state["revision"], int)
        self.assertEqual(self.state(), state)
        self.json_response(self.request("/api/export"), 409)
        self.json_response(self.post("/api/move", {"occurrence_id": "missing", "offset_mm": [0, 0, 0],
                                                 "revision": state["revision"]}), 409)
        self.assertEqual(self.state(), state)

    def test_import_returns_shared_definition_meshes_and_source_hash(self):
        before = self.state()
        state = self.import_boxes()
        self.assertGreater(state["revision"], before["revision"])
        self.assertEqual(state["csrf_token"], self.token)
        model = state["model"]
        self.assertEqual(model["name"], "source #1.step")
        self.assertEqual(model["units"], "mm")
        self.assertEqual(model["source_sha256"], hashlib.sha256(self.box_bytes).hexdigest())
        occurrences = self.by_name(state)
        definition = occurrences["Fixed"]["definition_id"]
        self.assertEqual(set(model["meshes"]), {definition})
        mesh = model["meshes"][definition]
        self.assert_finite_flat(mesh["positions"], 9)
        self.assert_finite_flat(mesh["edges"], 6)
        vertices = list(zip(*[iter(mesh["positions"])] * 3))
        self.assert_vector([min(point[axis] for point in vertices) for axis in range(3)], [0, 0, 0])
        self.assert_vector([max(point[axis] for point in vertices) for axis in range(3)], [10, 20, 30])
        for name, x in (("Fixed", 0), ("Movable", 30)):
            occurrence = occurrences[name]
            matrix = [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 0]]
            self.assert_matrix(occurrence["matrix_3x4"], matrix)
            self.assert_matrix(occurrence["baseline_matrix_3x4"], matrix)
            self.assert_vector(occurrence["offset_mm"], [0, 0, 0])
            self.assert_vector(occurrence["bounds_mm"], [x, 0, 0, x + 10, 20, 30])
        self.assertEqual(self.source.read_bytes(), self.box_bytes)
        self.assertEqual(self.state(), state)

    def test_curved_part_is_tessellated_not_replaced_by_bounds(self):
        state = self.json_response(self.import_step(self.cylinder_bytes, "cylinders.step"))
        occurrence = self.by_name(state)["Fixed"]
        mesh = state["model"]["meshes"][occurrence["definition_id"]]
        self.assert_finite_flat(mesh["positions"], 9)
        self.assert_finite_flat(mesh["edges"], 6)
        vertices = list(zip(*[iter(mesh["positions"])] * 3))
        self.assertGreater(len(vertices), 36)
        self.assertTrue(all(x * x + y * y <= 25.0001 and -0.00001 <= z <= 20.00001
                            for x, y, z in vertices))
        self.assertTrue(any(0.1 < abs(x) < 4.9 and 0.1 < abs(y) < 4.9 for x, y, _ in vertices))
        self.assert_vector(occurrence["bounds_mm"], [-5, -5, 0, 5, 5, 20], places=5)

    def test_moves_are_absolute_isolated_and_keep_original_outline(self):
        initial = self.import_boxes()
        before = self.by_name(initial)
        moved = self.json_response(self.move(initial, [10, -2, 3]))
        after = self.by_name(moved)
        self.assertEqual(after["Fixed"], before["Fixed"])
        self.assertEqual(after["Movable"]["baseline_matrix_3x4"], before["Movable"]["matrix_3x4"])
        self.assert_matrix(after["Movable"]["matrix_3x4"], [[1, 0, 0, 40], [0, 1, 0, -2], [0, 0, 1, 3]])
        self.assert_vector(after["Movable"]["bounds_mm"], [40, -2, 3, 50, 18, 33])
        self.assert_vector(after["Movable"]["offset_mm"], [10, -2, 3])
        self.assertEqual(moved["model"]["meshes"], initial["model"]["meshes"])
        # A fresh revision with the same absolute offset must not apply it twice.
        repeated = self.json_response(self.move(moved, [10, -2, 3]))
        self.assertEqual(self.by_name(repeated), after)
        restored = self.json_response(self.move(repeated, [0, 0, 0]))
        self.assertEqual(self.by_name(restored), before)
        self.assertEqual(self.source.read_bytes(), self.box_bytes)

    def test_rotated_baseline_is_preserved_while_translation_changes(self):
        initial = self.json_response(self.import_step(self.rotated_bytes, "rotated.step"))
        before = self.by_name(initial)
        baseline = before["Movable"]["matrix_3x4"]
        self.assertAlmostEqual(baseline[0][2], 1, places=6)
        moved = self.json_response(self.move(initial, [4, 5, 6]))
        after = self.by_name(moved)
        self.assertEqual(after["Fixed"], before["Fixed"])
        self.assertEqual(after["Movable"]["baseline_matrix_3x4"], baseline)
        expected = [row[:] for row in baseline]
        for axis, delta in enumerate([4, 5, 6]):
            expected[axis][3] += delta
        self.assert_matrix(after["Movable"]["matrix_3x4"], expected)
        expected_bounds = [value + [4, 5, 6][index % 3]
                           for index, value in enumerate(before["Movable"]["bounds_mm"])]
        self.assert_vector(after["Movable"]["bounds_mm"], expected_bounds)

    def test_stale_and_repeated_move_requests_are_rejected_atomically(self):
        initial = self.import_boxes()
        moved = self.json_response(self.move(initial, [10, 0, 0]))
        self.assertGreater(moved["revision"], initial["revision"])
        for offset in ([10, 0, 0], [-10, 0, 0]):
            self.json_response(self.move(initial, offset), 409)
            self.assertEqual(self.state(), moved)

    def test_simultaneous_moves_have_one_revision_winner(self):
        initial = self.import_boxes()
        barrier = threading.Barrier(2)

        def move_when_ready(offset):
            barrier.wait(timeout=10)
            return self.move(initial, offset)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(move_when_ready, offset) for offset in ([10, 0, 0], [-10, 0, 0])]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        winner = self.json_response(next(result for result in results if result[0] == 200))
        self.json_response(next(result for result in results if result[0] == 409), 409)
        self.assertEqual(self.state(), winner)

    def test_invalid_move_values_leave_state_unchanged(self):
        state = self.import_boxes()
        valid = {"occurrence_id": self.by_name(state)["Movable"]["id"], "offset_mm": [0, 0, 0],
                 "revision": state["revision"]}
        bad_offsets = [None, [], [1], [1, 2], [1, 2, 3, 4], "123", [True, 0, 0],
                       [1_000_001, 0, 0], [0, -1_000_001, 0], [10 ** 400, 0, 0],
                       ["1", 0, 0], [{}, 0, 0], [None, 0, 0]]
        for value in (float("nan"), float("inf"), float("-inf")):
            for axis in range(3):
                offset = [0, 0, 0]
                offset[axis] = value
                bad_offsets.append(offset)
        payloads = [dict(valid, offset_mm=offset) for offset in bad_offsets]
        payloads.extend(dict(valid, occurrence_id=value) for value in ("missing", "", None, True, {}, []))
        payloads.extend(dict(valid, revision=value) for value in (None, True, str(state["revision"]), 1.5))
        payloads.extend({key: value for key, value in valid.items() if key != missing} for missing in valid)
        payloads.extend([None, [], "bad", 1, True])
        for payload in payloads:
            with self.subTest(payload=payload):
                self.json_response(self.post("/api/move", payload), 400)
                self.assertEqual(self.state(), state)

    def test_failed_import_retains_previous_model_and_revision(self):
        state = self.json_response(self.move(self.import_boxes(), [10, 0, 0]))
        for body in (b"", b"not a STEP document", b"ISO-10303-21;\nHEADER;\nENDSEC;\n"):
            with self.subTest(body=body):
                self.json_response(self.import_step(body), 400)
                self.assertEqual(self.state(), state)
        self.assertEqual(self.source.read_bytes(), self.box_bytes)

    def test_upload_is_staged_independently_of_the_users_source(self):
        original = self.source.read_bytes()
        state = self.json_response(self.import_step(original, self.source.name))
        staged = self.server.session.assembly.source_path
        self.assertNotEqual(staged.resolve(), self.source.resolve())
        self.assertEqual(staged.read_bytes(), original)
        # The HTTP server owns a staged copy, not an ongoing path to this file.
        self.source.write_bytes(b"user independently replaced their local file")
        moved = self.json_response(self.move(state, [10, 0, 0]))
        self.assertEqual(moved["model"]["source_sha256"], hashlib.sha256(original).hexdigest())
        self.assertEqual(self.source.read_bytes(), b"user independently replaced their local file")
        self.assertEqual(self.request("/api/export")[0], 200)
        self.assertEqual(self.source.read_bytes(), b"user independently replaced their local file")
        self.assertEqual(staged.read_bytes(), original)

    def test_download_roundtrip_preserves_exact_geometry_and_shared_instances(self):
        state = self.json_response(self.move(self.import_boxes(), [10, -2, 3]))
        status, headers, body = self.request("/api/export")
        self.assertEqual(status, 200, body[:500])
        self.assertIn("attachment", headers.get("Content-Disposition", "").lower())
        self.assertIn(".step", headers.get("Content-Disposition", "").lower())
        self.assertTrue(body.startswith(b"ISO-10303-21;"))
        exported = self.directory / "download.step"
        exported.write_bytes(body)
        reimported = StepAssembly.load(exported).snapshot()
        self.assertEqual(reimported["root_count"], 1)
        occurrences = self.by_name({"model": reimported})
        for name, bounds in (("Fixed", [0, 0, 0, 10, 20, 30]), ("Movable", [40, -2, 3, 50, 18, 33])):
            self.assert_vector(occurrences[name]["bounds_mm"], bounds)
            self.assert_vector(occurrences[name]["dimensions_mm"], [10, 20, 30])
            self.assertAlmostEqual(occurrences[name]["volume_mm3"], 6000, places=6)
        self.assertEqual(self.state(), state)
        self.assertEqual(self.request("/api/export")[0], 200)
        self.assertEqual(self.source.read_bytes(), self.box_bytes)

    def test_stale_tab_cannot_export_a_different_model_revision(self):
        tab_b = self.import_boxes()
        tab_a = self.json_response(self.move(tab_b, [10, -2, 3]))
        self.json_response(self.request("/api/export?revision=" + str(tab_b["revision"])), 409)
        self.assertEqual(self.state(), tab_a)
        replacement = self.json_response(self.import_step(self.cylinder_bytes, "new-model.step"))
        self.json_response(self.request("/api/export?revision=" + str(tab_a["revision"])), 409)
        self.assertEqual(self.state(), replacement)
        status, headers, body = self.request("/api/export?revision=" + str(replacement["revision"]))
        self.assertEqual(status, 200, body[:500])
        self.assertIn("attachment", headers.get("Content-Disposition", ""))
        exported = self.directory / "current-model.step"
        exported.write_bytes(body)
        occurrences = self.by_name({"model": StepAssembly.load(exported).snapshot()})
        self.assertAlmostEqual(occurrences["Fixed"]["volume_mm3"], math.pi * 5 ** 2 * 20, places=5)
        self.assertEqual(self.state(), replacement)

    def test_export_requires_one_well_formed_revision(self):
        state = self.import_boxes()
        paths = ["/api/export", "/api/export?revision=", "/api/export?revision=bad",
                 "/api/export?revision=true", "/api/export?revision=1.5", "/api/export?revision=-1",
                 "/api/export?revision=" + "9" * 17,
                 "/api/export?revision=" + quote("١"),
                 "/api/export?revision={0}&revision={0}".format(state["revision"])]
        for path in paths:
            with self.subTest(path=path):
                self.json_response(self.request(path, with_revision=False), 400)
                self.assertEqual(self.state(), state)

    def test_replacement_model_rejects_previous_occurrence_and_revision(self):
        original = self.import_boxes()
        replacement = self.json_response(self.import_step(self.cylinder_bytes, "new.step"))
        self.assertGreater(replacement["revision"], original["revision"])
        self.assertNotEqual(replacement["model"]["source_sha256"], original["model"]["source_sha256"])
        self.json_response(self.move(original, [10, 0, 0]), 409)
        old_id = self.by_name(original)["Movable"]["id"]
        self.json_response(self.post("/api/move", {"occurrence_id": old_id, "offset_mm": [10, 0, 0],
                                                 "revision": replacement["revision"]}), 400)
        self.assertEqual(self.state(), replacement)

    def test_demo_creates_and_replaces_a_real_shared_box_model(self):
        before = self.state()
        state = self.json_response(self.post("/api/demo", {}))
        self.assertGreater(state["revision"], before["revision"])
        self.by_name(state)
        for mesh in state["model"]["meshes"].values():
            self.assert_finite_flat(mesh["positions"], 9)
        moved = self.json_response(self.move(state, [10, 0, 0]))
        reset = self.json_response(self.post("/api/demo", {}))
        self.assertGreater(reset["revision"], moved["revision"])
        self.assert_vector(self.by_name(reset)["Movable"]["offset_mm"], [0, 0, 0])
        self.assert_vector(self.by_name(reset)["Movable"]["bounds_mm"], [30, 0, 0, 40, 20, 30])

    def test_stale_tab_cannot_replace_newer_moves_with_import_or_demo(self):
        tab_b = self.import_boxes()
        tab_a = self.json_response(self.move(tab_b, [10, -2, 3]))
        staged = self.server.session.assembly.source_path
        source_bytes = staged.read_bytes()
        for operation in ("import", "demo"):
            with self.subTest(operation=operation):
                if operation == "import":
                    response = self.import_step(self.cylinder_bytes, "stale-tab.step",
                                                headers={"X-Revision": str(tab_b["revision"])})
                else:
                    response = self.post("/api/demo", {"revision": tab_b["revision"]})
                self.json_response(response, 409)
                self.assertEqual(self.state(), tab_a)
                self.assertEqual(self.server.session.assembly.source_path, staged)
                self.assertEqual(staged.read_bytes(), source_bytes)
        # After explicitly refreshing, a replacement is allowed again.
        replacement = self.json_response(self.import_step(self.cylinder_bytes, "refreshed.step",
                                                           headers={"X-Revision": str(tab_a["revision"])}))
        self.assertGreater(replacement["revision"], tab_a["revision"])
        self.assertEqual(replacement["model"]["source_sha256"], hashlib.sha256(self.cylinder_bytes).hexdigest())

    def test_replacement_requires_a_well_formed_revision(self):
        state = self.json_response(self.move(self.import_boxes(), [10, 0, 0]))
        for revision in (None, "", "not-an-integer", "true", "1.5", "-1", "9" * 17):
            with self.subTest(route="import", revision=revision):
                headers = {} if revision is None else {"X-Revision": revision}
                self.json_response(self.import_step(headers=headers, with_revision=False), 400)
                self.assertEqual(self.state(), state)
        payloads = [{}] + [{"revision": value} for value in
                           (None, True, str(state["revision"]), "", 1.5, [], {}, float("nan"), float("inf"))]
        for payload in payloads:
            with self.subTest(route="demo", payload=payload):
                self.json_response(self.post("/api/demo", payload, with_revision=False), 400)
                self.assertEqual(self.state(), state)

    def test_simultaneous_replacements_have_one_revision_winner(self):
        initial = self.import_boxes()
        barrier = threading.Barrier(2)

        def replace_when_ready(operation):
            barrier.wait(timeout=10)
            if operation == "import":
                return self.import_step(self.cylinder_bytes, "replacement.step",
                                        headers={"X-Revision": str(initial["revision"])})
            return self.post("/api/demo", {"revision": initial["revision"]})

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(replace_when_ready, operation) for operation in ("import", "demo")]
            results = [future.result(timeout=30) for future in futures]
        self.assertEqual(sorted(result[0] for result in results), [200, 409])
        winner = self.json_response(next(result for result in results if result[0] == 200))
        self.json_response(next(result for result in results if result[0] == 409), 409)
        self.assertEqual(self.state(), winner)

    def test_csrf_is_required_on_every_mutating_route(self):
        state = self.import_boxes()
        for token in (None, "not-the-session-token"):
            headers = {} if token is None else {"X-CSRF-Token": token}
            for path, payload in (("/api/demo", {}), ("/api/move", {"occurrence_id": "anything"})):
                with self.subTest(token=token, path=path):
                    self.json_response(self.post(path, payload, headers=headers, csrf=False), 403)
                    self.assertEqual(self.state(), state)
            self.json_response(self.import_step(headers=headers, csrf=False), 403)
            self.assertEqual(self.state(), state)

    def test_host_origin_and_cors_boundaries(self):
        state = self.import_boxes()
        for host in ("attacker.example", "attacker.example:%s" % self.server.server_address[1]):
            with self.subTest(host=host):
                self.json_response(self.request("/api/state", headers={"Host": host}), 403)
                self.json_response(self.post("/api/demo", {}, headers={"Host": host}), 403)
        for origin in ("https://attacker.example", "null", "http://127.0.0.1:1", self.base + ".attacker.example"):
            with self.subTest(origin=origin):
                self.json_response(self.post("/api/demo", {}, headers={"Origin": origin}), 403)
                self.assertEqual(self.state(), state)
        for fetch_site in ("cross-site", "same-site"):
            self.json_response(self.post("/api/demo", {}, headers={"Sec-Fetch-Site": fetch_site}), 403)
            self.assertEqual(self.state(), state)
        same_origin = self.json_response(self.post("/api/demo", {}, headers={"Origin": self.base}))
        self.by_name(same_origin)
        response = self.request("/api/state", method="OPTIONS", headers={"Origin": "https://attacker.example",
                                                                          "Access-Control-Request-Method": "POST"})
        self.assertGreaterEqual(response[0], 400)
        self.assertIsNone(response[1].get("Access-Control-Allow-Origin"))

    def test_media_type_malformed_json_and_size_limits(self):
        state = self.import_boxes()
        self.json_response(self.import_step(headers={"Content-Type": "text/plain"}), 415)
        self.json_response(self.post("/api/move", {}, headers={"Content-Type": "text/plain"}), 415)
        self.json_response(self.request("/api/move", method="POST", data=b"{broken",
                                        headers={"Content-Type": "application/json"}), 400)
        self.json_response(self.request("/api/move", method="POST", data=b"\xff",
                                        headers={"Content-Type": "application/json"}), 400)
        # Reject lengths before reading the oversized upload. Sending a small
        # body avoids a BrokenPipe race when a compliant server refuses early.
        self.json_response(self.import_step(b"x", headers={"Content-Length": str(20 * 1024 * 1024 + 1)}), 413)
        self.json_response(self.post("/api/move", {}, headers={"Content-Length": str(64 * 1024 + 1)}), 413)
        self.json_response(self.post("/api/demo", {}, headers={"Transfer-Encoding": "chunked"}), 400)
        self.assertEqual(self.state(), state)

    def test_invalid_body_length_headers_do_not_change_model(self):
        state = self.import_boxes()
        # urllib supplies Content-Length automatically. Use a standard-library
        # HTTP connection only for deliberately malformed framing headers.
        cases = [
            ("missing length", [], 411),
            ("invalid length", [("Content-Length", "not-a-number")], 411),
            ("negative length", [("Content-Length", "-1")], 413),
            ("duplicate lengths", [("Content-Length", "2"), ("Content-Length", "2")], 411),
            ("duplicate host", [("Host", "attacker.example"), ("Content-Length", "0")], 403),
        ]
        for label, headers, expected in cases:
            with self.subTest(case=label):
                connection = HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
                try:
                    connection.putrequest("POST", "/api/demo")
                    connection.putheader("Content-Type", "application/json")
                    connection.putheader("X-CSRF-Token", self.token)
                    for key, value in headers:
                        connection.putheader(key, value)
                    connection.endheaders()
                    response = connection.getresponse()
                    self.json_response((response.status, response.headers, response.read()), expected)
                finally:
                    connection.close()
                self.assertEqual(self.state(), state)

    def test_upload_filename_is_display_only_and_invalid_names_are_atomic(self):
        initial = self.import_boxes()
        for filename in ("invalid.txt", "bad\nname.step", "bad\x00name.step", "x" * 256 + ".step"):
            with self.subTest(filename=filename):
                self.json_response(self.import_step(filename=filename), 400)
                self.assertEqual(self.state(), initial)
        replacement = self.json_response(self.import_step(self.cylinder_bytes, str(self.source)))
        self.assertEqual(replacement["model"]["name"], self.source.name)
        self.assertEqual(self.source.read_bytes(), self.box_bytes)
        self.assertEqual(replacement["model"]["source_sha256"], hashlib.sha256(self.cylinder_bytes).hexdigest())
        unicode_name = "pièces #2.stp"
        renamed = self.json_response(self.import_step(filename=unicode_name))
        self.assertEqual(renamed["model"]["name"], unicode_name)

    def test_failed_tessellation_keeps_model_and_removes_candidate_staging(self):
        state = self.json_response(self.move(self.import_boxes(), [10, 0, 0]))
        staging = self.server.session.directory
        files_before = sorted(str(path.relative_to(staging)) for path in staging.rglob("*"))
        with patch("engineering_workbench.viewer.display_meshes", side_effect=ValueError("geometry budget exceeded")):
            self.json_response(self.import_step(self.cylinder_bytes, "large.step"), 400)
        self.assertEqual(self.state(), state)
        self.assertEqual(sorted(str(path.relative_to(staging)) for path in staging.rglob("*")), files_before)
        self.assertEqual(self.request("/api/export")[0], 200)

    def test_server_context_manager_cleans_its_private_directory(self):
        with make_server(host="127.0.0.1", port=0) as server:
            self.assertIsNot(server.session, self.server.session)
            staging = server.session.directory
            self.assertTrue(staging.is_dir())
        self.assertFalse(staging.exists())

    def test_static_assets_are_local_and_traversal_is_not_served(self):
        status, headers, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        assets = _LocalAssets()
        assets.feed(body.decode("utf-8"))
        self.assertTrue(assets.paths, "The UI must load at least one local script or stylesheet")
        self.assertEqual(assets.external, [], "The local viewer must not rely on CDN resources")
        for path in assets.paths:
            with self.subTest(asset=path):
                asset_status, _, asset_body = self.request(path)
                self.assertEqual(asset_status, 200)
                self.assertGreater(len(asset_body), 0)
        for path in ("/../cad.py", "/%2e%2e/cad.py", "/static/../../cad.py",
                     "/static/%2e%2e/%2e%2e/cad.py", "/.git/config", "/%2fetc%2fpasswd"):
            with self.subTest(path=path):
                result = self.request(path)
                self.assertIn(result[0], (400, 404))
                self.assertNotIn(b"class StepAssembly", result[2])
                self.assertNotIn(b"root:x:0:0", result[2])


if __name__ == "__main__":
    unittest.main()

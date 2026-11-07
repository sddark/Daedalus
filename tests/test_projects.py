"""End-to-end persistent-project HTTP and lifecycle regressions."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

from engineering_workbench.cad import StepAssembly, create_two_instance_fixture
from engineering_workbench.history import CheckpointStore, UncertainCheckpoint
from engineering_workbench.viewer import ViewerSession, make_server
import test_viewer as viewer_helpers


class ProjectHTTPTests(unittest.TestCase):
    request = viewer_helpers.ViewerHTTPTests.request
    json_response = viewer_helpers.ViewerHTTPTests.json_response
    state = viewer_helpers.ViewerHTTPTests.state
    post = viewer_helpers.ViewerHTTPTests.post
    import_step = viewer_helpers.ViewerHTTPTests.import_step
    import_boxes = viewer_helpers.ViewerHTTPTests.import_boxes
    by_name = viewer_helpers.ViewerHTTPTests.by_name

    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source.step"
            create_two_instance_fixture(source)
            cls.box_bytes = source.read_bytes()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name) / "projects"
        self.server = make_server(port=0, workspace=self.workspace)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 10)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%s" % self.server.server_port
        self.opener = build_opener(ProxyHandler({}))
        self.token = self.server.session.csrf_token

    def move(self, state, offset):
        return self.json_response(self.post("/api/move", {
            "revision": state["revision"], "occurrence_id": self.by_name(state)["Movable"]["id"],
            "offset_mm": offset}))

    def save_body(self, state, message="Save movement"):
        return {"revision": state["revision"], "expected_head": state["project"]["head"],
                "request_id": str(uuid4()), "message": message}

    def save(self, state, message="Save movement"):
        return self.json_response(self.post("/api/checkpoint", self.save_body(state, message)))

    def test_save_compare_restore_reopen_and_export_roundtrip(self):
        start = self.import_boxes()
        self.assertTrue(start["persistence_enabled"])
        self.assertFalse(start["project"]["dirty"])
        original_head = start["project"]["head"]
        moved = self.move(start, [12, -3, 2])
        self.assertTrue(moved["project"]["dirty"])
        saved = self.save(moved)
        self.assertFalse(saved["project"]["dirty"])
        self.assertTrue(saved["checkpoint_result"]["created"])
        self.assertEqual(len(saved["project"]["checkpoints"]), 2)
        comparison = self.json_response(self.request(
            f"/api/compare?checkpoint={original_head}&revision={saved['revision']}"))
        self.assertEqual(comparison["report"]["changes"][0]["delta_mm"], [12, -3, 2])
        key = self.by_name(start)["Movable"]["id"]
        self.assertEqual(comparison["matrices"][key], self.by_name(start)["Movable"]["matrix_3x4"])
        restored = self.json_response(self.post("/api/restore", {
            "revision": saved["revision"], "expected_head": saved["project"]["head"],
            "checkpoint_id": original_head}))
        self.assertEqual(self.by_name(restored)["Movable"]["offset_mm"], [0, 0, 0])
        self.assertTrue(restored["project"]["dirty"])
        self.assertEqual(restored["project"]["head"], saved["project"]["head"])
        descendant = self.save(restored, "Restore original placement")
        self.assertEqual(descendant["project"]["checkpoints"][0]["parent"], saved["project"]["head"])
        self.assertEqual(len(descendant["project"]["checkpoints"]), 3)
        session = ViewerSession(self.workspace)
        self.addCleanup(session.close)
        reopened = session.open_project({"revision": 0, "project_id": saved["project"]["id"]})
        self.assertEqual(self.by_name(reopened)["Movable"]["offset_mm"], [0, 0, 0])
        exported = Path(self.temp.name) / "reopened.step"
        exported.write_bytes(session.export_step(reopened["revision"]))
        parts = {item["name"]: item for item in StepAssembly.load(exported).snapshot()["occurrences"]}
        self.assertEqual(parts["Movable"]["translation_mm"], [30, 0, 0])
        self.assertAlmostEqual(parts["Movable"]["volume_mm3"], 6000)
        self.assertEqual(session.store.source_path.read_bytes(), self.box_bytes)

    def test_dirty_guards_and_explicit_discard_preserve_projects(self):
        first = self.import_boxes()
        dirty = self.move(first, [5, 0, 0])
        before = deepcopy(dirty)
        bodies = [("/api/demo", {"revision": dirty["revision"]}),
                  ("/api/project/open", {"revision": dirty["revision"], "project_id": first["project"]["id"]}),
                  ("/api/restore", {"revision": dirty["revision"], "expected_head": first["project"]["head"],
                                    "checkpoint_id": first["project"]["head"]})]
        for route, body in bodies:
            self.json_response(self.post(route, body), 409)
            self.assertEqual(self.state(), before)
        self.json_response(self.import_step(), 409)
        self.assertEqual(self.state(), before)
        second = self.json_response(self.import_step(headers={"X-Discard-Changes": "true"}))
        self.assertNotEqual(second["project"]["id"], first["project"]["id"])
        self.assertEqual(len(second["projects"]), 2)
        first_path = self.workspace / first["project"]["id"]
        self.assertEqual(CheckpointStore(first_path).source_path.read_bytes(), self.box_bytes)
        reopened = self.json_response(self.post("/api/project/open", {
            "revision": second["revision"], "project_id": first["project"]["id"]}))
        self.assertEqual(self.by_name(reopened)["Movable"]["offset_mm"], [0, 0, 0])

    def test_checkpoint_retries_noops_and_stale_guards(self):
        start = self.import_boxes()
        noop_body = self.save_body(start)
        first = self.json_response(self.post("/api/checkpoint", noop_body))
        self.assertFalse(first["checkpoint_result"]["created"])
        replay = self.json_response(self.post("/api/checkpoint", noop_body))
        self.assertTrue(replay["checkpoint_result"]["replayed"])
        moved = self.move(first, [7, 0, 0])
        self.json_response(self.post("/api/checkpoint", self.save_body(start)), 409)
        body = self.save_body(moved)
        saved = self.json_response(self.post("/api/checkpoint", body))
        replay = self.json_response(self.post("/api/checkpoint", body))
        self.assertTrue(replay["checkpoint_result"]["replayed"])
        self.assertEqual(replay["project"]["head"], saved["project"]["head"])
        stale = self.save_body(moved)
        result = self.json_response(self.post("/api/checkpoint", stale), 409)
        self.assertEqual(result["code"], "stale_checkpoint")
        self.json_response(self.post("/api/restore", {
            "revision": moved["revision"], "expected_head": moved["project"]["head"],
            "checkpoint_id": moved["project"]["head"]}), 409)

    def test_all_new_mutations_enforce_csrf_and_origin(self):
        start = self.import_boxes()
        for route in ("/api/checkpoint", "/api/restore", "/api/project/open"):
            self.json_response(self.post(route, {}, csrf=False), 403)
            self.json_response(self.post(route, {}, headers={"Origin": "https://evil.invalid"}), 403)
        for route in ("/api/history", "/api/compare"):
            self.json_response(self.request(route, headers={"Host": "evil.invalid"}), 403)
        self.assertEqual(self.state(), start)

    def test_saved_pair_comparison_is_complete_directional_and_read_only(self):
        initial = self.import_boxes()
        first = self.save(self.move(initial, [10, -2, 1]), "First position")
        second = self.save(self.move(first, [25, 4, -3]), "Second position")
        working = self.move(second, [99, 20, 7])
        left, right = first["project"]["head"], second["project"]["head"]
        key = self.by_name(initial)["Movable"]["id"]
        store = self.server.session.store
        files = {str(path.relative_to(store.path)): path.read_bytes()
                 for path in store.path.rglob("*") if path.is_file()}
        assembly = self.server.session.assembly

        def comparison(before, after=None):
            suffix = "" if after is None else f"&after={after}"
            return self.json_response(self.request(
                f"/api/compare?checkpoint={before}&revision={working['revision']}{suffix}"))

        with patch("engineering_workbench.projects.load_checkpoint_assembly",
                   side_effect=AssertionError("Comparison must not reload native geometry")):
            pair = comparison(left, right)
            self.assertEqual(pair["checkpoint_id"], left)
            self.assertEqual(pair["after_checkpoint_id"], right)
            self.assertEqual(pair["report"]["changes"][0]["delta_mm"], [15, 6, -4])
            self.assertEqual(pair["report"]["unchanged_count"], 1)
            self.assertEqual(len(pair["matrices"]), 2)
            self.assertEqual(len(pair["after_matrices"]), 2)
            self.assertEqual(pair["matrices"][key], self.by_name(first)["Movable"]["matrix_3x4"])
            self.assertEqual(pair["after_matrices"][key], self.by_name(second)["Movable"]["matrix_3x4"])
            self.assertEqual(pair["after_offsets"][key], [25, 4, -3])
            reverse = comparison(right, left)
            self.assertEqual(reverse["report"]["changes"][0]["delta_mm"], [-15, -6, 4])
            same = comparison(left, left)
            self.assertEqual(same["report"]["changes"], [])
            self.assertEqual(same["matrices"], same["after_matrices"])
            current = comparison(left)
            self.assertIsNone(current["after_checkpoint_id"])
            self.assertEqual(current["report"]["changes"][0]["delta_mm"], [89, 22, 6])
            self.assertEqual(current["after_matrices"][key], self.by_name(working)["Movable"]["matrix_3x4"])
        self.assertEqual(self.state(), working)
        self.assertIs(self.server.session.assembly, assembly)
        self.assertEqual(files, {str(path.relative_to(store.path)): path.read_bytes()
                                for path in store.path.rglob("*") if path.is_file()})

    def test_saved_pair_rejects_invalid_foreign_and_stale_endpoints(self):
        foreign = self.import_boxes()["project"]["head"]
        state = self.import_boxes()
        head = state["project"]["head"]
        route = f"/api/compare?checkpoint={head}&revision={state['revision']}"
        for suffix in ("&after=", "&after=HEAD", "&after=../", "&after=" + "0" * 40,
                       "&after=" + head[:12], "&after=" + foreign,
                       f"&after={head}&after={head}", f"&after={head}&after="):
            with self.subTest(suffix=suffix):
                self.json_response(self.request(route + suffix), 400)
        self.json_response(self.request(
            f"/api/compare?checkpoint={head}&after={head}&revision=0"), 409)
        self.assertEqual(self.state(), state)

    def test_invalid_ids_and_comparison_revisions_fail_closed(self):
        start = self.import_boxes()
        for value in ("../", "../other", "HEAD", start["project"]["head"], "", None):
            self.json_response(self.post("/api/project/open", {
                "revision": start["revision"], "project_id": value}), 400)
        for value in ("HEAD", "../", "0"*40):
            self.json_response(self.request(
                f"/api/compare?checkpoint={value}&revision={start['revision']}"), 400)
        self.json_response(self.request("/api/compare"), 400)
        self.json_response(self.request(
            f"/api/compare?checkpoint={start['project']['head']}&revision=0"), 409)
        self.assertEqual(self.state(), start)

    def test_failed_candidate_load_does_not_touch_active_model(self):
        saved = self.save(self.move(self.import_boxes(), [5, 2, 1]))
        with patch("engineering_workbench.projects.display_meshes", side_effect=ValueError("test mesh failure")):
            self.json_response(self.post("/api/restore", {
                "revision": saved["revision"], "expected_head": saved["project"]["head"],
                "checkpoint_id": saved["project"]["checkpoints"][-1]["id"]}), 400)
        self.assertEqual(self.state()["model"], saved["model"])
        self.assertEqual(self.state()["revision"], saved["revision"])

    def test_recovery_blocks_edit_save_export_but_validated_reload_recovers(self):
        start = self.import_boxes()
        self.server.session.recovery_required = True
        self.json_response(self.post("/api/checkpoint", self.save_body(start)), 400)
        self.json_response(self.request("/api/export"), 409)
        self.json_response(self.post("/api/move", {
            "revision": start["revision"], "occurrence_id": self.by_name(start)["Movable"]["id"],
            "offset_mm": [10,0,0]}), 409)
        recovered = self.json_response(self.post("/api/project/open", {
            "revision": start["revision"], "project_id": start["project"]["id"], "discard_changes": True}))
        self.assertFalse(recovered["recovery_required"])
        self.assertFalse(recovered["project"]["dirty"])

    def test_uncertain_save_reports_inspection_required(self):
        moved = self.move(self.import_boxes(), [8,0,0])
        with patch.object(self.server.session.store, "save", side_effect=UncertainCheckpoint("Inspect history before retrying")):
            error = self.json_response(self.post("/api/checkpoint", self.save_body(moved)), 409)
        self.assertEqual(error["code"], "uncertain_checkpoint")
        self.assertTrue(self.state()["project"]["dirty"])

    def test_parallel_checkpoint_writers_have_one_winner(self):
        moved = self.move(self.import_boxes(), [5,0,0])
        body_a, body_b = self.save_body(moved, "A"), self.save_body(moved, "B")
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(lambda body: self.post("/api/checkpoint", body), (body_a, body_b)))
        self.assertEqual(sorted(response[0] for response in responses), [200,409])
        self.assertEqual(len(self.state()["project"]["checkpoints"]), 2)

    def test_project_links_are_not_followed(self):
        first = self.import_boxes()
        link_id = str(uuid4())
        (self.workspace / link_id).symlink_to(self.workspace / first["project"]["id"], target_is_directory=True)
        state = self.state()
        self.assertIn(link_id, state["unavailable_projects"])
        self.assertNotIn(link_id, [item["id"] for item in state["projects"]])
        self.json_response(self.post("/api/project/open", {
            "revision": state["revision"], "project_id": link_id}), 400)

    def test_workspace_close_does_not_remove_durable_source(self):
        first = self.import_boxes()
        directory = self.server.session.directory
        source = self.server.session.store.source_path
        self.server.session.close()
        self.assertFalse(directory.exists())
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), self.box_bytes)

    def test_independent_stale_session_cannot_silently_rebase_and_undo_other_moves(self):
        initial = self.import_boxes()
        other = ViewerSession(self.workspace)
        self.addCleanup(other.close)
        stale = other.open_project({"revision": 0, "project_id": initial["project"]["id"]})
        latest = self.save(self.move(initial, [10, 0, 0]))
        fixed = self.by_name(stale)["Fixed"]["id"]
        local = other.move({"revision": stale["revision"], "occurrence_id": fixed, "offset_mm": [0, 4, 0]})
        self.assertEqual(local["project"]["head"], initial["project"]["head"])
        self.assertEqual(local["project"]["latest_head"], latest["project"]["head"])
        self.assertTrue(local["project"]["history_conflict"])
        self.assertTrue(local["project"]["dirty"])
        from engineering_workbench.history import StaleCheckpoint
        for head in (local["project"]["head"], latest["project"]["head"]):
            with self.assertRaises(StaleCheckpoint):
                other.checkpoint({"revision": local["revision"], "expected_head": head,
                                  "request_id": str(uuid4()), "message": "Stale changes"})
        self.assertEqual(self.by_name(other.state())["Fixed"]["offset_mm"], [0,4,0])
        self.assertEqual(self.server.session.store.head(), latest["project"]["head"])
        reopened = other.open_project({"revision": local["revision"],
                                      "project_id": initial["project"]["id"], "discard_changes": True})
        self.assertEqual(self.by_name(reopened)["Movable"]["offset_mm"], [10,0,0])
        self.assertFalse(reopened["project"]["history_conflict"])

    def test_damaged_active_project_state_allows_explicit_escape(self):
        first = self.import_boxes()
        second = self.import_boxes()
        opened = self.json_response(self.post("/api/project/open", {
            "revision": second["revision"], "project_id": first["project"]["id"]}))
        source = self.server.session.store.source_path
        source.chmod(0o600)
        source.write_bytes(b"damaged")
        broken = self.state()
        self.assertTrue(broken["recovery_required"])
        self.assertTrue(broken["project"]["unavailable"])
        self.assertEqual(broken["model"], opened["model"])
        self.assertIn(second["project"]["id"], [item["id"] for item in broken["projects"]])
        healthy = self.json_response(self.post("/api/project/open", {
            "revision": broken["revision"], "project_id": second["project"]["id"], "discard_changes": True}))
        self.assertFalse(healthy["recovery_required"])
        self.assertEqual(healthy["project"]["id"], second["project"]["id"])
        # Import is also an escape route with explicit discard.
        self.server.session.store.source_path.chmod(0o600)
        self.server.session.store.source_path.write_bytes(b"damaged too")
        new = self.json_response(self.import_step(headers={"X-Discard-Changes": "true"}))
        self.assertFalse(new["recovery_required"])

    def test_cross_site_document_navigation_does_not_relax_api_boundaries(self):
        navigation = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate",
                      "Sec-Fetch-Dest": "document"}
        self.assertEqual(self.request("/", headers=navigation)[0], 200)
        self.json_response(self.request("/api/state", headers=navigation), 403)
        self.json_response(self.request("/", headers={"Sec-Fetch-Site": "cross-site"}), 403)
        self.json_response(self.post("/api/demo", {}, headers=navigation), 403)

    def test_committed_then_uncertain_retries_only_existing_receipt(self):
        moved = self.move(self.import_boxes(), [9,0,0])
        body = self.save_body(moved)
        store = self.server.session.store
        original_save = store.save
        def commit_then_lose_response(*args, **kwargs):
            original_save(*args, **kwargs)
            raise UncertainCheckpoint("Save response was lost; inspect before retrying")
        with patch.object(store, "save", side_effect=commit_then_lose_response):
            self.json_response(self.post("/api/checkpoint", body), 409)
        uncertain = self.state()
        self.assertTrue(uncertain["project"]["history_conflict"])
        self.assertEqual(uncertain["project"]["head"], moved["project"]["head"])
        fresh = dict(body, request_id=str(uuid4()), expected_head=uncertain["project"]["latest_head"])
        self.json_response(self.post("/api/checkpoint", fresh), 409)
        retried = self.json_response(self.post("/api/checkpoint", body))
        self.assertTrue(retried["checkpoint_result"]["replayed"])
        self.assertFalse(retried["project"]["history_conflict"])
        self.assertFalse(retried["project"]["dirty"])
        self.assertEqual(len(retried["project"]["checkpoints"]), 2)


if __name__ == "__main__":
    unittest.main()

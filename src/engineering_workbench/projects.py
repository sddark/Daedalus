"""Persistent project lifecycle for the trusted-local viewer.

Only CLI-selected workspace paths are accepted. Browser callers use opaque IDs,
and saved projects are never removed by transient-session cleanup.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re
from uuid import uuid4

from .checkpoints import load_checkpoint_assembly, save_session_checkpoint
from .history import CheckpointStore, HistoryError, StaleCheckpoint
from .mesh import display_meshes
from .placements import canonical, compare, make_state

PROJECT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def runtime_kernel_version():
    for package in ("cadquery-ocp-novtk", "cadquery-ocp"):
        try:
            return package + "==" + version(package)
        except PackageNotFoundError:
            pass
    raise HistoryError("No supported OCP distribution is installed")


def _request_error(status, message):
    from .viewer import RequestError
    return RequestError(status, message)


class ProjectSession:
    """Mixin using ViewerSession's lock, model and revision guards."""

    def _init_projects(self, workspace):
        self.workspace = None if workspace is None else Path(workspace).expanduser().absolute()
        self.store = None
        self.project_id = None
        self.base_head = None
        if self.workspace is not None:
            if self.workspace.is_symlink():
                raise HistoryError("The workspace must not be a symbolic link")
            self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self.workspace.is_dir():
                raise HistoryError("Expected a project workspace directory")
        self.kernel_version = runtime_kernel_version()

    def _project_path(self, project_id):
        if self.workspace is None:
            raise _request_error(409, "Persistent checkpoints are disabled for this session")
        if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
            raise _request_error(400, "Invalid project ID")
        path = self.workspace / project_id
        if path.is_symlink():
            raise _request_error(400, "Project links are not supported")
        return path

    @staticmethod
    def _project_name(store):
        display = store.path / "display.json"
        if display.is_symlink():
            raise HistoryError("Invalid project display metadata")
        if not display.exists():
            return "Saved assembly"
        if display.stat().st_size > 4096:
            raise HistoryError("Invalid project display metadata")
        value = json.loads(display.read_text(encoding="utf-8"))
        name = value.get("name") if isinstance(value, dict) else None
        if not isinstance(name, str) or not name or len(name) > 255:
            raise HistoryError("Invalid project display name")
        return name

    def _working_placements(self):
        if self.store is None or self.model is None:
            raise _request_error(409, "Import or open a project first")
        return make_state(self.store.manifest, {
            item["id"]: item["offset_mm"] for item in self.model["occurrences"]
        })

    def _project_dirty(self, head=None):
        if self.store is None:
            return False
        if self.recovery_required:
            return True
        return canonical(self._working_placements()) != canonical(self.store.read(head or self.base_head))

    def _guard_discard(self, discard_changes):
        if discard_changes is True:
            return
        if self.workspace is not None and self._project_dirty():
            raise _request_error(409, "Unsaved movements: save a checkpoint or explicitly discard them before continuing")

    def _project_state(self):
        active = None
        if self.store is not None:
            try:
                history = self.store.history()
                active = {"id": self.project_id, "name": self.model["name"],
                          "head": self.base_head, "latest_head": history["head_id"],
                          "history_conflict": self.base_head != history["head_id"],
                          "dirty": self._project_dirty(), "checkpoints": history["checkpoints"]}
            except (OSError, ValueError, HistoryError):
                active = {"id": self.project_id, "name": self.model["name"],
                          "head": self.base_head, "latest_head": None, "history_conflict": True,
                          "dirty": True, "checkpoints": [], "unavailable": True,
                          "error": "Saved project is unavailable or damaged. Open a healthy project or reimport the source."}
        projects, unavailable = [], []
        if self.workspace is not None:
            for path in sorted(self.workspace.iterdir()):
                if not PROJECT_ID.fullmatch(path.name):
                    continue
                try:
                    store = self.store if path.name == self.project_id else CheckpointStore(self._project_path(path.name))
                    projects.append({"id": path.name, "name": self._project_name(store), "head": store.head()})
                except (OSError, ValueError, HistoryError):
                    unavailable.append(path.name)
        return {"persistence_enabled": self.workspace is not None, "project": active,
                "projects": projects, "unavailable_projects": unavailable}

    def _create_project(self, source, snapshot, name):
        if self.workspace is None:
            return None, None
        project_id = str(uuid4())
        store = CheckpointStore.create(self._project_path(project_id), source.read_bytes(),
                                       snapshot, self.kernel_version)
        # Display metadata has no authority over geometry or Git history.
        with (store.path / "display.json").open("x", encoding="utf-8") as file:
            json.dump({"name": name}, file, ensure_ascii=False)
            file.write("\n")
        return project_id, store

    def checkpoint(self, request):
        with self.lock:
            if self.store is None:
                raise _request_error(409, "Import or open a persistent project first")
            # An external advance permits receipt reconciliation, never a new write.
            replay_only = self.store.head() != self.base_head
            result = save_session_checkpoint(
                self, self.store, revision=request.get("revision"),
                expected_head=request.get("expected_head"), message=request.get("message"),
                request_id=request.get("request_id"), replay_only=replay_only,
            )
            self.base_head = result.checkpoint_id
            state = self.state()
            state["checkpoint_result"] = asdict(result)
            return state

    def checkpoint_history(self, skip=0):
        with self.lock:
            if self.store is None:
                raise _request_error(409, "Import or open a project first")
            return self.store.history(skip=skip)

    def compare_checkpoint(self, checkpoint_id, revision, after_checkpoint_id=None):
        """Compare immutable placement data without adopting either endpoint."""
        with self.lock:
            self._check_revision(revision)
            if self.recovery_required:
                raise _request_error(409, "Recover the document before comparing movements")
            current = self._working_placements()
            saved = self.store.read(checkpoint_id)
            after = current if after_checkpoint_id is None else self.store.read(after_checkpoint_id)
            manifest = self.store.manifest

            def matrices(state):
                result = {}
                for key, item in manifest["occurrences"].items():
                    matrix = deepcopy(item["matrix_3x4"])
                    for axis in range(3):
                        matrix[axis][3] += state["offsets"][key][axis]
                    result[key] = matrix
                return result

            return {"revision": self.revision, "checkpoint_id": checkpoint_id,
                    "after_checkpoint_id": after_checkpoint_id,
                    "report": compare(manifest, saved, after),
                    "offsets": saved["offsets"], "matrices": matrices(saved),
                    "after_offsets": after["offsets"], "after_matrices": matrices(after)}

    def _adopt_checkpoint(self, store, project_id, checkpoint_id, name, expected_head):
        # Construct and validate everything before touching the active session.
        candidate, snapshot = load_checkpoint_assembly(store, checkpoint_id, self.kernel_version)
        snapshot.update(name=name, meshes=display_meshes(candidate))
        if store.head() != expected_head:
            raise StaleCheckpoint("History changed while loading; refresh before continuing")
        previous = self.assembly
        self.assembly, self.model = candidate, snapshot
        self.store, self.project_id = store, project_id
        self.base_head = expected_head
        self.recovery_required = False
        self.revision += 1
        if previous is not None and previous.source_path.parent in self._owned_imports:
            import shutil
            shutil.rmtree(previous.source_path.parent)
            self._owned_imports.remove(previous.source_path.parent)
        return self.state()

    def restore_checkpoint(self, request):
        with self.lock:
            self._check_revision(request.get("revision"))
            if self.store is None:
                raise _request_error(409, "Import or open a project first")
            head = self.store.head()
            if request.get("expected_head") != head:
                raise StaleCheckpoint("History changed; refresh before restoring")
            self._guard_discard(request.get("discard_changes"))
            return self._adopt_checkpoint(self.store, self.project_id,
                request.get("checkpoint_id"), self.model["name"], head)

    def open_project(self, request):
        with self.lock:
            self._check_revision(request.get("revision"))
            self._guard_discard(request.get("discard_changes"))
            project_id = request.get("project_id")
            store = CheckpointStore(self._project_path(project_id))
            head = store.head()
            return self._adopt_checkpoint(store, project_id, head, self._project_name(store), head)

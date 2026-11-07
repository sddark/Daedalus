"""Local, application-owned Git checkpoints. No worktree or remote operations.

Git refs are the sole durable checkpoint authority. The main head and retry token
are updated in one ref transaction, so no second JSON database needs reconciliation.
This module does not autosave an uncheckpointed ViewerSession.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import stat
import time
from uuid import uuid4

from .placements import canonical, digest, identifier, make_manifest, make_state, validate_manifest, validate_state

MAX_SOURCE = 20 * 1024 * 1024
MAX_METADATA = 2 * 1024 * 1024
HEAD = "refs/heads/main"
ROOT = "refs/daedalus/source"
FILES = {"source.step", "project.json", "placements.json", "request.json"}
ZERO_OID = "0" * 40


class HistoryError(RuntimeError):
    pass


class StaleCheckpoint(HistoryError):
    pass


class UncertainCheckpoint(HistoryError):
    """Inspect head/request ID before retrying; the ref transaction may have committed."""


@dataclass(frozen=True)
class SaveResult:
    checkpoint_id: str
    head_id: str
    created: bool
    replayed: bool = False


def _oid(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise HistoryError("Expected a full checkpoint ID")
    return value


def _decode(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise HistoryError("Duplicate JSON field")
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (ValueError, UnicodeError) as error:
        raise HistoryError("Malformed stored JSON") from error


class CheckpointStore:
    def __init__(self, path: Path):
        self.path = Path(path).absolute()
        self.repository = self.path / "history.git"
        self.hooks = self.path / "empty-hooks"
        self.source_path = self.path / "source.step"
        self.git = shutil.which("git")
        if self.git is None:
            raise HistoryError("Git is required for checkpoints")
        if self.path.is_symlink() or not self.path.is_dir():
            raise HistoryError("Expected an application-owned project directory")
        for directory in (self.repository, self.hooks):
            if directory.is_symlink() or not directory.is_dir():
                raise HistoryError("Invalid project directory layout")
        if self.source_path.is_symlink() or not self.source_path.is_file():
            raise HistoryError("Invalid source file")
        with self._lock():
            self._load_identity()
            self._verify_source()

    @classmethod
    def create(cls, path: Path, source: bytes, snapshot: dict, kernel_version: str):
        if not isinstance(source, bytes) or not 0 < len(source) <= MAX_SOURCE:
            raise HistoryError("Expected source bytes up to 20 MiB")
        manifest = make_manifest(str(uuid4()), snapshot, kernel_version)
        if len(canonical(manifest)) > MAX_METADATA:
            raise HistoryError("Project manifest exceeds size limit")
        if hashlib.sha256(source).hexdigest() != manifest["source_sha256"]:
            raise HistoryError("Source bytes do not match the imported snapshot")
        path = Path(path).absolute()
        # Exclusive leaf creation prevents adopting or overwriting an existing project.
        path.mkdir(mode=0o700, parents=False, exist_ok=False)
        store = object.__new__(cls)
        store.path, store.repository = path, path / "history.git"
        store.hooks, store.source_path = path / "empty-hooks", path / "source.step"
        store.git = shutil.which("git")
        try:
            if store.git is None:
                raise HistoryError("Git is required for checkpoints")
            store.hooks.mkdir(mode=0o700)
            with store.source_path.open("xb") as file:
                file.write(source)
                file.flush()
                os.fsync(file.fileno())
            store.source_path.chmod(0o400)
            store._run("init", "--bare", "--object-format=sha1", "--template=" + str(store.hooks),
                       str(store.repository), repo=False)
            with store._lock():
                source_oid = store._blob(source)
                manifest_oid = store._blob(canonical(manifest))
                state = make_state(manifest)
                request_id = str(uuid4())
                request = {"request_id": request_id, "parent": None, "state_digest": digest(state),
                           "message": "Import STEP source"}
                commit = store._make_commit(source_oid, manifest_oid, state, request)
                request_ref = store._request_ref(request_id)
                receipt = store._blob(canonical({"request": request, "checkpoint_id": commit}))
                store._update_refs(f"create {HEAD} {commit}\ncreate {ROOT} {commit}\ncreate {request_ref} {receipt}\n")
            return cls(path)
        except BaseException:
            # An uncertain ref outcome leaves the project for explicit inspection.
            if store.repository.is_dir():
                try:
                    if store._ref(HEAD) is not None:
                        raise UncertainCheckpoint("Project initialization may have succeeded; reopen it before retrying")
                except UncertainCheckpoint:
                    raise
                except Exception:
                    raise UncertainCheckpoint("Could not verify project initialization; preserve and inspect its directory")
            shutil.rmtree(path)
            raise

    @property
    def manifest(self):
        return deepcopy(self._manifest)

    def _environment(self):
        # Do not inherit GIT_DIR, GIT_WORK_TREE, config-count overrides, credentials,
        # author identity, executable hooks, signing settings or external diff tools.
        return {"PATH": os.defpath, "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_NO_REPLACE_OBJECTS": "1", "GIT_TERMINAL_PROMPT": "0",
                "GIT_AUTHOR_NAME": "Daedalus", "GIT_AUTHOR_EMAIL": "local@localhost",
                "GIT_COMMITTER_NAME": "Daedalus", "GIT_COMMITTER_EMAIL": "local@localhost"}

    def _run(self, *args, input=None, repo=True, allow_failure=False):
        command = [self.git, "-c", "core.hooksPath=" + str(self.hooks),
                   "-c", "commit.gpgSign=false", "-c", "core.fsmonitor=false",
                   "-c", "core.attributesFile=" + os.devnull]
        if repo:
            command += ["--git-dir=" + str(self.repository)]
        command += list(args)
        try:
            result = subprocess.run(command, input=input, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=self.path, env=self._environment(), timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise HistoryError("Git operation failed or timed out") from error
        if result.returncode and not allow_failure:
            raise HistoryError("Git operation failed")
        return result

    @contextmanager
    def _lock(self):
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path / "project.lock", flags, 0o600)
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise HistoryError("Project is busy in another operation")
                    time.sleep(0.01)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _ref(self, ref):
        result = self._run("show-ref", "--verify", "--quiet", ref, allow_failure=True)
        if result.returncode:
            # Missing refs are exit 1; other errors are not a missing checkpoint.
            if result.returncode == 1:
                return None
            raise HistoryError("Cannot read checkpoint reference")
        return _oid(self._run("show-ref", "--verify", "--hash", ref).stdout.decode().strip())

    @staticmethod
    def _request_ref(request_id):
        return "refs/requests/" + identifier(request_id).replace("-", "")

    def _update_refs(self, updates):
        payload = ("start\n" + updates + "prepare\ncommit\n").encode()
        self._run("update-ref", "--stdin", input=payload)

    def _blob(self, data):
        return _oid(self._run("hash-object", "-w", "--stdin", "--no-filters", input=data).stdout.decode().strip())

    def _make_commit(self, source_oid, manifest_oid, state, request):
        entries = {"source.step": source_oid, "project.json": manifest_oid,
                   "placements.json": self._blob(canonical(state)), "request.json": self._blob(canonical(request))}
        tree_input = b"".join(f"100644 blob {oid}\t{name}\0".encode() for name, oid in sorted(entries.items()))
        tree_oid = _oid(self._run("mktree", "-z", input=tree_input).stdout.decode().strip())
        args = ["commit-tree", tree_oid]
        if request["parent"] is not None:
            args += ["-p", _oid(request["parent"])]
        return _oid(self._run(*args, input=(request["message"] + "\n").encode()).stdout.decode().strip())

    def _tree(self, commit):
        commit = _oid(commit)
        if self._run("cat-file", "-t", commit).stdout.strip() != b"commit":
            raise HistoryError("Checkpoint ID is not a commit")
        data = self._run("ls-tree", "-z", commit).stdout
        if len(data) > 4096:
            raise HistoryError("Unexpected checkpoint tree")
        result = {}
        for entry in data.rstrip(b"\0").split(b"\0"):
            metadata, name = entry.split(b"\t", 1)
            mode, kind, oid = metadata.split(b" ")
            name = name.decode("ascii")
            if mode != b"100644" or kind != b"blob" or name not in FILES or name in result:
                raise HistoryError("Unsupported checkpoint path or file mode")
            result[name] = _oid(oid.decode())
        if set(result) != FILES:
            raise HistoryError("Incomplete checkpoint tree")
        return result

    def _read_blob(self, oid, maximum=MAX_METADATA):
        size = int(self._run("cat-file", "-s", _oid(oid)).stdout)
        if not 0 <= size <= maximum:
            raise HistoryError("Stored object exceeds size limit")
        result = self._run("cat-file", "blob", oid).stdout
        if len(result) != size:
            raise HistoryError("Incomplete object read")
        return result

    def _load_identity(self):
        root = self._ref(ROOT)
        if root is None:
            raise HistoryError("Missing project source reference")
        tree = self._tree(root)
        self._source_oid, self._manifest_oid = tree["source.step"], tree["project.json"]
        self._manifest = validate_manifest(_decode(self._read_blob(self._manifest_oid)))
        source = self._read_blob(self._source_oid, MAX_SOURCE)
        if hashlib.sha256(source).hexdigest() != self.manifest["source_sha256"]:
            raise HistoryError("Stored source digest does not match project manifest")
        head = self._ref(HEAD)
        if head is None:
            raise HistoryError("Missing project checkpoint head")
        self._read_state(head)

    def _verify_source(self):
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        try:
            with os.fdopen(os.open(self.source_path, flags), "rb") as source:
                details = os.fstat(source.fileno())
                if not stat.S_ISREG(details.st_mode) or not 0 < details.st_size <= MAX_SOURCE:
                    raise HistoryError("Original source is not a supported regular file")
                actual = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError as error:
            raise HistoryError("Cannot read original source") from error
        if actual != self.manifest["source_sha256"]:
            raise HistoryError("Original source was modified; project is read-only until repaired")

    def _read_state(self, commit):
        tree = self._tree(commit)
        if tree["source.step"] != self._source_oid or tree["project.json"] != self._manifest_oid:
            raise HistoryError("Checkpoint changed the immutable source or project identity")
        return validate_state(self.manifest, _decode(self._read_blob(tree["placements.json"])))

    def _known(self, commit, head):
        _oid(commit)
        result = self._run("merge-base", "--is-ancestor", commit, head, allow_failure=True)
        if result.returncode:
            raise HistoryError("Checkpoint is not in this project's current history")

    def head(self):
        with self._lock():
            self._verify_source()
            result = self._ref(HEAD)
            if result is None:
                raise HistoryError("Missing checkpoint head")
            return result

    def read(self, commit=None):
        with self._lock():
            self._verify_source()
            head = self._ref(HEAD)
            if head is None:
                raise HistoryError("Missing checkpoint head")
            commit = head if commit is None else _oid(commit)
            self._known(commit, head)
            return self._read_state(commit)

    def save(self, state, *, expected_head: str, message: str, request_id: str, replay_only=False):
        expected_head = _oid(expected_head)
        request_ref = self._request_ref(request_id)
        if not isinstance(message, str) or not message.strip() or len(message) > 1000 or "\0" in message:
            raise HistoryError("Checkpoint message must contain 1-1000 characters without NUL")
        state = validate_state(self.manifest, state)
        request = {"request_id": request_id, "parent": expected_head,
                   "state_digest": digest(state), "message": message}
        with self._lock():
            self._verify_source()
            head = self._ref(HEAD)
            if head is None:
                raise HistoryError("Missing checkpoint head")
            previous = self._ref(request_ref)
            if previous is not None:
                receipt = _decode(self._read_blob(previous))
                if not isinstance(receipt, dict) or set(receipt) != {"request", "checkpoint_id"}:
                    raise HistoryError("Malformed checkpoint request receipt")
                checkpoint = _oid(receipt["checkpoint_id"])
                self._known(checkpoint, head)
                if receipt["request"] != request:
                    raise HistoryError("Request ID was already used for different checkpoint data")
                if canonical(self._read_state(checkpoint)) != canonical(state):
                    raise HistoryError("Checkpoint retry data does not match stored state")
                return SaveResult(checkpoint, head, False, True)
            if replay_only:
                raise StaleCheckpoint("Another session saved this project. Reopen its latest checkpoint before saving")
            if head != expected_head:
                raise StaleCheckpoint("History changed; refresh before saving")
            created = canonical(self._read_state(head)) != canonical(state)
            commit = self._make_commit(self._source_oid, self._manifest_oid, state, request) if created else head
            receipt_oid = self._blob(canonical({"request": request, "checkpoint_id": commit}))
            head_update = f"update {HEAD} {commit} {expected_head}\n" if created else f"verify {HEAD} {expected_head}\n"
            try:
                self._update_refs(head_update + f"create {request_ref} {receipt_oid}\n")
            except HistoryError as error:
                # The head/ref verification and durable request receipt are atomic,
                # including successful saves with no changes and no new commit.
                try:
                    observed, retry_receipt = self._ref(HEAD), self._ref(request_ref)
                except HistoryError as verification_error:
                    raise UncertainCheckpoint("Checkpoint result is uncertain; inspect history before retrying") from verification_error
                if observed == commit and retry_receipt == receipt_oid:
                    return SaveResult(commit, commit, created)
                if retry_receipt is None and observed == expected_head:
                    raise HistoryError("Checkpoint was not saved; previous head is intact") from error
                if retry_receipt is None and observed not in (None, commit):
                    raise StaleCheckpoint("History changed while saving") from error
                raise UncertainCheckpoint("Checkpoint result is uncertain; inspect history before retrying") from error
            return SaveResult(commit, commit, created)

    def history(self, *, limit=50, skip=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(skip) is not int or not 0 <= skip <= 10000:
            raise HistoryError("Invalid history page")
        with self._lock():
            head = self._ref(HEAD)
            if head is None:
                raise HistoryError("Missing checkpoint head")
            commits = self._run("rev-list", "--first-parent", f"--max-count={limit}", f"--skip={skip}", head).stdout.decode().splitlines()
            result = []
            for commit in commits:
                tree = self._tree(commit)
                self._read_state(commit)
                request = _decode(self._read_blob(tree["request.json"]))
                result.append({"id": commit, "parent": request["parent"], "message": request["message"]})
            return {"head_id": head, "checkpoints": result}

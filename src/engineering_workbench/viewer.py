"""Trusted-local, single-document STEP viewer. No public network deployment."""

from __future__ import annotations

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import mimetypes
from pathlib import Path
import secrets
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from .cad import StepAssembly, create_two_instance_fixture
from .mesh import display_meshes

MAX_IMPORT_BYTES = 20 * 1024 * 1024
MAX_JSON_BYTES = 64 * 1024
STATIC = Path(__file__).with_name("static")


class RequestError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class ViewerSession:
    """One transient document, with immutable baseline and serialized OCCT work."""

    def __init__(self):
        self._temporary = tempfile.TemporaryDirectory(prefix="daedalus-session-")
        self.directory = Path(self._temporary.name)
        self.lock = threading.RLock()
        self.csrf_token = secrets.token_urlsafe(32)
        self.revision = 0
        self.assembly = None
        self.model = None

    def close(self):
        with self.lock:
            self.assembly = None
            self.model = None
            self._temporary.cleanup()

    def state(self):
        with self.lock:
            return {"csrf_token": self.csrf_token, "revision": self.revision, "model": deepcopy(self.model)}

    def import_step(self, data: bytes | None, name: str, expected_revision=None):
        with self.lock:
            if expected_revision is not None:
                self._check_revision(expected_revision)
            # A failed replacement cannot destroy the currently displayed model.
            candidate = Path(tempfile.mkdtemp(prefix="import-", dir=self.directory))
            source = candidate / "source.step"
            try:
                if data is None:
                    create_two_instance_fixture(source)
                else:
                    if not data or len(data) > MAX_IMPORT_BYTES:
                        raise RequestError(413 if data else 400, "Choose a non-empty STEP file up to 20 MiB")
                    source.write_bytes(data)
                assembly = StepAssembly.load(source)
                snapshot = assembly.snapshot()
                meshes = display_meshes(assembly)
                for item in snapshot["occurrences"]:
                    item["baseline_matrix_3x4"] = deepcopy(item["matrix_3x4"])
                    item["offset_mm"] = [0.0, 0.0, 0.0]
                snapshot.update(name=name, meshes=meshes)
            except Exception:
                import shutil
                shutil.rmtree(candidate)
                raise
            previous = self.assembly
            self.assembly, self.model = assembly, snapshot
            self.revision += 1
            if previous is not None:
                import shutil
                shutil.rmtree(previous.source_path.parent)
            return self.state()

    def move(self, request: dict):
        with self.lock:
            if self.assembly is None:
                raise RequestError(409, "Import a STEP assembly first")
            self._check_revision(request.get("revision"))
            occurrence_id = request.get("occurrence_id")
            item = next((item for item in self.model["occurrences"] if item["id"] == occurrence_id), None)
            if item is None:
                raise RequestError(400, "Unknown component")
            offset = request.get("offset_mm")
            if not isinstance(offset, list) or len(offset) != 3 or any(
                type(value) not in (int, float) or abs(value) > 1_000_000 or not math.isfinite(value)
                for value in offset
            ):
                raise RequestError(400, "Offsets must be three finite numbers within ±1,000,000 mm")
            baseline = item["baseline_matrix_3x4"]
            current = item["matrix_3x4"]
            delta = [baseline[index][3] + offset[index] - current[index][3] for index in range(3)]
            self.assembly.translate_occurrence(occurrence_id, delta)
            fresh = self.assembly.snapshot()
            previous = {item["id"]: item for item in self.model["occurrences"]}
            for item in fresh["occurrences"]:
                old = previous[item["id"]]
                item["baseline_matrix_3x4"] = old["baseline_matrix_3x4"]
                item["offset_mm"] = list(offset) if item["id"] == occurrence_id else old["offset_mm"]
            fresh.update(name=self.model["name"], meshes=self.model["meshes"])
            self.model = fresh
            self.revision += 1
            return self.state()

    def _check_revision(self, revision):
        if type(revision) is not int:
            raise RequestError(400, "Revision must be an integer")
        if revision != self.revision:
            raise RequestError(409, "Model changed; refresh before changing or replacing it")

    def export_step(self, expected_revision=None):
        with self.lock:
            if self.assembly is None:
                raise RequestError(409, "Import a STEP assembly first")
            if expected_revision is not None:
                self._check_revision(expected_revision)
            with tempfile.TemporaryDirectory(prefix="export-", dir=self.directory) as directory:
                destination = Path(directory) / "modified.step"
                self.assembly.export_step(destination)
                return destination.read_bytes()


class ViewerServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True

    def __init__(self, address):
        self.session = ViewerSession()
        try:
            super().__init__(address, ViewerHandler)
        except Exception:
            self.session.close()
            raise

    def server_close(self):
        super().server_close()
        self.session.close()


class ViewerHandler(BaseHTTPRequestHandler):
    server_version = "Daedalus"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        # Avoid logging imported names, paths, tokens or model payloads.
        pass

    def _security(self, mutation=False):
        port = self.server.server_port
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        host = self.headers.get("Host", "")
        if len(self.headers.get_all("Host", [])) != 1 or host not in allowed:
            raise RequestError(403, "Only the local viewer address is allowed")
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            raise RequestError(403, "Cross-origin requests are not allowed")
        if self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
            raise RequestError(403, "Cross-site requests are not allowed")
        if mutation and not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), self.server.session.csrf_token):
            raise RequestError(403, "Missing or invalid request token; reload this page")

    def _send(self, status, data, content_type, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status, data):
        self._send(status, json.dumps(data, allow_nan=False).encode(), "application/json; charset=utf-8")

    def _handle_error(self, error):
        if isinstance(error, RequestError):
            self._json(error.status, {"error": str(error)})
        elif isinstance(error, (ValueError, KeyError)):
            self._json(400, {"error": str(error)})
        else:
            self._json(500, {"error": "The geometry operation failed; the input file was preserved"})

    def _read_body(self, maximum):
        if self.headers.get("Transfer-Encoding"):
            raise RequestError(400, "Transfer encoding is not supported")
        if len(self.headers.get_all("Content-Length", [])) != 1:
            raise RequestError(411, "Exactly one Content-Length is required")
        try:
            length = int(self.headers["Content-Length"])
        except (TypeError, ValueError):
            raise RequestError(411, "A valid Content-Length is required")
        if length < 0 or length > maximum:
            self.close_connection = True
            raise RequestError(413, "Request exceeds the permitted size")
        data = self.rfile.read(length)
        if len(data) != length:
            raise RequestError(400, "Incomplete request")
        return data

    def do_GET(self):
        try:
            self._security()
            path = urlsplit(self.path).path
            if path == "/api/state":
                self._json(200, self.server.session.state())
            elif path == "/api/export":
                revisions = parse_qs(urlsplit(self.path).query).get("revision", [])
                if len(revisions) != 1 or len(revisions[0]) > 16 or not revisions[0].isascii() or not revisions[0].isdecimal():
                    raise RequestError(400, "Export requires the displayed model revision")
                self._send(200, self.server.session.export_step(int(revisions[0])), "application/step", {"Content-Disposition": 'attachment; filename="daedalus-modified.step"'})
            else:
                relative = "index.html" if path == "/" else unquote(path.removeprefix("/static/"))
                if path != "/" and not path.startswith("/static/"):
                    raise RequestError(404, "Not found")
                file = (STATIC / relative).resolve()
                if not file.is_relative_to(STATIC.resolve()) or not file.is_file():
                    raise RequestError(404, "Not found")
                mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}.get(file.suffix) or mimetypes.guess_type(file.name)[0] or "application/octet-stream"
                self._send(200, file.read_bytes(), mime)
        except Exception as error:
            self._handle_error(error)

    def do_POST(self):
        try:
            self._security(mutation=True)
            path = urlsplit(self.path).path
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip()
            if path == "/api/import":
                if content_type != "application/octet-stream":
                    raise RequestError(415, "Upload STEP as application/octet-stream")
                name = unquote(self.headers.get("X-Filename", "assembly.step")).replace("\\", "/").rsplit("/", 1)[-1]
                if not name or len(name) > 255 or any(ord(character) < 32 for character in name):
                    raise RequestError(400, "Invalid STEP filename")
                if Path(name).suffix.lower() not in (".step", ".stp"):
                    raise RequestError(400, "Choose a .step or .stp file")
                revision = self.headers.get("X-Revision", "")
                if len(self.headers.get_all("X-Revision", [])) != 1 or len(revision) > 16 or not revision.isascii() or not revision.isdecimal():
                    raise RequestError(400, "Upload requires the current X-Revision header")
                state = self.server.session.import_step(self._read_body(MAX_IMPORT_BYTES), name, int(revision))
            elif path in ("/api/move", "/api/demo"):
                if content_type != "application/json":
                    raise RequestError(415, "Send application/json")
                body = self._read_body(MAX_JSON_BYTES)
                try:
                    request = json.loads(body)
                except (ValueError, UnicodeError):
                    raise RequestError(400, "Invalid JSON request")
                if not isinstance(request, dict):
                    raise RequestError(400, "Request must be a JSON object")
                if path == "/api/move":
                    state = self.server.session.move(request)
                else:
                    if type(request.get("revision")) is not int:
                        raise RequestError(400, "Revision must be an integer")
                    state = self.server.session.import_step(None, "Two-instance demo.step", request["revision"])
            else:
                raise RequestError(404, "Not found")
            self._json(200, state)
        except Exception as error:
            self.close_connection = True
            self._handle_error(error)


def make_server(host="127.0.0.1", port=8000):
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("This experimental viewer binds only to loopback; use SSH forwarding for remote access")
    return ViewerServer((host, port))


def serve(port=8000):
    with make_server(port=port) as server:
        print(f"Daedalus Focus View: http://127.0.0.1:{server.server_port}", flush=True)
        print("Local session only. Export changes before closing; Ctrl+C stops the server.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass

"""vidlib's web server: the UI's page, a small JSON API, and a live event stream.

stdlib only (ThreadingHTTPServer), listening on 127.0.0.1. Being local isn't protection on
its own — any web page open in the browser can send requests to localhost — so:

- the Host header must name this server (127.0.0.1 or localhost, this port), which defeats
  DNS rebinding;
- a request carrying an Origin must come from this server's own pages;
- every API write takes a JSON body, and a cross-site JSON POST needs a CORS preflight this
  server never answers.

Live updates are Server-Sent Events on /api/events: the manager's state, sent whenever its
version changes (at most every EVENT_INTERVAL seconds, since progress lines arrive far
faster than anyone reads them), with a comment every few seconds to keep the line open.
"""

from __future__ import annotations

import errno
import json
import re
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any
from urllib.parse import parse_qs, urlsplit

from toolboxcli._common.console import warn
from toolboxcli.vidlib import naming
from toolboxcli.vidlib.downloads import DownloadManager
from toolboxcli.vidlib.library import Library, LibraryError
from toolboxcli.vidlib.status import PipelineStatus

DEFAULT_PORT = 8642
# How many ports from the requested one bind_server tries before giving up: enough for every
# library on the Mac to have a vidlib running at once.
PORT_TRIES = 20
EVENT_INTERVAL = 0.25
KEEPALIVE = 10.0
MAX_BODY = 1_000_000

_STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
}


class ApiError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status


# ── naming, as the API returns it ────────────────────────────────────


def _part_json(part: naming.NamedPart) -> dict[str, str]:
    return {"text": part.text, "match": part.match, "suggestion": part.suggestion}


def draft_json(draft: naming.Draft) -> dict[str, Any]:
    return {
        "people": [_part_json(p) for p in draft.people],
        "title": draft.title,
        "collection": _part_json(draft.collection) if draft.collection else None,
        "stem": draft.stem,
        "problems": draft.problems(),
        "complete": draft.complete,
    }


def name_from_title(title: str, roles: list[str] | None,
                    vocab: naming.Vocabulary) -> dict[str, Any]:
    """Segments, their roles (guessed, or as given) and the name they make."""
    segments = naming.split_segments(title)
    if roles is not None and len(roles) == len(segments) and all(r in naming.ROLES
                                                                  for r in roles):
        chosen: list[naming.Role] = [next(r for r in naming.ROLES if r == given)
                                     for given in roles]
    else:
        chosen = naming.guess_roles(segments, vocab)
    draft = naming.compose(segments, chosen, vocab)
    return {
        "segments": [{"text": s.text, "site": s.site, "role": r}
                     for s, r in zip(segments, chosen, strict=True)],
        **draft_json(draft),
    }


def _found(title: str, vocab: naming.Vocabulary, people: list[str],
           collection: str) -> list[dict[str, str]]:
    """Known people and collections sitting in a title, for the forms' pills."""
    return [{"kind": f.kind, "name": f.name, "title_without": f.title_without}
            for f in naming.find_in_title(title, vocab, people, collection)]


# ── the request handler ──────────────────────────────────────────────


class Handler(BaseHTTPRequestHandler):
    server: VidlibServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:   # noqa: A002
        pass   # the terminal belongs to the pipeline's display

    # ── plumbing ──

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _check_origin(self) -> None:
        if self.headers.get("Host", "") not in self._allowed_hosts():
            raise ApiError(HTTPStatus.FORBIDDEN, "unexpected Host header")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in self._allowed_hosts()}:
            raise ApiError(HTTPStatus.FORBIDDEN, "cross-origin requests are not allowed")

    def _body(self) -> dict[str, Any]:
        if self.headers.get_content_type() != "application/json":
            raise ApiError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "send JSON")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ApiError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError as exc:
            raise ApiError(HTTPStatus.BAD_REQUEST, f"bad JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError(HTTPStatus.BAD_REQUEST, "expected a JSON object")
        return data  # pyright: ignore[reportUnknownVariableType]

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(data).encode(), "application/json")

    def _handle(self, method: str) -> None:
        try:
            self._check_origin()
            self._route(method)
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except LibraryError as exc:
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except KeyError:
            self._json({"error": "no such download"}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self._json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:  # noqa: BLE001 — the page gets an answer, the loop goes on
            warn(f"Web page: {method} {self.path}: {type(exc).__name__}: {exc}")
            self._json({"error": f"{type(exc).__name__}: {exc}"},
                       HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")

    # ── routes ──

    def _route(self, method: str) -> None:
        url = urlsplit(self.path)
        path, query = url.path, parse_qs(url.query)
        mgr = self.server.manager

        if method == "GET" and path in _STATIC:
            name, content_type = _STATIC[path]
            self._send(HTTPStatus.OK, files("toolboxcli.vidlib").joinpath(
                "data", "web", name).read_bytes(), content_type)
            return
        if method == "GET" and path == "/api/state":
            self._json(self.server.state())
            return
        if method == "GET" and path == "/api/events":
            self._events()
            return
        if method == "GET" and path == "/api/downloads/seen":
            self._json({"seen": mgr.seen((query.get("url") or [""])[0])})
            return
        if method == "GET" and path == "/api/suggest":
            kind = (query.get("kind") or ["people"])[0]
            if kind not in ("people", "collections"):
                raise ApiError(HTTPStatus.BAD_REQUEST, "kind is people or collections")
            q = (query.get("q") or [""])[0]
            self._json({"suggestions": mgr.vocabulary().suggest(
                "people" if kind == "people" else "collections", q)})
            return
        if method == "POST" and path == "/api/name":
            body = self._body()
            roles: Any = body.get("roles")
            vocab = mgr.vocabulary()
            data = name_from_title(str(body.get("title") or ""),
                                   roles if isinstance(roles, list) else None, vocab)
            data["same_as"] = self.server.library.same_as(data["stem"])
            data["found"] = _found(data["title"], vocab, [p["text"] for p in data["people"]],
                                   data["collection"]["text"] if data["collection"] else "")
            self._json(data)
            return
        if method == "POST" and path == "/api/name/fields":
            body = self._body()
            vocab = mgr.vocabulary()
            fields = (str(body.get("people") or ""), str(body.get("title") or ""),
                      str(body.get("collection") or ""))
            # The rename form previews what a rename will do, blanks filled with "Unknown".
            if body.get("fill_unknown") is True:
                fields = naming.fill_unknown(*fields)
            title = fields[1]
            draft = naming.from_fields(*fields, vocab)
            data = draft_json(draft)
            # Known people and collections sitting in the title: the rename form offers them.
            data["found"] = _found(title, vocab, [p.text for p in draft.people],
                                   draft.collection.text if draft.collection else "")
            renaming: Any = body.get("path")
            data["same_as"] = self.server.library.same_as(
                draft.stem, renaming if isinstance(renaming, str) else None)
            self._json(data)
            return
        if method == "POST" and path == "/api/downloads":
            body = self._body()
            dl = mgr.add(str(body.get("url") or ""), str(body.get("name") or ""),
                         str(body.get("page_title") or ""))
            self._json({"id": dl.id}, HTTPStatus.CREATED)
            return
        if method == "POST" and path == "/api/downloads/bulk":
            body = self._body()
            urls: Any = body.get("urls")
            if not isinstance(urls, list):
                raise ApiError(HTTPStatus.BAD_REQUEST, "urls must be a list")
            added = mgr.add_bulk([str(u) for u in urls])  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            self._json({"added": len(added)}, HTTPStatus.CREATED)
            return
        if path.startswith("/api/library") or path in ("/api/thumb", "/api/strip"):
            self._library(method, path, query)
            return
        if method == "POST" and path in ("/api/pipeline/pause", "/api/pipeline/stop",
                                         "/api/pipeline/resume"):
            self._body()
            pipeline = self.server.pipeline
            if pipeline is None:
                raise ApiError(HTTPStatus.CONFLICT, "nothing is encoded in download-only mode")
            {"pause": pipeline.pause, "stop": pipeline.stop_now,
             "resume": pipeline.resume}[path.rsplit("/", 1)[1]]()
            self._json({"ok": True})
            return
        if method == "POST" and path == "/api/downloads/clear":
            self._body()
            self._json({"removed": mgr.clear_finished()})
            return
        if method == "POST" and path == "/api/downloads/import":
            self._body()
            moved, problems = mgr.import_waiting()
            self._json({"moved": moved, "problems": problems})
            return

        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "downloads"] and parts[2].isdigit():
            download_id = int(parts[2])
            action = parts[3] if len(parts) == 4 else None
            if method == "DELETE" and action is None:
                mgr.remove(download_id)
                self._json({"ok": True})
                return
            if method == "POST" and action in ("retry", "cancel", "name"):
                body = self._body()
                if action == "retry":
                    mgr.retry(download_id)
                elif action == "cancel":
                    mgr.cancel(download_id)
                else:
                    title: Any = body.get("page_title")
                    mgr.name_item(download_id, str(body.get("name") or ""),
                                  title if isinstance(title, str) else None)
                self._json({"ok": True})
                return
        raise ApiError(HTTPStatus.NOT_FOUND, f"no such endpoint: {method} {path}")

    def _library(self, method: str, path: str, query: dict[str, list[str]]) -> None:
        lib = self.server.library

        def arg(name: str) -> str:
            return (query.get(name) or [""])[0]

        if method == "GET" and path == "/api/library":
            try:
                offset = max(0, int(arg("offset") or 0))
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "offset is a number") from exc
            self._json(lib.query(arg("view") or "all", arg("q"), arg("person"),
                                 arg("collection"), arg("resolution"), arg("tag"), offset))
            return
        if method == "GET" and path == "/api/library/video":
            self._json(lib.video(arg("path")))
            return
        if method == "GET" and path in ("/api/thumb", "/api/strip"):
            try:
                image = (lib.thumbnail if path == "/api/thumb" else lib.filmstrip)(arg("path"))
            except LibraryError as exc:
                raise ApiError(HTTPStatus.NOT_FOUND, str(exc)) from exc
            self._send(HTTPStatus.OK, image, "image/jpeg")
            return
        if method == "GET" and path == "/api/library/parts":
            self._json({"groups": lib.part_groups()})
            return
        if method == "POST" and path == "/api/library/parts/dismiss":
            ids: Any = self._body().get("ids")
            if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):  # pyright: ignore[reportUnknownVariableType]
                raise ApiError(HTTPStatus.BAD_REQUEST, "ids must be a list of numbers")
            lib.dismiss_parts(ids)  # pyright: ignore[reportUnknownArgumentType]
            self._json({"ok": True})
            return
        if method == "POST" and path in ("/api/library/merge/plan", "/api/library/merge"):
            body = self._body()
            ids: Any = body.get("ids")
            if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):  # pyright: ignore[reportUnknownVariableType]
                raise ApiError(HTTPStatus.BAD_REQUEST, "ids must be a list of numbers")
            if path.endswith("/plan"):
                self._json(lib.merge_plan(ids))  # pyright: ignore[reportUnknownArgumentType]
            else:
                self._json(lib.queue_merge(ids, str(body.get("people") or ""),  # pyright: ignore[reportUnknownArgumentType]
                                           str(body.get("title") or ""),
                                           str(body.get("collection") or "")),
                           HTTPStatus.CREATED)
            return
        merge_action = re.fullmatch(r"/api/library/merge/(\d+)/(discard|forget)", path)
        if method == "POST" and merge_action:
            self._body()
            job_id = int(merge_action.group(1))
            if merge_action.group(2) == "discard":
                lib.discard_merge(job_id)
            elif self.server.pipeline is not None:
                self.server.pipeline.forget_merge(job_id)
            self._json({"ok": True})
            return
        if method == "GET" and path == "/api/library/review":
            self._json({"items": lib.review_items()})
            return
        if method == "POST" and path == "/api/library/decide":
            body = self._body()
            lib.decide(str(body.get("path") or ""), str(body.get("keep") or ""),
                       str(body.get("people") or ""), str(body.get("title") or ""),
                       str(body.get("collection") or ""))
            self._json({"ok": True})
            return
        if method == "POST" and path in ("/api/library/open", "/api/library/reveal",
                                         "/api/library/rename", "/api/library/trash"):
            body = self._body()
            target = str(body.get("path") or "")
            action = path.rsplit("/", 1)[1]
            if action == "rename":
                self._json(lib.rename(target, str(body.get("people") or ""),
                                      str(body.get("title") or ""),
                                      str(body.get("collection") or "")))
                return
            if action == "trash":
                lib.trash(target)
            else:
                lib.open(target, reveal=action == "reveal")
            self._json({"ok": True})
            return
        raise ApiError(HTTPStatus.NOT_FOUND, f"no such endpoint: {method} {path}")

    def _events(self) -> None:
        mgr = self.server.manager
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.close_connection = True
        seen = -1
        last_sent = 0.0
        while not self.server.stopping.is_set():
            version = mgr.wait_for_change(seen, KEEPALIVE)
            if version == seen:
                self.wfile.write(b": keepalive\n\n")
            else:
                pause = EVENT_INTERVAL - (time.monotonic() - last_sent)
                if pause > 0:
                    time.sleep(pause)
                state = self.server.state()
                seen = state["version"]
                self.wfile.write(f"data: {json.dumps(state)}\n\n".encode())
                last_sent = time.monotonic()
            self.wfile.flush()


class VidlibServer(ThreadingHTTPServer):
    daemon_threads = True   # an open event stream never holds up quitting
    # SO_REUSEADDR only skips TIME_WAIT on POSIX, but on Windows it lets a second server bind
    # a port that's already listening — the busy port would never be noticed.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, manager: DownloadManager, port: int = DEFAULT_PORT,
                 pipeline: PipelineStatus | None = None) -> None:
        self.manager = manager
        self.pipeline = pipeline   # None in download-only mode
        self.library = Library(manager.library, manager.db, manager.lock, pipeline)
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", port), Handler)

    def state(self) -> dict[str, Any]:
        """Everything the page shows: the downloads, and the pipeline when there is one."""
        state = self.manager.snapshot()
        state["pipeline"] = self.pipeline.snapshot() if self.pipeline is not None else None
        state["review_count"] = self.library.review_count()
        return state

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that hangs up or sends garbage isn't worth a traceback over the pipeline's
        display; anything else gets one line."""
        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionError | TimeoutError):
            return
        warn(f"Web page: {type(exc).__name__}: {exc}")

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.serve_forever, name="vidlib-web", daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self.stopping.set()
        self.shutdown()
        self.server_close()


# What a busy port raises: EADDRINUSE on POSIX; on Windows the socket's own WSAEADDRINUSE.
_PORT_BUSY = {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)}


def port_busy(exc: OSError) -> bool:
    return exc.errno in _PORT_BUSY


def bind_server(manager: DownloadManager, port: int = DEFAULT_PORT,
                pipeline: PipelineStatus | None = None,
                tries: int = PORT_TRIES) -> VidlibServer:
    """A server on *port*, or on the next free one after it when it's taken (another
    library's vidlib, or anything else). Any error but a busy port is raised as it is, and
    so is the last busy port's when none of the *tries* ports was free."""
    last = min(port + tries, 65536) - 1
    for candidate in range(port, last + 1):
        try:
            return VidlibServer(manager, candidate, pipeline=pipeline)
        except OSError as exc:
            if not port_busy(exc) or candidate == last:
                raise
    raise AssertionError("unreachable: the last try re-raises")

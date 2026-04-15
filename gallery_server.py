#!/usr/bin/env python3

from __future__ import annotations

import json
import mimetypes
import subprocess
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


APP_DIR = Path(__file__).resolve().parent
SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "Media Gallery"
INDEX_FILE = APP_DIR / "index.html"
DATA_JSON_FILE = SUPPORT_DIR / "gallery-data.json"
FALLBACK_DATA_JSON_FILE = APP_DIR / "gallery-data.json"
SYNC_SCRIPT = SUPPORT_DIR / "sync_gallery_data.py"
THUMB_DIR = SUPPORT_DIR / "cache" / "thumbs"
FALLBACK_THUMB_DIR = APP_DIR / "cache" / "thumbs"
HOST = "127.0.0.1"
PORT = 8765
VOLUMES_DIR = Path("/Volumes")
AUTO_SYNC_INTERVAL_SECONDS = 2
MIN_SYNC_GAP_SECONDS = 1

SEMANTIC_QUERY_EXPANSIONS = {
    "face": ["face", "person", "man", "woman", "people", "portrait", "selfie", "headshot"],
    "person": ["person", "people", "face", "man", "woman", "portrait", "selfie", "headshot"],
    "portrait": ["portrait", "selfie", "headshot", "face", "person", "people", "man", "woman"],
    "selfie": ["selfie", "portrait", "headshot", "face", "person", "people", "man", "woman"],
    "headshot": ["headshot", "portrait", "selfie", "face", "person", "people", "man", "woman"],
    "man": ["man", "person", "people", "face", "portrait", "selfie", "headshot"],
    "woman": ["woman", "person", "people", "face", "portrait", "selfie", "headshot"],
}

SYNC_LOCK = threading.Lock()
SYNC_PROCESS: subprocess.Popen[str] | None = None
LAST_VOLUME_SIGNATURE: tuple[str, ...] | None = None
LAST_ROOT_SIGNATURE: tuple[tuple[str, int], ...] | None = None
LAST_SYNC_TRIGGER_AT = 0.0


def build_collections(media_items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    for item in media_items:
        folder = str(item.get("folder", ""))
        if not folder:
            continue
        grouped.setdefault(folder, []).append(item)

    collections: list[dict[str, object]] = []
    for folder, items in grouped.items():
        items.sort(key=lambda item: (-int(item.get("timestamp", 0)), str(item.get("name", "")).lower()))
        latest = items[0]
        collections.append(
            {
                "id": folder,
                "folder": folder,
                "folderName": latest.get("folderName", Path(folder).name or folder),
                "folderRelative": latest.get("folderRelative", "."),
                "root": latest.get("root", ""),
                "rootName": latest.get("rootName", ""),
                "count": len(items),
                "latestPath": latest.get("path", ""),
                "latestKind": latest.get("kind", ""),
                "latestTimestamp": latest.get("timestamp", 0),
                "latestModified": latest.get("modified", ""),
            }
        )

    collections.sort(key=lambda item: (-int(item.get("latestTimestamp", 0)), str(item.get("folderName", "")).lower()))
    return collections


def prune_missing_media(payload: dict[str, object]) -> dict[str, object]:
    media_items = payload.get("media", [])
    if not isinstance(media_items, list):
        return payload

    filtered_media = [
        item for item in media_items
        if isinstance(item, dict) and Path(str(item.get("path", ""))).is_file()
    ]
    if len(filtered_media) == len(media_items):
        return payload

    updated = dict(payload)
    updated["media"] = filtered_media
    updated["collections"] = build_collections(filtered_media)
    return updated


def load_payload() -> dict[str, object]:
    def read_payload(path: Path) -> dict[str, object]:
        if not path.exists():
            return {"roots": [], "media": [], "collections": []}
        return json.loads(path.read_text(encoding="utf-8"))

    primary = read_payload(DATA_JSON_FILE)
    fallback = read_payload(FALLBACK_DATA_JSON_FILE)

    def root_signal_count(payload: dict[str, object]) -> int:
        roots = payload.get("roots", [])
        return sum(
            1
            for root in roots
            if isinstance(root, dict) and (root.get("status") or root.get("errors") or root.get("kind"))
        )

    def media_signal_count(payload: dict[str, object]) -> int:
        media = payload.get("media", [])
        return sum(
            1
            for item in media
            if isinstance(item, dict) and (
                item.get("width")
                or item.get("height")
                or item.get("durationSeconds") is not None
            )
        )

    primary_media_count = len(primary.get("media", []))
    fallback_media_count = len(fallback.get("media", []))
    if primary_media_count > fallback_media_count:
        media_source = primary
    elif fallback_media_count > primary_media_count:
        media_source = fallback
    else:
        media_source = primary if media_signal_count(primary) >= media_signal_count(fallback) else fallback
    roots_source = primary if root_signal_count(primary) >= root_signal_count(fallback) else fallback

    merged = dict(media_source)
    merged["roots"] = roots_source.get("roots", media_source.get("roots", []))
    merged["indexSource"] = "support" if media_source is primary else "project"
    return prune_missing_media(merged)


def mounted_volume_signature() -> tuple[str, ...]:
    if not VOLUMES_DIR.exists():
        return ()
    names: list[str] = []
    for volume in sorted(VOLUMES_DIR.iterdir()):
        if volume.name.startswith(".") or volume.is_symlink():
            continue
        names.append(str(volume.resolve()))
    return tuple(names)


def watched_root_signature() -> tuple[tuple[str, int], ...]:
    roots = [
        Path.home() / "Desktop",
        Path.home() / "Pictures",
        Path.home() / "Downloads",
        Path.home() / "Movies",
    ]
    signature: list[tuple[str, int]] = []
    for root in roots:
        try:
            signature.append((str(root.resolve()), root.stat().st_mtime_ns))
        except OSError:
            continue
    return tuple(signature)


def trigger_background_sync(force: bool = False) -> bool:
    global SYNC_PROCESS, LAST_SYNC_TRIGGER_AT

    if not SYNC_SCRIPT.exists():
        return False

    with SYNC_LOCK:
        if SYNC_PROCESS is not None and SYNC_PROCESS.poll() is None:
            return False
        if not force and time.monotonic() - LAST_SYNC_TRIGGER_AT < MIN_SYNC_GAP_SECONDS:
            return False

        SYNC_PROCESS = subprocess.Popen(
            ["/usr/bin/python3", str(SYNC_SCRIPT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        LAST_SYNC_TRIGGER_AT = time.monotonic()
        return True


def maybe_refresh_for_live_changes() -> None:
    global LAST_VOLUME_SIGNATURE, LAST_ROOT_SIGNATURE

    current_volume_signature = mounted_volume_signature()
    current_root_signature = watched_root_signature()

    if LAST_VOLUME_SIGNATURE is None:
        LAST_VOLUME_SIGNATURE = current_volume_signature
    elif current_volume_signature != LAST_VOLUME_SIGNATURE:
        LAST_VOLUME_SIGNATURE = current_volume_signature
        LAST_ROOT_SIGNATURE = current_root_signature
        trigger_background_sync(force=True)
        return

    if LAST_ROOT_SIGNATURE is None:
        LAST_ROOT_SIGNATURE = current_root_signature
    elif current_root_signature != LAST_ROOT_SIGNATURE:
        LAST_ROOT_SIGNATURE = current_root_signature
        trigger_background_sync(force=True)
        return

    if time.monotonic() - LAST_SYNC_TRIGGER_AT >= AUTO_SYNC_INTERVAL_SECONDS:
        trigger_background_sync()


def search_roots() -> list[str]:
    payload = load_payload()
    roots = payload.get("roots", [])
    return [
        str(root["path"])
        for root in roots
        if isinstance(root, dict) and root.get("path") and root.get("status") != "unavailable"
    ]


def spotlight_search(term: str) -> list[str]:
    raw_query = term.strip()
    if not raw_query:
        return []

    queries = SEMANTIC_QUERY_EXPANSIONS.get(raw_query.lower(), [raw_query])
    roots = search_roots()
    seen: list[str] = []

    for root in roots:
        for query in queries:
            result = subprocess.run(
                ["mdfind", "-onlyin", root, query],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                candidate = line.strip()
                if candidate and not candidate.startswith("202") and candidate not in seen:
                    seen.append(candidate)
    return seen


class GalleryHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self.serve_file(INDEX_FILE, "text/html; charset=utf-8")
            return
        if parsed.path == "/gallery-data.js":
            self.serve_file(APP_DIR / "gallery-data.js", "application/javascript; charset=utf-8")
            return
        if parsed.path == "/api/library":
            maybe_refresh_for_live_changes()
            self.send_json(load_payload())
            return
        if parsed.path == "/api/search":
            term = parse_qs(parsed.query).get("q", [""])[0]
            self.send_json({"paths": spotlight_search(term)})
            return
        if parsed.path == "/thumb":
            raw_path = parse_qs(parsed.query).get("path", [""])[0]
            self.serve_thumbnail(raw_path)
            return
        if parsed.path == "/media":
            raw_path = parse_qs(parsed.query).get("path", [""])[0]
            self.serve_media(raw_path)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/media":
            raw_path = parse_qs(parsed.query).get("path", [""])[0]
            self.serve_media(raw_path, head_only=True)
            return
        if parsed.path == "/thumb":
            raw_path = parse_qs(parsed.query).get("path", [""])[0]
            self.serve_thumbnail(raw_path, head_only=True)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/refresh":
            result = subprocess.run(
                ["/usr/bin/python3", str(SYNC_SCRIPT)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self.send_json(
                    {"ok": False, "error": (result.stderr or result.stdout or "Refresh failed").strip()},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/trash":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body.decode("utf-8"))
            raw_path = str(payload.get("path", "")).strip()
            if not raw_path:
                self.send_json({"ok": False, "error": "Missing path."}, status=HTTPStatus.BAD_REQUEST)
                return
            path = Path(raw_path).resolve()
            if not path.exists() or not path.is_file():
                self.send_json({"ok": False, "error": "File not found."}, status=HTTPStatus.NOT_FOUND)
                return
            allowed = any(path.is_relative_to(Path(root)) for root in search_roots())
            if not allowed:
                self.send_json({"ok": False, "error": "File is outside indexed roots."}, status=HTTPStatus.FORBIDDEN)
                return
            escaped_path = str(path).replace("\\", "\\\\").replace('"', '\\"')
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'tell application "Finder" to delete POSIX file "{escaped_path}"',
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self.send_json(
                    {"ok": False, "error": (result.stderr or result.stdout or "Unable to move file to Trash.").strip()},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/open":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body.decode("utf-8"))
            raw_path = str(payload.get("path", "")).strip()
            if not raw_path:
                self.send_json({"ok": False, "error": "Missing path."}, status=HTTPStatus.BAD_REQUEST)
                return
            path = Path(raw_path).resolve()
            if not path.exists() or not path.is_file():
                self.send_json({"ok": False, "error": "File not found."}, status=HTTPStatus.NOT_FOUND)
                return
            allowed = any(path.is_relative_to(Path(root)) for root in search_roots())
            if not allowed:
                self.send_json({"ok": False, "error": "File is outside indexed roots."}, status=HTTPStatus.FORBIDDEN)
                return
            result = subprocess.run(
                ["open", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self.send_json(
                    {"ok": False, "error": (result.stderr or result.stdout or "Unable to open file.").strip()},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/show-in-finder":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            payload = json.loads(body.decode("utf-8"))
            raw_path = str(payload.get("path", "")).strip()
            if not raw_path:
                self.send_json({"ok": False, "error": "Missing path."}, status=HTTPStatus.BAD_REQUEST)
                return
            path = Path(raw_path).resolve()
            if not path.exists() or not path.is_file():
                self.send_json({"ok": False, "error": "File not found."}, status=HTTPStatus.NOT_FOUND)
                return
            allowed = any(path.is_relative_to(Path(root)) for root in search_roots())
            if not allowed:
                self.send_json({"ok": False, "error": "File is outside indexed roots."}, status=HTTPStatus.FORBIDDEN)
                return
            result = subprocess.run(
                ["open", "-R", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                self.send_json(
                    {"ok": False, "error": (result.stderr or result.stdout or "Unable to show file in Finder.").strip()},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self.send_json({"ok": True})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def serve_media(self, raw_path: str, head_only: bool = False) -> None:
        path = Path(unquote(raw_path)).resolve()
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        allowed = any(path.is_relative_to(Path(root)) for root in search_roots())
        if not allowed:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime_type, _ = mimetypes.guess_type(path.name)
        self.serve_file(path, mime_type or "application/octet-stream", head_only=head_only)

    def serve_thumbnail(self, raw_path: str, head_only: bool = False) -> None:
        path = Path(unquote(raw_path)).resolve()
        if not path.is_file() or all(root not in path.parents for root in (THUMB_DIR, FALLBACK_THUMB_DIR)):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.serve_file(path, "image/png", head_only=head_only)

    def serve_file(self, path: Path, content_type: str, head_only: bool = False) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        file_size = path.stat().st_size
        range_header = self.headers.get("Range")
        start = 0
        end = file_size - 1
        status = HTTPStatus.OK

        if range_header and range_header.startswith("bytes="):
            start_text, _, end_text = range_header.replace("bytes=", "").partition("-")
            if start_text:
                start = int(start_text)
            if end_text:
                end = min(int(end_text), file_size - 1)
            status = HTTPStatus.PARTIAL_CONTENT

        content_length = max(0, end - start + 1)

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(content_length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.end_headers()
        if head_only:
            return
        with path.open("rb") as handle:
            handle.seek(start)
            self.wfile.write(handle.read(content_length))

    def send_json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), GalleryHandler)
    print(f"Media Gallery running at http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()

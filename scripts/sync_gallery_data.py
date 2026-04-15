#!/usr/bin/env python3

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections import defaultdict
from datetime import datetime
from hashlib import sha1
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
APP_DIR = SCRIPT_DIR.parent if SCRIPT_DIR.name == "scripts" else SCRIPT_DIR
OUTPUT_JS = APP_DIR / "gallery-data.js"
OUTPUT_JSON = APP_DIR / "gallery-data.json"
THUMB_DIR = APP_DIR / "cache" / "thumbs"
PROJECT_FALLBACK_JSON = APP_DIR / "gallery-data.json"
LOGGER = logging.getLogger("media_gallery_indexer")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".avif", ".bmp", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
MEDIA_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES
SKIP_DIR_NAMES = {
    ".git",
    ".Trash",
    ".Spotlight-V100",
    ".fseventsd",
    "node_modules",
    "__pycache__",
    ".venv",
}


def configure_logging() -> None:
    if LOGGER.handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def read_payload(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def seed_payload() -> dict[str, object]:
    candidates = [OUTPUT_JSON]
    if OUTPUT_JSON != PROJECT_FALLBACK_JSON:
        candidates.append(PROJECT_FALLBACK_JSON)

    best_payload: dict[str, object] | None = None
    best_count = -1
    for candidate in candidates:
        payload = read_payload(candidate)
        if not payload:
            continue
        media_count = len(payload.get("media", []))
        if media_count > best_count:
            best_payload = payload
            best_count = media_count
    return best_payload or {"roots": [], "media": [], "collections": []}


def configured_roots() -> list[Path]:
    roots = [
        Path.home() / "Desktop",
        Path.home() / "Pictures",
        Path.home() / "Downloads",
        Path.home() / "Movies",
    ]

    volumes = Path("/Volumes")
    if volumes.exists():
        for volume in sorted(volumes.iterdir()):
            if volume.name.startswith(".") or volume.is_symlink():
                continue
            roots.append(volume)

    seen: list[Path] = []
    for root in roots:
        resolved = root.resolve()
        if resolved.exists() and resolved not in seen:
            seen.append(resolved)
    return seen


def root_name(root: Path) -> str:
    return root.name if root.name else str(root)


def root_kind(root: Path) -> str:
    return "volume" if root.is_relative_to(Path("/Volumes")) else "system"


def should_skip_directory(path: Path) -> bool:
    try:
        resolved = path.resolve()
        app_dir = APP_DIR.resolve()
        if resolved == app_dir or resolved.is_relative_to(app_dir):
            return True
    except OSError:
        return True

    # Never surface generated poster caches as gallery media.
    if path.name == "thumbs" and path.parent.name == "cache":
        return True

    return (
        path.name.startswith(".")
        or path.name in SKIP_DIR_NAMES
        or path.suffix.lower() in {".app", ".photoslibrary", ".pkg"}
    )


def media_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return None


def mdls_raw(path: Path, name: str) -> str:
    result = subprocess.run(
        ["mdls", "-raw", "-name", name, "--", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def parse_mdls_int(value: str) -> int | None:
    if value in {"", "(null)"}:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def parse_mdls_float(value: str) -> float | None:
    if value in {"", "(null)"}:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def media_dimensions(path: Path) -> dict[str, int]:
    width = parse_mdls_int(mdls_raw(path, "kMDItemPixelWidth"))
    height = parse_mdls_int(mdls_raw(path, "kMDItemPixelHeight"))
    details: dict[str, int] = {}
    if width:
        details["width"] = width
    if height:
        details["height"] = height
    return details


def video_metadata(path: Path) -> dict[str, object]:
    try:
        output = mdls_raw(path, "kMDItemCodecs")
        codecs = [] if output in {"(null)", ""} else [line.strip().strip('",') for line in output.splitlines() if line.strip() and line.strip() not in {"(", ")"}]
    except Exception:
        codecs = []

    normalized = [codec.lower() for codec in codecs]
    browser_playable = any("h.264" in codec or "hevc" in codec or "aac" in codec for codec in normalized)
    if any("prores" in codec for codec in normalized):
        browser_playable = False

    duration_seconds = parse_mdls_float(mdls_raw(path, "kMDItemDurationSeconds"))

    details: dict[str, object] = {
        "codecs": codecs,
        "browserPlayable": browser_playable or not codecs,
    }
    if duration_seconds is not None:
        details["durationSeconds"] = duration_seconds
    return details


def thumbnail_key(path: Path, timestamp: int) -> str:
    return sha1(f"{path}:{timestamp}".encode("utf-8")).hexdigest()


def generate_video_thumbnail(path: Path, timestamp: int) -> str | None:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    key = thumbnail_key(path, timestamp)
    final_path = THUMB_DIR / f"{key}.png"
    if final_path.exists():
      return str(final_path)

    temp_dir = THUMB_DIR / f"tmp-{key}"
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["qlmanage", "-t", "-s", "720", "-o", str(temp_dir), str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        generated = next(temp_dir.glob("*.png"), None)
        if result.returncode == 0 and generated and generated.exists():
            generated.replace(final_path)
            return str(final_path)
        return None
    finally:
        for leftover in temp_dir.glob("*"):
            leftover.unlink(missing_ok=True)
        temp_dir.rmdir()


def build_media_record(path: Path, root: Path) -> dict[str, object]:
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime)
    folder = path.parent
    folder_relative = folder.relative_to(root) if folder != root else Path(".")

    record = {
        "id": str(path),
        "name": path.name.replace("\u202f", " "),
        "path": str(path),
        "kind": media_kind(path),
        "extension": path.suffix.lower(),
        "modified": modified.strftime("%Y-%m-%d %H:%M"),
        "timestamp": int(stat.st_mtime),
        "size": stat.st_size,
        "root": str(root),
        "rootName": root.name if root.name else str(root),
        "folder": str(folder),
        "folderName": folder.name if folder.name else str(folder),
        "folderRelative": str(folder_relative),
    }
    record.update(media_dimensions(path))
    if record["kind"] == "video":
        record.update(video_metadata(path))
        record["thumbnailPath"] = generate_video_thumbnail(path, record["timestamp"])
    return record


def root_accessible(root: Path) -> tuple[bool, str | None]:
    try:
        with os.scandir(root):
            return True, None
    except PermissionError as exc:
        return False, str(exc)
    except OSError as exc:
        return False, str(exc)


def collect_media(root: Path) -> tuple[list[dict[str, object]], bool, list[str]]:
    items: list[dict[str, object]] = []
    errors: list[str] = []

    accessible, root_error = root_accessible(root)
    if not accessible:
        if root_error:
            errors.append(root_error)
        return items, False, errors

    def onerror(exc: OSError) -> None:
        errors.append(str(exc))

    for current_root, dirnames, filenames in os.walk(root, onerror=onerror):
        current_path = Path(current_root)
        dirnames[:] = [
            name
            for name in dirnames
            if not should_skip_directory(current_path / name)
        ]

        for filename in filenames:
            if filename.startswith("."):
                continue
            path = current_path / filename
            kind = media_kind(path)
            if not kind:
                continue
            try:
                items.append(build_media_record(path, root))
            except (FileNotFoundError, PermissionError, OSError):
                continue
    return items, True, errors


def build_collections(media_items: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in media_items:
        grouped[str(item["folder"])].append(item)

    collections: list[dict[str, object]] = []
    for folder, items in grouped.items():
        items.sort(key=lambda item: (-int(item["timestamp"]), str(item["name"]).lower()))
        latest = items[0]
        collections.append(
            {
                "id": folder,
                "folder": folder,
                "folderName": latest["folderName"],
                "folderRelative": latest["folderRelative"],
                "root": latest["root"],
                "rootName": latest["rootName"],
                "count": len(items),
                "latestPath": latest["path"],
                "latestKind": latest["kind"],
                "latestTimestamp": latest["timestamp"],
                "latestModified": latest["modified"],
            }
        )

    collections.sort(key=lambda item: (-int(item["latestTimestamp"]), str(item["folderName"]).lower()))
    return collections


def write_payload(payload: dict[str, object]) -> None:
    OUTPUT_JS.write_text(
        "window.GALLERY_DATA = " + json.dumps(payload, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    configure_logging()
    roots = configured_roots()
    previous_payload = seed_payload()
    previous_items = [
        item for item in previous_payload.get("media", [])
        if isinstance(item, dict)
    ]
    previous_roots = [
        root for root in previous_payload.get("roots", [])
        if isinstance(root, dict) and root.get("path")
    ]
    previous_by_root: dict[str, list[dict[str, object]]] = defaultdict(list)
    for item in previous_items:
        root = str(item.get("root", "")).strip()
        if root:
            previous_by_root[root].append(item)
    previous_root_map = {str(root["path"]): root for root in previous_roots}

    media_items: list[dict[str, object]] = []
    root_states: list[dict[str, object]] = []
    current_root_keys: set[str] = set()
    for root in roots:
        scanned_items, accessible, errors = collect_media(root)
        root_key = str(root)
        current_root_keys.add(root_key)
        if accessible:
            media_items.extend(scanned_items)
            root_states.append(
                {
                    "path": root_key,
                    "name": root_name(root),
                    "kind": root_kind(root),
                    "status": "indexed",
                    "count": len(scanned_items),
                    "errors": errors,
                }
            )
            if errors:
                LOGGER.warning("Indexed %s with %d traversal warnings.", root_key, len(errors))
            else:
                LOGGER.info("Indexed %s with %d items.", root_key, len(scanned_items))
            continue

        preserved_items = previous_by_root.get(root_key, [])
        media_items.extend(preserved_items)
        root_states.append(
            {
                "path": root_key,
                "name": root_name(root),
                "kind": root_kind(root),
                "status": "preserved",
                "count": len(preserved_items),
                "errors": errors,
            }
        )
        LOGGER.warning(
            "Preserved %d existing items for %s because the root was not readable.",
            len(preserved_items),
            root_key,
        )

    for root_key, root_info in previous_root_map.items():
        root_path = Path(root_key)
        if root_key in current_root_keys or not root_path.is_relative_to(Path("/Volumes")):
            continue
        preserved_count = len(previous_by_root.get(root_key, []))
        root_states.append(
            {
                "path": root_key,
                "name": str(root_info.get("name") or root_path.name or root_key),
                "kind": "volume",
                "status": "unavailable",
                "count": preserved_count,
                "errors": ["This drive is not currently connected."],
            }
        )
        LOGGER.info("Marked removable root %s as unavailable.", root_key)

    media_items.sort(key=lambda item: (-int(item["timestamp"]), str(item["name"]).lower()))
    root_states.sort(key=lambda item: (item.get("kind") != "system", str(item.get("name", "")).lower()))
    payload = {
        "roots": root_states,
        "media": media_items,
        "collections": build_collections(media_items),
    }
    write_payload(payload)
    LOGGER.info("Wrote payload with %d media items across %d roots.", len(media_items), len(roots))


if __name__ == "__main__":
    main()

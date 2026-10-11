"""Checkpoints: the garment saved before a change, and found again to restore.

MD's API has no undo, and nothing removes a seam, so restoring a saved garment is the
only way back from a mistake. Checkpoints are ``.zpac``, not ``.zprj``: measured on
2026.0.315 (2026-10-11), every form of ``ExportZPrj`` re-points MD's open project at the
copy, so the user's next Save would go over the checkpoint, while ``ExportZPac`` leaves
the project alone. A ``.zpac`` carries the garment -- pieces and seams came back through
``ImportZpac`` -- and not the avatar, which ``ImportZpac`` leaves as it is.

This module is the md-mcp side only -- where checkpoints live, what they are called,
the record kept beside each, which ones are kept, and which one a name refers to. MD
writes and reads the files; ``bridge`` sends it the scripts.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import pathlib
import re
from typing import Any

#: How many checkpoints are kept. Older ones are deleted, and only files this module
#: named -- nothing else in the folder is touched.
KEEP = 30

_NAME = re.compile(r"^(\d{8}-\d{6})(?:-([A-Za-z0-9_-]+))?\.zpac$")


def folder() -> pathlib.Path:
    """Where checkpoints go: MD_MCP_CHECKPOINTS, or %LOCALAPPDATA%\\md-mcp\\checkpoints."""
    override = os.environ.get("MD_MCP_CHECKPOINTS")
    if override:
        return pathlib.Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return pathlib.Path(base) / "md-mcp" / "checkpoints"


def slug(label: str) -> str:
    """A label as it can appear in a file name: letters, digits, - and _ only."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", label.strip()).strip("-")
    return cleaned[:40]


def new_path(label: str = "", now: _dt.datetime | None = None) -> pathlib.Path:
    """A fresh checkpoint path. Never one that exists: a clash gets a numbered suffix."""
    stamp = (now or _dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    tag = slug(label)
    base = f"{stamp}-{tag}" if tag else stamp
    path = folder() / f"{base}.zpac"
    number = 2
    while path.exists():
        path = folder() / f"{base}-{number}.zpac"
        number += 1
    return path


def sidecar(path: pathlib.Path) -> pathlib.Path:
    return path.with_suffix(".json")


def record(path: pathlib.Path, label: str, scene: dict, extra: dict | None = None) -> dict:
    """Write the record kept beside a checkpoint: what the scene held when it was taken."""
    entry = {
        "path": str(path),
        "label": label,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "scene": {
            key: scene.get(key)
            for key in ("pattern_count", "pieces", "seam_group_count", "avatars", "fabrics")
            if key in scene
        },
    }
    entry.update(extra or {})
    sidecar(path).write_text(json.dumps(entry, indent=2), encoding="utf-8")
    return entry


def listed() -> list[dict]:
    """Every checkpoint this module made, newest first, with its record where there is one."""
    here = folder()
    if not here.is_dir():
        return []
    found = []
    for path in here.iterdir():
        match = _NAME.match(path.name)
        if not match or not path.is_file():
            continue
        entry: dict[str, Any] = {"path": str(path), "label": match.group(2) or ""}
        try:
            entry.update(json.loads(sidecar(path).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            entry["record"] = "missing"
        entry["_sort"] = (path.stat().st_mtime, path.name)
        found.append(entry)
    found.sort(key=lambda entry: entry["_sort"], reverse=True)
    for entry in found:
        del entry["_sort"]
    return found


def prune(keep: int = KEEP) -> list[str]:
    """Delete all but the newest ``keep`` checkpoints. Returns the paths removed."""
    removed = []
    for entry in listed()[keep:]:
        path = pathlib.Path(entry["path"])
        for victim in (path, sidecar(path)):
            try:
                victim.unlink()
            except FileNotFoundError:
                pass
        removed.append(str(path))
    return removed


def resolve(name: str) -> dict | None:
    """The checkpoint ``name`` means: "latest", a label, a file name, or a full path."""
    entries = listed()
    if not entries:
        return None
    if name in ("", "latest"):
        return entries[0]
    for entry in entries:
        path = pathlib.Path(entry["path"])
        if name in (entry["path"], path.name, path.stem) or (
            entry.get("label") and slug(name) == slug(entry["label"])
        ):
            return entry
    candidate = pathlib.Path(name)
    if candidate.suffix.lower() in (".zpac", ".zprj") and candidate.is_file():
        return {"path": str(candidate), "label": "", "record": "not made by md_checkpoint"}
    return None


def compare(saved: dict, now: dict, garment_only: bool = True) -> list[str]:
    """How the scene after a restore differs from the record taken with the checkpoint.

    ``garment_only`` leaves the avatar out: a .zpac carries none, and its import keeps
    whatever avatar is open.
    """
    differences = []
    keys = ["pattern_count", "seam_group_count", "fabrics"]
    if not garment_only:
        keys.append("avatars")
    for key in keys:
        if key in saved and saved.get(key) != now.get(key):
            differences.append(f"{key}: saved {saved.get(key)!r}, now {now.get(key)!r}")
    if "pieces" in saved:
        before = [piece.get("name") for piece in saved.get("pieces") or []]
        after = [piece.get("name") for piece in now.get("pieces") or []]
        if before != after:
            differences.append(f"piece names: saved {before!r}, now {after!r}")
    return differences

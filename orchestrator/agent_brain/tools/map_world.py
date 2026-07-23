"""
Map / world file tools — let the NPC and player explore saves together.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_brain import config

logger = logging.getLogger("npc.brain.map")


def _world_dirs() -> list[Path]:
    found: list[Path] = []
    # Dev plugin universe
    plugin_worlds = config.PLUGIN_RUN / "universe" / "worlds"
    if plugin_worlds.is_dir():
        found.extend(p for p in plugin_worlds.iterdir() if p.is_dir())
    # UserData Saves (layout may vary by version)
    saves = config.USERDATA / "Saves"
    if saves.is_dir():
        for p in saves.rglob("config.json"):
            # world folder is parent of config.json in some layouts
            parent = p.parent
            if parent not in found:
                found.append(parent)
    return found


def _pick_world(world: str | None) -> Path | None:
    world = (world or "default").strip()
    dirs = _world_dirs()
    if not dirs:
        return None
    for d in dirs:
        if d.name.lower() == world.lower():
            return d
    # substring
    for d in dirs:
        if world.lower() in d.name.lower():
            return d
    return dirs[0]


def read_map_markers(world: str | None = None) -> dict[str, Any]:
    wdir = _pick_world(world)
    if wdir is None:
        return {
            "ok": False,
            "content": (
                "No world saves found. Mount UserData or PluginRun "
                "(HYTALE_USERDATA / HYTALE_PLUGIN_RUN)."
            ),
            "reward": 0.0,
        }

    chunks: list[str] = [f"World path: {wdir}"]
    resource_names = [
        "SharedUserMapMarkers.json",
        "BlockMapMarkers.json",
        "BlockCounter.json",
        "InstanceData.json",
        "Time.json",
        "ReputationData.json",
    ]
    resources = wdir / "resources"
    search_roots = [resources, wdir]
    for name in resource_names:
        for root in search_roots:
            path = root / name
            if path.is_file():
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                    if len(text) > 6000:
                        text = text[:6000] + "\n...[truncated]"
                    chunks.append(f"--- {name} ---\n{text}")
                except OSError as e:
                    chunks.append(f"--- {name}: read error {e}")
                break

    if len(chunks) == 1:
        # list what exists
        try:
            listing = []
            for root in search_roots:
                if root.is_dir():
                    listing.extend(f.name for f in root.iterdir())
            chunks.append("Files present: " + ", ".join(sorted(set(listing))[:50]))
        except OSError:
            pass

    return {
        "ok": True,
        "content": "\n\n".join(chunks)[:8000],
        "data": {"world_path": str(wdir)},
        "reward": 0.18,
    }


def read_world_config(world: str | None = None) -> dict[str, Any]:
    wdir = _pick_world(world)
    if wdir is None:
        return {"ok": False, "content": "No world found", "reward": 0.0}
    cfg = wdir / "config.json"
    if not cfg.is_file():
        return {
            "ok": False,
            "content": f"No config.json under {wdir}",
            "data": {"world_path": str(wdir)},
            "reward": 0.0,
        }
    try:
        text = cfg.read_text(encoding="utf-8", errors="replace")
        # pretty if json
        try:
            obj = json.loads(text)
            text = json.dumps(obj, indent=2)[:6000]
        except json.JSONDecodeError:
            text = text[:6000]
        return {
            "ok": True,
            "content": text,
            "data": {"world_path": str(wdir)},
            "reward": 0.12,
        }
    except OSError as e:
        return {"ok": False, "content": str(e), "reward": 0.0}

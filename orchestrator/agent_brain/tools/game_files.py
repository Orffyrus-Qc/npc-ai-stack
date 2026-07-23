"""
Read-only access to Hytale game files (OpenHands FileEditor/Grep analog).

Allowed roots are hard-limited so the agent cannot escape the Hytale mount.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from pathlib import Path
from typing import Any

from agent_brain import config

logger = logging.getLogger("npc.brain.game_files")


def _allowed_bases() -> list[Path]:
    bases: list[Path] = []
    for p in (
        config.HYTALE_ROOT,
        config.GAME_LATEST,
        config.USERDATA,
        config.PLUGIN_RUN,
        config.ASSETS_ZIP.parent if config.ASSETS_ZIP.exists() else None,
    ):
        if p is None:
            continue
        try:
            if p.exists():
                bases.append(p.resolve())
        except OSError:
            continue
    return bases


def _is_under_allowed(path: Path) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for base in _allowed_bases():
        try:
            resolved.relative_to(base)
            return True
        except ValueError:
            continue
    # Assets.zip itself
    if config.ASSETS_ZIP.exists() and resolved == config.ASSETS_ZIP.resolve():
        return True
    return False


def resolve_path(spec: str) -> tuple[str, Path | None, str | None]:
    """
    Resolve a path spec.

    Returns (kind, path_or_none, zip_inner_or_none)
      kind: 'fs' | 'zip' | 'error'
    Aliases:
      Assets:<inner>     → Assets.zip member
      UserData:<rel>
      Client:<rel>
      ServerDir:<rel>
      PluginRun:<rel>
      Game:<rel>         → under game/latest
    """
    spec = (spec or "").strip().replace("\\", "/")
    if not spec:
        return "error", None, None

    if ":" in spec and not re.match(r"^[A-Za-z]:/", spec):
        alias, rest = spec.split(":", 1)
        rest = rest.lstrip("/")
        alias_l = alias.lower()
        if alias_l == "assets":
            return "zip", config.ASSETS_ZIP if config.ASSETS_ZIP.exists() else None, rest
        mapping = {
            "userdata": config.USERDATA,
            "client": config.GAME_LATEST / "Client",
            "serverdir": config.GAME_LATEST / "Server",
            "pluginrun": config.PLUGIN_RUN,
            "game": config.GAME_LATEST,
            "hytaleroot": config.HYTALE_ROOT,
        }
        base = mapping.get(alias_l)
        if base is None:
            return "error", None, None
        path = (base / rest).resolve()
        if not _is_under_allowed(path):
            return "error", None, None
        return "fs", path, None

    path = Path(spec)
    if not path.is_absolute():
        # Prefer UserData then GameLatest
        for base in (config.USERDATA, config.GAME_LATEST, config.HYTALE_ROOT):
            cand = (base / spec).resolve()
            if cand.exists() and _is_under_allowed(cand):
                return "fs", cand, None
        path = (config.HYTALE_ROOT / spec).resolve()
    if not _is_under_allowed(path):
        return "error", None, None
    return "fs", path, None


def read_game_file(path_spec: str, max_bytes: int | None = None) -> dict[str, Any]:
    max_bytes = max_bytes or config.MAX_FILE_BYTES
    kind, path, inner = resolve_path(path_spec)
    if kind == "error" or path is None:
        return {"ok": False, "content": f"Path not allowed or not found: {path_spec}"}

    try:
        if kind == "zip":
            if not path.exists():
                return {"ok": False, "content": f"Assets.zip missing at {path}"}
            with zipfile.ZipFile(path, "r") as zf:
                # normalize
                names = zf.namelist()
                target = inner
                if target not in names:
                    # case-insensitive / partial
                    matches = [n for n in names if n.replace("\\", "/").endswith(inner) or n == inner]
                    if not matches:
                        # try contains
                        matches = [n for n in names if inner.lower() in n.lower()][:5]
                        if not matches:
                            return {"ok": False, "content": f"No zip entry matching {inner}"}
                    target = matches[0]
                raw = zf.read(target)
                if len(raw) > max_bytes:
                    raw = raw[:max_bytes]
                    truncated = True
                else:
                    truncated = False
                text = raw.decode("utf-8", errors="replace")
                return {
                    "ok": True,
                    "content": text,
                    "data": {"entry": target, "truncated": truncated, "source": "Assets.zip"},
                    "reward": 0.15,
                }

        if not path.exists():
            return {"ok": False, "content": f"File does not exist: {path}"}
        if path.is_dir():
            kids = sorted(path.iterdir())[:40]
            listing = "\n".join(
                ("d " if c.is_dir() else "f ") + c.name for c in kids
            )
            return {
                "ok": True,
                "content": f"Directory {path}:\n{listing}",
                "data": {"is_dir": True},
                "reward": 0.05,
            }
        data = path.read_bytes()[:max_bytes]
        text = data.decode("utf-8", errors="replace")
        return {
            "ok": True,
            "content": text,
            "data": {"path": str(path), "truncated": path.stat().st_size > max_bytes},
            "reward": 0.15,
        }
    except Exception as e:
        logger.exception("read_game_file failed")
        return {"ok": False, "content": f"Read error: {e}"}


def list_game_tree(path_spec: str, limit: int = 40) -> dict[str, Any]:
    kind, path, inner = resolve_path(path_spec)
    if kind == "zip" and path is not None:
        prefix = (inner or "").rstrip("/") + "/" if inner else ""
        try:
            with zipfile.ZipFile(path, "r") as zf:
                seen: set[str] = set()
                rows: list[str] = []
                for name in zf.namelist():
                    n = name.replace("\\", "/")
                    if prefix and not n.startswith(prefix):
                        continue
                    rest = n[len(prefix):] if prefix else n
                    if not rest:
                        continue
                    part = rest.split("/")[0]
                    if part in seen:
                        continue
                    seen.add(part)
                    rows.append(part)
                    if len(rows) >= limit:
                        break
                return {
                    "ok": True,
                    "content": f"Assets.zip/{prefix or ''}\n" + "\n".join(rows),
                    "reward": 0.08,
                }
        except Exception as e:
            return {"ok": False, "content": str(e)}

    if kind != "fs" or path is None or not path.exists():
        return {"ok": False, "content": f"Cannot list: {path_spec}"}
    try:
        kids = sorted(path.iterdir())[:limit]
        listing = "\n".join(("d " if c.is_dir() else "f ") + c.name for c in kids)
        return {"ok": True, "content": listing, "reward": 0.05}
    except Exception as e:
        return {"ok": False, "content": str(e)}


def search_game_files(query: str, root: str = "All", limit: int = 15) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"ok": False, "content": "Empty query"}
    limit = max(1, min(int(limit or 15), 40))
    # Multi-word queries like "Campfire crafting" almost never match a filename;
    # also try each significant token so the agent still finds Bench_Campfire.json.
    tokens = [query] + [t for t in re.split(r"[\s_/.\-]+", query) if len(t) >= 3]
    # de-dupe preserving order
    seen_tok: set[str] = set()
    terms: list[str] = []
    for t in tokens:
        tl = t.lower()
        if tl not in seen_tok:
            seen_tok.add(tl)
            terms.append(tl)
    hits: list[str] = []

    def add(label: str) -> None:
        if label not in hits and len(hits) < limit:
            hits.append(label)

    def name_matches(name: str) -> bool:
        nl = name.lower()
        return any(term in nl for term in terms)

    root_l = (root or "All").lower()

    # Search Assets.zip names (fast, high value)
    if root_l in ("all", "assets") and config.ASSETS_ZIP.exists():
        try:
            with zipfile.ZipFile(config.ASSETS_ZIP, "r") as zf:
                for name in zf.namelist():
                    if name_matches(name):
                        add(f"Assets:{name}")
                        if len(hits) >= limit:
                            break
        except Exception as e:
            logger.warning("assets search failed: %s", e)

    # Filesystem walks (bounded depth)
    fs_roots: list[tuple[str, Path]] = []
    if root_l in ("all", "userdata") and config.USERDATA.exists():
        fs_roots.append(("UserData", config.USERDATA))
    if root_l in ("all", "client") and (config.GAME_LATEST / "Client").exists():
        fs_roots.append(("Client", config.GAME_LATEST / "Client"))
    if root_l in ("all", "serverdir") and (config.GAME_LATEST / "Server").exists():
        fs_roots.append(("ServerDir", config.GAME_LATEST / "Server"))
    if root_l in ("all", "pluginrun") and config.PLUGIN_RUN.exists():
        fs_roots.append(("PluginRun", config.PLUGIN_RUN))

    for label, base in fs_roots:
        if len(hits) >= limit:
            break
        try:
            for p in base.rglob("*"):
                if len(hits) >= limit:
                    break
                if name_matches(p.name):
                    try:
                        rel = p.relative_to(base)
                    except ValueError:
                        rel = p
                    add(f"{label}:{rel.as_posix()}")
        except Exception as e:
            logger.warning("walk %s failed: %s", label, e)

    if not hits:
        return {
            "ok": True,
            "content": f"No hits for {query!r} under {root}",
            "data": {"hits": []},
            "reward": 0.0,
        }
    return {
        "ok": True,
        "content": "\n".join(hits),
        "data": {"hits": hits},
        "reward": 0.2,
    }


def roots_status() -> dict[str, Any]:
    avail = config.roots_available()
    lines = [f"{k}: {v}" for k, v in avail.items()]
    return {
        "ok": bool(avail),
        "content": "Mounted roots:\n" + ("\n".join(lines) if lines else "(none — mount Hytale)"),
        "data": {k: str(v) for k, v in avail.items()},
        "reward": 0.05 if avail else 0.0,
    }

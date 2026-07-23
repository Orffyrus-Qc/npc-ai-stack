"""
Paths and feature flags for the agent brain.

Environment (set in docker-compose or host):

  HYTALE_ROOT          Mount point of %APPDATA%/Hytale (default /hytale)
  HYTALE_ASSETS_ZIP    Override path to Assets.zip
  HYTALE_SERVER_JAR    Override path to HytaleServer.jar
  HYTALE_USERDATA      Override UserData path
  HYTALE_PLUGIN_RUN    Optional path to hytale-plugin/run (dev universe)
  BRAIN_ALLOW_WEB      "1" to allow web_fetch
  BRAIN_MAX_STEPS      max tool steps per brain run (default 6)
  BRAIN_MAX_FILE_BYTES max bytes read from one file (default 24000)
"""

from __future__ import annotations

import os
from pathlib import Path


def _p(key: str, default: str) -> Path:
    return Path(os.environ.get(key, default))


HYTALE_ROOT = _p("HYTALE_ROOT", "/hytale")

# Default layout matches Windows install mounted at HYTALE_ROOT:
#   install/release/package/game/latest/{Assets.zip,Server,Client}
#   UserData/{Mods,Saves,...}
ASSETS_ZIP = _p(
    "HYTALE_ASSETS_ZIP",
    str(HYTALE_ROOT / "install" / "release" / "package" / "game" / "latest" / "Assets.zip"),
)
SERVER_JAR = _p(
    "HYTALE_SERVER_JAR",
    str(HYTALE_ROOT / "install" / "release" / "package" / "game" / "latest" / "Server" / "HytaleServer.jar"),
)
GAME_LATEST = _p(
    "HYTALE_GAME_LATEST",
    str(HYTALE_ROOT / "install" / "release" / "package" / "game" / "latest"),
)
USERDATA = _p("HYTALE_USERDATA", str(HYTALE_ROOT / "UserData"))
PLUGIN_RUN = _p("HYTALE_PLUGIN_RUN", "/plugin-run")  # optional

ALLOW_WEB = os.environ.get("BRAIN_ALLOW_WEB", "0") == "1"
MAX_STEPS = int(os.environ.get("BRAIN_MAX_STEPS", "6"))
MAX_FILE_BYTES = int(os.environ.get("BRAIN_MAX_FILE_BYTES", "24000"))
MAX_OBS_CHARS = int(os.environ.get("BRAIN_MAX_OBS_CHARS", "3500"))

# Hosts allowed for web_fetch when BRAIN_ALLOW_WEB=1
WEB_ALLOWLIST = {
    "hytale.com",
    "www.hytale.com",
    "hytale.fandom.com",
    "support.hytale.com",
    "hytalemodding.dev",
    "docs.openhands.dev",
    "github.com",
    "raw.githubusercontent.com",
}


def roots_available() -> dict[str, Path]:
    """Named roots that exist on disk right now."""
    candidates = {
        "HytaleRoot": HYTALE_ROOT,
        "AssetsZip": ASSETS_ZIP,
        "ServerJar": SERVER_JAR,
        "GameLatest": GAME_LATEST,
        "UserData": USERDATA,
        "PluginRun": PLUGIN_RUN,
        "Client": GAME_LATEST / "Client",
        "ServerDir": GAME_LATEST / "Server",
    }
    return {k: v for k, v in candidates.items() if v.exists()}

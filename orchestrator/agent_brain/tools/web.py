"""
Optional web research tool (OpenHands browser analog — minimal).

Gated by BRAIN_ALLOW_WEB=1 and host allowlist.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from agent_brain import config

logger = logging.getLogger("npc.brain.web")


def _host_ok(url: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    host = host.lower()
    if host in config.WEB_ALLOWLIST:
        return True
    return any(host.endswith("." + h) for h in config.WEB_ALLOWLIST)


def _strip_html(html: str) -> str:
    # crude but dependency-free
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def web_fetch(url: str, max_chars: int = 4000) -> dict[str, Any]:
    if not config.ALLOW_WEB:
        return {
            "ok": False,
            "content": (
                "web_fetch disabled. Set BRAIN_ALLOW_WEB=1 to enable. "
                "Prefer search_wiki / game files."
            ),
            "reward": 0.0,
        }
    url = (url or "").strip()
    if not url.startswith("https://"):
        return {"ok": False, "content": "Only https:// URLs allowed", "reward": 0.0}
    if not _host_ok(url):
        return {
            "ok": False,
            "content": f"Host not on allowlist: {urlparse(url).hostname}",
            "reward": 0.0,
        }
    max_chars = max(500, min(int(max_chars or 4000), 12000))
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "HytaleNpcBrain/0.1"})
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if "html" in ctype or url.endswith(".html"):
                body = _strip_html(r.text)
            else:
                body = r.text
            body = body[:max_chars]
            return {
                "ok": True,
                "content": body,
                "data": {"url": str(r.url), "status": r.status_code},
                "reward": 0.12,
            }
    except Exception as e:
        logger.warning("web_fetch failed: %s", e)
        return {"ok": False, "content": f"Fetch failed: {e}", "reward": -0.05}

"""
Offline crawler: pulls Hytale Wiki (hytale.fandom.com, standard MediaWiki)
content into WikiKnowledgeStore's Qdrant collection. This is the ONLY place
"internet access" actually happens in this stack - dialogue turns only ever
query the already-ingested, local Qdrant collection (wiki_knowledge.py's
search()), so a real conversation never waits on or depends on live network
access to an external site.

Same "not started by docker compose up" shape as skill_writer.py: run this
manually (`python wiki_ingest.py`) for a one-off seed/refresh, or let
main.py's wiki_refresh_daemon() call run_ingest_cycle() on a schedule.

Two-phase, incremental: first a cheap BATCHED revision-id check (up to 50
page titles per MediaWiki API call) against what's already stored, then the
heavier fetch+clean+chunk+embed step only for pages that are new or whose
revision id changed. A full wiki has far more pages than change day to day,
so after the first run this keeps refreshes fast and light on the wiki's
own servers.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

import httpx

from wiki_knowledge import WikiKnowledgeStore

logger = logging.getLogger("npc.wiki_ingest")

WIKI_API_URL = "https://hytale.fandom.com/api.php"
WIKI_PAGE_URL = "https://hytale.fandom.com/wiki/{title}"

REVISION_BATCH_SIZE = 50   # MediaWiki's own per-request titles= cap
PAGE_FETCH_DELAY_S = 0.3   # politeness delay between per-page wikitext fetches
CHUNK_CHARS = 1200         # ~300 tokens/chunk at ~4 chars/token

# 2026-07-22 real bug found live: the NPC recited a real-world Hypixel
# company history date ("August 1st 2014") mid-conversation, completely out
# of character - traced to the "Hypixel Network" wiki page (Category:Hypixel)
# getting ingested and later retrieved/parroted verbatim. This wiki mixes
# in-universe fantasy lore with real-world meta content (the studio, its
# staff, wiki administration, community pages) in the SAME mainspace
# namespace - confirmed live via the MediaWiki API's own categories
# (action=query&prop=categories) that real lore pages (Trork -> Enemies/
# Factions/Hostile/Races) and meta pages (Hypixel Network -> Hypixel;
# Developers -> Developers) are cleanly distinguishable this way, unlike
# namespace alone. A companion NPC has no business citing the real
# developer studio's founding date as if it were something it "picked up in
# its travels" through the fantasy world - excluded at the category level
# rather than trying to guess from title/content alone.
#
# Deliberately excludes wiki QUALITY/maintenance tags ("Articles in need of
# cleanup", "Citations needed", "Candidates for deletion") - a first version
# of this set included them and wrongly excluded real lore stubs (Adamantite
# is tagged both "Items" AND "Articles in need of cleanup" - a legitimate,
# if underwritten, lore page, not a real-world one). Those tags describe a
# page's EDITORIAL STATUS, not its TOPIC, and can sit on any page regardless
# of subject - only true topic categories belong in this set.
_META_CATEGORIES = {
    "hypixel", "developers", "community", "hytale wiki", "administration",
    "author", "blog posts", "concept art", "creators", "director",
    "disambiguations", "executive producer", "gamer", "games",
    "general wiki templates", "guides", "help", "hytale youtubers",
    "images", "modding", "music", "musician", "news",
    "pages with broken file links", "polls", "pre-release",
    "ru translation", "rights", "screenwriter", "servers",
    "site maintenance", "sitarist", "template documentation", "templates",
    "templates/infobox", "templates/navbox", "templates/quote",
    "templates/utility", "tutorials", "multiplayer", "updates", "videos",
    "voice actor", "youtuber",
}


async def _fetch_all_titles(client: httpx.AsyncClient) -> list[str]:
    titles: list[str] = []
    apcontinue = None
    while True:
        params = {"action": "query", "list": "allpages", "aplimit": "500", "format": "json"}
        if apcontinue:
            params["apcontinue"] = apcontinue
        resp = await client.get(WIKI_API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        titles.extend(p["title"] for p in data["query"]["allpages"])
        apcontinue = data.get("continue", {}).get("apcontinue")
        if not apcontinue:
            break
    return titles


async def _fetch_revisions_and_categories(
    client: httpx.AsyncClient, titles: list[str]
) -> dict[str, tuple[int, set[str]]]:
    """title -> (current revision id, lowercased category names), batched
    REVISION_BATCH_SIZE titles/call. Fetches both in the same request
    (prop=revisions|categories) rather than a separate round trip per page -
    category membership is what _is_meta_page() below uses to skip
    real-world/meta content before ever fetching its wikitext."""
    out: dict[str, tuple[int, set[str]]] = {}
    for i in range(0, len(titles), REVISION_BATCH_SIZE):
        batch = titles[i:i + REVISION_BATCH_SIZE]
        params = {
            "action": "query", "prop": "revisions|categories", "rvprop": "ids",
            "cllimit": "500", "titles": "|".join(batch), "format": "json",
        }
        resp = await client.get(WIKI_API_URL, params=params, timeout=30)
        resp.raise_for_status()
        pages = resp.json().get("query", {}).get("pages", {})
        for page in pages.values():
            revs = page.get("revisions")
            if not revs:
                continue
            cats = {
                c["title"].split(":", 1)[-1].strip().lower()
                for c in page.get("categories", [])
            }
            out[page["title"]] = (revs[0]["revid"], cats)
    return out


def _is_meta_page(categories: set[str]) -> bool:
    return bool(categories & _META_CATEGORIES)


async def _fetch_wikitext(client: httpx.AsyncClient, title: str) -> str | None:
    params = {"action": "parse", "page": title, "format": "json", "prop": "wikitext"}
    resp = await client.get(WIKI_API_URL, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        return None
    return data["parse"]["wikitext"]["*"]


# Strip MediaWiki markup down to plain prose - good enough for RAG context,
# not a faithful renderer. Order matters: templates/galleries/refs first
# (their contents shouldn't leak into prose), then link/emphasis syntax.
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*\}\}")
_TABLE_RE = re.compile(r"\{\|.*?\|\}", re.DOTALL)
_GALLERY_RE = re.compile(r"<gallery.*?</gallery>", re.DOTALL | re.IGNORECASE)
_REF_RE = re.compile(r"<ref.*?(</ref>|/>)", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
# [[Category:Weapons]] etc. are page metadata, not prose - stripped outright
# rather than converted to text (which _LINK_RE would otherwise turn into a
# stray "Category:Weapons" line, since there's no pipe to split off display
# text).
_CATEGORY_RE = re.compile(r"\[\[Category:[^\]]*\]\]\s*", re.IGNORECASE)
_LINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_EXTLINK_RE = re.compile(r"\[https?://\S+ ([^\]]+)\]")
_HEADING_RE = re.compile(r"^={2,6}\s*(.+?)\s*={2,6}$", re.MULTILINE)
_BOLD_ITALIC_RE = re.compile(r"'{2,3}")


def clean_wikitext(text: str) -> str:
    # Templates can nest one level deep in practice on this wiki (an
    # infobox containing a smaller template) - looping until stable handles
    # that without a full wikitext parser.
    prev = None
    while prev != text:
        prev = text
        text = _TEMPLATE_RE.sub("", text)
    # Wikitables ({| ... |}) are a separate syntax from {{templates}} (single
    # braces, pipe-delimited rows/cells) - crafting-recipe tables are common
    # on item pages and their raw markup ("!Result\n!Ingrediants\n|-\n|...")
    # reads as noise, not prose, so dropped rather than converted.
    text = _TABLE_RE.sub("", text)
    text = _GALLERY_RE.sub("", text)
    text = _REF_RE.sub("", text)
    text = _TAG_RE.sub("", text)
    text = _CATEGORY_RE.sub("", text)
    text = _LINK_RE.sub(lambda m: m.group(2) or m.group(1), text)
    text = _EXTLINK_RE.sub(lambda m: m.group(1), text)
    text = _HEADING_RE.sub(lambda m: m.group(1) + ":", text)
    text = _BOLD_ITALIC_RE.sub("", text)
    # Collapse the blank-line noise templates/galleries leave behind.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    # A heading whose only content was a stripped <gallery>/template (e.g.
    # "Gallery:") leaves a dangling heading line with nothing under it -
    # strip one repeatedly in case removing the last leaves a new one bare.
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"(^|\n)[^\n:]+:\s*$", "", text).strip()
    return text


def chunk_text(text: str, chunk_chars: int = CHUNK_CHARS) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if current and len(current) + len(p) + 2 > chunk_chars:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current:
        chunks.append(current)
    return chunks


async def run_ingest_cycle(store: WikiKnowledgeStore, max_pages: int | None = None) -> dict:
    """Returns a summary dict for logging: {checked, changed, skipped, excluded, errors}."""
    summary = {"checked": 0, "changed": 0, "skipped": 0, "excluded": 0, "errors": 0}
    async with httpx.AsyncClient() as client:
        try:
            titles = await _fetch_all_titles(client)
        except Exception:
            logger.exception("wiki_ingest: failed to list pages, aborting cycle")
            return summary
        if max_pages is not None:
            titles = titles[:max_pages]
        summary["checked"] = len(titles)

        try:
            live_info = await _fetch_revisions_and_categories(client, titles)
        except Exception:
            logger.exception("wiki_ingest: failed to fetch revision/category info, aborting cycle")
            return summary

        for title in titles:
            info = live_info.get(title)
            if info is None:
                continue
            live_rev, categories = info
            try:
                if _is_meta_page(categories):
                    # Real-world/meta content (the studio, staff, wiki
                    # administration, community pages) - never belongs in an
                    # in-character companion's mouth. Clean up in case an
                    # earlier cycle (before this filter existed) already
                    # ingested it - see _META_CATEGORIES's comment.
                    await store.delete_page(title)
                    summary["excluded"] += 1
                    continue
                stored_rev = await store.get_revision(title)
                if stored_rev == live_rev:
                    summary["skipped"] += 1
                    continue
                wikitext = await _fetch_wikitext(client, title)
                await asyncio.sleep(PAGE_FETCH_DELAY_S)
                if wikitext is None:
                    continue
                cleaned = clean_wikitext(wikitext)
                if not cleaned:
                    continue
                chunks = chunk_text(cleaned)
                url = WIKI_PAGE_URL.format(title=title.replace(" ", "_"))
                await store.replace_page(title, url, live_rev, chunks)
                summary["changed"] += 1
            except Exception:
                logger.exception("wiki_ingest: failed on page %r, skipping", title)
                summary["errors"] += 1

    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    async def _main() -> None:
        store = WikiKnowledgeStore()
        await store.start()
        started = time.monotonic()
        summary = await run_ingest_cycle(store)
        logger.info("wiki_ingest one-off run: %s (%.1fs)", summary, time.monotonic() - started)

    asyncio.run(_main())

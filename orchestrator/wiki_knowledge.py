"""
Semantic-search store for Hytale-wiki knowledge - a separate Qdrant
collection from npc_episodic (memory.py), since this holds world knowledge
(retrieved by topic-similarity to the player's question) rather than
per-player conversation history. Populated by wiki_ingest.py (the offline
crawler); read here at dialogue time via search().

Kept as its own module rather than folded into memory.py: different
lifecycle (world knowledge refreshed on a schedule vs. per-conversation
writes), different query shape (no npc_id/player_id scoping - this is the
same for every conversation), and MemoryStore already has enough going on.
"""

from __future__ import annotations

import logging
import uuid

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams,
)

from embedding import EMBED_DIM, embed
from memory import QDRANT_URL

logger = logging.getLogger("npc.wiki")

COLLECTION = "wiki_knowledge"


class WikiKnowledgeStore:
    def __init__(self):
        self._qdrant = AsyncQdrantClient(url=QDRANT_URL)

    async def start(self) -> None:
        collections = await self._qdrant.get_collections()
        if COLLECTION not in [c.name for c in collections.collections]:
            await self._qdrant.create_collection(
                COLLECTION,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )

    async def search(self, query: str, limit: int = 3) -> list[str]:
        result = await self._qdrant.query_points(
            COLLECTION,
            query=embed(query),
            limit=limit,
        )
        return [h.payload["chunk_text"] for h in result.points]

    # -- ingestion support (called by wiki_ingest.py) ------------------------

    async def get_revision(self, title: str) -> int | None:
        """Stored revision_id for ANY chunk of this page, or None if the page
        has never been ingested. All chunks of a page share the same
        revision_id (re-written together on every ingest), so checking one
        chunk is enough to decide whether this page needs re-fetching."""
        points, _ = await self._qdrant.scroll(
            COLLECTION,
            scroll_filter=Filter(must=[FieldCondition(key="title", match=MatchValue(value=title))]),
            limit=1,
            with_payload=True,
        )
        return points[0].payload["revision_id"] if points else None

    async def delete_page(self, title: str) -> None:
        """Removes all stored chunks for a page - used both when a page's
        chunk count shrinks on re-ingest (see replace_page) and when a page
        already ingested under an older ruleset turns out to be excluded now
        (see wiki_ingest.py's _META_CATEGORIES cleanup)."""
        await self._qdrant.delete(
            COLLECTION,
            points_selector=Filter(must=[FieldCondition(key="title", match=MatchValue(value=title))]),
        )

    async def replace_page(self, title: str, url: str, revision_id: int, chunks: list[str]) -> None:
        """Delete all existing chunks for this page (handles the chunk count
        shrinking between revisions) and insert the fresh set. Point ids are
        deterministic (uuid5 of title:chunk_index - Qdrant requires an
        unsigned int or a real UUID, not an arbitrary string) so re-ingesting
        an UNCHANGED page would just overwrite identical points - but callers
        should check get_revision() first and skip unchanged pages entirely
        to avoid the wasted embedding work."""
        await self.delete_page(title)
        if not chunks:
            return
        await self._qdrant.upsert(
            COLLECTION,
            points=[
                PointStruct(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{title}:{i}")),
                    vector=embed(chunk),
                    payload={
                        "title": title, "url": url,
                        "revision_id": revision_id,
                        "chunk_text": chunk, "chunk_index": i,
                    },
                )
                for i, chunk in enumerate(chunks)
            ],
        )

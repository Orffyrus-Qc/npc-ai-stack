"""
Two-tier NPC memory:

  1. EPISODIC - raw interaction snippets in Qdrant, embedded for similarity
     search, namespaced per (npc_id). Rolling: compressed then pruned.
  2. SEMANTIC - durable distilled facts in Postgres ("player_X is a blacksmith",
     "the north bridge is broken"). Queried directly, no vectors needed.

Compression: when an NPC's episodic count exceeds a threshold, the oldest
batch is summarized into 1-2 semantic facts by the LLM (as a LOW-priority /
offline job - never competing with live dialogue for a GPU slot) and the raw
entries are deleted.

Embeddings run on CPU via fastembed (ONNX, small model) - the GPU stays
reserved for the chat model.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass

import asyncpg
from fastembed import TextEmbedding
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams,
)

logger = logging.getLogger("npc.memory")

QDRANT_URL = "http://memory-db:6333"
PG_DSN = "postgresql://npc:npc@fact-db:5432/npc"
COLLECTION = "npc_episodic"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"   # 384-dim, fast on CPU
EMBED_DIM = 384

COMPRESS_THRESHOLD = 60   # episodic entries per NPC before compression kicks in
COMPRESS_BATCH = 30       # oldest N entries summarized + deleted per pass


@dataclass
class EpisodicEntry:
    npc_id: str
    player_id: str
    text: str          # "Player asked about the broken bridge; I told them..."
    ts: float


class MemoryStore:
    def __init__(self):
        self._qdrant = AsyncQdrantClient(url=QDRANT_URL)
        self._embedder = TextEmbedding(EMBED_MODEL)  # loads ONNX model, CPU-only
        self._pg: asyncpg.Pool | None = None

    # -- lifecycle ----------------------------------------------------------

    async def start(self) -> None:
        self._pg = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
        async with self._pg.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS semantic_facts (
                    id UUID PRIMARY KEY,
                    npc_id TEXT NOT NULL,
                    player_id TEXT,           -- NULL = world fact, not player-specific
                    fact TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_facts_npc ON semantic_facts (npc_id);
            """)
        collections = await self._qdrant.get_collections()
        if COLLECTION not in [c.name for c in collections.collections]:
            await self._qdrant.create_collection(
                COLLECTION,
                vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
            )

    def _embed(self, text: str) -> list[float]:
        return list(next(iter(self._embedder.embed([text]))))

    # -- episodic -----------------------------------------------------------

    async def remember_episode(self, e: EpisodicEntry) -> None:
        await self._qdrant.upsert(
            COLLECTION,
            points=[PointStruct(
                id=str(uuid.uuid4()),
                vector=self._embed(e.text),
                payload={"npc_id": e.npc_id, "player_id": e.player_id,
                         "text": e.text, "ts": e.ts},
            )],
        )

    async def recall_similar(
        self, npc_id: str, player_id: str, query: str, limit: int = 4
    ) -> list[str]:
        """
        Topic-relevant recall, scoped to THIS player. Previously filtered only
        by npc_id - a real bug where two different players talking to the same
        NPC would have their conversations bleed into each other's recalled
        memories (NPC could "remember" something player B said and surface it
        while talking to player A). Always filter by both.
        """
        # AsyncQdrantClient.search() was removed in favor of query_points()
        # (qdrant-client >= ~1.10) - the response wraps hits in .points.
        result = await self._qdrant.query_points(
            COLLECTION,
            query=self._embed(query),
            query_filter=Filter(must=[
                FieldCondition(key="npc_id", match=MatchValue(value=npc_id)),
                FieldCondition(key="player_id", match=MatchValue(value=player_id)),
            ]),
            limit=limit,
        )
        return [h.payload["text"] for h in result.points]

    async def recall_recent(
        self, npc_id: str, player_id: str, limit: int = 3
    ) -> list[str]:
        """
        Chronological recall (most recent exchanges), scoped to this player -
        distinct from recall_similar's topic-similarity search. Needed so an
        NPC can reference "last time we talked" even when the player's current
        message (e.g. a plain "hi") doesn't semantically match the earlier
        topic closely enough for similarity search to surface it.
        """
        points, _ = await self._qdrant.scroll(
            COLLECTION,
            scroll_filter=Filter(must=[
                FieldCondition(key="npc_id", match=MatchValue(value=npc_id)),
                FieldCondition(key="player_id", match=MatchValue(value=player_id)),
            ]),
            limit=1024,
            with_payload=True,
        )
        points.sort(key=lambda p: p.payload["ts"], reverse=True)
        return [p.payload["text"] for p in points[:limit]]

    # -- semantic -----------------------------------------------------------

    async def add_fact(self, npc_id: str, fact: str, player_id: str | None = None) -> None:
        async with self._pg.acquire() as conn:
            await conn.execute(
                "INSERT INTO semantic_facts (id, npc_id, player_id, fact, created_at) "
                "VALUES ($1, $2, $3, $4, $5)",
                uuid.uuid4(), npc_id, player_id, fact, time.time(),
            )

    async def get_facts(self, npc_id: str, player_id: str | None = None,
                        limit: int = 6) -> list[str]:
        async with self._pg.acquire() as conn:
            rows = await conn.fetch(
                "SELECT fact FROM semantic_facts WHERE npc_id = $1 "
                "AND (player_id IS NULL OR player_id = $2) "
                "ORDER BY created_at DESC LIMIT $3",
                npc_id, player_id, limit,
            )
        return [r["fact"] for r in rows]

    # -- compression (run as offline/low-priority job) ----------------------

    async def episodic_count(self, npc_id: str) -> int:
        res = await self._qdrant.count(
            COLLECTION,
            count_filter=Filter(must=[
                FieldCondition(key="npc_id", match=MatchValue(value=npc_id)),
            ]),
        )
        return res.count

    async def oldest_batch(self, npc_id: str, n: int = COMPRESS_BATCH):
        """Fetch the oldest N raw episodes (ids + texts) for summarization."""
        points, _ = await self._qdrant.scroll(
            COLLECTION,
            scroll_filter=Filter(must=[
                FieldCondition(key="npc_id", match=MatchValue(value=npc_id)),
            ]),
            limit=1024,
            with_payload=True,
        )
        points.sort(key=lambda p: p.payload["ts"])
        batch = points[:n]
        return [p.id for p in batch], [p.payload["text"] for p in batch]

    async def delete_points(self, ids: list) -> None:
        await self._qdrant.delete(COLLECTION, points_selector=ids)


COMPRESSION_PROMPT = """Below are {n} raw interaction notes from an NPC's memory.
Distill them into at most 2 short, durable facts worth remembering long-term
(things about specific players, the world, or recurring events). Ignore
small talk. Reply as a JSON array of strings, nothing else.

Notes:
{notes}"""


async def compress_npc_memory(store: MemoryStore, llm_call, npc_id: str) -> int:
    """
    llm_call: async fn(prompt:str) -> str, wired by the orchestrator through
    the AMBIENT/low-priority path or run in an offline window.
    Returns number of raw episodes pruned.
    """
    if await store.episodic_count(npc_id) < COMPRESS_THRESHOLD:
        return 0
    ids, texts = await store.oldest_batch(npc_id)
    if not ids:
        return 0
    prompt = COMPRESSION_PROMPT.format(n=len(texts), notes="\n".join(f"- {t}" for t in texts))
    try:
        import json
        raw = await llm_call(prompt)
        facts = json.loads(raw)
        for fact in facts[:2]:
            if isinstance(fact, str) and fact.strip():
                await store.add_fact(npc_id, fact.strip())
    except Exception:
        logger.exception("compression summarize failed npc=%s (keeping raw)", npc_id)
        return 0
    await store.delete_points(ids)
    logger.info("compressed %d episodes -> facts for npc=%s", len(ids), npc_id)
    return len(ids)

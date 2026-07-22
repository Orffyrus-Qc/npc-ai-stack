"""
Structured "unresolved conversation" tracking - an NPC occasionally brings
up something left open from a past conversation with a specific player: an
unanswered question, a still-pending request, or a conversation that cut
off mid-topic. Detected by the model itself via the THREAD/THREAD_SUMMARY
tags (see llm_client.py's SYSTEM_TEMPLATE), piggybacked on the same
completion as ACTION/TONE - zero extra LLM calls.

Persistence: same Postgres as taming/personality/semantic facts.
"""

from __future__ import annotations

import time
import uuid

import asyncpg

PG_DSN = "postgresql://npc:npc@fact-db:5432/npc"

MAX_MENTIONS = 3           # past this many surfacings, mark stale rather than nag forever
MENTION_COOLDOWN_S = 600   # don't re-surface the same open thread within 10 minutes


class ThreadStore:
    def __init__(self):
        self._pg: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pg = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
        async with self._pg.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_threads (
                    id UUID PRIMARY KEY,
                    npc_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    opened_at DOUBLE PRECISION NOT NULL,
                    resolved_at DOUBLE PRECISION,
                    last_mentioned_at DOUBLE PRECISION,
                    times_mentioned INT NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS idx_threads_npc_player
                    ON conversation_threads (npc_id, player_id, status);
            """)

    async def open_thread(self, npc_id: str, player_id: str, summary: str) -> None:
        """
        One open thread per (npc_id, player_id) at a time - a new OPEN while
        one is already open replaces its summary rather than piling up
        several, keeping "occasionally brings up ONE thing" simple. A real
        conversation rarely has more than one real loose end with the same
        person at once worth surfacing.
        """
        async with self._pg.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id FROM conversation_threads "
                "WHERE npc_id=$1 AND player_id=$2 AND status='open'",
                npc_id, player_id)
            if existing:
                await conn.execute(
                    "UPDATE conversation_threads SET summary=$1 WHERE id=$2",
                    summary, existing["id"])
            else:
                await conn.execute(
                    "INSERT INTO conversation_threads "
                    "(id, npc_id, player_id, summary, status, opened_at) "
                    "VALUES ($1,$2,$3,$4,'open',$5)",
                    uuid.uuid4(), npc_id, player_id, summary, time.time())

    async def resolve_thread(self, npc_id: str, player_id: str) -> None:
        async with self._pg.acquire() as conn:
            await conn.execute(
                "UPDATE conversation_threads SET status='resolved', resolved_at=$1 "
                "WHERE npc_id=$2 AND player_id=$3 AND status='open'",
                time.time(), npc_id, player_id)

    async def get_open_thread(self, npc_id: str, player_id: str) -> str | None:
        """
        Returns a hint-worthy summary if: an open thread exists, it hasn't
        already been surfaced MAX_MENTIONS times (past that, mark 'stale' -
        the player isn't engaging with it, so stop nagging), and it wasn't
        JUST surfaced (MENTION_COOLDOWN_S) - "sometimes", not every single
        reply. Marks the thread as mentioned (increments times_mentioned,
        bumps last_mentioned_at) whenever it returns a hint, since that's
        the point a hint actually reaches the prompt this turn - a known
        approximation, since there's no cheap way to tell from the reply
        text alone whether the model actually brought it up just because it
        was hinted.
        """
        async with self._pg.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, summary, times_mentioned, last_mentioned_at "
                "FROM conversation_threads "
                "WHERE npc_id=$1 AND player_id=$2 AND status='open' "
                "ORDER BY opened_at ASC LIMIT 1",
                npc_id, player_id)
            if not row:
                return None
            if row["times_mentioned"] >= MAX_MENTIONS:
                await conn.execute(
                    "UPDATE conversation_threads SET status='stale' WHERE id=$1",
                    row["id"])
                return None
            now = time.time()
            if row["last_mentioned_at"] is not None and now - row["last_mentioned_at"] < MENTION_COOLDOWN_S:
                return None
            await conn.execute(
                "UPDATE conversation_threads SET times_mentioned=times_mentioned+1, "
                "last_mentioned_at=$1 WHERE id=$2",
                now, row["id"])
            return row["summary"]

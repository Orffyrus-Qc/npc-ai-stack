"""
Taming/ownership: at most one tamed NPC per player, decided in-character by
the NPC itself (see llm_client.py's ACTION tag) based on trust built up in
personality.py - this module only enforces the hard constraint (1 per
player) and tracks who owns what. It does not decide *whether* to tame -
that's the model's call, gated on trust_of_player already in its prompt.

Persistence: same Postgres as semantic facts/personality.
"""

from __future__ import annotations

import time

import asyncpg

PG_DSN = "postgresql://npc:npc@fact-db:5432/npc"


class TamingStore:
    def __init__(self):
        self._pg: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pg = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
        async with self._pg.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS npc_taming (
                    npc_id TEXT PRIMARY KEY,
                    owner_player_id TEXT NOT NULL,
                    tamed_at DOUBLE PRECISION NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_taming_owner
                    ON npc_taming (owner_player_id);
            """)

    async def get_owner(self, npc_id: str) -> str | None:
        async with self._pg.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT owner_player_id FROM npc_taming WHERE npc_id=$1", npc_id)
        return row["owner_player_id"] if row else None

    async def owned_npc_for_player(self, player_id: str) -> str | None:
        async with self._pg.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT npc_id FROM npc_taming WHERE owner_player_id=$1", player_id)
        return row["npc_id"] if row else None

    async def try_tame(self, npc_id: str, player_id: str) -> bool:
        """
        Enforces the hard "1 tamed NPC per player" rule regardless of what
        the model decided - the model only decides *whether this NPC* is
        willing, not whether the rule is satisfied. Idempotent: taming an
        NPC you already own succeeds (no-op).
        """
        existing_owner = await self.get_owner(npc_id)
        if existing_owner is not None:
            return existing_owner == player_id
        existing_pet = await self.owned_npc_for_player(player_id)
        if existing_pet is not None:
            return False
        async with self._pg.acquire() as conn:
            await conn.execute(
                "INSERT INTO npc_taming (npc_id, owner_player_id, tamed_at) "
                "VALUES ($1,$2,$3) ON CONFLICT (npc_id) DO NOTHING",
                npc_id, player_id, time.time())
        return await self.get_owner(npc_id) == player_id

    async def release(self, npc_id: str) -> None:
        async with self._pg.acquire() as conn:
            await conn.execute("DELETE FROM npc_taming WHERE npc_id=$1", npc_id)

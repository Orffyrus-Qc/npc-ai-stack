"""
Evolving personality: bounded nudges to the trait vector after interactions.

Principles:
  - Every update is small (LEARNING_RATE) and clamped to [floor, ceiling]
    per trait so no single interaction (or spam) flips a character.
  - Traits decay slowly toward each NPC's BASELINE, so a grumpy blacksmith
    treated kindly warms up, but reverts toward grumpy over in-game weeks
    if left alone. Identity is sticky; mood is plastic.
  - trust_of_player is stored per (npc, player) pair; the other traits are
    per NPC.

Persistence: same Postgres as semantic facts.
"""

from __future__ import annotations

import time

import asyncpg

from llm_client import Personality

PG_DSN = "postgresql://npc:npc@fact-db:5432/npc"

LEARNING_RATE = 0.03          # max nudge per interaction event
DECAY_PER_DAY = 0.01          # drift toward baseline per real-time day
TRAIT_FLOOR = 0.05
TRAIT_CEIL = 0.95

# outcome -> {trait: direction}. Emitted by the plugin/orchestrator after
# an interaction resolves. Extend freely.
OUTCOME_EFFECTS: dict[str, dict[str, float]] = {
    "player_was_kind":      {"warmth": +1, "trust_of_player": +1},
    "player_was_rude":      {"warmth": -1, "trust_of_player": -1, "aggression": +0.5},
    "player_attacked_npc":  {"trust_of_player": -3, "aggression": +2, "warmth": -2},
    "player_helped_quest":  {"trust_of_player": +2, "warmth": +1},
    "player_gave_gift":     {"trust_of_player": +1.5, "warmth": +1},
    "joke_landed":          {"humor": +1},
    "player_shared_news":   {"curiosity": +0.5},
    "player_lied_caught":   {"trust_of_player": -2},
}


def _clamp(v: float) -> float:
    return max(TRAIT_FLOOR, min(TRAIT_CEIL, v))


def apply_outcome(p: Personality, outcome: str) -> Personality:
    effects = OUTCOME_EFFECTS.get(outcome, {})
    for trait, direction in effects.items():
        cur = getattr(p, trait)
        setattr(p, trait, _clamp(cur + LEARNING_RATE * direction))
    return p


def apply_decay(p: Personality, baseline: Personality, days_elapsed: float) -> Personality:
    amt = min(1.0, DECAY_PER_DAY * days_elapsed)
    for trait in ("warmth", "aggression", "humor", "curiosity", "trust_of_player"):
        cur = getattr(p, trait)
        base = getattr(baseline, trait)
        setattr(p, trait, _clamp(cur + (base - cur) * amt))
    return p


class PersonalityStore:
    def __init__(self):
        self._pg: asyncpg.Pool | None = None

    async def start(self) -> None:
        self._pg = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=4)
        async with self._pg.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS npc_personality (
                    npc_id TEXT NOT NULL,
                    player_id TEXT NOT NULL DEFAULT '',  -- '' = the NPC's shared traits
                    warmth REAL, aggression REAL, humor REAL,
                    curiosity REAL, trust_of_player REAL,
                    baseline JSONB NOT NULL,
                    updated_at DOUBLE PRECISION NOT NULL,
                    PRIMARY KEY (npc_id, player_id)
                );
                CREATE TABLE IF NOT EXISTS npc_outcome_log (
                    id BIGSERIAL PRIMARY KEY,
                    npc_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    ts DOUBLE PRECISION NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_outcome_log_npc_ts
                    ON npc_outcome_log (npc_id, ts);
            """)

    async def load(self, npc_id: str, player_id: str,
                   default_baseline: Personality) -> Personality:
        """
        Loads the NPC's shared traits merged with the per-player trust value,
        applying time decay on read. Creates rows lazily.
        """
        import json
        async with self._pg.acquire() as conn:
            shared = await conn.fetchrow(
                "SELECT * FROM npc_personality WHERE npc_id=$1 AND player_id=''",
                npc_id)
            now = time.time()
            if shared is None:
                b = default_baseline
                await conn.execute(
                    "INSERT INTO npc_personality VALUES ($1,'',$2,$3,$4,$5,$6,$7,$8)",
                    npc_id, b.warmth, b.aggression, b.humor, b.curiosity,
                    b.trust_of_player, json.dumps(vars(b)), now)
                shared_p = Personality(**vars(b))
                baseline = b
            else:
                baseline = Personality(**json.loads(shared["baseline"]))
                shared_p = Personality(
                    warmth=shared["warmth"], aggression=shared["aggression"],
                    humor=shared["humor"], curiosity=shared["curiosity"],
                    trust_of_player=baseline.trust_of_player)
                days = (now - shared["updated_at"]) / 86400.0
                shared_p = apply_decay(shared_p, baseline, days)

            rel = await conn.fetchrow(
                "SELECT trust_of_player, updated_at FROM npc_personality "
                "WHERE npc_id=$1 AND player_id=$2", npc_id, player_id)
            if rel is not None:
                trust = rel["trust_of_player"]
                days = (now - rel["updated_at"]) / 86400.0
                trust = _clamp(trust + (baseline.trust_of_player - trust)
                               * min(1.0, DECAY_PER_DAY * days))
                shared_p.trust_of_player = trust
            return shared_p

    async def record_outcome(self, npc_id: str, player_id: str, outcome: str,
                             default_baseline: Personality) -> Personality:
        import json
        p = await self.load(npc_id, player_id, default_baseline)
        p = apply_outcome(p, outcome)
        now = time.time()
        async with self._pg.acquire() as conn:
            await conn.execute(
                # player_id='' is a literal in the WHERE clause, not a bind
                # param - an unused positional arg here previously left $2
                # unreferenced in the query text, which asyncpg's prepare
                # step can't type-infer (IndeterminateDatatypeError).
                "UPDATE npc_personality SET warmth=$2, aggression=$3, humor=$4, "
                "curiosity=$5, updated_at=$6 WHERE npc_id=$1 AND player_id=''",
                npc_id, p.warmth, p.aggression, p.humor, p.curiosity, now)
            await conn.execute(
                "INSERT INTO npc_personality (npc_id, player_id, warmth, aggression, "
                "humor, curiosity, trust_of_player, baseline, updated_at) "
                "VALUES ($1,$2,0,0,0,0,$3,$4,$5) "
                "ON CONFLICT (npc_id, player_id) DO UPDATE "
                "SET trust_of_player=$3, updated_at=$5",
                npc_id, player_id, p.trust_of_player,
                json.dumps(vars(default_baseline)), now)
            await conn.execute(
                "INSERT INTO npc_outcome_log (npc_id, player_id, outcome, ts) "
                "VALUES ($1,$2,$3,$4)",
                npc_id, player_id, outcome, now)
        return p

    async def recent_outcome_counts(
        self, npc_id: str, since_days: float = 3.0
    ) -> dict[str, int]:
        """
        Counts of each outcome type for this NPC in the last `since_days`.
        Used by the skill-writer meta-agent to spot patterns worth writing
        a new skill for (e.g. lots of player_attacked_npc -> a self-defense
        skill might be worth proposing).
        """
        since_ts = time.time() - since_days * 86400.0
        async with self._pg.acquire() as conn:
            rows = await conn.fetch(
                "SELECT outcome, COUNT(*) AS n FROM npc_outcome_log "
                "WHERE npc_id=$1 AND ts >= $2 GROUP BY outcome ORDER BY n DESC",
                npc_id, since_ts)
        return {r["outcome"]: r["n"] for r in rows}

    async def all_npc_ids(self) -> list[str]:
        async with self._pg.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT npc_id FROM npc_personality WHERE npc_id != '_system'")
        return [r["npc_id"] for r in rows]

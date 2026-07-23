"""
Experience store — reinforcement-style memory of (goal, action, reward).

Persists to Postgres (same fact-db as personality) when available; also keeps
an in-process ring buffer for the current session.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import deque
from typing import Any

import asyncpg

from agent_brain.types import Experience

logger = logging.getLogger("npc.brain.exp")

PG_DSN = os.environ.get(
    "POSTGRES_DSN",
    "postgresql://npc:npc@fact-db:5432/npc",
)


class ExperienceStore:
    def __init__(self, maxlen: int = 500):
        self._buf: deque[Experience] = deque(maxlen=maxlen)
        self._pool: asyncpg.Pool | None = None

    async def start(self) -> None:
        try:
            self._pool = await asyncpg.create_pool(PG_DSN, min_size=1, max_size=3)
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS npc_experience (
                        id TEXT PRIMARY KEY,
                        npc_id TEXT NOT NULL,
                        player_id TEXT NOT NULL DEFAULT '',
                        goal TEXT NOT NULL,
                        action_name TEXT NOT NULL,
                        action_args JSONB NOT NULL DEFAULT '{}',
                        observation_ok BOOLEAN NOT NULL,
                        observation_summary TEXT NOT NULL,
                        reward DOUBLE PRECISION NOT NULL,
                        ts DOUBLE PRECISION NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_npc_exp_npc
                        ON npc_experience (npc_id, ts DESC);
                    CREATE TABLE IF NOT EXISTS npc_lessons (
                        id SERIAL PRIMARY KEY,
                        npc_id TEXT NOT NULL,
                        player_id TEXT NOT NULL DEFAULT '',
                        topic TEXT NOT NULL DEFAULT 'gameplay',
                        lesson TEXT NOT NULL,
                        confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                        ts DOUBLE PRECISION NOT NULL
                    );
                    """
                )
            logger.info("experience store ready")
        except Exception:
            logger.exception("experience store DB unavailable — buffer only")
            self._pool = None

    async def record(self, exp: Experience) -> None:
        self._buf.append(exp)
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO npc_experience
                    (id, npc_id, player_id, goal, action_name, action_args,
                     observation_ok, observation_summary, reward, ts)
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    exp.id,
                    exp.npc_id,
                    exp.player_id,
                    exp.goal,
                    exp.action_name,
                    json.dumps(exp.action_args),
                    exp.observation_ok,
                    exp.observation_summary[:2000],
                    exp.reward,
                    exp.ts,
                )
        except Exception:
            logger.exception("failed to persist experience")

    async def record_lesson(
        self,
        npc_id: str,
        player_id: str,
        lesson: str,
        confidence: float,
        topic: str = "gameplay",
    ) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO npc_lessons (npc_id, player_id, topic, lesson, confidence, ts)
                    VALUES ($1,$2,$3,$4,$5,$6)
                    """,
                    npc_id,
                    player_id or "",
                    topic,
                    lesson[:2000],
                    max(0.0, min(1.0, confidence)),
                    time.time(),
                )
        except Exception:
            logger.exception("failed to persist lesson")

    async def top_lessons(self, npc_id: str, limit: int = 5) -> list[str]:
        if not self._pool:
            return []
        try:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT lesson, confidence FROM npc_lessons
                    WHERE npc_id=$1
                    ORDER BY confidence DESC, ts DESC
                    LIMIT $2
                    """,
                    npc_id,
                    limit,
                )
            return [f"[{r['confidence']:.2f}] {r['lesson']}" for r in rows]
        except Exception:
            logger.exception("top_lessons failed")
            return []

    async def recent_reward_stats(self, npc_id: str, limit: int = 50) -> dict[str, Any]:
        recent = [e for e in reversed(self._buf) if e.npc_id == npc_id][:limit]
        if not recent and self._pool:
            try:
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(
                        """
                        SELECT reward, action_name FROM npc_experience
                        WHERE npc_id=$1 ORDER BY ts DESC LIMIT $2
                        """,
                        npc_id,
                        limit,
                    )
                rewards = [float(r["reward"]) for r in rows]
                return {
                    "n": len(rewards),
                    "mean_reward": sum(rewards) / len(rewards) if rewards else 0.0,
                    "actions": [r["action_name"] for r in rows[:10]],
                }
            except Exception:
                return {"n": 0, "mean_reward": 0.0, "actions": []}
        rewards = [e.reward for e in recent]
        return {
            "n": len(rewards),
            "mean_reward": sum(rewards) / len(rewards) if rewards else 0.0,
            "actions": [e.action_name for e in recent[:10]],
        }

    def best_actions_for_goal(self, goal_substr: str, limit: int = 5) -> list[Experience]:
        goal_l = goal_substr.lower()
        scored = [
            e for e in self._buf
            if goal_l in e.goal.lower() and e.observation_ok
        ]
        scored.sort(key=lambda e: e.reward, reverse=True)
        return scored[:limit]

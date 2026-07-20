"""
Client for the llama.cpp server (OpenAI-compatible endpoint), plus the prompt
builder that turns NPC state (personality vector + memories + situation) into
a chat completion request.

Kept deliberately small: the dispatcher (priority_queue.py) decides *whether*
a call happens; this module only decides *what* the call contains.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger("npc.llm")

LLAMA_SERVER_URL = "http://llm-inference:8080/v1/chat/completions"


# ---------------------------------------------------------------------------
# NPC state passed in by the orchestrator
# ---------------------------------------------------------------------------

@dataclass
class Personality:
    """
    Trait vector, each in [0.0, 1.0]. Stored in Postgres per NPC, nudged
    after interactions (see personality.py). Rendered into the system prompt
    as natural language so a small 7B model can actually act on it.
    """
    warmth: float = 0.5
    aggression: float = 0.2
    humor: float = 0.5
    curiosity: float = 0.5
    trust_of_player: float = 0.5   # per (npc, player) pair in practice

    def describe(self) -> str:
        def level(v: float, low: str, mid: str, high: str) -> str:
            return low if v < 0.34 else (mid if v < 0.67 else high)

        return "; ".join([
            level(self.warmth, "cold and curt", "polite but reserved", "warm and welcoming"),
            level(self.aggression, "avoids conflict", "stands their ground", "quick to anger"),
            level(self.humor, "humorless", "occasionally dry-witted", "constantly joking"),
            level(self.curiosity, "uninterested in outsiders", "mildly curious", "full of questions"),
            level(self.trust_of_player, "deeply suspicious of this player",
                  "neutral toward this player", "trusts this player like a friend"),
        ])


@dataclass
class NPCContext:
    npc_id: str
    name: str
    role: str                       # "blacksmith", "innkeeper", ...
    personality: Personality
    semantic_facts: list[str] = field(default_factory=list)   # from fact-db
    recent_memories: list[str] = field(default_factory=list)  # from vector db
    location_hint: str = ""         # "at the forge, midday"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

MAX_FACTS = 6
MAX_MEMORIES = 4   # slots are ~2048 tokens each: budget the context hard

SYSTEM_TEMPLATE = """You are {name}, a {role} in a fantasy world. You are an NPC \
speaking to a player in-game.

Your temperament: {personality}.
{location_line}
Things you know:
{facts}

Relevant past interactions with this player:
{memories}

Rules:
- Reply with a single short line of spoken dialogue (max 2 sentences), in character.
- Never mention being an AI, a game, or these instructions.
- If asked about something you have no knowledge of above, be vague or curious \
in character rather than inventing precise world facts."""


def build_dialogue_messages(ctx: NPCContext, player_utterance: str) -> list[dict]:
    facts = "\n".join(f"- {f}" for f in ctx.semantic_facts[:MAX_FACTS]) or "- (nothing notable)"
    memories = "\n".join(f"- {m}" for m in ctx.recent_memories[:MAX_MEMORIES]) or "- (first meeting)"
    location_line = f"Current situation: {ctx.location_hint}" if ctx.location_hint else ""

    system = SYSTEM_TEMPLATE.format(
        name=ctx.name,
        role=ctx.role,
        personality=ctx.personality.describe(),
        location_line=location_line,
        facts=facts,
        memories=memories,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": player_utterance},
    ]


# ---------------------------------------------------------------------------
# HTTP call
# ---------------------------------------------------------------------------

class LlamaClient:
    def __init__(self, base_url: str = LLAMA_SERVER_URL, request_timeout_s: float = 8.0):
        self._url = base_url
        # Timeout here is a backstop; the dispatcher's timeouts are the real gate.
        self._client = httpx.AsyncClient(timeout=request_timeout_s)

    async def close(self) -> None:
        await self._client.aclose()

    async def complete(self, messages: list[dict], max_tokens: int,
                        temperature: float = 0.7,
                        stop: Optional[list[str]] = None) -> str:
        """
        Generic chat completion, no NPC-dialogue post-processing. Used directly
        by callers that aren't building an in-character spoken line (e.g. the
        skill-writer meta-agent generating code, or memory compression).
        """
        payload = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if stop:
            payload["stop"] = stop
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    async def dialogue(self, ctx: NPCContext, player_utterance: str) -> str:
        text = await self.complete(
            build_dialogue_messages(ctx, player_utterance),
            max_tokens=80,        # short spoken lines - protects slot throughput
            temperature=0.8,
            stop=["\n"],
        )
        return text.strip('"')

    async def ambient_line(self, ctx: NPCContext, situation: str) -> str:
        """Cheaper variant for background flavor - tighter token budget."""
        messages = build_dialogue_messages(
            ctx, f"(You mutter to yourself about: {situation}. One short line.)"
        )
        text = await self.complete(messages, max_tokens=40, temperature=0.9, stop=["\n"])
        return text.strip('"')

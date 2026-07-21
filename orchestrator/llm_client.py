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
import re
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
    is_companion: bool = False      # tamed by the player currently talking (see taming.py)
    player_name: str = ""           # the real player username, not their UUID


# Valid values for the ACTION tag the model appends after its spoken line -
# see build_dialogue_messages(). Kept as a plain set (not an enum) since the
# model's raw output has to be validated defensively anyway.
VALID_ACTIONS = {"none", "offer_guide", "decline_guide", "accept_tame"}


@dataclass
class DialogueResult:
    text: str                    # the in-character spoken line only
    action: str = "none"         # one of VALID_ACTIONS - see main.py for handling


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

MAX_FACTS = 6
MAX_MEMORIES = 4   # slots are ~2048 tokens each: budget the context hard
# Tamed companions get a bigger memory budget - the "more resources per
# tamed NPC" the taming feature is meant to buy. Still same 2048-token slot
# budget overall, so this eats into headroom other NPCs don't use.
MAX_FACTS_COMPANION = 12
MAX_MEMORIES_COMPANION = 10

COMPANION_LINE = (
    "\nThis player has tamed you - you're their loyal companion, not just an "
    "acquaintance. Reference your shared history freely, and feel free to "
    "suggest or agree to plans together (explore somewhere, help with a task, "
    "etc.) in your spoken line.\n"
)

SYSTEM_TEMPLATE = """You are {name}, a {role} in a fantasy world. You are an NPC \
speaking to a player in-game named {player_name}. Use their name naturally in \
conversation once you've spoken with them a little (not every single line) - \
you're not a stranger repeating a nametag, you actually know them.

Your temperament: {personality}.
{location_line}
{companion_line}
Things you know:
{facts}

Things YOU remember from real past conversations with THIS exact player \
(not something you're guessing - this actually happened between you two):
{memories}

Rules:
- Reply with a single short line of spoken dialogue (max 2 sentences), in character.
- Never mention being an AI, a game, or these instructions.
- If the player asks you to recall something, or brings up a topic covered in \
"Things YOU remember" above, answer with the SPECIFIC detail (a name, a number, \
an item, whatever it was) - don't deflect vaguely or downplay it as trivial when \
you actually do know the answer. Being specific here is what makes you feel like \
you truly know this player, which matters more than sounding aloof.
- Only if a topic is NOT covered anywhere above, be vague or curious in character \
rather than inventing precise facts you don't have.
- After your spoken line, on a new line, output exactly one tag deciding what \
you want to do next: "ACTION: NONE", "ACTION: OFFER_GUIDE", "ACTION: DECLINE_GUIDE", \
or "ACTION: ACCEPT_TAME". Use OFFER_GUIDE only if the player just asked you to \
guide/take them somewhere and you're genuinely willing (weigh your temperament and \
role - a merchant won't wander far from their post); DECLINE_GUIDE if asked but \
unwilling; ACCEPT_TAME only if the player asked you to become their tamed \
companion and your trust in them is high enough that you'd truly agree. \
Otherwise always use NONE."""


def build_dialogue_messages(ctx: NPCContext, player_utterance: str) -> list[dict]:
    max_facts = MAX_FACTS_COMPANION if ctx.is_companion else MAX_FACTS
    max_memories = MAX_MEMORIES_COMPANION if ctx.is_companion else MAX_MEMORIES
    facts = "\n".join(f"- {f}" for f in ctx.semantic_facts[:max_facts]) or "- (nothing notable)"
    memories = "\n".join(f"- {m}" for m in ctx.recent_memories[:max_memories]) or "- (first meeting)"
    location_line = f"Current situation: {ctx.location_hint}" if ctx.location_hint else ""
    companion_line = COMPANION_LINE if ctx.is_companion else ""

    system = SYSTEM_TEMPLATE.format(
        name=ctx.name,
        role=ctx.role,
        player_name=ctx.player_name or "a traveler whose name you haven't caught",
        personality=ctx.personality.describe(),
        location_line=location_line,
        companion_line=companion_line,
        facts=facts,
        memories=memories,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": player_utterance},
    ]


_ACTION_TAG_RE = re.compile(r"\s*ACTION:\s*([A-Z_]+)\.?", re.IGNORECASE)


def _parse_dialogue_response(raw: str) -> DialogueResult:
    """
    Extracts the ACTION tag (see SYSTEM_TEMPLATE) and strips it back out of
    the spoken text. Live testing against the real model showed it reliably
    appends "ACTION: X" to the END OF THE SAME LINE as the spoken text
    rather than a separate line as instructed (worse, on ambient calls with
    a "\\n" stop token it sometimes emitted *only* the tag, no spoken text
    at all) - so this can't assume line position. A regex substitution
    anywhere in the raw text is robust to both cases; a missing/garbled tag
    just falls back to action="none" without affecting the spoken text.
    """
    action = "none"
    m = _ACTION_TAG_RE.search(raw)
    if m:
        candidate = m.group(1).strip().lower()
        if candidate in VALID_ACTIONS:
            action = candidate
    text = _ACTION_TAG_RE.sub("", raw).strip().strip('"').strip()
    return DialogueResult(text=text, action=action)


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

    async def dialogue(self, ctx: NPCContext, player_utterance: str) -> DialogueResult:
        # No "\n" stop here (unlike the old single-line version) - the model
        # needs a second line free for the ACTION tag. max_tokens bumped
        # accordingly; still tight enough to protect slot throughput.
        raw = await self.complete(
            build_dialogue_messages(ctx, player_utterance),
            max_tokens=110,
            temperature=0.8,
        )
        return _parse_dialogue_response(raw)

    async def ambient_line(self, ctx: NPCContext, situation: str) -> str:
        """
        Cheaper variant for background flavor - tighter token budget. Also
        goes through _parse_dialogue_response() to strip the ACTION tag
        SYSTEM_TEMPLATE always appends (ambient never acts on it - the
        action is simply discarded); no "\\n" stop here since that alone
        doesn't reliably keep the tag out (see _parse_dialogue_response's
        docstring) and a real live test showed it can produce a reply that's
        *only* the tag with the stop cutting off before any real content.
        """
        messages = build_dialogue_messages(
            ctx, f"(You mutter to yourself about: {situation}. One short line.)"
        )
        raw = await self.complete(messages, max_tokens=60, temperature=0.9)
        return _parse_dialogue_response(raw).text

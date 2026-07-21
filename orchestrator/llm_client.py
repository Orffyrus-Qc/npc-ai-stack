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
            level(self.aggression,
                  "avoids danger and physical confrontation, would rather not fight",
                  "willing to stand and fight if it comes to that",
                  "eager for a fight, itching for a reason to draw a weapon"),
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
    is_tamed_by_anyone: bool = False  # this NPC has left to be SOMEONE's companion
                                       # (not necessarily this player's) - distinct from
                                       # is_companion, and what gates shop access below


# Valid values for the ACTION tag the model appends after its spoken line -
# see build_dialogue_messages(). Kept as a plain set (not an enum) since the
# model's raw output has to be validated defensively anyway.
VALID_ACTIONS = {"none", "offer_guide", "offer_fight", "decline_guide", "accept_tame", "open_shop"}

# Valid values for the TONE tag - see build_dialogue_messages()'s TONE rule.
# Deliberately named "tone" here, distinct from the wire-protocol/
# personality.py sense of "outcome" (player_was_kind/player_was_rude/etc,
# see main.py's handle_dialogue) - this is the raw per-turn signal the model
# reports; main.py decides whether/how to turn "kind"/"rude" into an actual
# recorded outcome. Not every tone maps to an outcome - "neutral" records
# nothing, on purpose (most exchanges are just ordinary conversation, and
# treating every single turn as an evaluable event would flood
# npc_outcome_log with noise and nudge personality on pure small talk).
VALID_TONES = {"kind", "rude", "neutral"}


@dataclass
class DialogueResult:
    text: str                    # the in-character spoken line only
    action: str = "none"         # one of VALID_ACTIONS - see main.py for handling
    tone: str = "neutral"        # one of VALID_TONES - see main.py for handling


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

SHOP_LINE_AVAILABLE = (
    "\nIf this player asks to see your wares, browse, or buy/sell something, "
    "and you're still running your stall (see below), that's a normal request - "
    "react warmly and use the OPEN_SHOP action for it.\n"
)
SHOP_LINE_UNAVAILABLE = (
    "\nYou no longer run a shop or carry trade goods - you left that behind "
    "when you became someone's companion. If asked to see your wares or trade, "
    "decline warmly in character (you're not selling anymore); never use "
    "OPEN_SHOP.\n"
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
- Check "Things YOU remember" above before you speak: if it shows you already said \
something very close to what you're about to say again, DO NOT repeat that same \
line - a real person doesn't say the exact same sentence every time they're greeted. \
Vary your wording, add a new detail, or react to the fact that the player is back \
again instead.
- After your spoken line, on a new line, output exactly one tag deciding what \
you want to do next: "ACTION: NONE", "ACTION: OFFER_GUIDE", "ACTION: OFFER_FIGHT", \
"ACTION: DECLINE_GUIDE", "ACTION: ACCEPT_TAME", or "ACTION: OPEN_SHOP". These only \
apply if the player just asked you to lead them somewhere, help against a threat, \
become their tamed companion, or see your wares/trade - otherwise always use NONE.
- Also output a second tag, on its own line, judging how the player treated \
YOU personally in THIS message only (not their history, not the world) - \
"TONE: KIND", "TONE: RUDE", or "TONE: NEUTRAL". KIND means real warmth, \
gratitude, a compliment, or a genuinely thoughtful gesture aimed at you. RUDE \
means insults, mockery, threats, or dismissiveness aimed at you. Ordinary \
conversation, questions, requests, or small talk - even blunt or curt ones - \
are NEUTRAL; reserve KIND/RUDE for messages where the player's tone toward \
you personally is actually unmistakable, not just "not perfectly polite".
- If asked to help against a hostile creature (including one mentioned in your \
current situation below) or to lead the player somewhere: decide for yourself, \
weighing your own courage/aggression, your trust in this player, and your role \
(a merchant tied to their post is a very different case from a bold adventurer) \
whether you'd (a) actually fight alongside them - OFFER_FIGHT, (b) lead them \
there but leave the fighting to them - OFFER_GUIDE, or (c) refuse entirely - \
DECLINE_GUIDE. Concretely: if your temperament above says you avoid danger and \
physical confrontation, OR you don't yet trust this player much, do NOT pick \
OFFER_FIGHT - use OFFER_GUIDE (if mildly willing to help at all) or DECLINE_GUIDE \
(if not). Only pick OFFER_FIGHT if you're genuinely someone who stands and \
fights (or actively seeks a fight) AND you have real trust in this player - \
being sociable or warm is not the same as being willing to risk your life in \
combat, don't confuse the two.
- ACCEPT_TAME only if the player asked you to become their tamed companion and \
your trust in them is high enough that you'd truly agree.
{shop_line}"""


# Capping facts/memories by *count* alone (MAX_FACTS_COMPANION etc. above)
# assumes each entry is short - true on average, but nothing enforces it,
# and one real production incident already came from exactly this gap (an
# unrelated caller wrapping a large summarization prompt in this same
# template - see main.py's low_prio_llm fix). Facts/memories are free-form
# LLM-generated text with no hard length cap of their own, so counts alone
# can't guarantee the assembled prompt fits a 2048-token slot
# (docker-compose.yml's --ctx-size/--parallel). This estimate doesn't need
# to be exact, just conservative enough that the real tokenizer's count
# stays under the true limit with margin for the chat template's own
# overhead and this request's max_tokens completion budget.
_CHARS_PER_TOKEN_ESTIMATE = 4
_PROMPT_TOKEN_BUDGET = 1700


def _approx_tokens(text: str) -> int:
    return len(text) // _CHARS_PER_TOKEN_ESTIMATE


def build_dialogue_messages(ctx: NPCContext, player_utterance: str) -> list[dict]:
    max_facts = MAX_FACTS_COMPANION if ctx.is_companion else MAX_FACTS
    max_memories = MAX_MEMORIES_COMPANION if ctx.is_companion else MAX_MEMORIES
    # Both already ordered newest/most-relevant-first (get_facts' ORDER BY,
    # recall_recent's sort) - trimming from the end below drops the oldest/
    # least-relevant entries first.
    facts_list = list(ctx.semantic_facts[:max_facts])
    memories_list = list(ctx.recent_memories[:max_memories])
    shop_line = SHOP_LINE_UNAVAILABLE if ctx.is_tamed_by_anyone else SHOP_LINE_AVAILABLE
    location_line = f"Current situation: {ctx.location_hint}" if ctx.location_hint else ""
    companion_line = COMPANION_LINE if ctx.is_companion else ""

    def render() -> str:
        facts = "\n".join(f"- {f}" for f in facts_list) or "- (nothing notable)"
        memories = "\n".join(f"- {m}" for m in memories_list) or "- (first meeting)"
        return SYSTEM_TEMPLATE.format(
            name=ctx.name,
            role=ctx.role,
            player_name=ctx.player_name or "a traveler whose name you haven't caught",
            personality=ctx.personality.describe(),
            location_line=location_line,
            companion_line=companion_line,
            facts=facts,
            memories=memories,
            shop_line=shop_line,
        )

    system = render()
    budget = _PROMPT_TOKEN_BUDGET - _approx_tokens(player_utterance)
    # Alternate trimming whichever list is currently longer so one can't be
    # emptied to zero while the other stays fat - both are informative, and
    # a companion with 100+ memories but 0 facts (or vice versa) reads worse
    # than a slightly shorter version of both.
    while _approx_tokens(system) > budget and (facts_list or memories_list):
        if len(memories_list) >= len(facts_list) and memories_list:
            memories_list.pop()
        elif facts_list:
            facts_list.pop()
        system = render()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": player_utterance},
    ]


_ACTION_TAG_RE = re.compile(r"\s*ACTION:\s*([A-Z_]+)\.?", re.IGNORECASE)
_TONE_TAG_RE = re.compile(r"\s*TONE:\s*([A-Z_]+)\.?", re.IGNORECASE)


def _parse_dialogue_response(raw: str) -> DialogueResult:
    """
    Extracts the ACTION and TONE tags (see SYSTEM_TEMPLATE) and strips them
    back out of the spoken text. Live testing against the real model showed
    it reliably appends "ACTION: X" to the END OF THE SAME LINE as the
    spoken text rather than a separate line as instructed (worse, on
    ambient calls with a "\\n" stop token it sometimes emitted *only* the
    tag, no spoken text at all) - so this can't assume line position. A
    regex substitution anywhere in the raw text is robust to both cases;
    the same treatment applies to TONE, added later - a missing/garbled tag
    just falls back to its default (action="none", tone="neutral") without
    affecting the spoken text.
    """
    action = "none"
    m = _ACTION_TAG_RE.search(raw)
    if m:
        candidate = m.group(1).strip().lower()
        if candidate in VALID_ACTIONS:
            action = candidate
    tone = "neutral"
    m = _TONE_TAG_RE.search(raw)
    if m:
        candidate = m.group(1).strip().lower()
        if candidate in VALID_TONES:
            tone = candidate
    text = _ACTION_TAG_RE.sub("", raw)
    text = _TONE_TAG_RE.sub("", text).strip().strip('"').strip()
    return DialogueResult(text=text, action=action, tone=tone)


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
                        stop: Optional[list[str]] = None,
                        repeat_penalty: Optional[float] = None,
                        presence_penalty: Optional[float] = None) -> str:
        """
        Generic chat completion, no NPC-dialogue post-processing. Used directly
        by callers that aren't building an in-character spoken line (e.g. the
        skill-writer meta-agent generating code, or memory compression).
        """
        payload = {"messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if stop:
            payload["stop"] = stop
        # repeat_penalty is a llama.cpp-server extension (not standard OpenAI
        # API); presence_penalty is standard but llama.cpp honors it too. Both
        # left unset (None) for generic callers like skill_writer/compression
        # that don't need them - only dialogue/ambient set these, since a
        # small 7B model reusing near-identical prompts (same personality,
        # same cached situation, similar short player messages) otherwise
        # reliably regenerates the exact same line turn after turn, confirmed
        # by live testing (see dialogue()'s comment).
        if repeat_penalty is not None:
            payload["repeat_penalty"] = repeat_penalty
        if presence_penalty is not None:
            payload["presence_penalty"] = presence_penalty
        resp = await self._client.post(self._url, json=payload)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()

    async def dialogue(self, ctx: NPCContext, player_utterance: str) -> DialogueResult:
        # No "\n" stop here (unlike the old single-line version) - the model
        # needs two lines free now: the ACTION tag plus the TONE tag added
        # later (see SYSTEM_TEMPLATE and main.py's use of .tone for outcome
        # inference). max_tokens bumped accordingly; still tight enough to
        # protect slot throughput.
        #
        # repeat_penalty/presence_penalty added after live testing showed
        # NPCs reliably repeating the exact same greeting/offer line verbatim
        # across separate conversation turns - the prompt is nearly identical
        # each time (same personality, same cached situation, short similar
        # player messages), so even temperature=0.8 alone wasn't enough
        # variety. Combined with the new "don't repeat yourself" rule in
        # SYSTEM_TEMPLATE (which needs the model to actually notice the
        # repeat via "Things YOU remember") - the penalty works even when
        # memory recall doesn't surface a matching past line.
        raw = await self.complete(
            build_dialogue_messages(ctx, player_utterance),
            max_tokens=130,
            temperature=0.8,
            repeat_penalty=1.15,
            presence_penalty=0.4,
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
        raw = await self.complete(messages, max_tokens=60, temperature=0.9,
                                   repeat_penalty=1.15, presence_penalty=0.4)
        return _parse_dialogue_response(raw).text

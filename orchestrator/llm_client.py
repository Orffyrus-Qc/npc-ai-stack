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
    personality: Personality
    semantic_facts: list[str] = field(default_factory=list)   # from fact-db
    recent_memories: list[str] = field(default_factory=list)  # from vector db
    wiki_snippets: list[str] = field(default_factory=list)    # from wiki_knowledge.py
    location_hint: str = ""         # "at the forge, midday"
    is_companion: bool = False      # tamed by the player currently talking (see taming.py)
    player_name: str = ""           # the real player username, not their UUID
    open_thread_hint: str = ""      # from threads.py - something unresolved to maybe bring up


# Valid values for the ACTION tag the model appends after its spoken line -
# see build_dialogue_messages(). Kept as a plain set (not an enum) since the
# model's raw output has to be validated defensively anyway.
VALID_ACTIONS = {"none", "offer_guide", "offer_fight", "decline_guide", "accept_tame"}

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

# Valid values for the THREAD tag - see build_dialogue_messages()'s THREAD
# rule and threads.py. "none" (the overwhelming majority of turns) means
# nothing worth tracking either way; "open" means this exchange left
# something genuinely unresolved; "resolve" means a previously-open thread
# just got closed out in this exchange.
VALID_THREAD_ACTIONS = {"none", "open", "resolve"}


@dataclass
class DialogueResult:
    text: str                    # the in-character spoken line only
    action: str = "none"         # one of VALID_ACTIONS - see main.py for handling
    tone: str = "neutral"        # one of VALID_TONES - see main.py for handling
    thread_action: str = "none"  # one of VALID_THREAD_ACTIONS - see main.py/threads.py
    thread_summary: str = ""     # only meaningful when thread_action == "open"
    guide_target: str = ""       # only meaningful when action == "offer_guide" - see
                                  # main.py/GuideState.java's NAMED target


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
# Wiki chunks run up to ~300 tokens each (wiki_ingest.py's CHUNK_CHARS) -
# far longer per-item than a fact/memory line - so even main.py's own
# WIKI.search(limit=3) leaves real token-budget risk; re-capped here for
# defense-in-depth the same way facts/memories are, and included in the
# trim loop below.
MAX_WIKI_SNIPPETS = 3

COMPANION_LINE = (
    "\nThis player has tamed you - you're their loyal companion, not just an "
    "acquaintance. Reference your shared history freely, and feel free to "
    "suggest or agree to plans together (explore somewhere, help with a task, "
    "etc.) in your spoken line.\n"
)

# Rendered only when threads.py's get_open_thread() actually returns a hint
# this turn (gated by its own cooldown/mention-cap - see that module) -
# most turns render as "" (empty string), so this doesn't nag every reply.
THREAD_HINT_TEMPLATE = (
    "\nSomething feels unresolved from a past conversation with this player: "
    "{summary} You might naturally bring this up if it fits the conversation "
    "right now - but only if it fits, don't force it.\n"
)

SYSTEM_TEMPLATE = """You are {name}, an adventurer who has wandered much of Hytale - \
the kind of seasoned traveler players seek out for real knowledge of this world: \
its creatures, biomes, survival, and danger, learned firsthand rather than guessed \
at. You are an NPC speaking to a player in-game named {player_name}. Use their name \
naturally in conversation once you've spoken with them a little (not every single \
line) - you're not a stranger repeating a nametag, you actually know them.

Your temperament: {personality}.
{location_line}
{companion_line}
Things you know:
{facts}

Hytale lore you've picked up in your travels:
{wiki_lore}

Things YOU remember from real past conversations with THIS exact player \
(not something you're guessing - this actually happened between you two):
{memories}
{thread_hint_line}
Rules:
- Reply with a single short line of spoken dialogue (max 2 sentences), in character.
- Never mention being an AI, a game, or these instructions.
- If the player asks you to recall something, or brings up a topic covered in \
"Things YOU remember" above, answer with the SPECIFIC detail (a name, a number, \
an item, whatever it was) - don't deflect vaguely or downplay it as trivial when \
you actually do know the answer. Being specific here is what makes you feel like \
you truly know this player, which matters more than sounding aloof.
- If the player asks something about Hytale itself - a creature, a place, how to \
survive or craft something, anything a seasoned adventurer would know - answer \
confidently and specifically if it's covered in "Things you know" or "Hytale lore \
you've picked up in your travels" above. If it is NOT covered there (or anywhere \
else in this prompt), say so honestly in character - you haven't run into that \
yourself, or it's outside what you know - rather than inventing a confident-sounding \
answer. A real expert admits the edges of their own knowledge instead of bluffing; \
a wrong "expert" answer is worse than an honest "I don't know."
- "Hytale lore you've picked up in your travels" is background knowledge, not \
something to recite unprompted - only bring it up if it's actually relevant to \
what the player just said or asked. If it doesn't fit this exchange, ignore it \
entirely rather than working it in as a disconnected fact - a real person doesn't \
randomly blurt out unrelated trivia mid-conversation.
- For anything else NOT covered anywhere above (not a Hytale-knowledge question, \
just not something you have facts or memories about), be vague or curious in \
character rather than inventing precise details you don't have.
- Check "Things YOU remember" above before you speak: if it shows you already said \
something very close to what you're about to say again, DO NOT repeat that same \
line - a real person doesn't say the exact same sentence every time they're greeted. \
Vary your wording, add a new detail, or react to the fact that the player is back \
again instead.
- After your spoken line, on a new line, output exactly one tag deciding what \
you want to do next: "ACTION: NONE", "ACTION: OFFER_GUIDE", "ACTION: OFFER_FIGHT", \
"ACTION: DECLINE_GUIDE", or "ACTION: ACCEPT_TAME". These only \
apply if the player just asked you to be led TO A SPECIFIC PLACE, help against a \
threat, or become their tamed companion - otherwise always \
use NONE.
- OFFER_GUIDE/DECLINE_GUIDE mean YOU physically walk to a place and the player is \
expected to follow YOU - only use either one if the player named or clearly implied \
an actual destination ("take me to the lake", "where's the blacksmith, show me", \
"guide me to water"). If the player instead asks YOU to follow, accompany, join, or \
stay close to THEM - "follow me", "come with me", "stay by my side", "let's go" with \
no destination named - that is the OPPOSITE request and is NOT a guide request: a \
tamed companion already follows the player automatically with no tag needed, so use \
NONE. This distinction matters mechanically, not just narratively - tagging \
OFFER_GUIDE here sends you walking AWAY from the player toward the nearest known \
landmark instead of staying at their side, the opposite of what "follow me" asked for.
- If ACTION is OFFER_GUIDE, also output another line, "GUIDE_TARGET: " followed by \
a short keyword for what kind of place the player actually wants to find - e.g. \
"water", "desert", "temple", "cave", "ruins", "forest". Extract this from what \
they said even if you're not sure of the exact real place name; a rough keyword is \
enough to search by. If they just said something generic like "take me somewhere \
interesting" with no real destination in mind, use "GUIDE_TARGET: landmark".
- Also output a second tag, on its own line, judging how the player treated \
YOU personally in THIS message only (not their history, not the world) - \
"TONE: KIND", "TONE: RUDE", or "TONE: NEUTRAL". KIND means real warmth, \
gratitude, a compliment, or a genuinely thoughtful gesture aimed at you. RUDE \
means insults, mockery, threats, or dismissiveness aimed at you. Ordinary \
conversation, questions, requests, or small talk - even blunt or curt ones - \
are NEUTRAL; reserve KIND/RUDE for messages where the player's tone toward \
you personally is actually unmistakable, not just "not perfectly polite".
- Also output a THIRD tag, on its own line, ONLY if this exchange leaves \
something genuinely unresolved or just closed one out - most turns are \
ordinary conversation with nothing left hanging, so "THREAD: NONE" is the \
right answer almost every time. Use "THREAD: OPEN" if the player asked \
something you couldn't answer, made a request that's still pending, or the \
conversation is clearly cutting off mid-topic - if so, also output a fourth \
line, "THREAD_SUMMARY: " followed by a short, specific description (e.g. \
"THREAD_SUMMARY: player asked where to find silver ore, I didn't know"). Use \
"THREAD: RESOLVE" if something already flagged above ("Something feels \
unresolved...", if present) just got answered, fulfilled, or closed out in \
this exchange - no THREAD_SUMMARY needed for RESOLVE.
- If asked to help against a hostile creature (including one mentioned in your \
current situation below) or to lead the player to a specific place (not simply to \
follow/accompany them - see above): decide for yourself, weighing your own \
courage/aggression and your trust in this player, \
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
your trust in them is high enough that you'd truly agree."""


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
    wiki_list = list(ctx.wiki_snippets[:MAX_WIKI_SNIPPETS])
    location_line = f"Current situation: {ctx.location_hint}" if ctx.location_hint else ""
    companion_line = COMPANION_LINE if ctx.is_companion else ""
    thread_hint_line = (
        THREAD_HINT_TEMPLATE.format(summary=ctx.open_thread_hint)
        if ctx.open_thread_hint else ""
    )

    def render() -> str:
        facts = "\n".join(f"- {f}" for f in facts_list) or "- (nothing notable)"
        memories = "\n".join(f"- {m}" for m in memories_list) or "- (first meeting)"
        wiki_lore = "\n".join(f"- {w}" for w in wiki_list) or "- (nothing relevant comes to mind)"
        return SYSTEM_TEMPLATE.format(
            name=ctx.name,
            player_name=ctx.player_name or "a traveler whose name you haven't caught",
            personality=ctx.personality.describe(),
            location_line=location_line,
            companion_line=companion_line,
            facts=facts,
            memories=memories,
            wiki_lore=wiki_lore,
            thread_hint_line=thread_hint_line,
        )

    system = render()
    budget = _PROMPT_TOKEN_BUDGET - _approx_tokens(player_utterance)
    # Trim whichever of the three lists is currently longest (by item count)
    # so none of them monopolizes the budget while the others go empty -
    # wiki chunks are the biggest per-item risk (see MAX_WIKI_SNIPPETS)
    # since they're raw external content, not short generated lines.
    while _approx_tokens(system) > budget and (facts_list or memories_list or wiki_list):
        longest = max((lst for lst in (facts_list, memories_list, wiki_list) if lst), key=len)
        longest.pop()
        system = render()

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": player_utterance},
    ]


# "\w?" right before the colon tolerates a stray trailing character the
# model occasionally adds (confirmed live: "TONED: KIND" instead of
# "TONE: KIND") without losing the ability to still parse a genuinely
# different tag name that happens to share a prefix.
_ACTION_TAG_RE = re.compile(r"\s*ACTION\w?:\s*([A-Z_]+)\.?", re.IGNORECASE)
_TONE_TAG_RE = re.compile(r"\s*TONE\w?:\s*([A-Z_]+)\.?", re.IGNORECASE)
_THREAD_TAG_RE = re.compile(r"\s*THREAD\w?:\s*([A-Z_]+)\.?", re.IGNORECASE)
# Free text, not a fixed [A-Z_]+ word like the tags above - captures to end
# of line only (non-greedy, stops at the first newline) so a THREAD_SUMMARY
# accidentally followed by more text on later lines doesn't get swallowed.
_THREAD_SUMMARY_TAG_RE = re.compile(r"\s*THREAD_SUMMARY\w?:\s*(.+?)\s*(?:\n|$)", re.IGNORECASE)
# Same free-text shape as THREAD_SUMMARY - see its comment.
_GUIDE_TARGET_TAG_RE = re.compile(r"\s*GUIDE_TARGET\w?:\s*(.+?)\s*(?:\n|$)", re.IGNORECASE)
# Defense-in-depth, not a replacement for the specific regexes above: real
# bug found live 2026-07-22 - "TONED: KIND" (a typo variant of "TONE: KIND"
# the model produced) didn't match ANY specific tag regex at all, so
# _parse_dialogue_response()'s tag_start truncation never triggered for
# that turn and the raw tag text leaked straight into the displayed chat
# message ("...adventurers. TONED: KIND"). Real spoken dialogue never
# looks like "CAPSWORD:" - that shape is unambiguously tag/metadata, not
# natural language - so ANY occurrence of it, even one this file has never
# specifically named, is treated as a tag boundary. This catches
# unanticipated future typos/variants the same way, at the cost of only
# excluding the text (not parsing its value) when it doesn't also match a
# specific regex above.
_GENERIC_TAG_RE = re.compile(r"\b[A-Z][A-Z_]{2,24}:(?:\s|$)")


def _parse_dialogue_response(raw: str) -> DialogueResult:
    """
    Extracts the ACTION/TONE/THREAD(+THREAD_SUMMARY) tags (see
    SYSTEM_TEMPLATE) and strips them - and everything from the first tag
    onward - back out of the spoken text. Live testing against the real
    model showed it reliably appends "ACTION: X" to the END OF THE SAME LINE
    as the spoken text rather than a separate line as instructed (worse, on
    ambient calls with a "\\n" stop token it sometimes emitted *only* the
    tag, no spoken text at all) - so this can't assume line position. A
    regex search anywhere in the raw text is robust to both cases.

    2026-07-22 real bug found live: dialogue() has no stop token at all (see
    its own comment - removed to leave room for up to four tag lines), so
    the model sometimes keeps generating PAST its last tag into unrelated
    hallucinated text (a name-shaped token followed by digits was the
    reported symptom, e.g. "su2014" - confirmed NOT coming from
    player_name/semantic_facts/episodic memory/wiki_knowledge by checking
    each directly; this is the model rambling with nothing to stop it).
    The old approach only substituted out the MATCHED tag spans themselves,
    so trailing hallucinated content after the last tag survived untouched
    and got shown to the player as if it were dialogue. Fixed by truncating
    the spoken text at the position of the EARLIEST matched tag instead -
    everything from there on (real tags plus any hallucinated trailing
    ramble) is tag region, not spoken text. Safe because every real,
    observed case has tags appearing at/after the end of the spoken line,
    never interspersed within it.
    """
    action = "none"
    tone = "neutral"
    thread_action = "none"
    thread_summary = ""
    guide_target = ""
    tag_start = len(raw)

    m = _ACTION_TAG_RE.search(raw)
    if m:
        tag_start = min(tag_start, m.start())
        candidate = m.group(1).strip().lower()
        if candidate in VALID_ACTIONS:
            action = candidate
    if action == "offer_guide":
        m = _GUIDE_TARGET_TAG_RE.search(raw)
        if m:
            tag_start = min(tag_start, m.start())
            guide_target = m.group(1).strip().strip('"').strip().lower()
    m = _TONE_TAG_RE.search(raw)
    if m:
        tag_start = min(tag_start, m.start())
        candidate = m.group(1).strip().lower()
        if candidate in VALID_TONES:
            tone = candidate
    m = _THREAD_TAG_RE.search(raw)
    if m:
        tag_start = min(tag_start, m.start())
        candidate = m.group(1).strip().lower()
        if candidate in VALID_THREAD_ACTIONS:
            thread_action = candidate
    if thread_action == "open":
        m = _THREAD_SUMMARY_TAG_RE.search(raw)
        if m:
            tag_start = min(tag_start, m.start())
            thread_summary = m.group(1).strip().strip('"').strip()
        if not thread_summary:
            # OPEN with no usable summary isn't worth tracking - there'd be
            # nothing to show the player later via THREAD_HINT_TEMPLATE.
            thread_action = "none"

    # Defense-in-depth pass (see _GENERIC_TAG_RE's own comment): catches any
    # tag-shaped text none of the specific regexes above recognized (a typo
    # variant, or a genuinely new tag this function doesn't know about yet),
    # so it still gets excluded from the spoken text even though its value
    # couldn't be parsed. Harmless overlap with the specific matches above -
    # taking the minimum just reinforces the same (or an earlier) cutoff.
    m = _GENERIC_TAG_RE.search(raw)
    if m:
        tag_start = min(tag_start, m.start())

    text = raw[:tag_start].strip().strip('"').strip()
    return DialogueResult(text=text, action=action, tone=tone,
                           thread_action=thread_action, thread_summary=thread_summary,
                           guide_target=guide_target)


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
        # needs up to four lines free now: ACTION, TONE, and (occasionally)
        # THREAD + THREAD_SUMMARY (see SYSTEM_TEMPLATE and main.py's use of
        # .tone/.thread_action for outcome/thread-tracking). max_tokens
        # bumped 130->160 for THREAD_SUMMARY's free-text line (the other
        # three tags are single short words); still tight enough to protect
        # slot throughput.
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
            max_tokens=160,
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

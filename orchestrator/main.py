"""
Orchestrator entrypoint. Exposes a WebSocket the Java plugin connects to.

Wire protocol (JSON, one object per message)
--------------------------------------------
Plugin -> orchestrator:
  {"type": "dialogue",  "req_id": "...", "npc_id": "...", "player_id": "...",
   "player_name": "...", "npc_name": "...", "npc_role": "...", "text": "...",
   "situation": "..."}
  {"type": "ambient",   "req_id": "...", "npc_id": "...", "npc_name": "...",
   "npc_role": "...", "situation": "smithing at the forge"}
  {"type": "outcome",   "npc_id": "...", "npc_role": "...", "player_id": "...",
   "outcome": "player_was_kind"}          # fire-and-forget
  # "npc_role" is required here (not optional like it might look) - see
  # handle_outcome()'s comment: an outcome that's the first-ever event
  # recorded for an npc_id must still resolve the correct DEFAULT_BASELINES
  # entry, or that NPC's personality baseline gets stuck wrong forever.
  # "player_id" is the player's UUID (stable identity for memory/personality
  # keying); "player_name" is their real in-game username, used only so the
  # NPC can address them by name - never used as a lookup key, since names
  # can change but the UUID can't.

Orchestrator -> plugin:
  {"type": "say", "req_id": "...", "npc_id": "...", "text": "...", "action": "..."}
  # empty text on ambient = skip this tick (GPU busy) - plugin just no-ops
  # "action" is one of llm_client.VALID_ACTIONS ("none", "offer_guide",
  # "offer_fight", "decline_guide", "accept_tame") - the NPC's
  # own in-character decision about what to do next (see
  # llm_client.SYSTEM_TEMPLATE's ACTION tag). "accept_tame" has already
  # been enforced against the 1-tamed-NPC-per-player rule (see taming.py)
  # by the time this is sent. "offer_guide" drives real movement (GuideState/
  # SeekLandmarkSensor in the plugin - walks toward the nearest known
  # landmark, or nearest water if the player's message mentioned it).
  # "offer_fight"/"decline_guide" are still informational only - real
  # combat exists in the plugin (Attack action gated on IsCompanion +
  # a locked hostile Target) but is driven by passive hostile-detection,
  # not this specific per-message decision. The "situation" field the
  # plugin sends in may include a live-detected nearby-threat note (see
  # hytale-plugin's ThreatMemory.java) alongside static location info,
  # which is what these three actions typically react to.
  # "guide_target" (added 2026-07-22) is only meaningful when
  # action=="offer_guide" - a short keyword the model extracted from what
  # the player actually asked for ("temple", "desert", "cave", "water",
  # ...), used by GuideState.Target.NAMED to search real zones/prefabs by
  # keyword (see NearbyLandmarks.closestNamedPosition()) instead of only
  # the fixed "nearest landmark"/"nearest water" choice from before.
  #
  # OpenHands-style brain (2026-07-22): player help/research questions
  # ("how do I craft…", "where is…") are handled by agent_brain.AgentLoop
  # with tools over mounted game files/map/wiki before producing "say".
  # Self-learning runs in brain_learn_daemon on ambient GPU slots only.
  # See docs/OPENHANDS_NPC_BRAIN.md.

The plugin should treat every call as async: send the event, keep ticking,
apply the "say" whenever it arrives. A 200ms-2s delay reads as the NPC
thinking; a blocked game loop reads as a broken server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import websockets

import last_reply
from llm_client import DialogueResult, LlamaClient, NPCContext, Personality
from memory import EpisodicEntry, MemoryStore, compress_npc_memory
from personality import PersonalityStore
from priority_queue import NPCRequestDispatcher
from skill_runtime import SkillRuntime
from taming import TamingStore
from threads import ThreadStore
from wiki_ingest import run_ingest_cycle
from wiki_knowledge import WikiKnowledgeStore

# OpenHands-style agent brain (game-file tools, map, web, self-learning) - Mori/Adventurer.
from agent_brain.curriculum import goal_from_player_text, next_curriculum_goal
from agent_brain.experience import ExperienceStore
from agent_brain.loop import AgentLoop, BrainSession
from agent_brain.tools.registry import build_default_registry

# Pest's brain: a real openhands-sdk agent, not a reimplementation - see
# docs/PEST_OPENHANDS_BRAIN.md and pest_brain/__init__.py for why this is a
# deliberately separate package from agent_brain above.
from pest_brain.session import run_pest_turn
from pest_brain.tools import set_wiki_search_fn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("npc.orchestrator")

# asyncio.create_task() only holds a weak ref to the task via the event loop -
# an unreferenced task can be garbage-collected mid-execution. Keep a strong
# ref here for every fire-and-forget task we spawn, and log anything it raises
# instead of letting it vanish silently.
_background_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)

    def _done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if not t.cancelled() and t.exception() is not None:
            logger.error("background task failed", exc_info=t.exception())

    task.add_done_callback(_done)
    return task


# Match llama.cpp --parallel (docker-compose.yml's llm-inference command) -
# 2026-07-21 real load test on the RTX 3060: --parallel 6 / --ctx-size 12288
# (still 2048 tokens/slot, same per-slot budget as before) beat the previous
# --parallel 4 / --ctx-size 8192 at every concurrency level tested (better
# p50/p95 latency, ~25% higher peak throughput) for +227MB VRAM. Pushing to
# --parallel 8 / --ctx-size 16384 didn't move the throughput ceiling
# (~4.5-4.9 req/s either way - this GPU's real compute ceiling for this
# model, not a slot-count limit) and made latency noisier, so 6 is the
# chosen point, not just "the biggest one that fit." ambient_max_in_flight
# stays at 2 (unchanged, not scaled proportionally) - CLAUDE.md's hard rule
# is "ambient capped at 2 of N slots" as a small absolute number so dialogue
# always has the bulk of slots free, and more total slots only strengthens
# that: dialogue's guaranteed minimum went from 4-2=2 slots to 6-2=4.
DISPATCHER = NPCRequestDispatcher(
    max_concurrent_slots=6,
    ambient_max_in_flight=2,
)

LLM = LlamaClient()
MEMORY = MemoryStore()
PERSONALITY = PersonalityStore()
TAMING = TamingStore()
WIKI = WikiKnowledgeStore()
THREADS = ThreadStore()
SKILLS = SkillRuntime(Path(os.environ.get("SANDBOX_DIR", "/sandbox")))
EXPERIENCE = ExperienceStore()

# Pest's "please restart to activate" evolve_notice push (see
# pest_notice_daemon() and NpcAiBridge.NoticeHandler's javadoc) -
# pest_evolve.py (a separate, offline process) appends one JSON line per
# promoted skill here; this process polls it. Lives directly under
# SANDBOX_DIR (not approved/) so skill_runtime.py's `*.py` glob never sees it.
PEST_NOTICES_PATH = Path(os.environ.get("SANDBOX_DIR", "/sandbox")) / "pest_notices.jsonl"
PEST_NOTICE_POLL_INTERVAL_S = 30.0

# Set/cleared by plugin_connection() - see its comment. Single-plugin-
# connection assumption, same as everywhere else in this file.
_CURRENT_WS = None

# How often the NPC self-learns from game files when idle (OpenHands explore loop).
# Uses ambient GPU slots only — never steals dialogue capacity.
BRAIN_LEARN_INTERVAL_S = int(os.environ.get("BRAIN_LEARN_INTERVAL_S", "900"))

# Player phrases that should route through the tool-using brain (help / research)
# rather than pure in-character chat. Keep conservative: ordinary RP stays on
# the fast dialogue path.
_HELP_RE = re.compile(
    r"\b("
    r"how (do|can|to)|where (do|can|is|are)|what is|what's|"
    r"help me|can you help|teach me|explain|recipe|craft|how to|"
    r"look up|search the|game file|wiki|map marker|find the"
    r")\b",
    re.I,
)

# How often wiki_refresh_daemon() re-crawls hytale.fandom.com - see that
# function. Wiki content doesn't change hour to hour, and run_ingest_cycle()
# already skips unchanged pages cheaply via a batched revision-id check, so
# a daily cadence is plenty rather than genuinely wasteful.
WIKI_REFRESH_INTERVAL_S = 24 * 3600

# Minimum felt delay for a dialogue reply - see handle_dialogue()'s comment
# at the return site. 1.3s sits comfortably above this stack's own p50
# latency at moderate load (README's 2026-07-21 load-test table) - most
# real replies already take close to or longer than this, so in practice
# this mostly pads the fast/cached-slot cases rather than adding to every
# single turn.
MIN_REPLY_DELAY_S = 1.3

# Single-NPC project now (Adventurer only - see CLAUDE.md's 2026-07-22
# consolidation entry); FALLBACK_BASELINE stays as a generic catch-all for
# any npc_role string that doesn't match (shouldn't happen in practice with
# only one role in play, but costs nothing to keep as a safety net).
DEFAULT_BASELINES: dict[str, Personality] = {
    # Mori — adventure companion (auto-spawn, name-chat, OpenHands learning).
    "mori": Personality(warmth=0.75, aggression=0.45, humor=0.6,
                        curiosity=0.85, trust_of_player=0.7),
    # Pest — independent companion, same auto-spawn/follow/fight shape as
    # Mori, but its dialogue runs on a real openhands-sdk agent (pest_brain/)
    # instead of the fast single-shot path - see docs/PEST_OPENHANDS_BRAIN.md.
    "pest": Personality(warmth=0.7, aggression=0.45, humor=0.5,
                        curiosity=0.9, trust_of_player=0.7),
    # Bold and quick to trust ("easier to convince to become a companion") -
    # higher starting trust_of_player than the 0.5 default, plus enough
    # aggression to be plausibly willing to actually fight (not just guide).
    "adventurer": Personality(warmth=0.65, aggression=0.5, humor=0.55,
                               curiosity=0.7, trust_of_player=0.6),
}
FALLBACK_BASELINE = Personality()

# Maps llm_client.VALID_TONES (the model's own per-turn judgment) to
# personality.OUTCOME_EFFECTS keys - see handle_dialogue()'s comment for why
# this exists at all. "neutral" is deliberately absent: no outcome recorded.
TONE_TO_OUTCOME = {"kind": "player_was_kind", "rude": "player_was_rude"}


async def _build_context(msg: dict) -> NPCContext:
    npc_id, player_id = msg["npc_id"], msg.get("player_id", "")
    baseline = DEFAULT_BASELINES.get(msg.get("npc_role", ""), FALLBACK_BASELINE)
    persona = await PERSONALITY.load(npc_id, player_id, baseline)
    facts = await MEMORY.get_facts(npc_id, player_id or None)

    memories: list[str] = []
    wiki_snippets: list[str] = []
    if player_id and (msg.get("text") or msg.get("situation")):
        query = msg.get("text", msg.get("situation", ""))
        # Recent-first (chronological - lets the NPC bring up "last time we
        # talked" even on a plain "hi" that similarity search wouldn't
        # match), then similarity-matched memories not already included.
        # Both scoped to THIS player - see recall_similar/recall_recent's
        # docstrings for the cross-player bleed bug this fixes.
        recent = await MEMORY.recall_recent(npc_id, player_id)
        similar = await MEMORY.recall_similar(npc_id, player_id, query)
        memories = list(recent)
        for m in similar:
            if m not in memories:
                memories.append(m)
        # Local Qdrant similarity search only - wiki_ingest.py is the only
        # place this stack ever makes a live request to the actual wiki, so
        # a real conversation never waits on (or depends on) external
        # network access. Gated the same way memories are (only real
        # player-directed turns, never ambient) since there's no query to
        # match against otherwise.
        wiki_snippets = await WIKI.search(query)

    owner = await TAMING.get_owner(npc_id)
    is_companion = bool(player_id) and owner == player_id
    # Mori/Pest adventure companions: always bonded to the speaking player
    # without requiring an ACCEPT_TAME speech act (plugin already
    # auto-spawns + follows both the same way).
    if player_id and not is_companion and (
        (msg.get("npc_role") or "").lower() in ("mori", "pest")
        or (npc_id or "").lower() in ("mori", "pest")
    ):
        if await TAMING.try_tame(npc_id, player_id):
            is_companion = True
            owner = player_id
    # Not gated on real text/situation like memories/wiki_snippets above -
    # this is a direct per-(npc,player) lookup, not a similarity search, so
    # there's no "query" it needs. get_open_thread() itself applies the
    # mention-cooldown/cap (see threads.py) that keeps this occasional
    # rather than appearing on every single turn.
    open_thread_hint = await THREADS.get_open_thread(npc_id, player_id) if player_id else None
    # Self-study lessons (OpenHands brain experience store) — cheap Postgres
    # read; empty list if DB not up yet or no curriculum run has landed.
    lessons = await EXPERIENCE.top_lessons(npc_id, limit=3)
    return NPCContext(
        npc_id=npc_id,
        name=msg.get("npc_name", npc_id),
        personality=persona,
        semantic_facts=facts,
        recent_memories=memories,
        wiki_snippets=wiki_snippets,
        location_hint=msg.get("situation", ""),
        is_companion=is_companion,
        player_name=msg.get("player_name", ""),
        open_thread_hint=open_thread_hint or "",
        last_reply=last_reply.get(npc_id, player_id) if player_id else "",
        lessons=lessons,
    )


def _wants_brain_help(text: str) -> bool:
    t = (text or "").strip()
    if len(t) < 4:
        return False
    return bool(_HELP_RE.search(t))


async def _run_brain(session: BrainSession):
    """
    One OpenHands-style tool loop. Each LLM step goes through the dialogue
    GPU path so player-facing research still wins over ambient.
    """
    tools = build_default_registry(
        wiki_search=lambda q: WIKI.search(q),
    )
    loop = AgentLoop(
        tools=tools,
        experience=EXPERIENCE,
        llm_chat=lambda messages, max_tokens, temperature: DISPATCHER.request_dialogue(
            session.npc_id,
            lambda: LLM.complete(messages, max_tokens=max_tokens, temperature=temperature),
        ),
    )
    return await loop.run(session)


def _play_proposal_to_wire(prop: dict | None) -> tuple[str, str, str, str]:
    """
    Map brain play proposal → (action, guide_target, play_action, play_target).

    Plugin maps go_to/explore/gather/mine onto GuideState; other intents are
    still delivered as play_action for logging / future sensors.
    """
    if not prop:
        return "none", "", "", ""
    play_action = str(prop.get("action") or "").strip().lower()
    play_target = str(prop.get("target") or "").strip()
    if play_action in ("go_to", "gather", "mine", "craft", "trade", "build"):
        # Plugin guide path already walks by keyword.
        return "offer_guide", play_target or play_action, play_action, play_target
    if play_action == "explore":
        return "offer_guide", "landmark", play_action, play_target or "landmark"
    if play_action == "fight":
        return "offer_fight", "", play_action, play_target
    if play_action == "rest":
        return "none", "", play_action, play_target
    return "none", "", play_action, play_target


async def handle_brain_help(msg: dict) -> tuple[str, str, bool, str, str, str]:
    """Player asked for help / research — use game files + wiki tools.

    Returns (text, action, is_companion, guide_target, play_action, play_target).
    """
    turn_started = time.monotonic()
    npc_id = msg["npc_id"]
    player_id = msg.get("player_id", "")
    text = msg.get("text", "")
    goal = goal_from_player_text(text)
    session = BrainSession(
        npc_id=npc_id,
        player_id=player_id,
        goal=goal,
        player_message=text,
    )
    try:
        result = await _run_brain(session)
    except Exception:
        logger.exception("brain help failed npc=%s", npc_id)
        result = None

    if result is None or not result.say:
        # Fall back to normal character dialogue if tools path failed.
        return await handle_dialogue_character(msg)

    # Persist as episodic memory so the NPC "remembers" helping
    if result.say:
        _spawn(MEMORY.remember_episode(EpisodicEntry(
            npc_id=npc_id,
            player_id=player_id,
            text=(
                f'Player asked for help: "{text[:200]}". '
                f'I researched game files and said: "{result.say[:200]}"'
            ),
            ts=time.time(),
        )))
        last_reply.record(npc_id, player_id, result.say)
        # Promote high-confidence research into semantic facts
        if result.success and result.total_reward >= 0.3:
            _spawn(MEMORY.add_fact(
                npc_id,
                f"Helped player with: {text[:120]} → {result.say[:200]}",
                player_id or None,
            ))

    owner = await TAMING.get_owner(npc_id)
    is_companion = bool(player_id) and owner == player_id
    elapsed = time.monotonic() - turn_started
    if elapsed < MIN_REPLY_DELAY_S:
        await asyncio.sleep(MIN_REPLY_DELAY_S - elapsed)
    logger.info(
        "brain help npc=%s steps=%s reward=%.2f success=%s play=%s",
        npc_id, result.steps, result.total_reward, result.success,
        result.play_proposal,
    )
    action, guide_target, play_action, play_target = _play_proposal_to_wire(
        result.play_proposal
    )
    return result.say, action, is_companion, guide_target, play_action, play_target


async def handle_dialogue_character(msg: dict) -> tuple[str, str, bool, str, str, str]:
    """Original in-character dialogue path (no multi-step tools).

    Returns (text, action, is_companion, guide_target, play_action, play_target)
    with play_* empty (brain-only fields).
    """
    turn_started = time.monotonic()
    ctx = await _build_context(msg)
    raw = await DISPATCHER.request_dialogue(
        ctx.npc_id, lambda: LLM.dialogue(ctx, msg["text"])
    )
    if isinstance(raw, DialogueResult):
        text, action, tone = raw.text, raw.action, raw.tone
        thread_action, thread_summary = raw.thread_action, raw.thread_summary
    else:
        # The dispatcher fell back to its generic "..." string sentinel
        # (GPU busy / call timed out / call raised) instead of running
        # LLM.dialogue() - the NPC just visibly "pauses", no action decided,
        # and there's no real reply to judge tone from either.
        text, action, tone = raw, "none", "neutral"
        thread_action, thread_summary = "none", ""

    if action == "accept_tame":
        player_id = msg.get("player_id", "")
        if player_id and await TAMING.try_tame(ctx.npc_id, player_id):
            logger.info("npc %s tamed by player %s", ctx.npc_id, player_id)
            # ctx.is_companion was computed in _build_context() BEFORE this
            # taming happened, so it's still stale False - fix it up now so
            # the is_companion resync sent below reflects the taming that
            # just happened on THIS turn, not one turn later.
            ctx.is_companion = True
        else:
            # The model decided "yes" without knowing about the hard 1-per-
            # player rule (it only reasons about trust, not this
            # constraint) - enforce it here and be upfront about the
            # mismatch rather than silently pretending the taming worked.
            action = "none"
            text += " (Something holds you back from actually committing to this.)"

    logger.info("npc %s (player %s): action=%s tone=%s is_companion=%s",
                ctx.npc_id, msg.get("player_id", ""), action, tone, ctx.is_companion)

    # Remember the exchange (fire-and-forget so we don't add latency) - only
    # when there's an actual reply to remember. text=="" means the dispatcher
    # fell back to silence (GPU busy/timeout/error - see priority_queue.py),
    # not a real exchange; "I replied: ''" isn't a memory worth keeping, and
    # storing it would let a transient failure permanently pollute this NPC's
    # memory of the player.
    if text:
        _spawn(MEMORY.remember_episode(EpisodicEntry(
            npc_id=ctx.npc_id,
            player_id=msg.get("player_id", ""),
            text=f'Player said: "{msg["text"][:200]}". I replied: "{text[:200]}"',
            ts=time.time(),
        )))
        # Synchronous (not _spawn'd) - see last_reply.py's docstring for
        # why this needs to be immediate, unlike the episodic write above:
        # a fast next turn must never be able to race ahead of this.
        last_reply.record(ctx.npc_id, msg.get("player_id", ""), text)

    # Real outcome detection, inferred from the exchange itself rather than
    # a new Java-side game hook (see CLAUDE.md/task tracking for why:
    # NpcAiBridge.sendOutcome() still has no caller anywhere in the plugin,
    # so this is currently the ONLY source of real outcome data - without
    # it, personality.py's trait evolution and skill_writer.py's own trigger
    # condition (recent_outcome_counts) are both inert for every real NPC).
    # "neutral" records nothing on purpose - most turns are just ordinary
    # conversation, and treating every single one as an evaluable event
    # would flood npc_outcome_log with noise. Zero extra GPU cost: TONE
    # rides on the same completion as ACTION (see llm_client.py), not a
    # second call.
    outcome = TONE_TO_OUTCOME.get(tone)
    player_id = msg.get("player_id", "")
    if outcome and player_id:
        baseline = DEFAULT_BASELINES.get(msg.get("npc_role", ""), FALLBACK_BASELINE)
        _spawn(PERSONALITY.record_outcome(ctx.npc_id, player_id, outcome, baseline))

    # Structured unresolved-conversation tracking (threads.py) - same
    # zero-extra-call piggyback as TONE above, just a separate tag. "none"
    # (the overwhelming majority of turns) does nothing on purpose, same
    # reasoning as "neutral" tone above.
    if player_id and thread_action == "open" and thread_summary:
        _spawn(THREADS.open_thread(ctx.npc_id, player_id, thread_summary))
    elif player_id and thread_action == "resolve":
        _spawn(THREADS.resolve_thread(ctx.npc_id, player_id))
    # A reply that arrives too fast reads as unnaturally instant - real
    # conversation has a beat where the other person visibly thinks before
    # answering, and the plugin's "IsAwaitingReply" particle (see
    # AwaitingReplyState.java) needs at least a moment on screen to register
    # as anything, not flash and vanish. Pads fast replies up to a minimum
    # felt delay; never adds ON TOP of an already-slow one, so this never
    # compounds load-induced latency. Deliberately measured from the start
    # of this whole turn (context build + GPU wait + LLM call), not just the
    # LLM call itself, and deliberately AFTER the dispatcher's slot was
    # already released (see request_dialogue's finally block) - padding
    # happens on this player's own perceived latency only, never while
    # holding a GPU slot other players could be waiting on.
    elapsed = time.monotonic() - turn_started
    if elapsed < MIN_REPLY_DELAY_S:
        await asyncio.sleep(MIN_REPLY_DELAY_S - elapsed)

    # ctx.is_companion is re-checked fresh (Postgres-backed via taming.py,
    # not the plugin's own ephemeral CompanionState) and returned on every
    # single turn - see the wire-send comment in plugin_connection() for why
    # a one-time action=="accept_tame" isn't enough to keep the two in sync.
    guide_target = raw.guide_target if isinstance(raw, DialogueResult) else ""
    return text, action, ctx.is_companion, guide_target, "", ""


async def handle_pest_dialogue(msg: dict) -> tuple[str, str, bool, str, str, str]:
    """
    Pest's dialogue: a real openhands-sdk agent turn (pest_brain/), not the
    fast single-call path Mori/Adventurer use - see
    docs/PEST_OPENHANDS_BRAIN.md. Unlike Mori (which only detours into
    agent_brain's lighter loop for help-like text, see _wants_brain_help),
    EVERY Pest message goes through this - that's the whole point of a
    separate NPC whose brain literally runs on OpenHands, not just
    something inspired by it.

    Returns (text, action, is_companion, guide_target, play_action, play_target).
    """
    turn_started = time.monotonic()
    ctx = await _build_context(msg)
    result = await run_pest_turn(ctx, msg.get("text", ""), msg.get("situation", ""))

    if result.say:
        _spawn(MEMORY.remember_episode(EpisodicEntry(
            npc_id=ctx.npc_id,
            player_id=msg.get("player_id", ""),
            text=(
                f'Player said: "{msg.get("text", "")[:200]}". '
                f'I replied: "{result.say[:200]}"'
            ),
            ts=time.time(),
        )))
        last_reply.record(ctx.npc_id, msg.get("player_id", ""), result.say)

    elapsed = time.monotonic() - turn_started
    if elapsed < MIN_REPLY_DELAY_S:
        await asyncio.sleep(MIN_REPLY_DELAY_S - elapsed)

    logger.info(
        "pest turn npc=%s success=%s play=%s",
        ctx.npc_id, result.success, result.play_proposal,
    )
    action, guide_target, play_action, play_target = _play_proposal_to_wire(
        result.play_proposal
    )
    return result.say, action, ctx.is_companion, guide_target, play_action, play_target


async def handle_dialogue(msg: dict) -> tuple[str, str, bool, str, str, str]:
    """
    Route Pest's dialogue to its real OpenHands brain; everyone else (Mori,
    Adventurer) always uses the fast single-call dialogue path.

    2026-07-22: reverted routing help-like text (_wants_brain_help) to
    agent_brain.AgentLoop's multi-step tool loop for Mori/Adventurer - live-
    reported as "confused... doesn't seem to understand how to use it
    wisely," and directly confirmed in the real server log: a genuine bug
    (see loop.py's now-fixed budget-exhausted fallback) was splicing raw
    internal tool-call bookkeeping ("[OK reward=+0.20] Assets:Common/...")
    into what the player saw as the NPC's spoken reply whenever the small
    local model (Qwen2.5-7B) didn't converge on answer_help/finish within
    budget - which, empirically, was often enough for this to read as
    "the NPC is confused" rather than an occasional edge case. Beyond that
    one bug, real multi-step ReAct-style tool orchestration is inherently
    less reliable on a 7B model than a single well-grounded prompt - and
    the plain dialogue path (handle_dialogue_character -> _build_context)
    ALREADY includes wiki-grounded knowledge (WIKI.search() populates
    NPCContext.wiki_snippets on every real player turn, not just help-
    routed ones), which is exactly what the user confirmed worked well
    for "game question and recipe" before this routing was added. Net:
    Mori/Adventurer no longer gamble on the flaky path for something the
    reliable path already covers reasonably well.

    agent_brain/handle_brain_help/_wants_brain_help are NOT deleted -
    brain_learn_daemon's background self-study loop still uses the same
    AgentLoop (failures there are invisible to players, gated on
    result.success before ever becoming a semantic fact), and the
    infrastructure remains available to revisit later. Pest is unaffected
    either way - it never used this path; its dialogue always runs through
    pest_brain.session.run_pest_turn (a real openhands-sdk agent, not this
    hand-rolled loop) by design.
    """
    if (msg.get("npc_role") or "").lower() == "pest":
        return await handle_pest_dialogue(msg)
    return await handle_dialogue_character(msg)


async def handle_ambient(msg: dict) -> str:
    ctx = await _build_context(msg)

    # If this NPC has an approved skill (sandbox/approved/ - see
    # skill_runtime.py), let it decide first: zero GPU cost, and an NPC
    # with a learned skill is meant to actually use it rather than always
    # falling back to improvised LLM flavor text. Only "say"/"idle" have
    # anywhere to go on the plugin side today - see skill_runtime.py's
    # docstring for why other action types are logged, not dropped
    # silently, but don't produce a line yet.
    skill_state = {
        "event": "idle_tick",
        "player_id": None,
        # Not tracked anywhere in the wire protocol/orchestrator yet - an
        # honest gap, not a real value pretending to be one. A skill
        # relying on npc_hp actually varying won't behave usefully until
        # this is real.
        "npc_hp": 100,
        "time_of_day": msg.get("situation", ""),
        "nearby_players": 1,
    }
    skill_out = await SKILLS.try_decide(ctx.npc_id, skill_state)
    if skill_out is not None:
        action = skill_out.get("action")
        if action == "say":
            return skill_out.get("text", "")
        if action != "idle":
            logger.info("npc %s approved skill chose action=%s - not wired into "
                        "the plugin yet, no ambient line this tick", ctx.npc_id, action)
        return ""

    return await DISPATCHER.request_ambient(
        ctx.npc_id, lambda: LLM.ambient_line(ctx, msg.get("situation", "the day"))
    )


async def handle_outcome(msg: dict) -> None:
    # player_id="" is the sentinel key for the NPC's own shared-trait row
    # (see PersonalityStore); an outcome with no real player would silently
    # overwrite that row's trust_of_player instead of tracking a relationship.
    player_id = msg.get("player_id", "")
    if not player_id:
        logger.warning("dropping outcome with no player_id: npc=%s outcome=%s",
                        msg.get("npc_id"), msg.get("outcome"))
        return
    npc_role = msg.get("npc_role", "")
    if not npc_role:
        # Only actually matters the first time this npc_id is ever seen
        # (load() creates the personality row with whatever baseline is
        # passed here, and nothing ever corrects it later) - but there's no
        # way to tell from here whether this is that first time, so warn
        # every time rather than silently risking a wrong-forever baseline.
        logger.warning("outcome missing npc_role, baseline may default wrong: npc=%s outcome=%s",
                        msg.get("npc_id"), msg.get("outcome"))
    baseline = DEFAULT_BASELINES.get(npc_role, FALLBACK_BASELINE)
    await PERSONALITY.record_outcome(
        msg["npc_id"], player_id, msg["outcome"], baseline
    )
    # Only the failure paths above used to log anything - a successfully
    # recorded outcome (e.g. from NoteAttackedByPlayerAction.java) was
    # otherwise invisible in the orchestrator's own log, making it hard to
    # confirm live whether a game-side event actually reached here at all.
    logger.info("npc %s (player %s): outcome=%s", msg["npc_id"], player_id, msg["outcome"])


async def plugin_connection(ws) -> None:
    logger.info("plugin connected: %s", ws.remote_address)
    # Single-plugin-connection assumption already holds everywhere else in
    # this file (one Hytale server per orchestrator) - tracked here so
    # pest_notice_daemon() can push an unprompted evolve_notice at any time,
    # not just as a reply to an in-flight request (see that daemon's
    # docstring). Cleared on disconnect so the daemon correctly treats a
    # gap between sessions as "nothing to send yet", not a crash.
    global _CURRENT_WS
    _CURRENT_WS = ws

    async def process(raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("bad message from plugin: %.120s", raw)
            return
        mtype = msg.get("type")
        try:
            is_companion = False
            guide_target = ""
            play_action = ""
            play_target = ""
            if mtype == "dialogue":
                try:
                    (text, action, is_companion, guide_target,
                     play_action, play_target) = await handle_dialogue(msg)
                except Exception:
                    # handle_dialogue does its own real work (taming,
                    # personality, memory) beyond just the LLM call, which
                    # DISPATCHER.request_dialogue already guards with a
                    # timeout+fallback - a bug anywhere in that surrounding
                    # logic used to propagate all the way out here and skip
                    # the "say" send entirely, leaving the player with total
                    # protocol-level silence (no message sent at all) instead
                    # of a normal empty-text reply. Still send "say" with
                    # empty text on an unexpected bug, same as any other
                    # dialogue fallback (see priority_queue.py's 2026-07-21
                    # removal of BUSY_LINES) - never a canned line pretending
                    # to be something the NPC actually decided to say.
                    logger.exception("handle_dialogue failed unexpectedly npc=%s",
                                      msg.get("npc_id"))
                    text, action = "", "none"
            elif mtype == "ambient":
                text, action = await handle_ambient(msg), "none"
            elif mtype == "outcome":
                await handle_outcome(msg)
                return
            else:
                logger.warning("unknown message type: %s", mtype)
                return
            await ws.send(json.dumps({
                "type": "say", "req_id": msg.get("req_id", ""),
                "npc_id": msg["npc_id"], "text": text, "action": action,
                # Authoritative taming truth (Postgres-backed, survives a
                # plugin/world restart), resynced on every single reply - not
                # just when action=="accept_tame" fires. The plugin's own
                # CompanionState is a plain in-memory map that resets to
                # empty on every server restart, while this doesn't; without
                # resending this every turn, a previously-tamed NPC silently
                # stops following after any restart, because the model has
                # no reason to re-decide ACCEPT_TAME for a player it already
                # considers a companion.
                "is_companion": is_companion,
                # Only meaningful when action=="offer_guide" - a short
                # keyword the model extracted from what the player actually
                # asked for (see llm_client.py's GUIDE_TARGET rule), used by
                # GuideState.Target.NAMED/NearbyLandmarks.closestNamedPosition()
                # to search real zones/prefabs by keyword instead of the old
                # fixed landmark/water-only choice.
                "guide_target": guide_target,
                # OpenHands brain play intent (may be empty). Plugin maps
                # go_to/explore/gather onto GuideState; rest/fight/etc. are
                # recorded for PlayIntentState / future sensors.
                "play_action": play_action,
                "play_target": play_target,
            # ensure_ascii=False: 2026-07-22 real bug found live - the model
            # naturally uses em-dashes ("Emerald Wilds—I've got a feeling...")
            # in its dialogue style. json.dumps()'s default ensure_ascii=True
            # escapes any non-ASCII character as "\uXXXX", and
            # NpcAiBridge.java's hand-rolled extract() doesn't decode that
            # escape (it only handles single-char escapes like \" and \\,
            # so "—" became literal "u2014" glued onto the adjacent
            # word - confirmed live in the real server log:
            # "Emerald Wildsu2014I've got a feeling..."). Sending raw UTF-8
            # instead of \uXXXX escapes sidesteps the gap entirely for any
            # non-ASCII character (em-dashes, curly quotes, accents), not
            # just this one - see NpcAiBridge.java's extract() for the
            # complementary fix on the decoding side.
            }, ensure_ascii=False))
        except Exception:
            logger.exception("failed handling %s", mtype)

    try:
        async for raw in ws:
            # each message handled concurrently - the dispatcher does the gating
            _spawn(process(raw))
    except websockets.ConnectionClosed:
        logger.info("plugin disconnected")
    finally:
        if _CURRENT_WS is ws:
            _CURRENT_WS = None


async def compression_daemon() -> None:
    """
    Offline memory compression. Runs through NPCs slowly, routed via the
    AMBIENT path so it can never displace live dialogue. If the server is
    busy, request_ambient returns '' and we just retry the NPC next cycle.
    """
    async def low_prio_llm(prompt: str) -> str:
        # Plain completion, NOT LLM.dialogue() - dialogue() wraps every call
        # in the full NPC-roleplay SYSTEM_TEMPLATE (personality, ACTION-tag
        # rules, etc.), which is irrelevant here and was pushing this request
        # past the 2048-token slot budget on its own for any NPC with a
        # non-trivial batch of notes to summarize - causing llama.cpp to
        # 400 every single sweep, forever, silently mislogged by the
        # `if not result: raise RuntimeError("gpu busy, ...")` below as GPU
        # contention. Confirmed live: compression had never once succeeded
        # for any NPC as a result (0 rows in semantic_facts).
        async def call() -> str:
            return await LLM.complete([{"role": "user", "content": prompt}],
                                       max_tokens=200)
        result = await DISPATCHER.request_ambient("_compression", call)
        if not result:
            raise RuntimeError("gpu busy, retry later")
        return result

    while True:
        await asyncio.sleep(300)  # sweep every 5 minutes
        try:
            for npc_id in await PERSONALITY.all_npc_ids():
                await compress_npc_memory(MEMORY, low_prio_llm, npc_id)
                await asyncio.sleep(5)
        except Exception:
            logger.exception("compression sweep error")


async def brain_learn_daemon() -> None:
    """
    Self-directed learning loop (OpenHands explore-the-workspace pattern).

    When the world is quiet, the NPC works through a curriculum of goals
    against real game files / map data, records (action, reward) experiences,
    and writes durable lessons. Uses AMBIENT GPU slots only so live players
    always win. Disabled if BRAIN_LEARN_INTERVAL_S <= 0.
    """
    if BRAIN_LEARN_INTERVAL_S <= 0:
        logger.info("brain_learn_daemon disabled (BRAIN_LEARN_INTERVAL_S<=0)")
        return
    # Wait once so dialogue can settle after boot
    await asyncio.sleep(min(120, BRAIN_LEARN_INTERVAL_S))
    while True:
        try:
            goal = next_curriculum_goal()
            # Synthetic npc id for solo Adventurer — same as live companion
            # once one exists; still useful for lessons shared across sessions.
            npc_ids = await PERSONALITY.all_npc_ids()
            npc_id = npc_ids[0] if npc_ids else "adventurer_self"
            session = BrainSession(
                npc_id=npc_id,
                player_id="",
                goal=goal,
                player_message="",
            )
            tools = build_default_registry(wiki_search=lambda q: WIKI.search(q))

            async def ambient_llm(messages, max_tokens, temperature):
                # Ambient path: may return "" if GPU busy — treat as abort step
                out = await DISPATCHER.request_ambient(
                    f"_brain_learn_{npc_id}",
                    lambda: LLM.complete(
                        messages, max_tokens=max_tokens, temperature=temperature
                    ),
                )
                return out or '{"tool":"finish","args":{"say":"","success":false},"thought":"gpu busy"}'

            loop = AgentLoop(
                tools=tools,
                experience=EXPERIENCE,
                llm_chat=ambient_llm,
                max_steps=4,  # keep self-study cheap
            )
            result = await loop.run(session)
            logger.info(
                "brain self-learn goal=%r steps=%s reward=%.2f say=%.80s",
                goal.text[:80], result.steps, result.total_reward, result.say,
            )
            if result.success and result.say:
                _spawn(MEMORY.add_fact(
                    npc_id,
                    f"[self-study] {goal.text[:80]} → {result.say[:180]}",
                    None,
                ))
        except Exception:
            logger.exception("brain_learn_daemon error")
        await asyncio.sleep(BRAIN_LEARN_INTERVAL_S)


async def wiki_refresh_daemon() -> None:
    """
    Periodic Hytale-wiki re-crawl (wiki_ingest.py). Unlike compression_daemon
    above, this runs ONE cycle immediately on startup rather than sleeping
    first - a fresh deployment should get real wiki knowledge right away,
    not wait a full WIKI_REFRESH_INTERVAL_S before the first player ever gets
    a grounded answer. run_ingest_cycle() is cheap to re-run redundantly
    (a batched revision-id check skips anything unchanged), so running once
    here even if a manual `python wiki_ingest.py` seed already just happened
    costs almost nothing.
    """
    while True:
        try:
            summary = await run_ingest_cycle(WIKI)
            logger.info("wiki refresh cycle: %s", summary)
        except Exception:
            logger.exception("wiki refresh cycle error")
        await asyncio.sleep(WIKI_REFRESH_INTERVAL_S)


async def pest_notice_daemon() -> None:
    """
    Delivers Pest's "please restart" evolve_notice pushes (see
    skill_runtime.py's activation-epoch comment and pest_evolve.py) across
    the process boundary between pest_evolve.py (a separate, offline,
    profile-gated container - see docker-compose.yml) and this live
    orchestrator process. pest_evolve.py appends one JSON line per promoted
    skill to PEST_NOTICES_PATH; this polls it and forwards anything new
    over the live plugin connection.

    Fire-and-forget, same tolerance as everywhere else in this stack that
    talks to a possibly-absent plugin connection: if no player is online
    right when a notice lands, it's simply not delivered this cycle - there
    is deliberately no per-notice redelivery/ack tracking (a missed toast
    message once is a trivial, harmless gap, not a correctness bug; the
    thing that actually matters, the skill only activating after a real
    restart, is enforced by skill_runtime.py itself, not by this daemon).
    """
    seen_lines = 0
    while True:
        await asyncio.sleep(PEST_NOTICE_POLL_INTERVAL_S)
        try:
            if not PEST_NOTICES_PATH.is_file():
                continue
            lines = PEST_NOTICES_PATH.read_text(errors="replace").splitlines()
            new_lines = lines[seen_lines:]
            seen_lines = len(lines)
            if not new_lines or _CURRENT_WS is None:
                continue
            for line in new_lines:
                try:
                    notice = json.loads(line)
                except json.JSONDecodeError:
                    continue
                player_id = notice.get("player_id")
                text = notice.get("text")
                npc_id = notice.get("npc_id", "Pest")
                if not player_id or not text:
                    continue
                await _CURRENT_WS.send(json.dumps({
                    "type": "evolve_notice", "npc_id": npc_id,
                    "player_id": player_id, "text": text,
                }, ensure_ascii=False))
        except Exception:
            logger.exception("pest_notice_daemon error")


async def main() -> None:
    await MEMORY.start()
    await PERSONALITY.start()
    await TAMING.start()
    await WIKI.start()
    await THREADS.start()
    await EXPERIENCE.start()
    # Pest's brain (pest_brain/tools.py) shares the SAME wiki knowledge base
    # every other NPC uses - wired here (not at import time) since WIKI
    # itself is only constructed at module load, this just binds the
    # already-existing singleton.
    set_wiki_search_fn(WIKI.search)
    _spawn(compression_daemon())
    _spawn(wiki_refresh_daemon())
    _spawn(brain_learn_daemon())
    _spawn(pest_notice_daemon())
    async with websockets.serve(plugin_connection, "0.0.0.0", 8765,
                                ping_interval=20, ping_timeout=20):
        logger.info("orchestrator listening on :8765 (OpenHands-style brain enabled)")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

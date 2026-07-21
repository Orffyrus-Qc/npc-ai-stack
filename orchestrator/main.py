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
  # "offer_fight", "decline_guide", "accept_tame", "open_shop") - the NPC's
  # own in-character decision about what to do next (see
  # llm_client.SYSTEM_TEMPLATE's ACTION tag). "accept_tame" has already
  # been enforced against the 1-tamed-NPC-per-player rule (see taming.py),
  # and "open_shop" against "this NPC already became someone's companion,
  # they don't run a shop anymore" (also taming.py), by the time this is
  # sent - the plugin can act on "open_shop" directly with no further
  # checks needed. "offer_guide" drives real movement (GuideState/
  # SeekLandmarkSensor in the plugin - walks toward the nearest known
  # landmark, or nearest water if the player's message mentioned it).
  # "offer_fight"/"decline_guide" are still informational only - real
  # combat exists in the plugin (Attack action gated on IsCompanion +
  # a locked hostile Target) but is driven by passive hostile-detection,
  # not this specific per-message decision. The "situation" field the
  # plugin sends in may include a live-detected nearby-threat note (see
  # hytale-plugin's ThreatMemory.java) alongside static location info,
  # which is what these three actions typically react to.

The plugin should treat every call as async: send the event, keep ticking,
apply the "say" whenever it arrives. A 200ms-2s delay reads as the NPC
thinking; a blocked game loop reads as a broken server.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from pathlib import Path

import websockets

from llm_client import DialogueResult, LlamaClient, NPCContext, Personality
from memory import EpisodicEntry, MemoryStore, compress_npc_memory
from personality import PersonalityStore
from priority_queue import BUSY_LINES, NPCRequestDispatcher
from skill_runtime import SkillRuntime
from taming import TamingStore

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
SKILLS = SkillRuntime(Path(os.environ.get("SANDBOX_DIR", "/sandbox")))

# Minimum felt delay for a dialogue reply - see handle_dialogue()'s comment
# at the return site. 1.3s sits comfortably above this stack's own p50
# latency at moderate load (README's 2026-07-21 load-test table) - most
# real replies already take close to or longer than this, so in practice
# this mostly pads the fast/cached-slot cases rather than adding to every
# single turn.
MIN_REPLY_DELAY_S = 1.3

# Per-role defaults; move to config/DB as your cast grows.
DEFAULT_BASELINES: dict[str, Personality] = {
    "blacksmith": Personality(warmth=0.3, aggression=0.4, humor=0.3, curiosity=0.3),
    "innkeeper":  Personality(warmth=0.8, aggression=0.1, humor=0.7, curiosity=0.6),
    "elder":      Personality(warmth=0.6, aggression=0.05, humor=0.2, curiosity=0.5),
    "merchant":   Personality(warmth=0.55, aggression=0.15, humor=0.5, curiosity=0.45),
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

    owner = await TAMING.get_owner(npc_id)
    is_companion = bool(player_id) and owner == player_id
    return NPCContext(
        npc_id=npc_id,
        name=msg.get("npc_name", npc_id),
        role=msg.get("npc_role", "villager"),
        personality=persona,
        semantic_facts=facts,
        recent_memories=memories,
        location_hint=msg.get("situation", ""),
        is_companion=is_companion,
        player_name=msg.get("player_name", ""),
        # Whether this NPC has left to be ANYONE's companion, not just this
        # player's - a tamed trader no longer runs a shop for anyone,
        # regardless of who tamed them. Gates the OPEN_SHOP action below.
        is_tamed_by_anyone=owner is not None,
    )


async def handle_dialogue(msg: dict) -> tuple[str, str, bool]:
    turn_started = time.monotonic()
    ctx = await _build_context(msg)
    raw = await DISPATCHER.request_dialogue(
        ctx.npc_id, lambda: LLM.dialogue(ctx, msg["text"])
    )
    if isinstance(raw, DialogueResult):
        text, action, tone = raw.text, raw.action, raw.tone
    else:
        # The dispatcher fell back to its generic "..." string sentinel
        # (GPU busy / call timed out / call raised) instead of running
        # LLM.dialogue() - the NPC just visibly "pauses", no action decided,
        # and there's no real reply to judge tone from either.
        text, action, tone = raw, "none", "neutral"

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
    elif action == "open_shop" and ctx.is_tamed_by_anyone:
        # Same defense-in-depth as accept_tame above: ctx.is_tamed_by_anyone
        # was already in the prompt telling the model not to do this, but
        # enforce the hard rule server-side too rather than trust it always
        # will. Note the NPC is already gone (not "text += ..." here, since
        # ctx was already told it has no shop - a stray OPEN_SHOP is a model
        # slip, not a real request to react to in the reply).
        action = "none"

    logger.info("npc %s (player %s): action=%s tone=%s is_companion=%s",
                ctx.npc_id, msg.get("player_id", ""), action, tone, ctx.is_companion)

    # Remember the exchange (fire-and-forget so we don't add latency)
    _spawn(MEMORY.remember_episode(EpisodicEntry(
        npc_id=ctx.npc_id,
        player_id=msg.get("player_id", ""),
        text=f'Player said: "{msg["text"][:200]}". I replied: "{text[:200]}"',
        ts=time.time(),
    )))

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
    return text, action, ctx.is_companion


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

    async def process(raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("bad message from plugin: %.120s", raw)
            return
        mtype = msg.get("type")
        try:
            is_companion = False
            if mtype == "dialogue":
                try:
                    text, action, is_companion = await handle_dialogue(msg)
                except Exception:
                    # handle_dialogue does its own real work (taming,
                    # personality, memory) beyond just the LLM call, which
                    # DISPATCHER.request_dialogue already guards with a
                    # timeout+fallback - a bug anywhere in that surrounding
                    # logic used to propagate all the way out here and skip
                    # the "say" send entirely, leaving the player with total
                    # silence instead of even a busy-fallback line. Never
                    # let an unexpected server-side bug be worse for the
                    # player than the GPU just being busy.
                    logger.exception("handle_dialogue failed unexpectedly npc=%s",
                                      msg.get("npc_id"))
                    text, action = random.choice(BUSY_LINES), "none"
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
            }))
        except Exception:
            logger.exception("failed handling %s", mtype)

    try:
        async for raw in ws:
            # each message handled concurrently - the dispatcher does the gating
            _spawn(process(raw))
    except websockets.ConnectionClosed:
        logger.info("plugin disconnected")


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


async def main() -> None:
    await MEMORY.start()
    await PERSONALITY.start()
    await TAMING.start()
    _spawn(compression_daemon())
    async with websockets.serve(plugin_connection, "0.0.0.0", 8765,
                                ping_interval=20, ping_timeout=20):
        logger.info("orchestrator listening on :8765")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

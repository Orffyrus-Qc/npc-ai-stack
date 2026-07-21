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
  {"type": "outcome",   "npc_id": "...", "player_id": "...",
   "outcome": "player_was_kind"}          # fire-and-forget
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
import time

import websockets

from llm_client import DialogueResult, LlamaClient, NPCContext, Personality
from memory import EpisodicEntry, MemoryStore, compress_npc_memory
from personality import PersonalityStore
from priority_queue import NPCRequestDispatcher
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


# Match llama.cpp --parallel. One slot is implicitly kept honest for
# compression jobs by the ambient_max_in_flight cap.
DISPATCHER = NPCRequestDispatcher(
    max_concurrent_slots=4,
    ambient_max_in_flight=2,
)

LLM = LlamaClient()
MEMORY = MemoryStore()
PERSONALITY = PersonalityStore()
TAMING = TamingStore()

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
    ctx = await _build_context(msg)
    raw = await DISPATCHER.request_dialogue(
        ctx.npc_id, lambda: LLM.dialogue(ctx, msg["text"])
    )
    if isinstance(raw, DialogueResult):
        text, action = raw.text, raw.action
    else:
        # The dispatcher fell back to its generic "..." string sentinel
        # (GPU busy / call timed out / call raised) instead of running
        # LLM.dialogue() - the NPC just visibly "pauses", no action decided.
        text, action = raw, "none"

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

    logger.info("npc %s (player %s): action=%s is_companion=%s",
                ctx.npc_id, msg.get("player_id", ""), action, ctx.is_companion)

    # Remember the exchange (fire-and-forget so we don't add latency)
    _spawn(MEMORY.remember_episode(EpisodicEntry(
        npc_id=ctx.npc_id,
        player_id=msg.get("player_id", ""),
        text=f'Player said: "{msg["text"][:200]}". I replied: "{text[:200]}"',
        ts=time.time(),
    )))
    # ctx.is_companion is re-checked fresh (Postgres-backed via taming.py,
    # not the plugin's own ephemeral CompanionState) and returned on every
    # single turn - see the wire-send comment in plugin_connection() for why
    # a one-time action=="accept_tame" isn't enough to keep the two in sync.
    return text, action, ctx.is_companion


async def handle_ambient(msg: dict) -> str:
    ctx = await _build_context(msg)
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
    baseline = DEFAULT_BASELINES.get(msg.get("npc_role", ""), FALLBACK_BASELINE)
    await PERSONALITY.record_outcome(
        msg["npc_id"], player_id, msg["outcome"], baseline
    )


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
                text, action, is_companion = await handle_dialogue(msg)
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
        async def call() -> str:
            ctx = NPCContext(npc_id="_system", name="system", role="archivist",
                             personality=Personality())
            return (await LLM.dialogue(ctx, prompt)).text
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

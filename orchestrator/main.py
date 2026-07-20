"""
Orchestrator entrypoint. Exposes a WebSocket the Java plugin connects to.

Wire protocol (JSON, one object per message)
--------------------------------------------
Plugin -> orchestrator:
  {"type": "dialogue",  "req_id": "...", "npc_id": "...", "player_id": "...",
   "npc_name": "...", "npc_role": "...", "text": "...", "situation": "..."}
  {"type": "ambient",   "req_id": "...", "npc_id": "...", "npc_name": "...",
   "npc_role": "...", "situation": "smithing at the forge"}
  {"type": "outcome",   "npc_id": "...", "player_id": "...",
   "outcome": "player_was_kind"}          # fire-and-forget

Orchestrator -> plugin:
  {"type": "say", "req_id": "...", "npc_id": "...", "text": "..."}
  # empty text on ambient = skip this tick (GPU busy) - plugin just no-ops

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

from llm_client import LlamaClient, NPCContext, Personality
from memory import EpisodicEntry, MemoryStore, compress_npc_memory
from personality import PersonalityStore
from priority_queue import NPCRequestDispatcher

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("npc.orchestrator")

# Match llama.cpp --parallel. One slot is implicitly kept honest for
# compression jobs by the ambient_max_in_flight cap.
DISPATCHER = NPCRequestDispatcher(
    max_concurrent_slots=4,
    ambient_max_in_flight=2,
)

LLM = LlamaClient()
MEMORY = MemoryStore()
PERSONALITY = PersonalityStore()

# Per-role defaults; move to config/DB as your cast grows.
DEFAULT_BASELINES: dict[str, Personality] = {
    "blacksmith": Personality(warmth=0.3, aggression=0.4, humor=0.3, curiosity=0.3),
    "innkeeper":  Personality(warmth=0.8, aggression=0.1, humor=0.7, curiosity=0.6),
}
FALLBACK_BASELINE = Personality()


async def _build_context(msg: dict) -> NPCContext:
    npc_id, player_id = msg["npc_id"], msg.get("player_id", "")
    baseline = DEFAULT_BASELINES.get(msg.get("npc_role", ""), FALLBACK_BASELINE)
    persona = await PERSONALITY.load(npc_id, player_id, baseline)
    facts = await MEMORY.get_facts(npc_id, player_id or None)
    memories = (
        await MEMORY.recall_similar(npc_id, msg.get("text", msg.get("situation", "")))
        if msg.get("text") or msg.get("situation") else []
    )
    return NPCContext(
        npc_id=npc_id,
        name=msg.get("npc_name", npc_id),
        role=msg.get("npc_role", "villager"),
        personality=persona,
        semantic_facts=facts,
        recent_memories=memories,
        location_hint=msg.get("situation", ""),
    )


async def handle_dialogue(msg: dict) -> str:
    ctx = await _build_context(msg)
    reply = await DISPATCHER.request_dialogue(
        ctx.npc_id, lambda: LLM.dialogue(ctx, msg["text"])
    )
    # Remember the exchange (fire-and-forget so we don't add latency)
    asyncio.create_task(MEMORY.remember_episode(EpisodicEntry(
        npc_id=ctx.npc_id,
        player_id=msg.get("player_id", ""),
        text=f'Player said: "{msg["text"][:200]}". I replied: "{reply[:200]}"',
        ts=time.time(),
    )))
    return reply


async def handle_ambient(msg: dict) -> str:
    ctx = await _build_context(msg)
    return await DISPATCHER.request_ambient(
        ctx.npc_id, lambda: LLM.ambient_line(ctx, msg.get("situation", "the day"))
    )


async def handle_outcome(msg: dict) -> None:
    baseline = DEFAULT_BASELINES.get(msg.get("npc_role", ""), FALLBACK_BASELINE)
    await PERSONALITY.record_outcome(
        msg["npc_id"], msg.get("player_id", ""), msg["outcome"], baseline
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
            if mtype == "dialogue":
                text = await handle_dialogue(msg)
            elif mtype == "ambient":
                text = await handle_ambient(msg)
            elif mtype == "outcome":
                await handle_outcome(msg)
                return
            else:
                logger.warning("unknown message type: %s", mtype)
                return
            await ws.send(json.dumps({
                "type": "say", "req_id": msg.get("req_id", ""),
                "npc_id": msg["npc_id"], "text": text,
            }))
        except Exception:
            logger.exception("failed handling %s", mtype)

    try:
        async for raw in ws:
            # each message handled concurrently - the dispatcher does the gating
            asyncio.create_task(process(raw))
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
            return await LLM.dialogue(ctx, prompt)
        result = await DISPATCHER.request_ambient("_compression", call)
        if not result:
            raise RuntimeError("gpu busy, retry later")
        return result

    while True:
        await asyncio.sleep(300)  # sweep every 5 minutes
        try:
            # naive sweep: NPCs seen in personality table
            async with PERSONALITY._pg.acquire() as conn:  # noqa: SLF001
                rows = await conn.fetch(
                    "SELECT DISTINCT npc_id FROM npc_personality")
            for r in rows:
                await compress_npc_memory(MEMORY, low_prio_llm, r["npc_id"])
                await asyncio.sleep(5)
        except Exception:
            logger.exception("compression sweep error")


async def main() -> None:
    await MEMORY.start()
    await PERSONALITY.start()
    asyncio.create_task(compression_daemon())
    async with websockets.serve(plugin_connection, "0.0.0.0", 8765,
                                ping_interval=20, ping_timeout=20):
        logger.info("orchestrator listening on :8765")
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())

"""
Priority dispatcher for NPC LLM requests on a single small-VRAM GPU.

Design goals (matched to an 8-12GB card running llama.cpp --parallel 4):
  - Player-facing DIALOGUE requests always get a slot, waiting briefly if needed.
  - Background AMBIENT requests (idle chatter, wandering commentary) NEVER block
    dialogue. If no slot is free immediately, they fall back to a canned /
    rule-based response instead of queuing.
  - A slow or hung inference call can't wedge the whole NPC population - every
    call has a hard timeout with a fallback.

Runs inside the orchestrator container, one instance per process, sized to
match the `--parallel N` value of the llama.cpp server it talks to.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import random
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("npc.priority_queue")

# Shown instead of the old flat "..." when the GPU has no free slot right now
# or the call itself timed out - an in-character "give me a moment" reads far
# better than a silent pause, and rotating through several avoids the NPC
# saying the exact same filler line every time it happens (the same
# repetition complaint that motivated dialogue()'s repeat_penalty).
_BUSY_LINES = (
    "Hold that thought a moment...",
    "Let me think on that for a moment.",
    "One moment, if you would.",
    "Give me just a moment to gather my thoughts.",
    "Ask me again in a moment, would you?",
    "My mind's elsewhere just now - try again shortly.",
)


class Priority(enum.IntEnum):
    DIALOGUE = 0   # player is actively talking to this NPC - highest priority
    AMBIENT = 1    # idle ticks, background flavor, non-blocking


class SlotPool:
    """
    Fixed pool of N GPU slots supporting both a blocking-with-timeout acquire
    (for dialogue) and a strict non-blocking try-acquire (for ambient).
    Plain counter + condition variable - no reliance on asyncio.Semaphore
    internals, which don't expose a safe non-blocking path.
    """

    def __init__(self, n: int):
        self._available = n
        self._cond = asyncio.Condition()

    async def acquire_wait(self, timeout_s: float) -> bool:
        try:
            async with self._cond:
                await asyncio.wait_for(
                    self._cond.wait_for(lambda: self._available > 0), timeout_s
                )
                self._available -= 1
                return True
        except asyncio.TimeoutError:
            return False

    async def try_acquire_nowait(self) -> bool:
        async with self._cond:
            if self._available > 0:
                self._available -= 1
                return True
            return False

    async def release(self) -> None:
        async with self._cond:
            self._available += 1
            self._cond.notify()


class NPCRequestDispatcher:
    """Owns the slot pool and arbitrates between dialogue and ambient requests."""

    def __init__(
        self,
        max_concurrent_slots: int = 4,
        dialogue_wait_timeout_s: float = 3.0,
        dialogue_call_timeout_s: float = 6.0,
        ambient_call_timeout_s: float = 4.0,
        ambient_max_in_flight: int = 2,
        fallback_fn: Optional[Callable[[str, Priority], str]] = None,
    ):
        self._slots = SlotPool(max_concurrent_slots)
        self._dialogue_wait_timeout_s = dialogue_wait_timeout_s
        self._dialogue_call_timeout_s = dialogue_call_timeout_s
        self._ambient_call_timeout_s = ambient_call_timeout_s
        self._ambient_max_in_flight = ambient_max_in_flight
        self._ambient_in_flight = 0
        self._fallback_fn = fallback_fn or self._default_fallback

    @staticmethod
    def _default_fallback(npc_id: str, priority: Priority) -> str:
        if priority is Priority.DIALOGUE:
            # An in-character "give me a moment" rather than a flat "..." or
            # the game silently stalling - see _BUSY_LINES above.
            return random.choice(_BUSY_LINES)
        return ""  # ambient ticks silently no-op when the GPU is busy

    async def request_dialogue(
        self, npc_id: str, call: Callable[[], Awaitable[str]]
    ) -> str:
        """
        Player-facing call. Waits briefly for a free slot; if none frees up in
        time, or the call itself times out, returns a graceful fallback rather
        than blocking the game loop.
        """
        start = time.monotonic()
        got_slot = await self._slots.acquire_wait(self._dialogue_wait_timeout_s)
        if not got_slot:
            logger.warning("dialogue slot wait timed out npc=%s", npc_id)
            return self._fallback_fn(npc_id, Priority.DIALOGUE)

        try:
            result = await asyncio.wait_for(call(), timeout=self._dialogue_call_timeout_s)
            logger.debug(
                "dialogue served npc=%s in %.2fs", npc_id, time.monotonic() - start
            )
            return result
        except asyncio.TimeoutError:
            logger.warning("dialogue call timed out npc=%s", npc_id)
            return self._fallback_fn(npc_id, Priority.DIALOGUE)
        except Exception:
            logger.exception("dialogue call failed npc=%s", npc_id)
            return self._fallback_fn(npc_id, Priority.DIALOGUE)
        finally:
            await self._slots.release()

    async def request_ambient(
        self, npc_id: str, call: Callable[[], Awaitable[str]]
    ) -> str:
        """
        Background/idle call. Never waits for a slot - if one isn't
        immediately free, or too many ambient calls are already running, it
        bails straight to the fallback. This is what keeps a village of idle
        NPCs from ever delaying a player's conversation.
        """
        if self._ambient_in_flight >= self._ambient_max_in_flight:
            return self._fallback_fn(npc_id, Priority.AMBIENT)

        got_slot = await self._slots.try_acquire_nowait()
        if not got_slot:
            return self._fallback_fn(npc_id, Priority.AMBIENT)

        self._ambient_in_flight += 1
        try:
            result = await asyncio.wait_for(call(), timeout=self._ambient_call_timeout_s)
            return result
        except asyncio.TimeoutError:
            logger.debug("ambient call timed out npc=%s (fallback used)", npc_id)
            return self._fallback_fn(npc_id, Priority.AMBIENT)
        except Exception:
            logger.exception("ambient call failed npc=%s", npc_id)
            return self._fallback_fn(npc_id, Priority.AMBIENT)
        finally:
            self._ambient_in_flight -= 1
            await self._slots.release()


# ---------------------------------------------------------------------------
# Demo / sanity check: floods ambient requests while one dialogue comes in.
# Run with `python priority_queue.py` to see dialogue win.
# ---------------------------------------------------------------------------
if __name__ == "__main__":

    async def _demo():
        dispatcher = NPCRequestDispatcher(max_concurrent_slots=4)

        async def fake_llm_call(npc_id: str, delay: float) -> str:
            await asyncio.sleep(delay)
            return f"[{npc_id}] generated reply"

        async def player_talks(npc_id: str):
            reply = await dispatcher.request_dialogue(
                npc_id, lambda: fake_llm_call(npc_id, 1.0)
            )
            print("dialogue:", npc_id, "->", reply)

        async def npc_idles(npc_id: str):
            reply = await dispatcher.request_ambient(
                npc_id, lambda: fake_llm_call(npc_id, 1.0)
            )
            print("ambient: ", npc_id, "->", reply or "(skipped)")

        await asyncio.gather(
            *[npc_idles(f"villager_{i}") for i in range(6)],
            player_talks("blacksmith"),
        )

    logging.basicConfig(level=logging.INFO)
    asyncio.run(_demo())

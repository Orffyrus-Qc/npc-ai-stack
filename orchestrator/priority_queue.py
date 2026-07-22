"""
Priority dispatcher for NPC LLM requests on a single small-VRAM GPU.

Design goals (matched to an 8-12GB card running llama.cpp --parallel 6):
  - Player-facing DIALOGUE requests always get a slot, waiting generously if
    needed - every real reply the player sees comes from the model, never
    from a pre-written line (see 2026-07-21's removal of BUSY_LINES: any
    canned filler text, however varied, is still the same NPC saying
    something it didn't actually decide - not what "AI-driven NPCs" means).
  - Background AMBIENT requests (idle chatter, wandering commentary) NEVER
    block dialogue. If no slot is free immediately, they silently skip this
    tick rather than queuing.
  - A slow or hung inference call can't wedge the whole NPC population -
    every call has a hard timeout, and on timeout/failure the NPC simply
    doesn't reply that turn (empty text) rather than speaking a line it
    never generated.

Runs inside the orchestrator container, one instance per process, sized to
match the `--parallel N` value of the llama.cpp server it talks to.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger("npc.priority_queue")


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
        # 2026-07-21: raised from 3.0/6.0 (the original conservative
        # numbers) alongside removing BUSY_LINES - a short timeout backed by
        # a canned-line fallback was fine when "give up" just meant "show a
        # filler line", but now "give up" means the NPC stays silent that
        # turn, so it's worth waiting much longer for a real slot/reply
        # before accepting that. Real load-test data (README's 2026-07-21
        # table) shows p95 latency staying under ~3.8s even at 12-16
        # concurrent requests on 6 slots - both numbers here sit well above
        # any observed real latency, so in practice this should only ever
        # bind under genuinely pathological load or a hung call, not normal
        # play.
        dialogue_wait_timeout_s: float = 12.0,
        dialogue_call_timeout_s: float = 10.0,
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
        # Empty for both priorities, deliberately, as of 2026-07-21: every
        # word an NPC says should come from the model, never a pre-written
        # line (see BUSY_LINES' removal, module docstring). The plugin
        # already treats empty "say" text as "this NPC has nothing to say
        # this turn" for ambient - dialogue now gets the exact same honest
        # silence instead of a fake reply, on the rare occasions the
        # generous timeouts above still aren't enough.
        return ""

    async def request_dialogue(
        self, npc_id: str, call: Callable[[], Awaitable[str]]
    ) -> str:
        """
        Player-facing call. Waits generously for a free slot; if none frees
        up in time, or the call itself times out, returns "" (no reply this
        turn) rather than blocking the game loop or inventing a line the
        model never actually said.
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

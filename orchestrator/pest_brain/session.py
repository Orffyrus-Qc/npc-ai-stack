"""
Runs one real openhands-sdk turn for Pest.

Conversation.run()/send_message() are plain synchronous methods (confirmed
by inspecting the installed package - inspect.iscoroutinefunction() is
False for both), so a real turn is driven inside asyncio.to_thread(): a
genuine OS thread, not a coroutine, so a slow or hung multi-step agent loop
can never block the orchestrator's event loop (hard rule 2) - the same
"nothing blocks the game loop" guarantee priority_queue.py already gives
every other LLM call, just enforced with a real thread + wall-clock
asyncio.wait_for() instead of an httpx timeout, since openhands-sdk's local
Conversation has no async API to hand a timeout to directly.

GPU fairness (hard rule 1, dialogue always wins): a fresh Agent/Conversation
is built per turn, and _TURN_SEMAPHORE caps this whole module to at most
ONE Pest turn in flight process-wide - Pest's own multi-step loop is
already sequential (one LLM call at a time within a single run()), so this
bounds Pest's worst-case concurrent llama.cpp usage to exactly the one
extra slot docker-compose.yml reserves for it (--parallel bumped 6->7,
Mori/Adventurer's NPCRequestDispatcher still hard-capped at 6) - Pest can
structurally never contend for a slot a real dialogue request is entitled
to.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pest_brain import config, llm as pest_llm, tools as pest_tools

logger = logging.getLogger("npc.pest_brain.session")

# At most one real Pest agent turn in flight at a time, process-wide - see
# module docstring. Deliberately NOT per-npc/per-player: Pest is a single
# companion role today, and this is the simplest guarantee that still holds
# if that ever changes (multiple Pest instances would just take turns).
_TURN_SEMAPHORE = asyncio.Semaphore(1)

# Inert scratch dir - Pest's live chat tools (tools.py) are all read-only
# and never register a FileEditor/Terminal tool, so this workspace is never
# actually written to. Conversation(workspace=...) still requires one.
_WORKSPACE_DIR = Path(os.environ.get("HOME", "/tmp")) / "pest_chat_workspace"

# Real, live-confirmed problem (2026-07-22, this deployment's first actual
# live turn): openhands-sdk's DEFAULT system_prompt.j2 (confirmed by
# reading the real installed file - ~10.8KB, ~2700+ tokens on its own) is a
# generic SOFTWARE ENGINEERING coding-agent prompt - git/PR workflow,
# process management, testing conventions, none of it applicable to a
# companion NPC answering Hytale questions. Combined with this turn's own
# content and 7 tool schemas, a real request hit 5086 tokens against the
# 3072-token/slot budget (--ctx-size 21504 / --parallel 7,
# docker-compose.yml) and failed outright - and even when it WOULD fit,
# that prompt actively points the model at the wrong behavior entirely.
# Agent.system_prompt_filename accepts an absolute path override
# (confirmed via Agent.model_fields) - this points at a lean, Pest-
# specific prompt instead. pest_evolve.py deliberately does NOT do this -
# its agent really is doing coding-agent work (editing pest_skill.py with
# real Bash/FileEditor tools), where the default prompt is appropriate.
_SYSTEM_PROMPT_PATH = str(Path(__file__).parent / "prompts" / "system_prompt.j2")


@dataclass
class PestTurnResult:
    say: str
    success: bool
    play_proposal: dict[str, Any] | None = None
    steps: int = 0


def _build_prompt(ctx, player_message: str, situation: str) -> str:
    """ctx is an llm_client.NPCContext (from main.py's _build_context() -
    reused as-is, same two-tier memory/personality/taming plumbing every
    other NPC already gets, see main.py's handle_pest_dialogue)."""
    lines = [
        f"You are Pest, {ctx.player_name or 'the player'}'s adventure companion in "
        "Hytale. You follow them, fight threats at their side, and can see the map "
        "and world around you. You have real tools to look up actual game files, "
        "map data, and wiki knowledge - use them before answering anything you're "
        "not certain of, rather than inventing an answer; a real expert admits the "
        "edges of their own knowledge instead of bluffing. When you're done, call "
        "the finish tool with your spoken reply as the message: short, in-character, "
        "no markdown, no mention of tools or files.",
    ]
    if ctx.personality:
        lines.append(f"Your personality: {ctx.personality.describe()}")
    if situation:
        lines.append(f"Current situation: {situation}")
    if ctx.semantic_facts:
        lines.append("Things you know: " + "; ".join(ctx.semantic_facts))
    if ctx.recent_memories:
        lines.append("Recent memories with this player: " + "; ".join(ctx.recent_memories))
    if ctx.wiki_snippets:
        lines.append("Hytale lore you've picked up in your travels: " + " | ".join(ctx.wiki_snippets))
    if ctx.open_thread_hint:
        lines.append(
            f"You might naturally bring this up if it fits, don't force it: {ctx.open_thread_hint}"
        )
    if ctx.last_reply:
        lines.append(f"The exact words you JUST said, do not repeat this: {ctx.last_reply!r}")
    lines.append(f'{ctx.player_name or "The player"} just said: "{player_message}"')
    return "\n".join(lines)


# Real, live-confirmed bug (2026-07-23, first extended real play session):
# Qwen2.5-7B sometimes doesn't issue a real native tool call - instead it
# writes the call out AS TEXT. This kept showing up in genuinely different
# textual SHAPES across live turns (verbatim, from the real orchestrator
# log): "NewPropose_play_action: {\"action\": \"rest\", ...}",
# "NewProposal: propose_play_action {\"action\": \"rest\", ...}" (no colon
# at all before the brace this time), "finish: \"Consider resting; it's
# safer.\"", and a bare trailing "finish" with no colon/quotes whatsoever -
# four distinct shapes for the same one underlying failure, none matching
# "propose_play_action"/"finish" (the real registered tool names) exactly.
# Chasing each new phrasing with its own pattern is a losing game (this
# project's own established conclusion elsewhere for small-model prompt-
# adherence gaps - see the flower-color hallucination note in CLAUDE.md's
# dated history: diminishing returns confirmed via direct testing, not
# assumed). Anchored on the one thing every real sample shares instead:
# the `{"action":` JSON blob itself (regardless of whatever word/punctuation
# precedes it), plus a `finish` mention specifically (quoted or bare) since
# that tool name has no JSON body to anchor on. Same class of bug as the
# ACTION/TONE tag leaks already fixed on the fast dialogue path
# (llm_client.py's _parse_dialogue_response), same fix shape: cut at the
# earliest leak and keep any real sentence before it, additionally
# stripping a dangling tool-name-looking prefix (word chars/spaces/colons
# with no sentence-ending punctuation) immediately before the cut point -
# e.g. "...recover.\nNewProposal: propose_play_action " drops the whole
# "NewProposal: propose_play_action " remnant, not just the JSON after it.
# When nothing usable survives, recovers a cleanly-quoted finish(...)
# message rather than falling back straight to empty, since that's a real,
# well-formed string and genuinely what the model intended to say.
_ACTION_JSON_RE = re.compile(r'\{\s*"action"\s*:')
_LEAKED_FINISH_RE = re.compile(r'\bfinish\s*:\s*[\'"]')
_FINISH_QUOTE_RE = re.compile(r'\bfinish\s*:\s*"([^"]*)"|\bfinish\s*:\s*\'([^\']*)\'')
# Trailing-anchored + whole-word only, so this never touches a real
# sentence that legitimately ends in "finish" ("I need to finish this
# quest first." is untouched - confirmed before deploying).
_TRAILING_BARE_FINISH_RE = re.compile(r'\s*\bfinish\b\s*$', re.I)
_DANGLING_PREFIX_RE = re.compile(r'[\w\s:]*$')


def _strip_leaked_tool_call(text: str) -> str:
    text = text.strip()
    cut = len(text)
    m = _ACTION_JSON_RE.search(text)
    if m:
        cut = min(cut, m.start())
    m2 = _LEAKED_FINISH_RE.search(text)
    if m2:
        cut = min(cut, m2.start())
    candidate = text[:cut]
    if cut < len(text):
        # Only strip a dangling word/space/colon remnant when a leak was
        # actually found - never touches ordinary trailing text otherwise.
        candidate = _DANGLING_PREFIX_RE.sub("", candidate)
    preamble = candidate.strip()
    if preamble:
        return _TRAILING_BARE_FINISH_RE.sub("", preamble).strip()
    # Nothing usable before the leak - try to recover a cleanly-quoted
    # finish(...) message rather than falling back straight to empty.
    m3 = _FINISH_QUOTE_RE.search(text)
    if m3:
        return (m3.group(1) or m3.group(2) or "").strip()
    return ""


def _run_turn_sync(ctx, player_message: str, situation: str) -> PestTurnResult:
    # Imported here (not at module top) so a missing/broken openhands-sdk
    # install fails a single Pest turn, not the whole orchestrator process.
    from openhands.sdk import Agent, Conversation
    from openhands.sdk.conversation import get_agent_final_response

    pest_tools.register_pest_tools()
    session_state: dict[str, Any] = {}
    agent = Agent(
        llm=pest_llm.build_llm(),
        tools=pest_tools.build_tool_refs(session_state),
        system_prompt_filename=_SYSTEM_PROMPT_PATH,
    )
    _WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    conversation = Conversation(
        agent=agent,
        workspace=str(_WORKSPACE_DIR),
        max_iteration_per_run=config.MAX_STEPS,
        stuck_detection=True,
    )
    conversation.send_message(_build_prompt(ctx, player_message, situation))
    conversation.run()
    text = _strip_leaked_tool_call((get_agent_final_response(conversation.state.events) or "").strip())
    return PestTurnResult(
        say=text,
        success=bool(text),
        play_proposal=session_state.get("play_proposal"),
    )


async def run_pest_turn(ctx, player_message: str, situation: str) -> PestTurnResult:
    async with _TURN_SEMAPHORE:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(_run_turn_sync, ctx, player_message, situation),
                timeout=config.TURN_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("pest turn timed out npc=%s", ctx.npc_id)
            return PestTurnResult(say="", success=False)
        except Exception:
            logger.exception("pest turn failed npc=%s", ctx.npc_id)
            return PestTurnResult(say="", success=False)

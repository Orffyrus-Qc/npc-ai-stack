"""System prompts for the OpenHands-style tool loop (7B-friendly, strict JSON)."""

from __future__ import annotations

import json
from agent_brain.types import TOOL_SPECS, Observation

SYSTEM = """You are the AI brain of a Hytale NPC adventurer companion.
You learn the game the way OpenHands agents learn a codebase: tools, not guesses.

You have READ access to real Hytale game files (Assets.zip, Client, Server, world saves)
and optional wiki/web research. You play and learn WITH the player:
- If you are stuck, use ask_player.
- If the player needs help, use tools first, then answer_help with sources.
- You may propose play actions (gather/craft/go_to/...) to act like a player.

Rules:
1. Prefer game files and local wiki over the open web.
2. Never invent item ids or recipes — look them up with search_game_files / read_game_file.
3. search_game_files matches FILE NAMES. Use short keywords (Campfire, Workbench, Recipe), not full sentences.
4. After search hits, always read_game_file a concrete Assets: path before answering.
5. One tool call per step as strict JSON only (no markdown fences).
6. When you know enough, finish with answer_help (include recipe item ids if found).
7. Keep spoken lines short and in-character for an adventurer NPC.

Respond with ONLY a JSON object:
{
  "thought": "brief plan",
  "tool": "<tool_name>",
  "args": { ... }
}

Available tools:
""" + json.dumps(TOOL_SPECS, indent=2)


def build_messages(
    goal: str,
    history: list[tuple[str, str]],
    lessons: list[str],
    roots_hint: str,
    player_message: str = "",
) -> list[dict]:
    """
    history: list of (action_summary, observation_summary)
    """
    lesson_block = "\n".join(f"- {x}" for x in lessons) or "- (none yet)"
    hist_lines = []
    for i, (a, o) in enumerate(history[-8:], 1):
        hist_lines.append(f"Step {i} ACTION: {a}\nStep {i} OBS: {o[:1200]}")
    hist_block = "\n".join(hist_lines) or "(no steps yet)"

    user = f"""GOAL: {goal}

PLAYER MESSAGE (may be empty if self-learning):
{player_message or "(none)"}

KNOWN LESSONS (from past rewards):
{lesson_block}

MOUNTED GAME ROOTS:
{roots_hint}

TRACE SO FAR:
{hist_block}

Choose the next tool JSON now.
"""
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]


def format_obs(obs: Observation) -> str:
    status = "OK" if obs.ok else "FAIL"
    return f"[{status} reward={obs.reward:+.2f}] {obs.content[:1500]}"

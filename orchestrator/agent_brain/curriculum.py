"""
Self-learning curriculum — goals the NPC pursues when idle (explore / master the game).

This is *not* classic policy-gradient RL training of a neural net. It is the
OpenHands-style goal loop + experience rewards: try → observe → record reward
→ prefer high-reward tool chains next time (experience replay in prompts).
"""

from __future__ import annotations

import itertools
from agent_brain.types import Goal

# Ordered catalog of things a "player-like" NPC should figure out from game files.
CURRICULUM: list[Goal] = [
    Goal(
        text="Discover how crafting benches work: find Campfire/Workbench item JSON and their recipes.",
        kind="learn",
        source="curriculum",
    ),
    Goal(
        text="Map the interaction system: sample Server/Item/Interactions JSON and list key Type values.",
        kind="learn",
        source="curriculum",
    ),
    Goal(
        text="Learn combat basics from a Weapon item JSON (damage, Primary/Secondary interactions).",
        kind="learn",
        source="curriculum",
    ),
    Goal(
        text="Find how drops work: open a Server/Drops JSON and summarize Container types.",
        kind="learn",
        source="curriculum",
    ),
    Goal(
        text="Inspect world/map data for the current save: markers and world config.",
        kind="explore",
        source="curriculum",
    ),
    Goal(
        text="Learn NPC role structure from Server/NPC (or Adventurer role) so you understand AI companions.",
        kind="learn",
        source="curriculum",
    ),
    Goal(
        text="Find language keys for item names in Server/Languages and how TranslationProperties work.",
        kind="learn",
        source="curriculum",
    ),
    Goal(
        text="Propose a short play plan for a new player: first resources to gather and why (from assets).",
        kind="play",
        source="curriculum",
    ),
]

_cycle = itertools.cycle(range(len(CURRICULUM)))


def next_curriculum_goal() -> Goal:
    idx = next(_cycle)
    g = CURRICULUM[idx]
    # fresh id each time
    return Goal(text=g.text, kind=g.kind, source=g.source)


def goal_from_player_text(text: str) -> Goal:
    t = (text or "").strip()
    lower = t.lower()
    if any(k in lower for k in ("help", "how do", "where", "what is", "recipe", "craft", "find")):
        return Goal(text=t, kind="help_player", source="player")
    return Goal(text=t, kind="play", source="player")

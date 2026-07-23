"""
Typed events for the OpenHands-style Action → Execute → Observation loop.

Mirrors openhands.sdk tool contracts without importing the SDK:
  LLM proposes JSON tool call → Action
  Executor runs tool          → Observation
  Pair stored as Experience   → reward / memory for RL-style learning
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any
import time
import uuid


class ActionName(str, Enum):
    # Knowledge tools (read-only)
    READ_GAME_FILE = "read_game_file"
    SEARCH_GAME_FILES = "search_game_files"
    LIST_GAME_TREE = "list_game_tree"
    READ_MAP_MARKERS = "read_map_markers"
    READ_WORLD_CONFIG = "read_world_config"
    SEARCH_WIKI = "search_wiki"
    WEB_FETCH = "web_fetch"
    # Social / HITL
    ASK_PLAYER = "ask_player"
    ANSWER_HELP = "answer_help"
    # Play / self-learning
    PROPOSE_PLAY_ACTION = "propose_play_action"
    RECORD_LEARNING = "record_learning"
    # Control
    FINISH = "finish"
    THINK = "think"


@dataclass
class Action:
    """What the LLM decided to do (OpenHands Action)."""
    name: str
    args: dict[str, Any] = field(default_factory=dict)
    thought: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "args": self.args,
            "thought": self.thought,
        }


@dataclass
class Observation:
    """Result of executing an Action (OpenHands Observation)."""
    action_id: str
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)
    reward: float = 0.0  # shaped reward for RL-style logging

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "ok": self.ok,
            "content": self.content[:4000],
            "data": self.data,
            "reward": self.reward,
        }


@dataclass
class Goal:
    """A self-assigned or player-assigned learning/play objective."""
    text: str
    kind: str = "learn"  # learn | help_player | explore | play
    source: str = "self"  # self | player | curriculum
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Experience:
    """
    One (state, action, observation, reward) transition — the unit of
    reinforcement learning / experience replay for the brain.
    """
    npc_id: str
    goal: str
    action_name: str
    action_args: dict[str, Any]
    observation_ok: bool
    observation_summary: str
    reward: float
    player_id: str = ""
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict:
        return asdict(self)


# Tools the agent may call. Keep in sync with agent_brain/tools/* registration.
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": ActionName.READ_GAME_FILE.value,
        "description": (
            "Read a file under the mounted Hytale install or UserData "
            "(Assets.zip entry, Server JSON, Client data, save config). "
            "path is relative to HYTALE_ROOT or a known root alias."
        ),
        "args": {
            "path": "str — e.g. Assets:Server/Item/Items/Bench/Bench_Campfire.json "
                    "or UserData:Saves/... or absolute under allowed roots"
        },
    },
    {
        "name": ActionName.SEARCH_GAME_FILES.value,
        "description": "Search file names and (for small text/json) content under game roots.",
        "args": {
            "query": "str — keyword or glob fragment",
            "root": "str — Assets|ServerJar|UserData|Client|All (default All)",
            "limit": "int — max hits (default 15)",
        },
    },
    {
        "name": ActionName.LIST_GAME_TREE.value,
        "description": "List directories/files one level deep under a game path.",
        "args": {"path": "str", "limit": "int optional"},
    },
    {
        "name": ActionName.READ_MAP_MARKERS.value,
        "description": (
            "Read world map markers / block markers from a save so player and "
            "NPC can navigate together."
        ),
        "args": {"world": "str optional world name, default 'default'"},
    },
    {
        "name": ActionName.READ_WORLD_CONFIG.value,
        "description": "Read world config.json for a named world under UserData/Saves or run/universe.",
        "args": {"world": "str optional"},
    },
    {
        "name": ActionName.SEARCH_WIKI.value,
        "description": "Search locally ingested Hytale wiki knowledge (no live network).",
        "args": {"query": "str"},
    },
    {
        "name": ActionName.WEB_FETCH.value,
        "description": (
            "Fetch a public HTTPS page (wiki, patch notes). Disabled unless "
            "BRAIN_ALLOW_WEB=1. Prefer SEARCH_WIKI first."
        ),
        "args": {"url": "str", "max_chars": "int optional"},
    },
    {
        "name": ActionName.ASK_PLAYER.value,
        "description": (
            "Ask the human player a clarifying question when stuck. "
            "Ends this agent turn with a spoken line."
        ),
        "args": {"question": "str"},
    },
    {
        "name": ActionName.ANSWER_HELP.value,
        "description": (
            "Give a clear, player-facing help answer grounded in tool findings. "
            "Ends this agent turn."
        ),
        "args": {"answer": "str", "sources": "list[str] optional"},
    },
    {
        "name": ActionName.PROPOSE_PLAY_ACTION.value,
        "description": (
            "Propose an in-game play action the NPC would take if acting like a player "
            "(gather, craft, go_to, explore, fight, rest). Logged for learning; "
            "plugin may enact a subset later."
        ),
        "args": {
            "action": "str — gather|craft|go_to|explore|fight|rest|mine|build|trade",
            "target": "str — item/place/entity id or description",
            "reason": "str",
        },
    },
    {
        "name": ActionName.RECORD_LEARNING.value,
        "description": "Commit a durable lesson the NPC learned (fact + confidence).",
        "args": {
            "lesson": "str",
            "confidence": "float 0-1",
            "topic": "str optional",
        },
    },
    {
        "name": ActionName.THINK.value,
        "description": "Internal reasoning step (no side effects). Use sparingly.",
        "args": {"note": "str"},
    },
    {
        "name": ActionName.FINISH.value,
        "description": "End the agent loop with a final spoken line to the player (or empty).",
        "args": {"say": "str optional spoken line", "success": "bool"},
    },
]

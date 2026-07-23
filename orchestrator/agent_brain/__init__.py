"""
OpenHands-inspired AI brain for Hytale NPCs.

Maps the OpenHands Software Agent SDK pattern onto this stack:

  OpenHands concept          →  This package
  -------------------------     --------------------------------
  Agent (reason-act loop)    →  loop.AgentLoop
  Conversation state         →  loop.BrainSession
  Tool Action/Observation    →  types.Action / types.Observation
  Workspace (files)          →  tools.game_files + tools.map_world
  Skills                     →  existing sandbox/approved skills + experience store
  Condenser                  →  experience summary / top-k rewards
  Human-in-the-loop          →  tools.player_help (ask_player / answer_help)

Not a dependency on openhands-sdk: same architecture, smaller footprint, wired
into the existing WebSocket orchestrator and GPU slot arbiter.

See docs/OPENHANDS_NPC_BRAIN.md for the full design.
"""

from agent_brain.loop import AgentLoop, BrainSession, BrainResult
from agent_brain.types import Action, Observation, Experience, Goal

__all__ = [
    "AgentLoop",
    "BrainSession",
    "BrainResult",
    "Action",
    "Observation",
    "Experience",
    "Goal",
]

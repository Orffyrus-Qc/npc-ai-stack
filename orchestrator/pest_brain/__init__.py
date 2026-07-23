"""
Pest's brain: a real openhands-sdk (github.com/OpenHands/software-agent-sdk)
agent, not a reimplementation - see docs/PEST_OPENHANDS_BRAIN.md.

Deliberately its own package, sibling to agent_brain/ (Mori/Adventurer's
lighter, hand-rolled Action/Observation loop - untouched by this work):
Pest's whole point is that its reasoning engine is the literal upstream
SDK, so wiring it in as a variant of agent_brain would blur exactly the
distinction the user asked for.

  session.py     one openhands.sdk.Conversation per turn, read-only tools
                 only, wall-clock-timeout + single-concurrent-turn guarded
                 (see session.py's module docstring for why)
  tools.py       real ToolDefinition subclasses wrapping the EXISTING
                 read-only agent_brain.tools functions (no duplicated
                 logic) - game files, map/world, wiki search, and a
                 propose_play_action tool for follow/lead parity with Mori
  llm.py         openhands.sdk.LLM pointed at the same local llama.cpp
                 server every other NPC already uses
  config.py      env-driven knobs

Self-evolution (real Bash/FileEditor tools) is NOT in this package - see
pest_evolve.py at the orchestrator root, a separate offline process, for
why that boundary matters.
"""

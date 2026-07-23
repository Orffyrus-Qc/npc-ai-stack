"""
Env-driven config for Pest's brain. Mirrors agent_brain/config.py's style
but stays a separate module on purpose - see pest_brain/__init__.py.
"""

from __future__ import annotations

import os

# openhands-sdk/litellm appends the OpenAI chat-completions route itself
# given an OpenAI-compatible base_url - this must NOT include
# "/chat/completions" (unlike llm_client.py's LLAMA_SERVER_URL, which calls
# httpx directly against the full path). Same llama.cpp server, same model,
# no new model/cloud dependency.
LLM_BASE_URL = os.environ.get("PEST_LLM_BASE_URL", "http://llm-inference:8080/v1")

# Informational only when base_url points at a local OpenAI-compatible
# server - llama.cpp always serves whichever --model docker-compose.yml
# started it with, regardless of what name is requested here.
MODEL_NAME = os.environ.get("PEST_LLM_MODEL", "qwen2.5-7b-instruct")

# Generous on purpose: a real multi-step openhands-sdk agent turn (several
# tool calls, each a real LLM round trip) legitimately takes longer than
# Mori/Adventurer's single-shot dialogue call. Pest's own "thinking"
# particle (Pest.json's IsAwaitingReply node) is what keeps this from
# reading as a hang. See session.py for how this timeout is enforced.
TURN_TIMEOUT_S = float(os.environ.get("PEST_BRAIN_TURN_TIMEOUT_S", "45"))
MAX_STEPS = int(os.environ.get("PEST_BRAIN_MAX_STEPS", "8"))

# sandbox/pest_workspace/ is the ONLY place a real Bash/FileEditor tool is
# ever granted to a Pest agent - pest_evolve.py (a separate, offline,
# profile-gated process - see that file's module docstring), never this
# live package. session.py's tools are read-only game-file/map/wiki access
# only.
EVOLVE_WORKSPACE = os.environ.get("PEST_EVOLVE_WORKSPACE", "/sandbox/pest_workspace")

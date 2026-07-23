"""
Pest's LLM: a real openhands.sdk.LLM pointed at the same local llama.cpp
server every other NPC's dialogue already uses (llm_client.py's
LLAMA_SERVER_URL, minus the /chat/completions suffix - see config.py).

Field names (model/api_key/base_url) confirmed by actually installing
openhands-sdk into python:3.12-slim (this project's own base image) and
inspecting openhands.sdk.LLM.model_fields directly, rather than trusting
documentation alone - same discipline this project already applies to
Hytale's own engine via javap disassembly (see CLAUDE.md's
"verify before fixing" practice).
"""

from __future__ import annotations

from pydantic import SecretStr

from openhands.sdk import LLM

from pest_brain import config


def build_llm() -> LLM:
    return LLM(
        # Real, live-confirmed gotcha (2026-07-22, this deployment's first
        # actual live turn): a bare model name ("qwen2.5-7b-instruct") made
        # litellm (which openhands-sdk uses internally) raise
        # "LLM Provider NOT provided" - litellm needs an explicit provider
        # prefix to know HOW to use base_url, it doesn't infer one from an
        # unrecognized model string. Confirmed the fix directly against the
        # real running llm-inference container (LLM(...).completion(...)
        # actually returned a real completion) before applying it here -
        # "openai/" tells litellm to use the OpenAI-compatible calling
        # convention against base_url, which is exactly what llama.cpp's
        # server-cuda image exposes.
        model=f"openai/{config.MODEL_NAME}",
        # llama.cpp's OpenAI-compatible server has no auth on this loopback-
        # only deployment (docker-compose.yml) - a real key isn't needed,
        # but the field itself is a required SecretStr.
        api_key=SecretStr("local"),
        base_url=config.LLM_BASE_URL,
        temperature=0.7,
    )

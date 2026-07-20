# CLAUDE.md — project context for Claude Code

This repo was designed and generated in a claude.ai session (July 2026).
This file is the session handoff: read it fully before making changes.

## What this project is

AI-driven NPCs for a **Hytale server plugin**: NPCs with evolving
personality, two-tier memory, and sandboxed skill self-improvement.
Everything runs on **one machine**: the Hytale server (Java) plus this
Docker stack sharing an **8–12GB VRAM GPU**.

## Architecture (and the reasoning behind it)

```
Hytale Java plugin (NpcAiBridge.java)
        │  WebSocket, JSON — protocol documented at top of orchestrator/main.py
        ▼
orchestrator/ (Python, asyncio)
  main.py            gateway + handlers + offline compression daemon
  priority_queue.py  GPU slot arbiter (see "hard rules" below)
  llm_client.py      personality+memory → system prompt; llama.cpp client
  memory.py          Qdrant episodic + Postgres semantic facts + compression
  personality.py     bounded trait nudges, per-player trust, decay to baseline
        │
        ▼
llm-inference: llama.cpp server-cuda, Qwen2.5-7B-Instruct Q4_K_M,
  --parallel 4 --cont-batching  (≈4 concurrent 2048-token slots)
memory-db: Qdrant (embeddings on CPU via fastembed — GPU stays for chat model)
fact-db: Postgres (facts, personality vectors)
sandbox/: skill validation in ephemeral --network none --read-only containers
```

## Hard rules — do not break these

1. **Dialogue always beats ambient.** Player-facing requests wait up to 3s
   for a slot; ambient/idle requests NEVER queue — no slot free now means
   instant canned fallback. Ambient is capped at 2 of 4 slots.
2. **Nothing blocks the game loop.** Every LLM call has a timeout + fallback.
   The plugin fires events async and applies replies when they arrive.
3. **Only llm-inference touches the GPU.** Embeddings, DBs, sandbox: CPU.
4. **Skill code changes go through the sandbox gate** (sandbox/
   run_skill_validation.sh → skill_harness.py → approved/). Facts and
   personality may update live; behavior code may not. Never hot-load
   unvalidated skills.
5. **NpcAiBridge.java stays free of Hytale API imports.** Hytale's NPC/ECS
   plugin APIs were still being renamed across 2026 patches — all game-API
   coupling belongs in the plugin handler code, not the transport.
6. Every skill is `decide(state: dict) -> dict` with an action from the
   whitelist in skill_harness.py. Extend the whitelist deliberately.

## Environment assumptions

- Host: ~8 cores / 32GB RAM / single NVIDIA GPU 8–12GB, nvidia-container-toolkit.
- Model: `scripts/download_model.sh` (Q4_K_M default; pass `q5_k_m` for 10–12GB).
- Bring-up: `docker compose up -d --build`; plugin connects to ws://host:8765.
- Postgres creds are local-only defaults (npc/npc) — change if ever exposed.

## State when handed off

- All Python passes syntax checks; priority_queue.py demo verified
  (dialogue wins over an ambient flood).
- NOT yet done: real load test of --parallel vs. actual GPU throughput;
  wiring NpcAiBridge into current Hytale NPC event hooks; any live run
  against a real llama.cpp instance.

## Agreed next steps (in order)

1. **Skill-writer meta-agent**: watches sandbox/rejected/*.log + NPC outcome
   stats, prompts the model to propose improved candidate skills into
   sandbox/candidates/. Runs offline/low-priority only (rule 1 applies).
2. Load-test llama.cpp slots on the real GPU; tune --parallel/ctx tradeoff.
3. Wire NpcAiBridge.java into the current Hytale plugin API (check current
   docs — API surface moves fast; hytalemodding.dev and the official
   modding posts are the references).
4. Optional: GitHub Actions workflow running skill_harness.py on push.

## Tuning table

See README.md — it maps symptoms (slow dialogue, world stutter, OOM) to the
specific knob to turn.

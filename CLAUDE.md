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
Hytale Java plugin (hytale-plugin/, Gradle project - see its README)
  NpcAiBridge.java        transport, zero Hytale imports
  NpcAiPlugin.java        entry point (extends JavaPlugin)
  NpcInteractListener.java  PlayerInteractEvent -> the bridge
        │  WebSocket, JSON — protocol documented at top of orchestrator/main.py
        ▼
orchestrator/ (Python, asyncio)
  main.py            gateway + handlers + offline compression daemon
  priority_queue.py  GPU slot arbiter (see "hard rules" below)
  llm_client.py      personality+memory → system prompt; llama.cpp client
  memory.py          Qdrant episodic + Postgres semantic facts + compression
  personality.py     bounded trait nudges, per-player trust, decay to baseline,
                     outcome history log (npc_outcome_log)
  skill_writer.py    OFFLINE meta-agent: outcome history + rejected/*.log ->
                     candidate skills. Not started by `docker compose up`
                     (profile "tools") - never touches approved/ directly.
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
   unvalidated skills. `skill_writer.py` only ever writes to
   `sandbox/candidates/` and only ever *statically* inspects code it
   generates (`ast.parse`, never import/exec) — it has no path to
   `approved/` and must not be given one.
5. **NpcAiBridge.java stays free of Hytale API imports.** Hytale's NPC/ECS
   plugin APIs were still being renamed across 2026 patches — all game-API
   coupling belongs in the plugin handler code (NpcAiPlugin.java,
   NpcInteractListener.java), not the transport. Still true as of the
   2026-07-20 scaffold: NpcAiBridge.java has zero Hytale imports.
6. Every skill is `decide(state: dict) -> dict` with an action from the
   whitelist in skill_harness.py. Extend the whitelist deliberately.

## Environment assumptions

- Host: ~8 cores / 32GB RAM / single NVIDIA GPU 8–12GB, nvidia-container-toolkit.
- Model: `scripts/download_model.sh` (Q4_K_M default; pass `q5_k_m` for 10–12GB).
- Bring-up: `docker compose up -d --build`; plugin connects to ws://host:8765.
- Postgres creds are local-only defaults (npc/npc) — change if ever exposed.

## State when handed off

- **2026-07-20: real end-to-end run completed** on an RTX 3060 12GB (see
  README.md's "What's actually been verified" section for full detail).
  `docker compose up -d --build` reaches healthy on all 4 services; a fake
  WebSocket "player" client held a real multi-turn conversation through
  the actual GPU-backed model; Qdrant recall, Postgres personality/trust
  math, and outcome logging all confirmed correct with real data. Measured
  VRAM: stable ~5.9GB whether idle or at 4 concurrent requests.
- That run surfaced and fixed 4 real bugs invisible to static review: the
  HF model is split into two shards (download_model.sh/docker-compose.yml
  assumed one file), the non-root orchestrator Dockerfile user broke
  fastembed's cache dir, unpinned qdrant-client resolved to a version that
  removed `.search()`, and personality.py's record_outcome() had an
  asyncpg-unparseable unbound placeholder. All fixed; see git log.
- **2026-07-20, same day: hytale-plugin/ scaffold added, then compiled and
  booted for real.** User installed Hytale; the real `HytaleServer.jar`
  (v0.5.7) was found on disk and inspected directly (constant-pool dump,
  no javap/JDK available yet) to ground every class/method name used in
  NpcAiPlugin.java and NpcInteractListener.java in bytecode that actually
  exists. Confirmed real that way: manifest.json shape, JavaPlugin/
  setup() lifecycle, EventRegistry.registerGlobal(), PlayerInteractEvent's
  fields, INonPlayerCharacter as the (non-deprecated) NPC marker.
  Then a JDK 25 (Temurin) got installed and `./gradlew build` succeeded
  clean. Went further still: `./gradlew runServer` booted a real local
  Hytale server with the plugin loaded, set up, AND enabled, through to
  "Hytale Server Booted! [Multiplayer, Fresh Universe]" - no crash, no
  plugin errors. That run also caught a real bug in the official
  template's own runServerJar Gradle task (--mods wants a directory, not
  a jar path; the server also auto-scans a CWD-relative mods/ dir, so
  also passing --mods= pointing at that same dir causes a hard-fail
  "duplicate plugin" error) - fixed in this repo's build.gradle.
  Two things still explicitly flagged as unverified in
  hytale-plugin/README.md: the thread-hop needed before the bridge's
  reply callback touches world state, and multi-turn chat-based
  conversation (needs PlayerChatEvent, IAsyncEvent-based - a different
  registration shape than what got used).
- NOT yet done: an actual Hytale client connecting and clicking a real
  NPC (server booted with the plugin enabled, but no player interaction
  happened in this run); sustained multi-player/multi-NPC load test;
  skill_writer.py against the real GPU (only verified with a fake LLM/DB
  so far).

## Agreed next steps (in order)

1. ~~Skill-writer meta-agent: watches sandbox/rejected/*.log + NPC outcome
   stats, prompts the model to propose improved candidate skills into
   sandbox/candidates/.~~ **Done** — `orchestrator/skill_writer.py`, run via
   `docker compose run --rm skill-writer [--dry-run] [--npc-id X]`. Pulls
   each NPC's `recent_outcome_counts()` (new `npc_outcome_log` table in
   personality.py) plus the last few `sandbox/rejected/*.log` as cautionary
   examples, asks the LLM for a `decide()` candidate, does a static
   syntax/shape check (never imports/execs the result), and writes anything
   plausible to `sandbox/candidates/`. Verified end-to-end locally (fake
   LLM/DB, real `skill_harness.py`) — NOT yet run against the real GPU/model.
   Read the GPU-contention caveat in the file's docstring before scheduling
   it: it bypasses the live orchestrator's dialogue-priority slot arbiter, so
   it must only run during confirmed low-player windows, same as
   `run_skill_validation.sh`.
2. Load-test llama.cpp slots on the real GPU; tune --parallel/ctx tradeoff.
3. **In progress** — Wire NpcAiBridge.java into the current Hytale plugin
   API. `hytale-plugin/` compiles clean against a real JDK 25 and boots
   successfully in a real local Hytale server (plugin loaded, set up, AND
   enabled - see above and hytale-plugin/README.md). Remaining: connect an
   actual Hytale client and click a real NPC to confirm PlayerInteractEvent
   fires as expected; confirm the thread-hop for touching world state; add
   PlayerChatEvent for real multi-turn conversations.
4. ~~Optional: GitHub Actions workflow running skill_harness.py on push.~~
   **Done** — see `.github/workflows/skill-validation.yml`. Two jobs: a
   harness self-test against `sandbox/examples/` (one known-good skill that
   must pass, one deliberately bad one that must fail) that runs on every
   push/PR touching `sandbox/**`, plus a job that validates anything queued
   in `sandbox/candidates/*.py`. This is a fast syntax/shape gate only — it
   does NOT replace `run_skill_validation.sh`'s locked-down Docker sandbox,
   which is still what actually promotes a skill to `approved/`.

## Tuning table

See README.md — it maps symptoms (slow dialogue, world stutter, OOM) to the
specific knob to turn.

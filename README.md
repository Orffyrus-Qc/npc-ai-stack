```text
╔════════════════════════════════════════════════════════════════════════╗
║                                                                        ║
║         ███████╗ █████╗ ██╗     ██╗     ██╗███╗   ██╗ ██████╗          ║
║         ██╔════╝██╔══██╗██║     ██║     ██║████╗  ██║██╔════╝          ║
║         █████╗  ███████║██║     ██║     ██║██╔██╗ ██║██║  ███╗         ║
║         ██╔══╝  ██╔══██║██║     ██║     ██║██║╚██╗██║██║   ██║         ║
║         ██║     ██║  ██║███████╗███████╗██║██║ ╚████║╚██████╔╝         ║
║         ╚═╝     ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝╚═╝  ╚═══╝ ╚═════╝          ║
║                                                                        ║
║    ██████╗ ██████╗ ██╗    ██╗    ███████╗ ██████╗ ███╗   ██╗███████╗   ║
║   ██╔════╝██╔═══██╗██║    ██║    ╚══███╔╝██╔═══██╗████╗  ██║██╔════╝   ║
║   ██║     ██║   ██║██║ █╗ ██║      ███╔╝ ██║   ██║██╔██╗ ██║█████╗     ║
║   ██║     ██║   ██║██║███╗██║     ███╔╝  ██║   ██║██║╚██╗██║██╔══╝     ║
║   ╚██████╗╚██████╔╝╚███╔███╔╝    ███████╗╚██████╔╝██║ ╚████║███████╗   ║
║    ╚═════╝ ╚═════╝  ╚══╝╚══╝     ╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚══════╝   ║
║                                                                        ║
║                                                                        ║
╚════════════════════════════════════════════════════════════════════════╝
```

---

# 🐄 YOU HAVE ENTERED A FALLING COW ZONE 🐄

```text
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                                                                                      ║
║  ⚠⚠⚠  STOP. READ THIS BEFORE YOU POINT A REAL SERVER AT THIS.  ⚠⚠⚠                   ║
║                                                                                      ║
║  THIS PROJECT IS UNDER CONSTRUCTION.                                                 ║
║  IT HAS NOT RUN END-TO-END AGAINST A REAL HYTALE SERVER YET.                         ║
║                                                                                      ║
║  YOU ARE ENTERING A FALLING COW ZONE.                                                ║
║  Expect missing pieces, wrong API names, and designs that may                        ║
║  change without mercy.                                                               ║
║                                                                                      ║
║  DO NOT treat this repo as a production NPC brain.                                   ║
║  DO NOT assume NpcAiBridge.java compiles against your HytaleServer.jar.              ║
║  DO NOT let the skill self-improvement loop run unattended against                   ║
║  a live player-facing world without watching the sandbox logs.                       ║
║                                                                                      ║
║  HYTALE IS EARLY ACCESS -- PLUGIN APIS MOVE BETWEEN BUILDS.                          ║
║  GPU/PARALLEL-SLOT TUNING KNOBS ARE STARTING POINTS, NOT BENCHMARKS.                 ║
║                                                                                      ║
║  If something here works someday, that will be a pleasant surprise --                ║
║  not a promise.                                                                      ║
║                                                                                      ║
║  Status:  🚧 UNDER CONSTRUCTION  ·  🧪 UNTESTED ON REAL SERVER  ·  🐄 FALLING COW ZONE  ║
║                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
```

> ### 🐄 Huge disclaimer (again, on purpose)
>
> **Falling Cow Zone** means: experimental self-improving NPC stack, not
> field-tested against a real Hytale server, not certified, not a finished
> mod. Use at your own risk. If a cow falls on your deployment schedule,
> that is expected weather in this zone.

---

# NPC AI Stack — single machine, 8–12GB GPU

Realistic, learning NPCs for a Hytale server: evolving personality, two-tier
memory, sandboxed skill self-improvement, all on one box with one small GPU.

### Status badge (honest edition)

```text
🚧 UNDER CONSTRUCTION · 🧪 UNTESTED ON REAL SERVER · 🐄 FALLING COW ZONE
```

## What this does in Hytale

- **NPCs talk back in character.** A player interacts with an NPC, the
  plugin sends the event over WebSocket, and the orchestrator builds a
  prompt from that NPC's personality + memory and gets a reply out of a
  local Qwen2.5-7B model — no cloud calls, no per-message API cost.
- **They remember you, specifically.** Two-tier memory: recent, concrete
  moments live in Qdrant (episodic), durable facts about you and the world
  get promoted into Postgres (semantic) and compressed over time so an
  NPC's context doesn't bloat forever. Ask a blacksmith about a quest you
  mentioned three days ago and it can still know.
- **Personalities drift, on purpose, within limits.** Each NPC has bounded
  trait nudges and a per-player trust score that shift based on how you
  treat them, then decay back toward baseline — so a grumpy NPC can warm
  up to a player over time without turning into a different character.
- **Idle NPCs mutter to themselves.** Ambient one-liners fire when nothing
  else is going on, but they're capped and always lose their GPU slot to
  an actual player talking to an NPC — dialogue never waits behind flavor
  text, and nothing the LLM does can stall the game loop (hard timeout +
  canned fallback on every call).
- **NPC behavior can improve itself, carefully.** New `decide(state)`
  skills get proposed, run through validation in a locked-down, networkless
  container, and only reach live NPCs if they pass — bad candidates get
  logged and rejected instead of shipped. Facts and personality can update
  live; actual decision-making code can't sneak in unvalidated.

> 🐄 **Cow note:** all of the above describes the intended in-game
> experience once `NpcAiBridge.java` is wired into real Hytale NPC event
> hooks — that wiring, and a live playtest against an actual server, are
> both still outstanding (see the warning box up top).

## Layout

```
docker-compose.yml          the whole stack (llama.cpp + orchestrator + DBs)
orchestrator/
  main.py                   WebSocket gateway + event handlers + compression daemon
  priority_queue.py         GPU slot arbiter: dialogue always wins, ambient never blocks
  llm_client.py             prompt builder (personality+memory -> system prompt) + llama.cpp client
  memory.py                 Qdrant episodic + Postgres semantic facts + compression
  personality.py            bounded trait nudges, per-player trust, decay to baseline
sandbox/
  run_skill_validation.sh   ephemeral locked-down containers for candidate skills
  skill_harness.py          the tests a skill must pass to be promoted
scripts/download_model.sh   fetch Qwen2.5-7B-Instruct GGUF
NpcAiBridge.java            plugin-side transport (no Hytale API coupling)
```

## Bring-up

```bash
# 1. host prereqs (once)
sudo apt install nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker

# 2. model (Q4_K_M for 8GB, pass q5_k_m for 10-12GB)
./scripts/download_model.sh

# 3. stack
docker compose up -d --build

# 4. smoke test the LLM directly
curl -s http://localhost:8080/v1/chat/completions -d '{
  "messages":[{"role":"user","content":"Say hello as a grumpy blacksmith."}],
  "max_tokens":40}' | python3 -m json.tool
```

Plugin connects to `ws://<host>:8765`. Protocol is documented at the top of
`orchestrator/main.py`.

> 🐄 **Cow note:** the bring-up above has been syntax-checked, not load-tested.
> `--parallel` vs. real GPU throughput hasn't been benchmarked, and
> `NpcAiBridge.java` hasn't been wired into a live Hytale NPC event hook yet.

## Tuning knobs

| Symptom | Knob |
|---|---|
| Dialogue feels slow under load | lower `--parallel` to 3 (fewer, faster slots) or shrink `max_tokens` |
| NPCs need longer memory context | `--parallel 2` + bigger per-slot ctx (concurrency<->context tradeoff) |
| World sim stutters during dialogue | trim `llm-inference` `cpus:` or pin hytale-server cores |
| Ambient chatter never fires | raise `ambient_max_in_flight` (costs dialogue headroom) |
| OOM on 8GB card | switch model to the 3B (Llama-3.2-3B / Qwen2.5-3B Q5_K_M) |

## Skill self-improvement flow

1. A meta-agent (or you) writes candidate `decide(state)->action` skills to `sandbox/candidates/`.
2. Cron runs `run_skill_validation.sh` during low-player windows:
   each candidate executes in a `--network none --read-only` throwaway container
   against `skill_harness.py`.
3. Pass -> timestamped copy in `approved/` (the registry the orchestrator loads).
   Fail -> `rejected/` with logs. Every rejection is a future test case.
4. Rollback = delete the newest approved version.

The live loop can always adjust *facts* and *personality*; only *behavior
code* goes through the sandbox gate. That split is what keeps a
self-improving NPC from becoming a self-sabotaging server.

> 🐄 **Cow note:** the skill-writer meta-agent that actually populates
> `sandbox/candidates/` isn't built yet — today candidates are added by
> hand. Watch the sandbox logs before trusting anything it promotes to
> `approved/`.

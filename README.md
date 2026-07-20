🇫🇷 [Lire en français](readme_fr.md)

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

### ✅ What's actually been verified (2026-07-20, RTX 3060 12GB)

Unlike most of this README, this part isn't aspirational — the full Docker
stack was actually brought up against a real GPU and exercised end to end:

- `docker compose up -d --build` → all 4 containers reach healthy/running.
- Real GPU inference confirmed: llama.cpp served in-character replies
  (60+ tok/s) using the real Qwen2.5-7B-Instruct Q4_K_M model.
- **Measured VRAM: a stable ~5.9GB** (idle *or* 4 concurrent requests —
  llama.cpp pre-allocates the `--parallel` KV cache at startup, it doesn't
  grow under load), leaving ~6GB free on a 12GB card.
- A fake "player" WebSocket client (matching `NpcAiBridge.java`'s protocol)
  held a real multi-turn conversation: the NPC's second reply showed
  genuine continuity from the first (Qdrant recall working), a
  `player_gave_gift` outcome correctly nudged `warmth` and
  `trust_of_player` by the exact expected amounts in Postgres, and the
  episodic memory count in Qdrant matched the turns sent.
- This test run found and fixed **four real bugs** that static review
  hadn't caught: `download_model.sh` assumed a single-file model that
  doesn't exist on HuggingFace (Qwen splits q4_k_m/q5_k_m into two
  shards); the non-root orchestrator user broke fastembed's model cache
  (a regression from hardening the Dockerfile); `qdrant-client`'s
  unpinned upper bound resolved to a version that removed `.search()`;
  and `personality.py` had an unbound SQL placeholder that only surfaces
  as an error against a real Postgres, not on read-through.

**Still not tested**: an actual Hytale server/client, `NpcAiBridge.java`
against real plugin APIs, sustained multi-NPC load, or `skill_writer.py`
against the real GPU (it was only verified with a fake LLM/DB — see
[the skill self-improvement section](#the-skill-writer-meta-agent)).

### 🐄 The Hytale plugin scaffold: from bytecode-verified to actually booted

[`hytale-plugin/`](hytale-plugin/) (added 2026-07-20) started as every
class/method confirmed to exist by inspecting the bytecode of a real
installed `HytaleServer.jar` (v0.5.7), not guessed from docs. Later the same
day, a JDK 25 got installed and it went further: `./gradlew build` compiles
clean, and `./gradlew runServer` boots a **real local Hytale server with
this plugin loaded, set up, and enabled** through to `Hytale Server Booted!
[Multiplayer, Fresh Universe]` — no crash, no plugin errors. That run also
caught and fixed a real bug in the official plugin template's own Gradle
task (`--mods` wants a directory, not a jar path, and the server
double-counts an explicitly-passed mods dir that's already auto-scanned).

Still not tested: an actual player connecting and clicking an NPC. Read
[`hytale-plugin/README.md`](hytale-plugin/README.md) for the exact,
up-to-date breakdown of what's confirmed vs. still a placeholder (notably:
the thread-hop for touching world state from the bridge's callback, and
multi-turn chat-based conversation).

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

### Worked example: one blacksmith, three visits

| Visit | What happens | Why |
|---|---|---|
| 1st meeting | Ask about a sword → *"Aye, I can forge you a blade, but good steel isn't cheap."* | Baseline personality (`warmth=0.3, aggression=0.4`) renders as "cold and curt"; no memories yet, so the prompt says `(first meeting)`. |
| 3 days later | Ask again → *"Back for that blade again? Coin ready this time?"* | `recall_similar()` finds the earlier exchange in Qdrant and feeds it back into the prompt as a relevant memory. |
| After a few gifts | Tone warms toward "polite but reserved" — but only with you | Each `player_gave_gift` outcome nudges `trust_of_player` and `warmth` a small, bounded amount. Trust is tracked per (NPC, player) pair, so other players see no change. |

Attack that same blacksmith instead, and `player_attacked_npc` tanks trust
hard and raises aggression — but since `warmth`/`aggression` are stored
per-NPC (not per-player), the blacksmith gets warier and angrier *with
everyone*, while their trust specifically in you drops the most. Leave
them alone for a couple of in-game weeks and both effects decay back
toward baseline.

## Layout

```
docker-compose.yml          the whole stack (llama.cpp + orchestrator + DBs)
orchestrator/
  main.py                   WebSocket gateway + event handlers + compression daemon
  priority_queue.py         GPU slot arbiter: dialogue always wins, ambient never blocks
  llm_client.py             prompt builder (personality+memory -> system prompt) + llama.cpp client
  memory.py                 Qdrant episodic + Postgres semantic facts + compression
  personality.py            bounded trait nudges, per-player trust, decay to baseline
  skill_writer.py           offline meta-agent: outcome history -> candidate skills
sandbox/
  run_skill_validation.sh   ephemeral locked-down containers for candidate skills
  skill_harness.py          the tests a skill must pass to be promoted
scripts/download_model.sh   fetch Qwen2.5-7B-Instruct GGUF
hytale-plugin/               Gradle project - see hytale-plugin/README.md
  src/main/java/com/orffyrus/npcai/
    NpcAiBridge.java        plugin-side transport (no Hytale API coupling)
    NpcAiPlugin.java        entry point (extends JavaPlugin)
    NpcInteractListener.java  wires PlayerInteractEvent -> the bridge
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

> 🐄 **Cow note:** steps 1-4 above have actually been run on a real RTX 3060 -
> see [what's been verified](#-whats-actually-been-verified-2026-07-20-rtx-3060-12gb).
> Still not done: benchmarking `--parallel` under sustained multi-player
> load (only tested up to 4 concurrent requests briefly), and
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

1. `skill_writer.py` (or you, by hand) writes candidate `decide(state)->action`
   skills to `sandbox/candidates/`.
2. Cron runs `run_skill_validation.sh` during low-player windows:
   each candidate executes in a `--network none --read-only` throwaway container
   against `skill_harness.py`.
3. Pass -> timestamped copy in `approved/` (the registry the orchestrator loads).
   Fail -> `rejected/` with logs. Every rejection is a future test case.
4. Rollback = delete the newest approved version.

The live loop can always adjust *facts* and *personality*; only *behavior
code* goes through the sandbox gate. That split is what keeps a
self-improving NPC from becoming a self-sabotaging server.

### The skill-writer meta-agent

`orchestrator/skill_writer.py` looks at each NPC's recent outcome history
(a new `npc_outcome_log` table) and the most recent `sandbox/rejected/*.log`
entries, asks the LLM to draft a `decide()` for whatever pattern stands out
(e.g. a lot of `player_attacked_npc` might prompt a self-defense reaction),
does a *static* syntax/shape check — it never imports or executes what it
generates — and queues anything plausible into `sandbox/candidates/`. It has
no path to `approved/`; step 2 above still decides that.

Run it manually or from cron:

```bash
docker compose run --rm skill-writer --dry-run          # preview, writes nothing
docker compose run --rm skill-writer                     # writes candidates
docker compose run --rm skill-writer --npc-id blacksmith_01 --since-days 7
```

> 🐄 **Cow note:** `skill-writer` is profile-gated (`docker compose up` won't
> start it) because it calls `llm-inference` directly, bypassing the live
> orchestrator's dialogue-priority slot arbiter entirely. Running it while
> players are online will compete with real dialogue for the same
> `--parallel` slots — only run it during a confirmed low-player window,
> same rule as `run_skill_validation.sh`. Verified end-to-end with a fake
> LLM/DB locally; not yet run against the real GPU/model.

CI runs a fast copy of this gate on every push/PR touching `sandbox/**`
(see [`.github/workflows/skill-validation.yml`](.github/workflows/skill-validation.yml)):
it checks a known-good and a known-bad reference skill in
`sandbox/examples/` still pass/fail as expected, then validates anything
queued in `sandbox/candidates/`. It's a quick shape check only — the real
gate is still the locked-down Docker sandbox above.

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
  TalkToAIAction.java / PlayerChatToAIListener.java  the real hooks into the
                     bridge (NpcInteractListener.java was deleted 2026-07-21 -
                     confirmed dead code, see "State when handed off" below)
        │  WebSocket, JSON — protocol documented at top of orchestrator/main.py
        ▼
orchestrator/ (Python, asyncio)
  main.py            gateway + handlers + offline compression daemon
  priority_queue.py  GPU slot arbiter (see "hard rules" below)
  llm_client.py      personality+memory → system prompt; llama.cpp client;
                     dialogue() also gets a TONE self-report (kind/rude/
                     neutral) from the model on every real turn, zero extra
                     GPU cost - see "Agreed next steps" below
  memory.py          Qdrant episodic + Postgres semantic facts + compression
  personality.py     bounded trait nudges, per-player trust, decay to baseline,
                     outcome history log (npc_outcome_log)
  skill_runtime.py   loads sandbox/approved/ skills and runs them for
                     ambient/idle ticks, isolated per-call subprocess
  skill_writer.py    OFFLINE meta-agent: outcome history + rejected/*.log ->
                     candidate skills. Not started by `docker compose up`
                     (profile "tools") - never touches approved/ directly.
        │
        ▼
llm-inference: llama.cpp server-cuda, Qwen2.5-7B-Instruct Q4_K_M,
  --parallel 6 --cont-batching  (6 concurrent 2048-token slots - real load
  test on the RTX 3060, 2026-07-21, see "Agreed next steps" below)
memory-db: Qdrant (embeddings on CPU via fastembed — GPU stays for chat model)
fact-db: Postgres (facts, personality vectors)
sandbox/: skill validation in ephemeral --network none --read-only containers
```

## Hard rules — do not break these

1. **Dialogue always beats ambient.** Player-facing requests wait up to 12s
   for a slot (raised from 3s on 2026-07-21, alongside removing BUSY_LINES -
   see hard rule 2); ambient/idle requests NEVER queue — no slot free now
   means an instant, silent no-op. Ambient is capped at 2 of 6 slots (was 2
   of 4 - see the 2026-07-21 load test below; the cap is a small absolute
   number on purpose, not a proportional split, so dialogue's guaranteed
   minimum only grows as slot count grows).
2. **Nothing blocks the game loop.** Every LLM call has a timeout. On
   timeout/failure the NPC simply doesn't reply that turn (empty text,
   handled the same as an ambient no-op) rather than blocking or speaking a
   fallback line - **no pre-written filler text exists anywhere in this
   stack** (`priority_queue.py`'s `BUSY_LINES` was removed 2026-07-21; every
   word an NPC says comes from the model or it says nothing). The plugin
   fires events async and applies replies when they arrive.
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
   TalkToAIAction.java, PlayerChatToAIListener.java), not the transport.
   Still true: NpcAiBridge.java has zero Hytale imports.
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
- **2026-07-21, LATEST: fully confirmed working end to end, including
  multi-turn conversation.** A real player clicks `AI_Talker`, gets a real
  AI reply visibly tagged with the NPC's name in chat (`[AI_Talker] ...`),
  and can keep talking by just typing in normal chat - all live-confirmed,
  not just "compiles and boots clean." Tagged `hytale-plugin-e2e-working`
  as the known-good baseline. Getting the reply to actually reach the
  player took two more real bugs found only by watching it fail live: a
  stale `PlayerRef` captured before the async LLM round trip (fixed by
  re-resolving via `Universe.get().getPlayer(uuid)` at reply time instead
  of reusing the original reference), and `NpcAiBridge`'s hand-rolled JSON
  reader requiring zero-space JSON (`"npc_id":"x"`) when Python's
  `json.dumps()` always emits a space (`"npc_id": "x"`), which silently
  dropped every reply before the callback ever ran. Two environment
  gotchas also cost real debugging time and are now documented prominently
  in hytale-plugin/README.md: Hytale only has `Adventure`/`Creative`
  gamemodes (no "Survival" - the NPC interact prompt doesn't appear in
  Creative), and every new world defaults to `NpcAiStack` disabled in its
  mod settings. See hytale-plugin/README.md for full detail.
- **2026-07-20: confirmed working live, end to end (first pass).** A real
  player spawned `AI_Talker`, clicked it, and the orchestrator's own logs
  showed real, repeated round trips (Qdrant recall -> LLM call -> memory
  write) on every click - the AI is genuinely generating replies from a
  real in-game interaction. Two more real bugs found only by watching it
  fail live, both fixed: TalkToAIActionBuilder redundantly called
  readCommonConfig() (the real, shipped BuilderActionOpenBarterShop
  doesn't), which made the role fail spawn validation entirely
  ("failed to find npc role"); and a dropped animation-reset instruction
  (diffed against the real Kweebec_Merchant.json to find it) left the NPC
  stuck animating in place. Replies are now sent back as an actual chat
  message via PlayerRef.sendMessage() - see hytale-plugin/README.md for
  the full detail.
- **2026-07-20, earlier: hytale-plugin/ scaffold added, then compiled and
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

1. ~~Nothing ever consumes `sandbox/approved/`.~~ **Done for the ambient/
   idle path, 2026-07-21** — `orchestrator/skill_runtime.py`. `docker-
   compose.yml` mounts `./sandbox` read-only into the orchestrator;
   `handle_ambient()` in `main.py` asks `SkillRuntime.try_decide(npc_id,
   state)` first, and only falls back to the usual LLM `ambient_line()`
   call if there's no approved skill for that NPC or the call didn't
   produce a usable result. `npc_id` association is parsed from
   `skill_writer.py`'s own header docstring (`ast.parse` only, never
   exec'd) rather than guessed from the filename, since `npc_id` itself can
   contain underscores. Each real call runs in a throwaway subprocess
   (`sandbox/skill_runner.py`, not imported in-process) with a CPU/memory
   rlimit and a hard timeout+kill, and its output is re-validated against
   `skill_harness.py`'s `validate_output()` every single call, not just
   once at promotion — verified live: a hung `decide()` gets killed at
   ~1.1s via the CPU rlimit (well under the 2s asyncio backstop), an
   invalid action is rejected and falls back cleanly, and a real "say"
   result is returned end-to-end.
   **Deliberately still open** (see `skill_runtime.py`'s docstring for the
   reasoning, not just a TODO): only wired into ambient/idle, not real
   dialogue — the `decide(state)` shape (`event`/`player_id`/`npc_hp`/
   `time_of_day`/`nearby_players`, no player utterance at all) and action
   vocabulary (`say`/`emote`/`walk_to`/`give_item`/`set_quest_flag`/
   `trade_offer`/`idle`) both read as reactive/ambient behavior, not
   conversation. Only `say`/`idle` produce anything today — `NpcAiBridge`'s
   wire protocol has no handler yet for `emote`/`walk_to`/`give_item`/
   `set_quest_flag`/`trade_offer`; a skill choosing one of those is logged,
   not silently dropped, but does nothing in-game until the plugin side is
   built. Also: `npc_hp`/`nearby_players` aren't tracked anywhere yet, so
   they're sent as fixed placeholders (100 / 1) — a skill that actually
   branches on them won't behave usefully until that's real. The trust
   model (subprocess isolation, not full container isolation like
   `run_skill_validation.sh`) is explicitly scoped to "skills are self-
   generated by the same local LLM already trusted for dialogue" — revisit
   if skill authorship ever becomes multi-tenant/untrusted.
2. ~~Skill-writer meta-agent - NOT yet run against the real GPU/model.~~
   **Actually run against the real GPU, 2026-07-21** - and it surfaced a
   real bug the fake-LLM/DB verification couldn't have caught: the model
   reliably confused the outcome-history counts shown as CONTEXT in the
   prompt (e.g. "player_helped_quest: 6 times") with actual live `state`
   dict keys/values, writing things like `"player_helped_quest" in state`
   or `state["event"] == "player_helped_quest"` - `state` never contains an
   outcome name, only `event`/`player_id`/`npc_hp`/`time_of_day`/
   `nearby_players`. Code like this passes shape validation (syntactically
   fine, valid dicts on every `TEST_STATES` case, since none reference an
   outcome name either) but is functionally dead - the branch checking for
   it can never fire on any real call, silently doing nothing forever once
   approved and actually loaded by `skill_runtime.py`. Fixed two ways: (1)
   the prompt in `_build_prompt()` now explicitly separates "history is
   context for you" from "these are the only real state keys, with these
   types" (also fixed a second confusion this surfaced: `time_of_day` is a
   free-text string, not a number - the model tried `time_of_day >= 18`
   once); (2) `_static_check()` now statically rejects (`ast.parse` only,
   still never imports/execs) any candidate referencing an outcome-effect
   name as a literal anywhere - confirmed this catches the exact bad
   candidate generated before the fix. Verified live end-to-end after the
   fix: generated a candidate with zero outcome-name confusion; a
   different, unrelated bug in that same candidate (a "say" action missing
   its `text` key) was correctly caught and rejected by the real
   `run_skill_validation.sh` sandbox, exactly as designed.
   `orchestrator/skill_writer.py`, run via `docker compose run --rm
   skill-writer [--dry-run] [--npc-id X]`. It bypasses the live
   orchestrator's dialogue-priority slot arbiter, so only run it during
   confirmed low-player windows, same as `run_skill_validation.sh`.
3. ~~Load-test llama.cpp slots on the real GPU; tune --parallel/ctx
   tradeoff.~~ **Done, 2026-07-21** - see README.md's "real concurrency load
   test" table. `--parallel 6` / `--ctx-size 12288` beat the old
   `--parallel 4` / `--ctx-size 8192` at every concurrency level tested
   (better p50/p95 latency, ~25% higher peak throughput) for +227MB VRAM,
   with 5.5GB still free. `--parallel 8` didn't move the throughput ceiling
   (~4.5-4.9 req/s either way - this GPU's real compute ceiling for this
   model, not a slot-count limit) and was noisier, so 6 is the chosen
   point. `main.py`'s `DISPATCHER.max_concurrent_slots` updated to match -
   if `--parallel` changes here, that must change too or the extra
   capacity just sits unused. Not yet tested: *sustained* real multi-player
   load over time (this was a synthetic burst-concurrency benchmark against
   `llm-inference` directly, not hours of real dialogue traffic).
4. ~~Wire NpcAiBridge.java into the Hytale plugin API.~~ **Done and
   confirmed live** - see "State when handed off" above: a real player has
   had a full multi-turn conversation with an in-game NPC through this
   exact path, tagged `hytale-plugin-e2e-working`.
5. ~~GitHub Actions workflow running skill_harness.py on push.~~ **Done** -
   see `.github/workflows/skill-validation.yml`. Two jobs: a harness
   self-test against `sandbox/examples/` (one known-good skill that must
   pass, one deliberately bad one that must fail), plus a job that validates
   anything queued in `sandbox/candidates/*.py` - as of 2026-07-21 this job
   runs each candidate through the same locked-down `docker run` isolation
   as `run_skill_validation.sh` (it used to invoke `skill_harness.py` bare
   on the runner, a real gap: a PR force-adding a file under
   `sandbox/candidates/` - gitignored, but that doesn't stop a tracked PR
   diff - would have executed arbitrary code with full runner privileges).
   Still a fast gate only, same as before - it does NOT replace
   `run_skill_validation.sh`, which is still what actually promotes a skill
   to `approved/`.
6. ~~Real outcome data never reached personality.py/skill_writer.py from
   actual gameplay.~~ **Done for kind/rude/attacked, 2026-07-21** - see the
   sections below. `player_was_kind`/`player_was_rude` are inferred
   straight from every real dialogue turn (zero new Java code).
   `player_attacked_npc` initially looked like it needed raw ECS
   (`DamageEventSystem`) - turned out not to: the real, shipped `"Damage"`
   Sensor Type (confirmed via disassembly of `SensorDamage`/
   `BuilderSensorDamage`, and via its real usage in the neutral Kweebec's
   own Panic trigger) is registered through the exact same
   `NPCPlugin.registerCoreComponentType()` extension point everything else
   in this plugin already uses - `NoteAttackedByPlayerAction.java` mirrors
   `NoteNearbyThreatAction.java`'s pattern exactly. **Boot-tested clean**
   (`./gradlew runServer` validates and boots with zero errors), **not yet
   confirmed against a real attack** (all 4 roles set `Invulnerable: true`;
   reasoned, not confirmed, that damage events still fire regardless - see
   hytale-plugin/README.md's table). Still genuinely open:
   `player_gave_gift`/`player_helped_quest`/`joke_landed`/
   `player_shared_news` need game systems (inventory, quests) that don't
   exist at all yet.

## 2026-07-21, later: real outcome data now flows from actual gameplay

Investigated task "wire sendOutcome from the Java plugin" and found the
straightforward version of that task doesn't have a clean answer: unlike
every other game mechanic this plugin integrates with (TalkToAI ~ shipped
OpenBarterShop, NoteNearbyThreat ~ Trork's own Mob/Attitude sensor,
SeekLandmark ~ `/locate zone`), there is NO existing high-level Sensor/
Condition JSON anywhere in the shipped game for "this NPC just took
damage" - confirmed by cataloguing every Sensor/Action `Type` used across
Trork's own Panic/Alerted/Reputation_Switch instruction JSON in
`Assets.zip`: Attitude/LineOfSight/Mob/Player/Target/Timeout/Timer/Watch/
etc, nothing damage-related. Hostile mobs are hostile by proximity/
attitude, not by reacting to being hit - there's no "under attack" reflex
to copy. The real hook is one layer deeper: `DamageEventSystem` (confirmed
via `javap` against the real `HytaleServer.jar`) is a raw ECS
`EntityEventSystem<EntityStore, Damage>` subclass - a fundamentally
different, higher-risk integration than the `NPCPlugin.
registerCoreComponentType()` extension point everything else in this
plugin uses, and not something to wire blindly without a live client to
verify against (this session's environment is backend-only). Deferred as
its own tracked task (see "Agreed next steps" #4) rather than rushed.

**Correction, same day, later**: this conclusion was wrong - see the new
section further below. `DamageEventSystem` (raw ECS) is real, but it's not
the only way in; a proper Sensor Type for exactly this ("Damage",
`Combat: true`) exists and is used by the shipped game itself, just not by
Trork specifically (the file catalogued above) - it took checking a
NEUTRAL creature's reactive behavior (Kweebec) to find it, since hostile
mobs don't need an "under attack" reflex at all (they're already hostile
by default).

Instead, fixed the actual underlying problem a different way: confirmed via
direct DB inspection that `npc_outcome_log` had zero real rows for any
actual live NPC (only stale test fixtures), which meant `personality.py`'s
trait evolution and `skill_writer.py`'s own trigger condition
(`recent_outcome_counts`) were both completely inert for real gameplay -
only reachable via manual test data. `sendOutcome`'s wire message was never
the only possible source of outcome data: the orchestrator already runs one
LLM call per dialogue turn with full context on how the player just spoke
to the NPC, so `llm_client.py`'s `dialogue()` now also asks for a `TONE:
KIND`/`TONE: RUDE`/`TONE: NEUTRAL` self-report on the SAME completion
(parsed the same tolerant way as the existing `ACTION` tag - no extra GPU
cost, no new Java code). `main.py`'s `handle_dialogue` maps kind/rude to
`player_was_kind`/`player_was_rude` outcomes (neutral records nothing, on
purpose - most turns are just conversation, not an evaluable event).
Verified live end-to-end: real kind/rude/neutral messages through a real
WebSocket connection to the running orchestrator correctly produced (or
correctly withheld) real rows in `npc_outcome_log`, and real trait nudges
in `npc_personality` - confirmed by direct Postgres inspection, then
cleaned up the test data.

This did NOT cover `player_attacked_npc` at the time it was written (see
the next section - it was fixed the same day, later) or
`player_gave_gift`/`player_helped_quest`/`joke_landed`/`player_shared_news`
(still open - need game systems, inventory/quests, that don't exist yet).

## 2026-07-21, later still: player_attacked_npc, via the real "Damage" sensor

Picked back up the deferred Java/ECS work above and found the earlier
conclusion was too pessimistic. Catalogued every Sensor/Action `Type`
across a NEUTRAL creature's own reactive behavior this time (the Kweebec,
not Trork) - `Server/NPC/Roles/Intelligent/Neutral/Kweebec/
Component_Kweebec_Instruction_Panic.json` references
`Component_Instruction_Damage_Check` (`Server/NPC/Roles/_Core/Components/
Steps/`), whose real Sensor is `{"Type": "Damage", "Combat": true}` -
confirmed via `javap` against `SensorDamage`/`BuilderSensorDamage`
(`com.hypixel.hytale.server.npc.corecomponents.combat`), registered
through the exact same `corecomponents` extension point as everything else
in this plugin. The earlier Trork-only catalogue missed it because hostile
mobs don't need an "under attack" reflex at all - they're already hostile
by default; the "am I being hit" reflex only exists for creatures that
start out neutral/passive.

`SensorDamage.getSensorInfo()` returns an `EntityPositionProvider` whose
`getTarget()` is a plain `Ref<EntityStore>` - the same type this plugin
already resolves to a `PlayerRef` everywhere else (`TalkToAIAction`, the
deleted `NpcInteractListener`). No raw ECS needed anywhere.

Added: `NoteAttackedByPlayerAction.java` + `...ActionBuilder.java`, mirroring
`NoteNearbyThreatAction.java`'s exact pattern - resolves the attacker,
checks it's really a `PlayerRef` (a Damage sensor match could be a hostile
mob's stray hit, not a player), debounces per NPC (`COOLDOWN_MILLIS =
15_000`, same reasoning as `ThreatMemory`'s staleness window - one real
attack is several hits in a few seconds, and `personality.py`'s
`LEARNING_RATE` assumes one nudge per interaction, not per hit), then calls
`bridge.sendOutcome(npcId, aiRole, playerUuid, "player_attacked_npc")`.
Registered as `NoteAttackedByPlayer` in `NpcAiPlugin.setup()`; added a
`{"Type": "Damage", "Combat": true}` sensor + action block to all 4 role
JSONs (`Adventurer`/`AI_Talker`/`Elder_Miri`/`Merchant_Oskar`).

Also fixed a real observability gap found while verifying this:
`handle_outcome()` in `main.py` only ever logged its FAILURE paths - a
successfully recorded outcome was invisible in the orchestrator's own log,
which would have made this specific feature impossible to confirm live
without directly querying Postgres. Added an info-level log line on
success.

Verified: `./gradlew build` clean; `./gradlew runServer` boots the real
Hytale server with "Registered NoteAttackedByPlayer NPC action type" and
zero validation errors attributable to any of the 4 modified role JSONs
("Validation complete. Loaded 977 NPC configurations, Generic: 4"); a
simulated `outcome` WebSocket message matching exactly what the new Java
code sends produced the new log line and a real row in `npc_outcome_log`,
confirming the wire contract end-to-end. **Not yet live-confirmed against
a real attack** - all 4 roles set `Invulnerable: true`, and while
`DamageSystems$PlayerDamageFilterSystem`'s own pattern (cancelling damage
via a flag on an event that still fires, rather than suppressing the event
outright - confirmed via bytecode) is good evidence the Damage sensor
should still match regardless, that's inference, not a live hit. See
hytale-plugin/README.md's verification table for how to confirm this
firmly with a real client.

## 2026-07-21, later still: reactive companion defense ("fight with me if I am attacked")

Requested: the tamed companion should fight alongside the player when the
player is attacked, not just when the companion happens to notice a
hostile on its own. The existing companion-combat sensor (`Adventurer.json`'s
Mob+Attitude block, `Range: 30`) is purely proactive - it only reacts to a
hostile the companion can already see/sense itself, which could miss an
attacker that's engaging the player specifically from outside the
companion's own detection.

Added a second, real, shipped sensor: `{"Type": "EntityEvent", "EventType":
"Damage", "NPCGroup": "Player", "SearchType": "PlayerOnly"}` - confirmed via
disassembly of `SensorEntityEvent`/`BuilderSensorEntityEvent`
(`com.hypixel.hytale.server.npc.corecomponents.world`) and via its real
usage in `Server/NPC/Roles/_Core/Components/Steps/
Component_Instruction_Allied_Damage_Check.json`. `EventSearchType` has four
real values (`PlayerFirst`/`PlayerOnly`/`NpcFirst`/`NpcOnly`) -
`PlayerOnly` matches only when a nearby player takes damage, ignoring NPCs
entirely. `NPCGroup` is a required field even for player-only search
(bytecode-confirmed via `TagSetExistsValidator.required()`) - satisfied
with the real, shipped `"Player"` NPCGroup asset
(`Server/NPC/Groups/Player.json` = `{"IncludeRoles": ["$player"]}`).

Combined this trigger with the *same* Mob+Attitude hostile search already
used for proactive detection, just widened to 45 blocks (from 30) - once we
know for certain the player was just hit nearby, it's worth searching
harder for whatever did it. Added only to `Adventurer.json`, the one
archetype where physical combat fits the character
(`aggression=0.5`, "bold and quick to trust") - `Elder_Miri`/`Merchant_Oskar`
stay non-combat companions by design, matching their low-aggression
personas (`0.05`/`0.15`) and the LLM's own `OFFER_FIGHT` reasoning ("only
pick OFFER_FIGHT if you're genuinely someone who stands and fights").

Confirmed real combat itself is driven entirely by this passive
IsCompanion+Target detection, independent of the LLM's own `OFFER_FIGHT`
dialogue decision (`main.py`'s wire-protocol docstring already documents
this: "offer_fight"/"decline_guide" are informational only) - so this
addition slots into the existing architecture rather than needing any new
wiring between dialogue and combat.

**Boot-tested clean** (`./gradlew runServer` validates and boots with zero
errors attributable to the new sensor block, `Loaded 977 NPC configurations,
Generic: 4`) - **not yet live-confirmed**, same caveat as the
companion-combat system it extends (see hytale-plugin/README.md's "Known
simplification" - no live test session so far has happened near a real
hostile creature).

## 2026-07-21, later still: a "thinking" icon, and replies stop feeling instant

Requested: an "AI process message waiting" icon over the NPC's head, and
fix the way replies arrive instantly - it breaks immersion.

**The icon.** Needed a real, shipped way to show a floating icon above an
entity - `EntityUIType` (`EntityStat`/`CombatText`) turned out to be a
narrow health-bar/damage-number system, not a general-purpose icon slot, so
that was a dead end. The real answer was simpler: `ActionSpawnParticles`
(`"Type": "SpawnParticles"`), already used in this project's own
`NoteNearbyThreat` sibling work (Trork's `Alerted` state spawns a particle
at `Offset: [0, 2.1, 0]`, confirmed real usage to copy the positioning
from) - and the shipped particle library has a whole `Server/Particles/
NPC/Emotions/` category (`Question`, `Question_Subtle`, `Angry`, `Hearts`,
`Sleepy`, `Stunned`, plus `.particlespawner`-only `ThoughtCloud_*` variants
that aren't compatible with `SpawnParticles`'s `ParticleSystemExistsValidator`
- confirmed via bytecode it only accepts `.particlesystem` assets).
Landed on `Question_Subtle.particlesystem` - a floating "?" reads as
"thinking about what to say" without being as visually loud as `Question`.

Needed a way to trigger it from Java (not the passive Sensor/Action tree
alone, since "a dialogue request is in flight" is state only Java code
knows about) - built the same way `CompanionState`/`GuideState` already
bridge Java state into JSON sensors: `AwaitingReplyState.java` (a
TTL-swept `ConcurrentHashMap`, same shape as `PendingShopOpen`) set by
`TalkToAIAction`/`PlayerChatToAIListener` right before `bridge.
sendDialogue()` and cleared the moment that specific reply's callback
fires, plus `IsAwaitingReplySensor.java`/`...Builder.java` (mirrors
`IsCompanionSensor` exactly) exposing it as `{"Type": "IsAwaitingReply"}`
in role JSON. `"Once": true` (the same modifier `Component_Instruction_
Wild_Panic_Passive.json` uses to fire an animation once per state-entry,
not every tick) keeps this from re-spawning particles every tick for the
whole wait. Added to both `Watching` and `$Interaction` states in all 4
role JSONs, since a click-triggered conversation's wait starts in
`$Interaction` (before the ~1s timeout reverts to `Watching`) but usually
finishes in `Watching`.

**The pacing.** Even with the icon, a reply that lands in 0.5s (a real,
observed latency at low concurrency - see the load-test table) doesn't
give the icon time to register as anything before being replaced by text.
`main.py`'s `handle_dialogue()` now pads any reply faster than
`MIN_REPLY_DELAY_S` (1.3s) up to that minimum, measured from the start of
the whole turn and applied AFTER the dispatcher's GPU slot is already
released (`request_dialogue`'s `finally` block) - this is purely this
player's own perceived latency, it never holds a slot open longer than the
real LLM call needed, so it can't cost other players GPU throughput. A
reply that's already slower than 1.3s is never delayed further - this only
pads the fast case, never compounds load-induced latency.

**Boot-tested clean** (`./gradlew runServer`: `Registered IsAwaitingReply
NPC sensor type`, zero validation errors, `ParticleSystem: 569` assets
loaded including the referenced one). Pacing verified live end-to-end:
three real dialogue turns through a real WebSocket connection to the
running orchestrator each measured 1.30s, confirmed padding a reply that
would otherwise have arrived instantly. The icon itself was **not
live-confirmed at the time** - and live testing (same day, see the section
below) found it genuinely never appeared. Boot-testing only proves the
role JSON validates; it can't catch "this sensor's own logic never
actually returns true," which is exactly what happened here.

## 2026-07-21, later still: removed every pre-written fallback line

Requested: let the AI talk, remove pre-made default responses. Two places
had one: `priority_queue.py`'s `BUSY_LINES` (six rotating "give me a
moment"-style lines, shown when the GPU had no free slot or the LLM call
timed out/failed) and a second, separate copy `main.py` fell back to if
`handle_dialogue()` itself threw an unexpected bug (added in the earlier
audit pass, before this request, specifically so a server-side bug
wouldn't be worse than a busy GPU - see that section above). Both removed;
both now return `""` instead - the exact same "nothing to say this turn"
sentinel `handle_ambient()` already used, so this isn't a new wire-protocol
concept, just dialogue finally getting the same honest-silence treatment
ambient always had.

Removing the fallback text without changing anything else would have made
"the GPU's momentarily busy" indistinguishable from real silence far more
often than necessary, so `NPCRequestDispatcher`'s timeouts came along with
it: `dialogue_wait_timeout_s` 3.0 -> 12.0, `dialogue_call_timeout_s` 6.0 ->
10.0 (hard rule 1's documented number, updated). Both sit well above the
real p95 latencies measured in the 2026-07-21 load test (~2.6-3.8s at
12-16 concurrent requests on 6 slots), so in practice a real reply arriving
late should now almost always still arrive - the empty-string path should
only bind under genuinely pathological load or a hung call, not normal
play with 1-3 players.

The trickier half was making sure "" doesn't leave the new "thinking" icon
(added just above) stuck: `NpcAiBridge.handleMessage()` used to skip
calling the reply handler entirely when `text` was empty, which would have
meant `AwaitingReplyState.clear()` never fires and the particle just sits
there for the full 20s TTL every time the AI has nothing to say. Fixed by
always invoking the handler once `text` is non-null (even ""), and moving
the "don't send an empty chat message" decision into
`TalkToAIAction`/`PlayerChatToAIListener`'s own handlers instead - they
still clear the awaiting-state and skip the rest of the action-handling
logic (nothing real to act on when there's no real reply), just without
sending a message. `handle_dialogue()` in `main.py` also stops recording an
episodic memory for an empty reply (`"I replied: ''"` isn't a real memory,
and would otherwise let a transient failure permanently pollute the NPC's
memory of the player).

Not touched, on purpose: the short system-enforced clarifier appended when
the model incorrectly decides ACCEPT_TAME against the hard 1-per-player
rule ("(Something holds you back from actually committing to this.)" in
`handle_dialogue()`). That's appended to real, model-generated text for a
specific correctness constraint, not a substitute for a reply the model
never gave - a different category from BUSY_LINES, left as-is. Revisit if
that reads as still in-scope for "let the AI talk."

Verified: dispatcher-level test confirms `request_dialogue()` now returns
`""` on a simulated call failure (was previously a random `BUSY_LINES`
pick); a real dialogue turn through the actual running orchestrator still
gets a real reply unaffected by any of this. `gradlew build`/`runServer`
clean after the Java-side changes (no role JSON touched this round, pure
logic changes in `NpcAiBridge.java`/`TalkToAIAction.java`/
`PlayerChatToAIListener.java`).

## 2026-07-21, later still: the "thinking" icon never appeared - real bug, found and fixed

Live-tested (first real live check of anything from this session): the
"thinking" particle never showed up at all. Boot-testing only proves role
JSON validates against the engine's schema - it can't catch "this custom
sensor's own logic never actually returns true when it should," which is
exactly what was wrong.

Root cause: `IsAwaitingReplySensor` used the JSON `"Once": true` modifier
to stop the particle from re-spawning every tick - looked like the obvious
tool for the job, matching a pattern seen in `Component_Instruction_
Wild_Panic_Passive.json`. Disassembling `SensorBase` (`matches()` returns
true unless both `once` and an internal `triggered` flag are set;
`triggered` is only flipped by `setOnce()`/`clearOnce()`, which a custom
sensor never calls itself - the *framework* calls them) plus finding a
second, even closer real reference -
`Component_Kweebec_Instruction_Search.json`, which spawns a "Question"
particle at the *exact same* `Offset: [0, 2.1, 0]` this code already used
- revealed the actual pattern: every real, shipped use of `"Once": true`
pairs it with an unconditional `"Type": "Any"` sensor, inside a
state-specific Instructions branch. It means "fire once per STATE ENTRY"
(reset by the state-transition machinery when the containing state is
re-entered), not "fire once per rising edge of a dynamic condition while
remaining in the same state." `IsAwaitingReply` flips true/false/true
repeatedly *within* one uninterrupted `Watching` visit (once per chat
message) - the state itself never re-enters between messages, so the
"Once" latch set on the very first reply never got a chance to reset, and
the particle never fired again for the rest of that visit (in the worst
case, never at all, depending on exactly when the player first looked).

That same search also ruled out a second suspect: the `Offset: [0, 2.1, 0]`
value (copied from Trork's own `Alerted` particle) looked like it might be
miscalibrated for the much smaller Kweebec-based models these NPCs use -
the Search file's identical offset on an identical model family confirmed
it was never the problem.

Fix: removed `"Once": true` from all 8 occurrences (4 role JSONs x 2
states each); `IsAwaitingReplySensor` now self-throttles with its own
2-second-per-NPC cooldown (a plain static map, same shape as everywhere
else in this plugin), independent of the framework's state-entry-tied
`Once` lifecycle entirely. Re-fires periodically for a long wait instead of
a single one-shot attempt that could latch shut.

`gradlew build`/`runServer` clean, zero validation errors, same NPC
counts as before. Not yet re-confirmed live (needs the next play session)
but the fix no longer depends on any framework behavior that couldn't be
directly proven correct via disassembly.

## 2026-07-21, later still: companion combat never actually attacked - real bug, found and fixed

Reported: NPC won't attack. Confirmed first (before digging further) that
this wasn't the two obvious non-bugs - the NPC was genuinely tamed and
following, and a real hostile mob was genuinely present - so this was a
real bug in the combat chain, not a misunderstanding of when combat is
supposed to trigger.

First ruled out my leading suspicion (that `LockOnTarget: true` on the Mob
sensor and the later bare `Target` sensor might write/read different
target slots when neither names one explicitly, since every real shipped
example I'd found up to that point always names an explicit `TargetSlot`).
Disassembled `BuilderSensorTarget`/`BuilderSensorEntityBase`'s
`readConfig()` and confirmed both independently default to the exact same
string, `"LockedTarget"`, when omitted - not the bug.

The real cause: the Mob sensor's `Prioritiser` filtered for
`AttitudesByPriority: ["Hostile"]` only. Disassembling
`SensorEntityPrioritiserAttitude.getPriority()` showed it calls
`WorldSupport.getAttitude(candidate, self, ...)` - a *pairwise* lookup
between this specific NPC and the candidate entity, not "is this creature
type hostile" as an inherent property - and confirmed (via an
`IllegalStateException` that fires if an unlisted attitude ever reaches
that code) that any attitude not in the priority list gets filtered out
entirely before reaching this point. A mob configured hostile toward
*players* has no reason to also be flagged Hostile toward another NPC like
Adventurer - it most likely resolves to Neutral instead, which wasn't in
the list. The real, shipped analog for "is this nearby thing dangerous" -
`Component_Instruction_Damage_Check.json`'s Sight/Sound-by-Attitude
sensors, which the neutral Kweebec uses to decide when to flee - checks
`[Hostile, Neutral]`, not `Hostile` alone, for exactly this reason.
Fixed both `AttitudesByPriority` lists in `Adventurer.json` (the original
30-block threat-noting sensor and the reactive-defense 45-block one added
earlier today) to match.

`gradlew build`/`runServer` clean, zero validation errors, same NPC
counts. Not yet re-confirmed live. Flagged one real trade-off inherited
from the shipped game's own equivalent check: this may now also make a
companion react to harmless Neutral creatures (passive wildlife), not just
genuinely dangerous ones - the same imprecision the real Kweebec fear-check
already accepts, not something worse than the shipped game does.

## 2026-07-21, later still: combat STILL didn't fire - real cause was never the attitude filter

Reported again: still not attacking. This environment has no live client, but
this machine turned out to BE the one the real play session runs on - found
real, timestamped server/client logs under `%APPDATA%/Hytale/UserData/`
(`Saves/NPC_TEST-07/logs/*_server.log`, `Logs/*_client.log`) from the actual
session, confirmed to be running the just-fixed jar (`[NpcAiPlugin] Registered
NoteAttackedByPlayer/IsAwaitingReply...` only exist in today's build). Cross-
referenced against `docker compose logs orchestrator` for the same window
using the real npc_id/player_id. This is a categorically different, far
stronger kind of evidence than boot-testing or reasoning from disassembly -
an actual recorded trace of what happened, not an inference about what should
happen.

The trace: player spawned Adventurer (01:24:15), clicked it (01:24:21, real
reply, `is_companion=True` - already tamed from an earlier session, Postgres
truth survived the restart as designed), then sent one chat message
(01:24:37) that the orchestrator classified as `action=offer_guide`. Reply
text: *"I'll follow close behind, Orffyrus. Lead the way."* - the NPC saying
**it** will follow the **player** - yet tagged `OFFER_GUIDE`, which the
system interprets as the opposite (the NPC walks to a landmark, the player is
expected to follow it). `GuideState` immediately started guiding; the
companion arrived at a landmark 2 seconds later and the session ended ~2.5
minutes after with no further NPC-AI activity logged at all. The companion
had physically left the player's side before any hostile encounter could
have happened near it - never an attitude-filter problem, that fix from
earlier may well be completely correct and simply never got exercised.

Root cause: `SYSTEM_TEMPLATE`'s `OFFER_GUIDE` rule said only "the player just
asked you to lead them somewhere" - genuinely ambiguous between "lead me
somewhere specific" (should trigger it) and "follow/accompany me" (should
NOT - a tamed companion already follows automatically with no tag needed,
per the passive Instructions-tree logic, not a per-message LLM decision).
The model conflated them. Fixed by making the distinction explicit and
mechanical in the prompt, not just narrative: OFFER_GUIDE/DECLINE_GUIDE only
when a real destination is named or clearly implied; "follow me"/"come with
me"/"stay close"/"let's go" with no destination is NONE, explicitly told
this matters because tagging OFFER_GUIDE here sends the companion walking
AWAY from the player, the opposite of what was asked.

Verified against the real model (not just reasoning about the prompt text):
"follow me" -> `none` (was the exact failure), "stay close to me" -> `none`,
"take me to the lake" -> `offer_guide`, "guide me to the nearest landmark" ->
`offer_guide`, "where's the blacksmith, show me" -> `offer_guide`. One
unrelated quirk noted, not fixed: "come with me" produced `offer_fight` with
some hallucinated flavor text about nearby bandits - doesn't reproduce this
bug (`OFFER_FIGHT` never moves the NPC, per `main.py`'s own wire-protocol
docstring: "informational only"), just the model being imaginative at
temperature 0.8 on short ambiguous input - worth another look someday, not
urgent.

## 2026-07-21 audit pass, approved/ wired up, then a real GPU load test

Asked to deep-audit the whole project (not chasing one symptom), fix what's
real, close the biggest gap the audit found, then settle the one remaining
open roadmap item with real data instead of a guess. Three batches:

**Load test**: real concurrency benchmark against `llm-inference` directly
(realistic ~1770-token prompts, not toy ones) across `--parallel` 4/6/8 -
see README.md's table. `--parallel 6` / `--ctx-size 12288` won outright
(better latency than 4 at every level, same throughput ceiling as 8 with
less VRAM and less noise); shipped as the new default, with `main.py`'s
`DISPATCHER` updated to match.

**New capability**: `orchestrator/skill_runtime.py` closes the
`sandbox/approved/` dead-end (see "Agreed next steps" #1 above for full
detail) - approved skills now actually run, for ambient/idle ticks only,
each call isolated in its own subprocess with a CPU/memory rlimit and a
timeout+kill, output re-validated every call against `skill_harness.py`'s
own rules. Real dialogue and the remaining action vocabulary
(`emote`/`walk_to`/`give_item`/`set_quest_flag`/`trade_offer`) are explicit,
documented non-goals for this pass, not oversights.

**Audit findings, all fixed and verified against the live stack unless
noted:**

- **Compression silently never worked, for any NPC, ever.** The offline
  memory-compression daemon (`main.py`'s `low_prio_llm`) called
  `LLM.dialogue()` - the in-character roleplay function - to do plain
  summarization, wrapping every compression prompt in the ~700-token NPC
  system template. Pushed requests over the 2048-token slot budget, logged
  as misleading "gpu busy" errors. Fixed: use `LLM.complete()` directly;
  added `MAX_COMPRESSION_CHARS` as a defensive batch-size cap in
  `memory.py`. Confirmed live: Adventurer's 128 stuck raw episodes
  compressed down to 98 with real distilled facts on the next run.
- **Root architectural fix**: `llm_client.build_dialogue_messages()` now
  enforces an actual token budget (`_PROMPT_TOKEN_BUDGET`), trimming
  facts/memories until the assembled prompt fits, instead of trusting
  fixed *counts* (`MAX_FACTS_COMPANION` etc.) to stay small - the
  compression bug above was one way that assumption broke; there was
  nothing stopping another. Verified with a worst-case synthetic prompt
  (12 padded facts + 10 real memories) against the real tokenizer.
- **Cross-player reply misdelivery** (`NpcAiBridge.java`): `sayHandlers` was
  keyed only by `npc_id`, so two players talking to the same NPC entity
  concurrently would silently steal each other's reply handler - one
  player's conversation could be delivered into another's chat. Fixed:
  replies are now routed per-request via the `req_id` the orchestrator
  already generated and echoed back (the client just never read it before).
- **`taming.py` race**: two `ACCEPT_TAME` decisions for the same player on
  different NPCs landing concurrently could raise an uncaught
  `UniqueViolationError`, dropping that player's reply entirely (worse than
  the busy-fallback). Fixed with a catch that resolves it the same way the
  existing-pet check would have. Also added a general backstop in
  `main.py`: any unexpected exception in `handle_dialogue` now still sends
  a busy-fallback line instead of total silence.
- **`GuideState`'s 25s give-up timeout was defeated by continued chat** -
  almost every reply decides `OFFER_GUIDE`, and `startGuiding()` reset the
  clock on every call, so a player who kept talking during a guide could
  reset it forever. Fixed: only resets on a genuinely new target.
- **Outcome messages missing `npc_role`** (`taming`/`personality` baseline):
  `sendOutcome`/`handle_outcome` didn't carry `npc_role` the way
  dialogue/ambient do, so an NPC's personality baseline could get created
  wrong forever if an outcome ever arrived before any dialogue. Latent, not
  live yet - `sendOutcome` has no caller in the plugin yet - fixed
  pre-emptively since it's clearly a planned integration point.
- **Orchestrator WebSocket had no auth and was bound to `0.0.0.0:8765`** on
  the host (`docker-compose.yml`) - any LAN peer could forge dialogue/
  outcome messages. Rebound to `127.0.0.1`, matching `llm-inference`'s
  existing loopback-only pattern; the plugin only ever needs
  `ws://localhost:8765` anyway.
- Deleted `NpcInteractListener.java` - its own docstring already called it
  confirmed-dead code (`PlayerInteractEvent` never fires for NPC clicks);
  kept alive only because it happened to still compile.
- Minor: `NpcAiBridge.esc()` now escapes all control characters, not just
  `\n`/`\r` (a raw tab in chat used to produce invalid JSON, silently
  dropping that turn); `PendingShopOpen` entries now expire (same
  unbounded-growth class as the conversation-map leak below);
  `ACTIVE_CONVERSATIONS` entries now get swept on a timer instead of only
  being checked lazily when that same player happened to chat again.

## Tuning table

See README.md — it maps symptoms (slow dialogue, world stutter, OOM) to the
specific knob to turn.

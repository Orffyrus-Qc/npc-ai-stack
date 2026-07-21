🐄 **Falling Cow Zone** — see the [repo root README](../README.md).

## 2026-07-21: a cast, not one demo NPC - stable identity + two new characters

With the reply-delivery chain confirmed working end to end, the next step
was turning "one hardcoded test NPC" into an actual small cast. Two changes:

**Fixed a real latent bug: `npc_id` was the spawned entity's own UUID, not a
stable character identity.** Every `/npc spawn` (or world reload) creates a
brand-new random entity UUID, and `TalkToAIAction` was using
`UUIDComponent.getUuid()` as the `npc_id` sent to the orchestrator - so the
personality/trust/memory the orchestrator was building up per NPC was being
silently thrown away and restarted from scratch on every single respawn.
Fixed by keying `npc_id` on `role.getRoleName()` instead (e.g. `"AI_Talker"`,
`"Elder_Miri"`) - stable across respawns, so a character's relationship with
a player actually persists now. Trade-off, accepted deliberately: two
simultaneously-spawned entities of the *same* role would share one
identity/conversation slot - fine as long as each named character exists as
a single instance in the world, which is the intended setup.

**Added two optional JSON fields on the `TalkToAI` action** so a role can
customize its AI identity without touching Java: `"DisplayName"` (the chat
name/tag shown in `[Name] ...` replies - falls back to the role's own name)
and `"AIRole"` (an occupation word like `"elder"`/`"merchant"`, sent to the
orchestrator as `npc_role` - it feeds `DEFAULT_BASELINES` in
`orchestrator/main.py` for a starting personality, and the `{role}` slot in
the system prompt template; falls back to `"villager"`). Read via the
engine's own `getString()` config-helper (same one
`BuilderSensorCanInteract` uses for its `ViewSector` field) rather than raw
Gson, so the field is registered as "known" and doesn't trip the framework's
"Unknown JSON attribute" boot warning.

**Two new NPCs**, both verbatim copies of `AI_Talker.json`'s proven
Instructions/MotionControllerList block (only `Appearance`,
`MemoriesCategory`, and the two new `TalkToAI` fields differ) - appearance
names pulled straight from the shipped `Assets.zip` to guarantee they're
real, valid models:
- `Elder_Miri.json` - `Appearance: "Kweebec_Sapling_Treesinger"`,
  `AIRole: "elder"` (calm, low-aggression baseline in `DEFAULT_BASELINES`).
- `Merchant_Oskar.json` - `Appearance: "Klops_Merchant"` (a different
  creature model, not another Kweebec), `AIRole: "merchant"` (warmer,
  business-savvy baseline).

Compiles clean, boots clean, all three roles (`AI_Talker`, `Elder_Miri`,
`Merchant_Oskar`) load with no validation warnings or errors - **not yet
live-tested that Elder Miri and Oskar actually talk in-character with their
own personalities**, that's the next thing to confirm.

## 🎉 2026-07-20: it works, confirmed live, end to end

A real player spawned `AI_Talker` in a real Hytale world, clicked it, and
each click produced a real round trip visible in the orchestrator's own
logs: Qdrant memory recall → `llm-inference` chat completion → episodic
memory write, repeating on every click. **The AI is genuinely generating
in-character replies from a real in-game interaction.** The reply is now
also sent back as an actual chat message to the player
(`PlayerRef.sendMessage(Message.raw(...))`), not just logged. (This part
took one more day and two more real bugs to actually deliver the reply -
see the 2026-07-21 sections below; that's the version to treat as done.)

Getting here took three real, live-tested fixes, in order:
1. `PlayerInteractEvent` (the original approach) turned out to never fire
   for NPC clicks at all — NPC interactions run through a JSON-driven
   `Interaction`/`Action` system instead (confirmed by reading the shipped
   `Kweebec_Merchant` role JSON: `"Sensor": {"Type": "HasInteracted"}` →
   `"Actions": [{"Type": "OpenBarterShop", ...}]`, no event involved). Fix:
   registered a custom `TalkToAI` action type via
   `NPCPlugin.get().registerCoreComponentType(...)`, the same call the
   built-in barter-shop action uses.
2. The shipped `AI_Talker.json` role failed spawn validation
   (`"failed to find npc role: AI_Talker"` in-game) because
   `TalkToAIActionBuilder` redundantly called `readCommonConfig()` - the
   real, shipped `BuilderActionOpenBarterShop.readConfig()` doesn't call it,
   so neither should ours. Removing that call fixed it.
3. The NPC got stuck animating in place - a whole instruction (clearing the
   wave animation after a delay) had been dropped while simplifying the
   role JSON from the original `Kweebec_Merchant.json`. Restored verbatim.

**2026-07-21 postscript:** the "stuck running in place" symptom above came
back on a later spawn, on a *different* world (`NPC_TESTS`), even with the
verbatim-restored `Instructions` block confirmed byte-identical in the
installed jar - real regression suspected. Investigated via the actual
Hytale server/client logs (`UserData/Saves/<world>/logs/*.log`,
`UserData/Logs/*_client.log`), not just the local `runServer` boot test:
no error or warning anywhere tied to the `AI_Talker` entity, and the one
animation-related log line found (`No animation with id Spawn on entity
with model ... Kweebec_Rootling.blockymodel`) turned out to be harmless,
pre-existing noise present in every session log back to 2026-07-20,
including the confirmed-working ones - so not the cause. Spawning in a
flat creative world instead confirmed the NPC is fine. **Root cause:
terrain under the spawn point in that particular world, not the plugin,
the role JSON, or the jar.** No code fix needed; noting here since the
symptom is easy to mistake for the JSON regression above.

## 2026-07-21: reply-not-arriving bug found and fixed (stale `PlayerRef`)

After the chat listener above was confirmed to correctly intercept and cancel
the player's message (no stray broadcast, no console log line — expected,
since cancellation suppresses both), the AI's reply still never showed up in
the player's chat, even though `docker compose logs orchestrator --timestamps`
confirmed the backend was completing real LLM calls every time. Both
`TalkToAIAction.execute()` and `PlayerChatToAIListener.handle()` captured a
`PlayerRef` once (at click/chat time) and reused that same object ~1-2
seconds later inside the async reply callback, once the LLM round trip
finished. That reference appears to go stale over that gap — `PlayerRef` has
an `isValid()` method, which only makes sense if instances can become
invalid — so calling `sendMessage()` on it silently did nothing.

Fix: both callbacks now store only the player's `UUID` and re-resolve a
fresh `PlayerRef` at the moment the reply actually arrives, via
`Universe.get().getPlayer(playerUuid)` (`Universe` is a singleton reachable
the same way as `NPCPlugin.get()`; `getPlayer(UUID)` does a live lookup). If
that returns `null` (player disconnected mid-conversation) the reply is
logged and dropped instead of crashing. Compiles and boots clean; **still
needs a real player to confirm the reply now actually shows up in chat** —
that's the very next thing to test.

## 2026-07-21, part 2: the actual reason no reply ever arrived

The `PlayerRef` fix above was real, but re-testing live still produced
nothing: clicking the NPC drove up orchestrator CPU (real LLM calls
happening) but no chat message ever appeared. The actual root cause was in
`NpcAiBridge.handleMessage()`'s hand-rolled JSON reader: `extract()` searched
for the literal substring `"npc_id":"` with no space between the colon and
the opening quote. `orchestrator/main.py` builds its `"say"` reply with
Python's `json.dumps()`, which inserts a space after every colon by
default - `"npc_id": "abc"`, not `"npc_id":"abc"`. That single space meant
`extract()` returned `null` for every real reply the orchestrator ever sent,
so `handleMessage()` hit its `if (npcId == null) return;` guard and the
registered callback was **never once invoked**, regardless of anything else
being correct. This explains the exact symptom reported twice: the backend
genuinely working (visible in orchestrator logs / CPU) while nothing ever
reached the player.

Fixed by making `extract()` tolerate (and skip) whitespace between the key,
the colon, and the opening quote of the value, instead of assuming compact
zero-space JSON. Rebuilt, boots clean, `com.orffyrus:NpcAiStack` still loads
and enables with no plugin-specific errors - **this is the next thing to
re-test live**, and this time it should actually work end to end.

## 🎉 2026-07-21: confirmed working end to end, for real this time

A real player clicked `AI_Talker`, got a real AI reply visibly tagged with
the NPC's name in chat (`[AI_Talker] ...`), and continuing the conversation
by just typing in normal chat worked too. Both bugs above (stale
`PlayerRef`, the JSON whitespace parser bug) are confirmed actually fixed
live, not just "compiles and boots clean." This is the version to treat as
the known-good baseline - see the git tag `hytale-plugin-e2e-working` on
this commit.

Two environment gotchas cost real debugging time getting here and are easy
to hit again, so they're called out explicitly:

- **The player must be in Adventure mode, not Creative.** Hytale has only
  two gamemodes - `Adventure` and `Creative` (there is no "Survival";
  `/gamemode survival` silently fails as an invalid command name and
  leaves you in whatever mode you were already in). The NPC interact
  prompt does not appear at all in Creative mode - `/gamemode adventure`
  before testing.
- **Every new world defaults to `NpcAiStack` disabled**, per Hytale's
  per-world mod settings - confirmed via
  `[PluginManager] Skipping mod com.orffyrus:NpcAiStack (Disabled by
  server config)` in that world's own server log
  (`UserData/Saves/<world>/logs/*.log`). This produces a
  `"failed to find npc role: AI_Talker"` error that looks like a spawn
  bug but isn't - enable the mod in that world's Mods settings first.

## Multi-turn conversation via chat

Clicking `AI_Talker` now also starts a tracked "conversation" for that
player (`NpcAiPlugin.ACTIVE_CONVERSATIONS`, keyed by `PlayerRef` UUID).
While a conversation is active, anything that player types in normal chat
gets intercepted by `PlayerChatToAIListener` (registered via
`getEventRegistry().registerAsyncGlobal(PlayerChatEvent.class, ...)` -
`PlayerChatEvent` is `IAsyncEvent`, a different registration shape than the
`Consumer`-based `registerGlobal` used elsewhere), cancelled so it never
hits normal server chat (`event.setCancelled(true)`), and forwarded as the
real typed text to the AI instead of the canned "the player interacts with
you" line used on first click. Replies are prefixed with the NPC's role
name (`Role.getRoleName()`, e.g. `[AI_Talker] ...`) for visual
identification. Saying "bye"/"goodbye"/"exit"/"leave"/"stop" ends the
conversation locally without an LLM call; conversations also expire after
5 minutes of inactivity. **Confirmed live 2026-07-21** - see above.

The cancel-suppresses-broadcast contract was confirmed by disassembling
`GamePacketHandler` (which dispatches `PlayerChatEvent` and checks
`isCancelled()` before broadcasting to `event.getTargets()`) - but there
was no existing shipped example of a *listener* for this event to copy
from (unlike `TalkToAI`, where `ActionOpenBarterShop` was a perfect
reference).

`PlayerInteractEvent` is still registered in `NpcAiPlugin` but is **not**
the NPC-talk mechanism - kept only in case it's useful for something else.

## What's real vs. what's still unverified

| Piece | Status |
|---|---|
| `manifest.json`, plugin lifecycle, `EventRegistry.registerGlobal` | **Confirmed** — real server boot |
| `PlayerInteractEvent` fires for NPC clicks | **Confirmed false** — not the mechanism, see above |
| `NPCPlugin.get().registerCoreComponentType("TalkToAI", ...)` | **Confirmed** — action fires for real |
| Custom asset packs (`AI_Talker.json`, `Elder_Miri.json`, `Merchant_Oskar.json`) ship, load, and spawn | `AI_Talker` **confirmed live**; the two new characters **compile/boot clean, not yet live-spawned** |
| Stable per-character `npc_id` (role name, not spawned entity UUID) | Fixed 2026-07-21, **not yet live-verified that trust/memory actually survives a respawn** - the bug it fixes (identity resetting every respawn) was real but silent, so there's no in-game symptom to re-check other than confirming the fix doesn't regress anything |
| Clicking `AI_Talker` triggers a real dialogue request | **Confirmed live** — repeated orchestrator round trips (Qdrant recall → LLM call → memory write) on each click |
| Reply reaches the player as a chat message | **Confirmed live** — two real bugs found and fixed along the way: stale captured `PlayerRef` (fixed via `Universe.get().getPlayer(uuid)`) and `NpcAiBridge.extract()` requiring zero-space JSON when Python's `json.dumps()` emits a space (fixed to tolerate whitespace) |
| Player lookup in `TalkToAIAction.execute()` | **Confirmed live** — the whole chain depends on it and it works |
| Thread-hop before `sendMessage()` in the reply callback | Reasoned-not-verified: `PlayerRef.sendMessage()` is used elsewhere from async command handlers in this codebase, so it's likely cross-thread-safe, but this is inference, not a confirmed guarantee. No entity/world-state mutation happens in the callback (kept deliberately narrow because of this). Not observed to be an issue across live testing so far. |
| Multi-turn conversation (`PlayerChatEvent`) | **Confirmed live** — typing after the first click continues the conversation with real text, visually tagged replies |

## How to actually test this (needs a real client - can't be done from here)

1. `docker compose up -d --build` at the repo root.
2. Build + install:
   ```bash
   ./gradlew build
   cp build/libs/NpcAiStack-0.1.0.jar "$APPDATA/Hytale/UserData/Mods/"
   ```
3. In-game: enable `NpcAiStack` in your world's mod settings (**every new
   world defaults to it disabled** - this tripped up testing once already;
   check `[PluginManager] Skipping mod com.orffyrus:NpcAiStack (Disabled by
   server config)` in the log if spawning fails with "failed to find npc
   role").
4. `/op self` if you haven't already, and `/gamemode adventure` - the NPC
   interact prompt does not appear in Creative mode at all.
5. `/npc clean` then `/npc spawn AI_Talker` (or `Elder_Miri` /
   `Merchant_Oskar` - three talkable characters now, see below).
6. Interact with it (click) - a reply should now appear as a chat message
   tagged with that character's name (`[Elder Miri] ...`), and typing
   afterward in normal chat should continue the conversation. Watching
   `docker compose logs orchestrator -f` at the same time confirms the
   backend side regardless of what shows up in-game.

## A real bug found and fixed by actually running this (still applies)

The official template's own `runServerJar` Gradle task passes the shadow
jar's file path directly as a `--mods` argument. On this server build that
throws `ValueConversionException: Path must be a directory!` — `--mods`
entries have to be directories. Separately, the server *always* auto-scans a
working-directory-relative `mods/` folder even with no `--mods` flag at all,
so also passing `--mods=<that same dir>` explicitly causes
`Tried to load duplicate plugin`. `build.gradle` here copies the shadow jar
into `run/mods/` and only passes `--mods` when `load_user_mods` pulls in a
*different* directory (the real `UserData/Mods`) — see the comments in the
`runServerJar` task.

Also caught: the shadow jar's `include` filter needs `Server/**` in
addition to `com/orffyrus/**` and `manifest.json`, or the shipped asset
pack (`AI_Talker.json`) silently gets left out of the built jar entirely.

## Build

```bash
./gradlew build
```

Needs a JDK 25 (this repo was verified with Eclipse Temurin 25.0.3) - see
the [official template](https://github.com/realBritakee/hytale-template-plugin)
this was adapted from for full IDE setup (IntelliJ IDEA recommended).

`gradle.properties` already points `hytale_home` auto-detection at the
standard install path, and `hytale_build=0.5.7` matches this machine's
installed server as of 2026-07-20 - update it if you're on a different
build (a mismatch fails the Maven dependency resolution loudly, not
silently). `includes_pack=true` since this now ships `AI_Talker.json`.

## Run a local test server (no real Hytale client attached yet)

```bash
./gradlew runServer
```

Builds, copies the jar into `run/mods/`, and launches `HytaleServer.jar`
with `--allow-op --disable-sentry`. Confirms the plugin/asset pack load and
the server boots - it can NOT confirm the actual spawn/interact/dialogue
flow, since that needs a connected player (`/npc spawn` requires
`AbstractPlayerCommand` context, which the console alone can't provide).
Ctrl+C to stop it; `run/` is gitignored.

## Next steps, in order

1. ~~Get a JDK 25 + this compiling.~~ **Done.**
2. ~~Confirm the plugin actually loads in a real server.~~ **Done.**
3. ~~Find the real NPC-interaction mechanism.~~ **Done** - `TalkToAI` action
   + `AI_Talker` role, live-confirmed working end to end.
4. ~~Confirm a real click reaches the orchestrator.~~ **Done, live** -
   repeated real dialogue round trips confirmed in the orchestrator's logs.
5. ~~Wire `PlayerChatEvent` for real multi-turn conversation.~~ **Done,
   confirmed live.**
6. ~~Confirm live: does the reply show up as a chat message?~~ **Done,
   confirmed live** - two real bugs found and fixed along the way (stale
   `PlayerRef`, JSON whitespace parsing), see above.
7. ~~Confirm live: does typing after the first click continue the
   conversation with real text?~~ **Done, confirmed live 2026-07-21.**
   Tagged `hytale-plugin-e2e-working` as the known-good baseline.
8. Confirm the thread-hop question properly (or find a case where it
   actually matters - neither reply callback touches entity/world state
   today, only sends a chat message; no issue observed in live testing
   so far, but this is inference, not a confirmed guarantee).
9. ~~Give the world an actual cast instead of one demo NPC.~~ **Done,
   compiles and boots clean** - fixed `npc_id` to be stable per character
   (was silently the spawned entity's UUID, resetting memory/trust on
   every respawn), added `DisplayName`/`AIRole` config to `TalkToAI`, and
   shipped two new characters (`Elder_Miri`, `Merchant_Oskar`) with their
   own personality baselines.
10. **You are here** - spawn `Elder_Miri` and `Merchant_Oskar` live and
    confirm each actually talks in character (distinct tone/personality
    from `AI_Talker`), and that talking to the same character again after
    a respawn/relog remembers the relationship instead of starting fresh.
11. Sustained/repeat testing: does the conversation survive multiple
    NPCs at once, a player disconnecting mid-conversation (the
    `Universe.getPlayer()` null-check path), or the 5-minute conversation
    timeout actually firing?
12. `skill_writer.py` against the real GPU/model (only verified so far
    with a fake LLM/DB) - see the root `CLAUDE.md` "Agreed next steps".
13. Load-test llama.cpp slots (`--parallel`/ctx tradeoff) with multiple
    concurrent NPC conversations for real, not just the fake-player test
    client.

🐄 **Falling Cow Zone** — see the [repo root README](../README.md).

**Update 2026-07-20, third pass:** live play testing (`Feran_Civilian`,
`Kweebec_Merchant` spawned, interacted with) showed **zero dialogue requests
ever reached the orchestrator**, despite the plugin loading fine and
`PlayerInteractEvent` compiling cleanly. Digging into the real server jar
explains why: **NPC interactions in this build don't fire a generic Java
event at all.** They run through a JSON-driven `Interaction`/`Action` system
— confirmed by reading the shipped `Kweebec_Merchant` role JSON directly,
which handles "player interacted" via `"Sensor": {"Type": "HasInteracted"}`
→ `"Actions": [{"Type": "OpenBarterShop", ...}]`, entirely inside data, no
event listener involved anywhere.

So this plugin now registers its **own** custom action type (`TalkToAI`),
following the exact pattern the built-in `OpenBarterShop` action uses
(disassembled `NPCShopPlugin`/`ActionOpenBarterShop` as the reference), and
ships a custom NPC role (`AI_Talker`) that uses it. `PlayerInteractEvent`
is still registered in `NpcAiPlugin` but is **not** the NPC-talk mechanism —
kept only in case it's useful for something else later.

## What's real vs. what's still unverified

| Piece | Status |
|---|---|
| `manifest.json`, plugin lifecycle, `EventRegistry.registerGlobal` | **Confirmed** — real server boot, prior pass |
| `PlayerInteractEvent` fires for NPC clicks | **Confirmed false** — real play session, zero dialogue requests despite real interactions |
| `NPCPlugin.get().registerCoreComponentType("TalkToAI", ...)` | **Confirmed** — log: `Registered TalkToAI NPC action type`, no error |
| Custom asset pack (`AI_Talker.json`) ships and loads | **Confirmed** — log: `Loaded pack: com.orffyrus:NpcAiStack`, role counted in `Loaded 973 NPC configurations ... Generic: 1` |
| `AI_Talker.json` validation | **Two SEVERE `FAIL: ... Once` / `FAIL: ... Enabled` lines** at boot - no stack trace, role still counts as loaded and plugin still enables. Likely a non-fatal descriptor/validation quirk in `TalkToAIActionBuilder`, but **not confirmed harmless** - could still block the action from firing for a real player. This is the biggest open question. |
| Player lookup in `TalkToAIAction.execute()` (`Role.getStateSupport().getInteractionIterationTarget()` → `PlayerRef`/`Player` components) | Compiles; copied line-for-line from disassembled `ActionOpenBarterShop.execute()`, but **never exercised against a real interacting player** |
| Thread-hop before the bridge's reply callback touches world state | **Still not verified** - unchanged from before |
| Multi-turn conversation (`PlayerChatEvent`) | **Still not implemented** - unchanged from before |

## How to actually test this (needs a real client - can't be done from here)

1. `docker compose up -d --build` at the repo root.
2. Build + install:
   ```bash
   ./gradlew build
   cp build/libs/NpcAiStack-0.1.0.jar "$APPDATA/Hytale/UserData/Mods/"
   ```
3. In-game: enable `NpcAiStack` in your world's mod settings, load the world.
4. `/op self` if you haven't already.
5. `/npc clean` then `/npc spawn AI_Talker` (a `Kweebec_Rootling`-appearance
   NPC that waves when you approach - same idle behavior as the built-in
   merchants, but its interaction triggers our AI instead of a shop).
6. Interact with it (click), watching `docker compose logs orchestrator -f`
   at the same time.
7. Report back exactly what happens - a dialogue request reaching the
   orchestrator confirms the whole chain works; nothing arriving points at
   the `Once`/`Enabled` validation warning actually being fatal, in which
   case `TalkToAIActionBuilder.readCommonConfig()` needs another look.

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
3. ~~Find the real NPC-interaction mechanism.~~ **Done** - it's the
   `Interaction`/`Action` JSON system, not events; `TalkToAI` action +
   `AI_Talker` role built and shipped.
4. **You are here** - spawn `AI_Talker` in a real client and interact with
   it; report whether the orchestrator receives anything.
5. If it does: confirm the thread-hop for touching world state, then wire
   `PlayerChatEvent` for real multi-turn conversation.
6. If it doesn't: resolve the `Once`/`Enabled` validation FAIL - likely
   means something about `TalkToAIActionBuilder`'s config handling is
   subtly wrong despite compiling and "loading" successfully.

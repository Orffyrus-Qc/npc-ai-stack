🐄 **Falling Cow Zone** — see the [repo root README](../README.md).

## 🎉 2026-07-20: it works, confirmed live, end to end

A real player spawned `AI_Talker` in a real Hytale world, clicked it, and
each click produced a real round trip visible in the orchestrator's own
logs: Qdrant memory recall → `llm-inference` chat completion → episodic
memory write, repeating on every click. **The AI is genuinely generating
in-character replies from a real in-game interaction.** The reply is now
also sent back as an actual chat message to the player
(`PlayerRef.sendMessage(Message.raw(...))`), not just logged.

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

## Multi-turn conversation via chat (new, NOT yet live-verified)

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
5 minutes of inactivity.

The cancel-suppresses-broadcast contract was confirmed by disassembling
`GamePacketHandler` (which dispatches `PlayerChatEvent` and checks
`isCancelled()` before broadcasting to `event.getTargets()`) - but there
was no existing shipped example of a *listener* for this event to copy
from (unlike `TalkToAI`, where `ActionOpenBarterShop` was a perfect
reference). It compiles clean and boots clean
(`Registered PlayerChatEvent -> AI conversation listener`, no error), but
**has not yet been exercised by a real player typing in chat** - that's
the next thing to confirm live.

`PlayerInteractEvent` is still registered in `NpcAiPlugin` but is **not**
the NPC-talk mechanism - kept only in case it's useful for something else.

## What's real vs. what's still unverified

| Piece | Status |
|---|---|
| `manifest.json`, plugin lifecycle, `EventRegistry.registerGlobal` | **Confirmed** — real server boot |
| `PlayerInteractEvent` fires for NPC clicks | **Confirmed false** — not the mechanism, see above |
| `NPCPlugin.get().registerCoreComponentType("TalkToAI", ...)` | **Confirmed** — action fires for real |
| Custom asset pack (`AI_Talker.json`) ships, loads, and spawns | **Confirmed live** — `/npc spawn AI_Talker` works in a real world |
| Clicking `AI_Talker` triggers a real dialogue request | **Confirmed live** — repeated orchestrator round trips (Qdrant recall → LLM call → memory write) on each click |
| Reply reaches the player as a chat message | **Just added, NOT yet live-verified** - `PlayerRef.sendMessage()` compiles and boots clean, but no player has confirmed seeing the message yet |
| Player lookup in `TalkToAIAction.execute()` | **Confirmed live** — the whole chain depends on it and it works |
| Thread-hop before `sendMessage()` in the reply callback | Reasoned-not-verified: `PlayerRef.sendMessage()` is used elsewhere from async command handlers in this codebase, so it's likely cross-thread-safe, but this is inference, not a confirmed guarantee. No entity/world-state mutation happens in the callback (kept deliberately narrow because of this). |
| Multi-turn conversation (`PlayerChatEvent`) | **Still not implemented** |

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
4. `/op self` if you haven't already.
5. `/npc clean` then `/npc spawn AI_Talker`.
6. Interact with it (click) - a reply should now appear as a chat message.
   Watching `docker compose logs orchestrator -f` at the same time confirms
   the backend side regardless of what shows up in-game.

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
   compiles and boots clean** - `PlayerChatToAIListener` + `ACTIVE_CONVERSATIONS`.
6. **You are here** - confirm live: does the reply show up as a chat
   message, and does typing after the first click actually continue the
   conversation with real text (not just the canned opener)?
7. Confirm the thread-hop question properly (or find a case where it
   actually matters - neither reply callback touches entity/world state
   today, only sends a chat message).

🐄 **Falling Cow Zone** — see the [repo root README](../README.md). Everything
in this directory was written 2026-07-20 against the real class names/method
signatures found by inspecting an actual installed `HytaleServer.jar`
(v0.5.7) — not guessed, not scraped from docs alone. But it has **never been
compiled or run** (no JDK 25 was available in the environment this was
written in), so treat it as a strong first draft, not working code.

## What's real vs. what's a placeholder

| Piece | Status |
|---|---|
| `manifest.json` fields | Verified against the real template + this build |
| `NpcAiPlugin extends JavaPlugin`, `setup()` | Verified against the real template's `ExamplePlugin.java` |
| `getEventRegistry().registerGlobal(...)` | Verified real method (`IEventRegistry.registerGlobal`) |
| `PlayerInteractEvent` fields/getters | Verified real (`getActionType`, `getTargetEntity`, `getPlayer`) - **but the class is marked `@Deprecated`** in this build, no replacement documented anywhere found |
| `INonPlayerCharacter` as the NPC check | Verified real, not deprecated. (`NPCMarkerComponent` was the more "obvious" choice but is `@Deprecated(forRemoval=true)`) |
| Thread-hop before touching world state in the bridge's reply callback | **Not verified.** `TaskRegistry` looks like task lifecycle bookkeeping, not a scheduler - see the TODO in `NpcInteractListener.java` |
| Multi-turn conversation (player types a follow-up) | **Not implemented.** Would need `PlayerChatEvent`, which is `IAsyncEvent`-based (a different registration shape) - flagged as a follow-up, not guessed at |

## Build

Needs a JDK 25 and (for full IDE run-configs) IntelliJ IDEA - see the
[official template](https://github.com/realBritakee/hytale-template-plugin)
this was adapted from.

```bash
./gradlew build
```

`gradle.properties` already points `hytale_home` auto-detection at the
standard install path, and `hytale_build=0.5.7` matches this machine's
installed server as of 2026-07-20 - update it if you're on a different
build (a mismatch fails the Maven dependency resolution loudly, not
silently).

## Install (singleplayer/local testing)

```bash
./gradlew build
cp build/libs/NpcAiStack-0.1.0.jar "$APPDATA/Hytale/UserData/Mods/"
```

Then in-game: create/open a world, go to its mod settings, enable
`NpcAiStack`, apply, and the plugin loads on next start. The AI stack
itself (`docker compose up -d --build` at the repo root) needs to already
be running and reachable at `ws://localhost:8765` - see the root README's
Bring-up section.

## Next steps, in order

1. Get a JDK 25 + this compiling - that alone will catch anything wrong in
   the guesses above (Gradle will fail loudly on any class/method that
   doesn't actually exist).
2. Confirm the thread-hop mechanism for touching entity/world state from
   the bridge's WebSocket-thread callback.
3. Wire `PlayerChatEvent` for real multi-turn conversation once you've
   confirmed the `IAsyncEvent` registration contract.
4. Test in a real singleplayer world against an actual NPC.

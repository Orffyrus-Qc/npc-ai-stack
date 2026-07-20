🐄 **Falling Cow Zone** — see the [repo root README](../README.md).

**Update 2026-07-20, same day, later:** a JDK 25 was installed and this was
actually built and run. `./gradlew build` succeeds, and `./gradlew runServer`
boots a **real local Hytale server (v0.5.7) with this plugin loaded, set up,
and enabled** all the way through to `Hytale Server Booted! [Multiplayer,
Fresh Universe]` — no crash, no plugin-related errors. That's real
verification, not just bytecode-inspection-grounded guessing. What's *still*
not tested: an actual player connecting and clicking an NPC (no client was
attached during this run), and everything listed under "still placeholder"
below.

## What's real vs. what's still a placeholder

| Piece | Status |
|---|---|
| `manifest.json` fields | **Confirmed** — server parsed and loaded it (`com.orffyrus:NpcAiStack`) |
| Compiles against the real API | **Confirmed** — `./gradlew build` succeeds clean (2 minor deprecation warnings, not errors) |
| `NpcAiPlugin extends JavaPlugin`, `setup()` lifecycle | **Confirmed** — log shows `Loaded`, then `Enabled plugin com.orffyrus:NpcAiStack` |
| `getEventRegistry().registerGlobal(...)` | **Confirmed compiles**; not yet confirmed to actually fire (no player interacted with an NPC in this run) |
| `PlayerInteractEvent` fields/getters | Compiles and is present - **but the class itself is `@Deprecated`** in this build, no replacement documented anywhere found |
| `INonPlayerCharacter` as the NPC check | Compiles; not yet exercised against a real NPC entity |
| Thread-hop before touching world state in the bridge's reply callback | **Still not verified.** `TaskRegistry` looks like task lifecycle bookkeeping, not a scheduler - see the TODO in `NpcInteractListener.java` |
| Multi-turn conversation (player types a follow-up) | **Still not implemented.** Would need `PlayerChatEvent`, which is `IAsyncEvent`-based (a different registration shape) - flagged as a follow-up, not guessed at |

## A real bug found and fixed by actually running this

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
silently).

## Run a local test server (no real Hytale client attached yet)

```bash
./gradlew runServer
```

This is exactly what was used to confirm the boot above. It builds, copies
the jar into `run/mods/`, and launches `HytaleServer.jar` with
`--allow-op --disable-sentry`. Ctrl+C to stop it; `run/` is gitignored.

## Install (singleplayer/local testing, for real this time)

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

1. ~~Get a JDK 25 + this compiling.~~ **Done.**
2. ~~Confirm the plugin actually loads in a real server.~~ **Done** —
   booted a full local server with it enabled.
3. Connect an actual Hytale client, spawn/find an NPC, and click it to
   confirm `PlayerInteractEvent` actually fires and reaches
   `NpcInteractListener` the way the code assumes.
4. Confirm the thread-hop mechanism for touching entity/world state from
   the bridge's WebSocket-thread callback.
5. Wire `PlayerChatEvent` for real multi-turn conversation once you've
   confirmed the `IAsyncEvent` registration contract.

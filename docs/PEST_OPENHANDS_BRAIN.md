# Pest — a companion whose brain is real OpenHands

**Status:** scaffolded 2026-07-22, alongside Mori's own uncommitted work
**Dependency:** [`openhands-sdk`](https://github.com/OpenHands/software-agent-sdk) (PyPI `openhands-sdk`, MIT, requires Python ≥3.12)
**Relation to Mori:** independent companion, not a rename/replacement - see
`CLAUDE.md`'s dated entry for why. Mori's `agent_brain/` package (a lighter,
hand-rolled "OpenHands-style" clone, see `docs/OPENHANDS_NPC_BRAIN.md`) is
untouched by this work.

---

## Why a real dependency this time

`docs/OPENHANDS_NPC_BRAIN.md` (Mori's design) deliberately avoided a literal
`openhands-sdk` dependency in the hot path - reasoned as "too heavy, wrong
sandbox" for a real-time game loop. That reasoning is still correct for
Mori. It does not, however, apply to a companion whose entire premise is
"this one's brain literally runs on OpenHands" - Pest exists specifically
to be that, so the tradeoff is accepted deliberately here, not overlooked.

Confirmed via actually installing `openhands-sdk` into `python:3.12-slim`
(this project's own orchestrator base image) and inspecting the real
package - not trusted from documentation alone, same discipline this
project applies to Hytale's own engine via `javap` disassembly:

- `openhands.sdk.LLM(model=, api_key=, base_url=)` accepts any
  OpenAI-compatible endpoint - pointed at the SAME `llm-inference` service
  (`http://llm-inference:8080/v1`) every other NPC's dialogue already uses.
  No new model, no cloud dependency.
- `Agent`/`Conversation`/`Tool` embed directly in a Python process - no
  Docker, no web frontend required for the SDK itself.
- Custom tools are real `ToolDefinition` subclasses (Action/Observation
  pydantic pairs + a `ToolExecutor`), registered process-wide via
  `register_tool()` and referenced per-`Agent` via `Tool(name=...)`.

## Two separate surfaces, two very different risk profiles

| | Live chat (`pest_brain/`) | Self-evolution (`pest_evolve.py`) |
|---|---|---|
| Runs | Embedded in the live orchestrator, every Pest dialogue turn | Separate, offline, profile-gated container (`docker compose run --rm pest-evolve`) |
| Tools | Read-only only: game files, map/world, wiki search, `propose_play_action` (decision-only) | Real `terminal` + `file_editor` (openhands-tools), jailed to `sandbox/pest_workspace/` |
| Concurrency/GPU | 1 concurrent turn max, own dedicated llama.cpp slot (`--parallel` 6→7) | Bypasses the dispatcher entirely, same caveat as `skill_writer.py` - never run while players are online |
| Output | A spoken line + optional play proposal | A candidate skill file, gated by the **unchanged** `sandbox/run_skill_validation.sh` → `skill_harness.py` pipeline (hard rule 4) |

Giving the always-on orchestrator process a raw shell tool would break the
project's own hard rule 4 (skill code changes always go through the
sandbox gate) and its "never import/exec untrusted generated code" stance -
so the real Bash/FileEditor tools only ever exist inside the separate,
manually-invoked `pest-evolve` service, never the live chat path. This
mirrors exactly why `skill_writer.py` is already a separate service instead
of part of the live gateway.

## "Please restart" is real

A newly-approved `approved/pest_*.py` skill is stamped by mtime; a fresh
orchestrator process (`skill_runtime.py`'s `_process_started_at`) only
activates Pest skills approved **before** it started - so a skill promoted
mid-session genuinely does nothing until the next real restart. Pest's
in-game "I've learned something new, please restart" message
(`pest_evolve.py` → `sandbox/pest_notices.jsonl` → `pest_notice_daemon()` in
`main.py` → `evolve_notice` WebSocket message → `NpcAiBridge.NoticeHandler`
→ a normal chat line) is asking for something actually required, not
flavor text.

## GPU/latency honesty

Pest's replies can take noticeably longer than Mori's - a real multi-step
tool-calling agent turn is several sequential LLM calls, not one. The
"thinking" particle (`Pest.json`'s `IsAwaitingReply` node, same mechanism
Mori already uses) is what keeps this from reading as a hang;
`PEST_BRAIN_TURN_TIMEOUT_S` (default 45s) is the hard ceiling before Pest
just says nothing that turn, same honest-silence convention every other
NPC's dialogue already follows (no pre-written fallback lines anywhere in
this stack).

## Not yet live-confirmed

Same honesty convention as every other feature in this project - see
`CLAUDE.md`'s dated log. As of this write-up:

- `openhands-sdk`'s core API (`LLM`/`Agent`/`Conversation`/`Tool`/
  `ToolDefinition`/`register_tool`) was verified by direct package
  inspection inside `python:3.12-slim`, this project's own base image.
- `openhands-tools`' real Bash/FileEditor tool names were ALSO directly
  confirmed the same way, not guessed: `openhands-sdk==1.17.0` +
  `openhands-tools==1.17.0` installed and imported together cleanly, and
  `list_registered_tools()` actually returned `['delegate', 'file_editor',
  'task_tool_set', 'task', 'task_tracker', 'terminal']` -
  `pest_evolve.py` uses `"terminal"`/`"file_editor"`, NOT the more
  commonly-assumed `"execute_bash"`/`"str_replace_editor"` names from the
  wider OpenHands/CodeAct ecosystem (that first guess was wrong and was
  caught by actually running the install, not left in). A real,
  live-confirmed version-pairing gotcha was also found and fixed this way:
  installing `openhands-tools` without an exact matching pin let pip
  resolve a too-new `openhands-sdk` (`ModuleNotFoundError:
  openhands.sdk.utils.path`) - both are exact-pinned in
  `requirements.txt` because of this, not out of general caution.
- No real in-game session has talked to Pest yet. Needs: `./gradlew build`/
  `runServer` boot test, `docker compose up -d --build orchestrator
  llm-inference` with `--parallel 7`, then a real player saying "Pest,
  hello" and confirming a grounded reply (ideally one that visibly used a
  tool, e.g. asking about a real recipe).

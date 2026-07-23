"""
Pest's self-evolution meta-agent - the "rewrite its own code to evolve"
half of the request (docs/PEST_OPENHANDS_BRAIN.md).

Offline, standalone script - deliberately NOT part of the live WebSocket
gateway, same shape as skill_writer.py (CLAUDE.md hard rule 4: skill code
changes always go through the sandbox gate; this file's whole existence is
about keeping a REAL Bash/FileEditor-capable agent away from the always-on
orchestrator process - see the module docstring in pest_brain/session.py
for the live chat path's read-only-tools-only counterpart).

Unlike skill_writer.py (one LLM completion, statically shape-checked),
this runs a REAL openhands-sdk Agent with real Terminal + FileEditor tools,
jailed to a throwaway workspace directory
(PEST_EVOLVE_WORKSPACE, default sandbox/pest_workspace/) that is NEVER the
live Hytale install and NEVER sandbox/approved/ directly. Whatever the
agent produces is copied into sandbox/candidates/ and still has to clear
the UNCHANGED sandbox/run_skill_validation.sh -> skill_harness.py gate
(the exact same promotion path every other candidate skill, from any NPC,
already goes through) before anything reaches approved/ - real Bash/file
access during drafting does not grant any special path around that gate.

Run via `docker compose run --rm pest-evolve [--dry-run]` (see
docker-compose.yml) - never automatically, never while players are online
(same GPU-contention caveat skill_writer.py already documents: this calls
llm-inference directly, bypassing NPCRequestDispatcher's dialogue-priority
arbitration).

On a successful promotion, appends one line to PEST_NOTICES_PATH
(sandbox/pest_notices.jsonl) for every player who currently owns a tamed
Pest (taming.py) - the live orchestrator's pest_notice_daemon() delivers
these as an in-game "please restart" chat line the next time it polls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from skill_writer import _load_harness_constants, _static_check  # noqa: E402
from taming import TamingStore  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("npc.pest_evolve")

NPC_ID = "Pest"
GEN_MAX_STEPS = 12
VALIDATION_TIMEOUT_S = 60


def _contract_readme(allowed_actions: set, max_say_len: int) -> str:
    actions_list = ", ".join(sorted(allowed_actions))
    return f"""# Pest skill contract

You are editing/writing `pest_skill.py` in this workspace: a single Python
function, `def decide(state: dict) -> dict`, that becomes one of Pest's
approved ambient/idle decisions once it clears validation (same mechanism
every NPC's approved/ skills already use - see orchestrator/skill_runtime.py
and sandbox/skill_harness.py, which are the actual, unmodifiable rules -
this file is a plain-language summary, not a substitute for reading them).

Rules (enforced by an automated harness afterward, not optional):
- Exactly one top-level function: `def decide(state: dict) -> dict`.
- `state` ONLY ever has these keys: event (str), player_id (str|None),
  npc_hp (int), time_of_day (str, free text - never compare with < > <= >=),
  nearby_players (int). Never assume any other key exists.
- Never raise on unexpected/missing/malformed values.
- Return a dict with "action" one of exactly: {actions_list}.
- If action is "say", include "text" (1-{max_say_len} chars).
- If action is "give_item", include "item_id" (str) and "count" (1-8 int).
- The returned dict must be JSON-serializable (plain strings/numbers/bools).
- No imports, no I/O, no network, no randomness that could throw.

You have a real terminal and file editor in this workspace ONLY - nothing
outside it is writable or relevant. Read `pest_skill.py` (your current
skill, may be a trivial placeholder), improve it, and leave the final
version saved at `pest_skill.py`. You may run `python -c "..."` to sanity-
test your own function against example state dicts before finishing - the
real validation gate runs separately, afterward, outside your control.
"""


def _prepare_workspace(workspace: Path, allowed_actions: set, max_say_len: int) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    readme = workspace / "CONTRACT.md"
    readme.write_text(_contract_readme(allowed_actions, max_say_len))
    skill_path = workspace / "pest_skill.py"
    if not skill_path.is_file():
        skill_path.write_text(
            '"""Pest\'s current ambient decision skill - starting point."""\n\n'
            "def decide(state: dict) -> dict:\n"
            "    return {\"action\": \"idle\"}\n"
        )


def _run_agent(workspace: Path) -> str | None:
    """
    Runs a real openhands-sdk Agent with Terminal+FileEditor tools jailed to
    `workspace`, asks it to improve pest_skill.py per CONTRACT.md, and
    returns the final file's contents (or None on any failure - never
    raises out to the caller, matching this project's "a broken meta-agent
    run degrades to 'no candidate this time', never a crash" convention,
    same as skill_writer.py's own per-npc try/except).

    Tool names ("terminal"/"file_editor") come from openhands-tools (the
    separate package providing real Bash/FileEditor executors) - confirmed
    real by installing openhands-sdk==1.17.0 + openhands-tools==1.17.0
    together into python:3.12-slim and calling
    openhands.sdk.list_registered_tools() directly (importing
    openhands.tools registers them as a side effect): actually returned
    ['delegate', 'file_editor', 'task_tool_set', 'task', 'task_tracker',
    'terminal'] - NOT the more commonly-guessed "execute_bash"/
    "str_replace_editor" names from the wider OpenHands/CodeAct ecosystem.
    "finish" is NOT in that list - it's a default tool Agent already
    includes automatically (include_default_tools=True, the default),
    not part of the named registry, so it's deliberately not referenced
    in tools= below.
    """
    from openhands.sdk import LLM, Agent, Conversation
    from openhands.sdk.conversation import get_agent_final_response
    from pydantic import SecretStr

    import openhands.tools  # noqa: F401  (registers terminal/file_editor)
    from openhands.sdk import Tool

    llm = LLM(
        # "openai/" provider prefix required - see pest_brain/llm.py's
        # build_llm() for the real, live-confirmed litellm gotcha this
        # fixes (a bare model name made litellm raise "LLM Provider NOT
        # provided" against the very first real Pest turn this deployment
        # ever ran).
        model=f"openai/{os.environ.get('PEST_LLM_MODEL', 'qwen2.5-7b-instruct')}",
        api_key=SecretStr("local"),
        base_url=os.environ.get("PEST_LLM_BASE_URL", "http://llm-inference:8080/v1"),
        temperature=0.4,
    )
    agent = Agent(
        llm=llm,
        tools=[
            Tool(name="terminal"),
            Tool(name="file_editor"),
        ],
    )
    conversation = Conversation(
        agent=agent,
        workspace=str(workspace),
        max_iteration_per_run=GEN_MAX_STEPS,
        stuck_detection=True,
    )
    conversation.send_message(
        "Read CONTRACT.md, then improve pest_skill.py to better fit that "
        "contract (fix bugs, cover more of the real state keys sensibly, "
        "keep it simple and safe). Save your final version to pest_skill.py "
        "and call finish when done."
    )
    conversation.run()
    get_agent_final_response(conversation.state.events)  # logged for humans, not parsed

    skill_path = workspace / "pest_skill.py"
    if not skill_path.is_file():
        return None
    return skill_path.read_text()


def _validate_candidate(sandbox_dir: Path, candidate_path: Path) -> bool:
    """
    Runs the EXISTING, unmodified sandbox gate - same call
    docker-compose.yml documents for manual/cron use. Note this script
    validates every *.py currently in candidates/ (its own long-standing
    batch design, not something this file changes) - so success is
    confirmed by checking specifically for OUR file's approved copy
    (run_skill_validation.sh renames to "${ts}_${name}" on PASS), not by
    scraping stdout, which could contain PASS/FAIL lines for unrelated
    candidates dropped by skill_writer.py in the same window.
    """
    script = sandbox_dir / "run_skill_validation.sh"
    approved_dir = sandbox_dir / "approved"
    result = subprocess.run(
        ["bash", str(script), str(candidate_path.parent),
         str(approved_dir), str(sandbox_dir / "rejected")],
        capture_output=True, text=True, timeout=VALIDATION_TIMEOUT_S,
    )
    logger.info("run_skill_validation.sh output:\n%s", result.stdout)
    if result.returncode != 0:
        logger.warning("run_skill_validation.sh exited %s:\n%s",
                        result.returncode, result.stderr)
    return any(approved_dir.glob(f"*_{candidate_path.name}"))


async def _notify_owners(sandbox_dir: Path, text: str) -> None:
    taming = TamingStore()
    await taming.start()
    try:
        owner = await taming.get_owner(NPC_ID)
    except Exception:
        logger.exception("could not resolve Pest's owner for evolve_notice")
        return
    if not owner:
        logger.info("Pest has no current owner - evolve_notice not written (nobody to tell yet)")
        return
    notices_path = sandbox_dir / "pest_notices.jsonl"
    with notices_path.open("a") as f:
        f.write(json.dumps({
            "npc_id": NPC_ID, "player_id": owner, "text": text, "ts": time.time(),
        }) + "\n")
    logger.info("wrote evolve_notice for owner=%s", owner)


def run(sandbox_dir: Path, dry_run: bool) -> int:
    workspace = Path(os.environ.get("PEST_EVOLVE_WORKSPACE", str(sandbox_dir / "pest_workspace")))
    allowed_actions, max_say_len = _load_harness_constants(sandbox_dir)
    _prepare_workspace(workspace, allowed_actions, max_say_len)

    try:
        code = _run_agent(workspace)
    except Exception:
        logger.exception("pest_evolve agent run failed")
        return 1
    if not code:
        logger.warning("agent produced no pest_skill.py - nothing to validate")
        return 1

    # Same static shape check skill_writer.py already applies before ever
    # writing a candidate - real Bash/FileEditor access during drafting
    # doesn't exempt Pest's output from the same first-pass sanity gate
    # every other NPC's candidates go through.
    problem = _static_check(code)
    if problem:
        logger.warning("discarding candidate: %s", problem)
        return 1

    ts = time.strftime("%Y%m%d%H%M%S")
    candidates_dir = sandbox_dir / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)
    out_path = candidates_dir / f"pest_{ts}.py"
    header = (
        f'"""Proposed by pest_evolve.py for npc={NPC_ID} at {ts}.\n'
        f'Drafted by a real openhands-sdk agent with Bash+FileEditor tools, jailed to '
        f'{workspace}.\nNot validated yet - run_skill_validation.sh decides if this is '
        f'safe."""\n\n'
    )
    if dry_run:
        logger.info("--dry-run, would write %s:\n%s%s", out_path, header, code)
        return 0
    out_path.write_text(header + code)
    logger.info("wrote candidate %s", out_path)

    approved = _validate_candidate(sandbox_dir, out_path)
    if approved:
        logger.info("Pest candidate approved - notifying owner(s)")
        asyncio.run(_notify_owners(
            sandbox_dir,
            "I've learned something new. Restart the server (or reconnect) so I can use it.",
        ))
    else:
        logger.info("Pest candidate rejected - see sandbox/rejected/ for the log")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-dir", default=os.environ.get("SANDBOX_DIR", "/sandbox"))
    parser.add_argument("--dry-run", action="store_true",
                         help="run the agent and print the result, but don't write/validate anything")
    args = parser.parse_args()
    sys.exit(run(Path(args.sandbox_dir), args.dry_run))


if __name__ == "__main__":
    main()

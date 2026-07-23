"""
Runtime consumption of sandbox/approved/ skills - the missing half of the
self-improvement loop (CLAUDE.md "Agreed next steps" #1). skill_writer.py
generates candidates and run_skill_validation.sh/skill_harness.py validates
them into approved/; until this module, nothing ever actually ran an
approved skill against a live NPC - a validated skill just sat there with
zero effect on any NPC.

Trust model, stated explicitly: each real decide() call runs in a throwaway
OS subprocess (sandbox/skill_runner.py), not imported directly into the
orchestrator's own process. That's a deliberate, lighter-weight boundary
than full container isolation (no --network none, no cgroup memory/pid
limits beyond what preexec_fn's rlimits give) - justified by what's
actually generating this code: skill_writer.py is the only thing that ever
populates candidates/, driven by the SAME local LLM this project already
trusts for every NPC's dialogue. The realistic risk is a mediocre or buggy
generated skill, not an adversarial third party smuggling an exploit past
review, so the goal here is "can't hang or crash the orchestrator, and
can't return something we act on blindly" rather than "assume it's hostile."
If skill authorship ever becomes multi-tenant/untrusted this needs real
container isolation per call (reuse run_skill_validation.sh's docker flags,
same as the CI fix in .github/workflows/skill-validation.yml) - not done
here.

Every call still gets:
  - A hard wall-clock timeout with a genuine SIGKILL on expiry (a real OS
    process can be killed outright, unlike a hung thread) - hard rule 2,
    nothing blocks the game loop, applies just as much to a skill call as
    an LLM call.
  - Best-effort CPU/memory rlimits via preexec_fn (POSIX only - this stack
    is Linux containers throughout, see docker-compose.yml).
  - Full re-validation of the returned dict against skill_harness.py's
    validate_output() - the exact same rules enforced at promotion time,
    checked again on every real call in case a whitelist changed or a file
    was hand-edited in approved/ after being validated.

Only wired into the AMBIENT path (main.py's handle_ambient): decide()'s
state shape (event/player_id/npc_hp/time_of_day/nearby_players) and action
vocabulary (say/emote/walk_to/give_item/set_quest_flag/trade_offer/idle)
both read as reactive/ambient behavior, not conversational dialogue - and
only "say"/"idle" have anywhere to go on the plugin side today
(NpcAiBridge's wire protocol has no handler yet for emote/walk_to/
give_item/set_quest_flag/trade_offer). Those are accepted and logged here
so a skill's decision is never silently swallowed, but produce no ambient
line until the plugin side is built to act on them - a real followup, not
done here.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import logging
import re
import resource
import sys
import time
from pathlib import Path

logger = logging.getLogger("npc.skill_runtime")

REFRESH_INTERVAL_S = 60.0
CALL_TIMEOUT_S = 2.0
CALL_CPU_SECONDS = 1
CALL_MEMORY_BYTES = 128 * 1024 * 1024
_NPC_HEADER_RE = re.compile(r"for npc=(\S+) at")


def _npc_id_from_docstring(path: Path) -> str | None:
    """
    Static-only (ast.parse, never exec) extraction of the npc_id
    skill_writer.py already embeds in its header docstring (see that
    file's `out_path.write_text(header + code)`). Deliberately never
    trusts the filename alone: npc_id itself can contain underscores
    (e.g. "Merchant_Oskar"), which makes splitting the promoted filename
    (`<approval_ts>_<npc_id>_<gen_ts>.py`) ambiguous.
    """
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except SyntaxError:
        return None
    doc = ast.get_docstring(tree)
    if not doc:
        return None
    m = _NPC_HEADER_RE.search(doc)
    return m.group(1) if m else None


def _limit_call_resources() -> None:
    """preexec_fn for the skill_runner.py subprocess - best-effort, POSIX
    only. Runs in the child after fork(), before exec()."""
    resource.setrlimit(resource.RLIMIT_CPU, (CALL_CPU_SECONDS, CALL_CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_AS, (CALL_MEMORY_BYTES, CALL_MEMORY_BYTES))


class SkillRuntime:
    """Tracks which approved skill (if any) currently applies to each
    npc_id, and runs its decide() through skill_runner.py."""

    def __init__(self, sandbox_dir: Path):
        self._approved_dir = sandbox_dir / "approved"
        self._runner_path = sandbox_dir / "skill_runner.py"
        self._validate_output = self._load_validator(sandbox_dir / "skill_harness.py")
        self._by_npc: dict[str, Path] = {}
        self._last_scan = 0.0
        # Pest's "please restart to activate" mechanic (docs/PEST_OPENHANDS_
        # BRAIN.md): pest_evolve.py (a separate, offline process - see that
        # file) can write a new approved/pest_*.py at any time, including
        # while this orchestrator process is already running. Recording this
        # process' own start time and refusing to activate anything newer
        # for npc_id "pest" specifically makes the in-chat "please restart"
        # ask real rather than decorative - a skill promoted mid-session
        # only takes effect starting the NEXT orchestrator start, which is
        # exactly what asking the player to restart is asking for. Every
        # other npc_id (Mori, Adventurer) is unaffected - they never had
        # this requirement and skill_writer.py's candidates should keep
        # activating immediately, same as before.
        self._process_started_at = time.time()

    @staticmethod
    def _load_validator(harness_path: Path):
        """
        Imports skill_harness.py itself (trusted, first-party code shipped
        in this repo, NOT a candidate skill) purely to reuse its
        validate_output() - avoids a second copy of those rules drifting
        out of sync with what actually gets enforced at promotion time.
        """
        spec = importlib.util.spec_from_file_location("skill_harness_ref", harness_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.validate_output

    def _rescan(self) -> None:
        if not self._approved_dir.is_dir():
            self._by_npc = {}
            return
        # Newest-mtime-wins per npc_id, so "rollback = delete the newest
        # approved version" (README) just works: once the newest file for
        # an npc_id is gone, the next-newest remaining one becomes active
        # again on the next rescan with no extra bookkeeping.
        newest: dict[str, tuple[float, Path]] = {}
        for path in self._approved_dir.glob("*.py"):
            npc_id = _npc_id_from_docstring(path)
            if not npc_id:
                continue
            mtime = path.stat().st_mtime
            # Pest-only: a skill approved after this process started isn't
            # active yet - see __init__'s comment. Falls through to whatever
            # older Pest skill (if any) was already approved before this
            # boot, same "newest-among-eligible-wins" shape as every other
            # npc_id, just with a smaller eligible set.
            if npc_id.lower() == "pest" and mtime > self._process_started_at:
                continue
            existing = newest.get(npc_id)
            if existing is None or mtime > existing[0]:
                newest[npc_id] = (mtime, path)
        self._by_npc = {npc_id: p for npc_id, (_, p) in newest.items()}

    def _maybe_rescan(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_scan >= REFRESH_INTERVAL_S:
            self._rescan()
            self._last_scan = now

    async def try_decide(self, npc_id: str, state: dict) -> dict | None:
        """
        Returns the skill's validated decision dict, or None if there's no
        approved skill for this npc_id, or the call raised/timed out/
        returned something invalid for any reason. Callers must always
        fall back to the normal LLM path on None - this is never "the"
        path, only an optional override when one's available and healthy.
        """
        self._maybe_rescan()
        path = self._by_npc.get(npc_id)
        if path is None:
            return None
        if not path.is_file():
            # Rolled back / deleted since the last periodic scan - don't
            # make the caller wait a full REFRESH_INTERVAL_S to notice.
            self._maybe_rescan(force=True)
            path = self._by_npc.get(npc_id)
            if path is None:
                return None

        import json
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, str(self._runner_path), str(path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=_limit_call_resources,
            )
        except Exception:
            logger.exception("failed to launch skill subprocess npc=%s", npc_id)
            return None

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(json.dumps(state).encode()),
                timeout=CALL_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning("approved skill call timed out npc=%s, killing", npc_id)
            proc.kill()
            await proc.wait()
            return None

        if proc.returncode != 0:
            logger.warning("approved skill call failed npc=%s rc=%s stderr=%.300s",
                            npc_id, proc.returncode,
                            stderr.decode(errors="replace") if stderr else "")
            return None

        try:
            out = json.loads(stdout.decode())
        except Exception:
            logger.warning("approved skill npc=%s produced unparseable output", npc_id)
            return None

        err = self._validate_output(out)
        if err:
            logger.warning("approved skill npc=%s returned invalid output: %s", npc_id, err)
            return None
        return out

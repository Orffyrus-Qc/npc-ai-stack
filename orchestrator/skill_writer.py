"""
Skill-writer meta-agent (CLAUDE.md "Agreed next steps" #1).

Offline, standalone script - NOT part of the live WebSocket gateway. It looks
at each NPC's recent outcome history and the most recent sandbox rejections,
asks the LLM to draft a new `decide(state) -> dict` candidate skill, does a
cheap *static* sanity check (syntax + shape only - it never imports or
executes the generated code), and drops anything that looks reasonable into
sandbox/candidates/. Nothing it writes is trusted: the existing
sandbox/run_skill_validation.sh -> skill_harness.py pipeline is still the only
thing that can promote a candidate to approved/ (CLAUDE.md hard rule 4).

Run via `docker compose run --rm skill-writer` (see docker-compose.yml), or
directly for local testing: `python skill_writer.py --dry-run`.

IMPORTANT - GPU contention caveat: unlike compression_daemon (main.py), this
process is NOT inside the live orchestrator, so it does NOT go through
NPCRequestDispatcher's dialogue-always-wins slot arbitration. Calling this
while players are online will compete with real dialogue for the same
llama.cpp --parallel slots. Only run it during confirmed low-player windows
(the same constraint run_skill_validation.sh already documents for itself).
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib.util
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from llm_client import LlamaClient  # noqa: E402
from personality import PersonalityStore  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("npc.skill_writer")

MIN_OUTCOMES_TO_ACT = 3     # ignore NPCs with too little recent signal to act on
MAX_REJECTED_LOGS = 5       # most-recent rejection logs to show as cautionary examples
MAX_LOG_CHARS = 500         # truncate each rejection log to keep the prompt bounded
GEN_MAX_TOKENS = 600
GEN_TEMPERATURE = 0.4


def _load_harness_constants(sandbox_dir: Path):
    """
    Import ALLOWED_ACTIONS/MAX_SAY_LEN straight from the real skill_harness.py
    so the prompt can never drift out of sync with what actually gets
    enforced. This only reads module-level constants - it does not run
    anything on untrusted candidate code.
    """
    harness_path = sandbox_dir / "skill_harness.py"
    spec = importlib.util.spec_from_file_location("skill_harness_ref", harness_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.ALLOWED_ACTIONS, mod.MAX_SAY_LEN


def _recent_rejection_snippets(sandbox_dir: Path, limit: int) -> list[str]:
    rejected_dir = sandbox_dir / "rejected"
    if not rejected_dir.is_dir():
        return []
    logs = sorted(rejected_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    snippets = []
    for log_path in logs[:limit]:
        text = log_path.read_text(errors="replace").strip()
        snippets.append(f"{log_path.stem}: {text[:MAX_LOG_CHARS]}")
    return snippets


def _build_prompt(npc_id: str, outcome_counts: dict[str, int],
                   rejection_snippets: list[str],
                   allowed_actions: set[str], max_say_len: int) -> list[dict]:
    actions_list = ", ".join(sorted(allowed_actions))
    outcomes_text = "\n".join(f"- {k}: {v} times" for k, v in outcome_counts.items()) \
        or "- (no notable outcomes)"
    rejections_text = "\n".join(f"- {s}" for s in rejection_snippets) \
        or "- (no prior rejections on record)"

    system = f"""You write a single Python function for an NPC AI's decision logic \
in a Hytale server plugin. You do not chat, explain, or add commentary - you \
write code only.

Contract (enforced by an automated test harness, not optional):
- Define exactly one function: def decide(state: dict) -> dict
- state may contain: event, player_id, npc_hp, time_of_day, nearby_players \
(and possibly other keys - ignore ones you don't recognize, never raise on \
unexpected/missing/malformed input, including negative numbers, huge \
strings, or None values).
- Return a dict with an "action" key whose value is one of exactly: \
{actions_list}.
- If action is "say", include a "text" key: a short string, 1 to {max_say_len} \
characters.
- If action is "give_item", include "item_id" (string) and "count" \
(integer, 1 to 8).
- The returned dict must be JSON-serializable (plain strings/numbers/bools \
only).
- No imports, no I/O, no network, no randomness that could throw, no top-level \
code besides the function definition.
- Output ONLY the raw Python source, no markdown fences, no explanation."""

    user = f"""NPC id: {npc_id}

Recent outcomes for this NPC (last few days):
{outcomes_text}

Known mistakes from previously rejected candidate skills (avoid repeating these):
{rejections_text}

Write a decide(state) that responds sensibly to this NPC's recent history."""

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _clean_code(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip() + "\n"


def _static_check(code: str) -> str | None:
    """Syntax + shape check only - never imports or executes candidate code.
    Returns an error string, or None if it looks plausible."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"syntax error: {e}"
    has_decide = any(
        isinstance(node, ast.FunctionDef) and node.name == "decide"
        for node in ast.walk(tree)
    )
    if not has_decide:
        return "no top-level decide() function found"
    return None


async def run(sandbox_dir: Path, since_days: float, max_per_run: int,
               only_npc_id: str | None, dry_run: bool) -> int:
    allowed_actions, max_say_len = _load_harness_constants(sandbox_dir)
    rejection_snippets = _recent_rejection_snippets(sandbox_dir, MAX_REJECTED_LOGS)

    personality = PersonalityStore()
    await personality.start()
    llm = LlamaClient()
    candidates_dir = sandbox_dir / "candidates"
    candidates_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    try:
        npc_ids = [only_npc_id] if only_npc_id else await personality.all_npc_ids()
        for npc_id in npc_ids:
            if written >= max_per_run:
                logger.info("hit max_per_run=%d, stopping", max_per_run)
                break

            counts = await personality.recent_outcome_counts(npc_id, since_days)
            total = sum(counts.values())
            if total < MIN_OUTCOMES_TO_ACT:
                logger.debug("skipping %s: only %d recent outcomes", npc_id, total)
                continue

            messages = _build_prompt(npc_id, counts, rejection_snippets,
                                      allowed_actions, max_say_len)
            try:
                raw = await llm.complete(messages, max_tokens=GEN_MAX_TOKENS,
                                          temperature=GEN_TEMPERATURE)
            except Exception:
                logger.exception("generation failed for npc=%s", npc_id)
                continue

            code = _clean_code(raw)
            problem = _static_check(code)
            if problem:
                logger.warning("discarding candidate for npc=%s: %s", npc_id, problem)
                continue

            ts = time.strftime("%Y%m%d%H%M%S")
            out_path = candidates_dir / f"{npc_id}_{ts}.py"
            header = (
                f'"""Proposed by skill_writer.py for npc={npc_id} at {ts}.\n'
                f"Recent outcomes considered: {counts}\n"
                f'Not validated yet - run_skill_validation.sh decides if this is safe."""\n\n'
            )
            if dry_run:
                logger.info("--dry-run, would write %s:\n%s%s", out_path, header, code)
            else:
                out_path.write_text(header + code)
                logger.info("wrote candidate %s", out_path)
            written += 1
    finally:
        await llm.close()

    logger.info("done: %d candidate(s) written", written)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-dir", default=os.environ.get("SANDBOX_DIR", "/sandbox"),
                         help="path containing skill_harness.py, candidates/, rejected/")
    parser.add_argument("--since-days", type=float, default=3.0,
                         help="how far back to look at outcome history")
    parser.add_argument("--max-per-run", type=int, default=3,
                         help="cap on candidates written per invocation (bounds GPU time)")
    parser.add_argument("--npc-id", default=None, help="only consider this one NPC")
    parser.add_argument("--dry-run", action="store_true",
                         help="generate and print, but don't write candidate files")
    args = parser.parse_args()

    asyncio.run(run(
        Path(args.sandbox_dir), args.since_days, args.max_per_run,
        args.npc_id, args.dry_run,
    ))


if __name__ == "__main__":
    main()

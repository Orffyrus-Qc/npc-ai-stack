"""
OpenHands-style agent loop for the Hytale NPC brain.

while not done and steps < max:
    LLM(tool JSON) → Action → ToolRegistry.execute → Observation
    record Experience(reward)
    if terminal tool → return spoken line

Dialogue always uses the shared GPU dispatcher when called from main.py.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from agent_brain import config
from agent_brain.experience import ExperienceStore
from agent_brain.prompts import build_messages, format_obs
from agent_brain.tools.game_files import roots_status
from agent_brain.tools.registry import ToolRegistry
from agent_brain.types import Action, Experience, Goal, Observation

logger = logging.getLogger("npc.brain.loop")

LLMChatFn = Callable[[list[dict], int, float], Awaitable[str]]


@dataclass
class BrainResult:
    say: str
    success: bool
    steps: int
    total_reward: float
    play_proposal: dict[str, Any] | None = None
    trace: list[dict] = field(default_factory=list)


@dataclass
class BrainSession:
    npc_id: str
    player_id: str
    goal: Goal
    player_message: str = ""


class AgentLoop:
    def __init__(
        self,
        tools: ToolRegistry,
        experience: ExperienceStore,
        llm_chat: LLMChatFn,
        max_steps: int | None = None,
    ):
        self.tools = tools
        self.experience = experience
        self.llm_chat = llm_chat
        self.max_steps = max_steps or config.MAX_STEPS

    async def run(self, session: BrainSession) -> BrainResult:
        self.tools.reset_turn_flags()
        history: list[tuple[str, str]] = []
        trace: list[dict] = []
        total_reward = 0.0
        lessons = await self.experience.top_lessons(session.npc_id, limit=5)
        roots = roots_status()["content"]

        # Bind learning recorder to this npc/player
        async def _learn(_n, _p, lesson, conf, topic):
            await self.experience.record_lesson(
                session.npc_id, session.player_id, lesson, conf, topic
            )

        self.tools._record_learning = _learn

        for step in range(self.max_steps):
            messages = build_messages(
                goal=session.goal.text,
                history=history,
                lessons=lessons,
                roots_hint=roots,
                player_message=session.player_message,
            )
            raw = await self.llm_chat(messages, 400, 0.3)
            action = self._parse_action(raw)
            if action is None:
                logger.warning("brain step %s unparseable: %.200s", step, raw)
                # force finish soft
                break

            obs = await self.tools.execute(action)
            total_reward += obs.reward

            exp = Experience(
                npc_id=session.npc_id,
                player_id=session.player_id,
                goal=session.goal.text,
                action_name=action.name,
                action_args=action.args,
                observation_ok=obs.ok,
                observation_summary=obs.content[:500],
                reward=obs.reward,
            )
            await self.experience.record(exp)

            history.append(
                (
                    f"{action.name} {json.dumps(action.args)[:200]}",
                    format_obs(obs),
                )
            )
            trace.append({"action": action.to_dict(), "observation": obs.to_dict()})

            # capture lessons from tool data
            if action.name == "record_learning" and obs.ok:
                lessons = await self.experience.top_lessons(session.npc_id, limit=5)

            if self.tools.finished:
                say = (self.tools.last_player_line or "").strip()
                return BrainResult(
                    say=say,
                    success=self.tools.finish_success or bool(say),
                    steps=step + 1,
                    total_reward=total_reward,
                    play_proposal=self.tools.last_play_proposal,
                    trace=trace,
                )

        # Budget exhausted without a real answer_help/finish call. Real,
        # live-confirmed bug (2026-07-22): this used to synthesize a
        # "humble line" by splicing history[-1][1] - format_obs()'s raw
        # internal debug string ("[OK reward=+0.20] Assets:Common/...")
        # meant only for the NEXT prompt's own TRACE SO FAR block, never
        # for a player - directly into what the player saw as the NPC's
        # spoken reply. Read live as "the NPC is confused / doesn't
        # understand how to use tools wisely" (it wasn't confused, it was
        # literally reciting its own tool-call bookkeeping).
        #
        # Fixed to empty text instead - same "no reply this turn" sentinel
        # every other failure path in this stack already uses (hard rule 2:
        # no pre-written fallback lines anywhere). This also matters
        # structurally: handle_brain_help() in main.py already has a
        # built-in safety net ("if result is None or not result.say:
        # fall back to normal character dialogue") that only ever triggers
        # on a falsy say - the leaky synthesized text was accidentally
        # defeating that fallback every single time by always being
        # truthy, so a failed multi-step run never got the chance to
        # gracefully degrade to the well-tuned plain dialogue path it was
        # designed to fall back to.
        return BrainResult(
            say="",
            success=False,
            steps=len(history),
            total_reward=total_reward,
            play_proposal=self.tools.last_play_proposal,
            trace=trace,
        )

    def _parse_action(self, raw: str) -> Action | None:
        text = (raw or "").strip()
        # strip markdown fences if any
        if "```" in text:
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
            if m:
                text = m.group(1)
        # find first JSON object
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            obj = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            # try to fix trailing commas lightly
            try:
                cleaned = re.sub(r",\s*}", "}", text[start : end + 1])
                obj = json.loads(cleaned)
            except json.JSONDecodeError:
                return None
        tool = obj.get("tool") or obj.get("name") or obj.get("action")
        if not tool:
            return None
        args = obj.get("args") or obj.get("arguments") or {}
        if not isinstance(args, dict):
            args = {}
        thought = str(obj.get("thought", "") or "")
        return Action(name=str(tool), args=args, thought=thought)

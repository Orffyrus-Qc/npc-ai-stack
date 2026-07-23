"""Dispatch Action → Observation."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from agent_brain.types import Action, ActionName, Observation
from agent_brain.tools import game_files, map_world, web

logger = logging.getLogger("npc.brain.tools")

# Optional wiki store injected at runtime
WikiSearchFn = Callable[[str], Awaitable[list[str]]]
RecordLearningFn = Callable[[str, str, str, float, str], Awaitable[None]]
RecordExperienceFn = Callable[..., Awaitable[None]]


class ToolRegistry:
    def __init__(
        self,
        wiki_search: WikiSearchFn | None = None,
        record_learning: RecordLearningFn | None = None,
    ):
        self._wiki_search = wiki_search
        self._record_learning = record_learning
        # Side-channel for HITL terminal actions
        self.last_player_line: str | None = None
        self.last_play_proposal: dict[str, Any] | None = None
        self.finished: bool = False
        self.finish_success: bool = False

    def reset_turn_flags(self) -> None:
        self.last_player_line = None
        self.last_play_proposal = None
        self.finished = False
        self.finish_success = False

    async def execute(self, action: Action) -> Observation:
        name = action.name
        args = action.args or {}
        try:
            result = await self._dispatch(name, args)
        except Exception as e:
            logger.exception("tool %s crashed", name)
            return Observation(
                action_id=action.id,
                ok=False,
                content=f"Tool crash: {e}",
                reward=-0.2,
            )

        ok = bool(result.get("ok", False))
        content = str(result.get("content", ""))
        reward = float(result.get("reward", 0.0 if ok else -0.05))
        data = dict(result.get("data") or {})
        return Observation(
            action_id=action.id,
            ok=ok,
            content=content,
            data=data,
            reward=reward,
        )

    async def _dispatch(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name == ActionName.READ_GAME_FILE.value:
            return game_files.read_game_file(str(args.get("path", "")))

        if name == ActionName.SEARCH_GAME_FILES.value:
            return game_files.search_game_files(
                str(args.get("query", "")),
                root=str(args.get("root", "All")),
                limit=int(args.get("limit", 15) or 15),
            )

        if name == ActionName.LIST_GAME_TREE.value:
            return game_files.list_game_tree(
                str(args.get("path", "Assets:")),
                limit=int(args.get("limit", 40) or 40),
            )

        if name == ActionName.READ_MAP_MARKERS.value:
            return map_world.read_map_markers(args.get("world"))

        if name == ActionName.READ_WORLD_CONFIG.value:
            return map_world.read_world_config(args.get("world"))

        if name == ActionName.SEARCH_WIKI.value:
            q = str(args.get("query", ""))
            if not self._wiki_search:
                return {"ok": False, "content": "Wiki store not wired", "reward": 0.0}
            snippets = await self._wiki_search(q)
            if not snippets:
                return {
                    "ok": True,
                    "content": f"No wiki hits for {q!r}. Try game files or ask_player.",
                    "reward": 0.0,
                }
            return {
                "ok": True,
                "content": "\n---\n".join(snippets),
                "reward": 0.2,
            }

        if name == ActionName.WEB_FETCH.value:
            return await web.web_fetch(
                str(args.get("url", "")),
                max_chars=int(args.get("max_chars", 4000) or 4000),
            )

        if name == ActionName.ASK_PLAYER.value:
            q = str(args.get("question", "")).strip()
            if not q:
                return {"ok": False, "content": "Empty question"}
            self.last_player_line = q
            self.finished = True
            return {
                "ok": True,
                "content": f"(Will ask player) {q}",
                "reward": 0.05,
            }

        if name == ActionName.ANSWER_HELP.value:
            ans = str(args.get("answer", "")).strip()
            sources = args.get("sources") or []
            if sources:
                ans = ans + "\n\n(Sources: " + ", ".join(str(s) for s in sources[:5]) + ")"
            self.last_player_line = ans
            self.finished = True
            self.finish_success = True
            return {
                "ok": bool(ans),
                "content": ans or "Empty answer",
                "reward": 0.35 if ans else -0.1,
            }

        if name == ActionName.PROPOSE_PLAY_ACTION.value:
            prop = {
                "action": str(args.get("action", "explore")),
                "target": str(args.get("target", "")),
                "reason": str(args.get("reason", "")),
            }
            self.last_play_proposal = prop
            return {
                "ok": True,
                "content": f"Proposed play: {prop}",
                "data": prop,
                "reward": 0.1,
            }

        if name == ActionName.RECORD_LEARNING.value:
            lesson = str(args.get("lesson", "")).strip()
            conf = float(args.get("confidence", 0.6) or 0.6)
            topic = str(args.get("topic", "gameplay"))
            if not lesson:
                return {"ok": False, "content": "Empty lesson"}
            if self._record_learning:
                # npc_id/player filled by loop via partial — we pass placeholders;
                # loop overrides by calling store itself. Keep tool self-contained:
                await self._record_learning("", "", lesson, conf, topic)
            return {
                "ok": True,
                "content": f"Recorded learning ({topic}, conf={conf:.2f}): {lesson}",
                "data": {"lesson": lesson, "confidence": conf, "topic": topic},
                "reward": 0.25 * max(0.0, min(1.0, conf)),
            }

        if name == ActionName.THINK.value:
            note = str(args.get("note", ""))
            return {"ok": True, "content": f"(thought) {note}", "reward": 0.0}

        if name == ActionName.FINISH.value:
            say = str(args.get("say", "") or "").strip()
            success = bool(args.get("success", True))
            self.last_player_line = say
            self.finished = True
            self.finish_success = success
            return {
                "ok": True,
                "content": say or "(finished silently)",
                "reward": 0.3 if success else 0.0,
            }

        if name == "roots_status":
            return game_files.roots_status()

        return {"ok": False, "content": f"Unknown tool: {name}", "reward": -0.1}


def build_default_registry(
    wiki_search: WikiSearchFn | None = None,
    record_learning: RecordLearningFn | None = None,
) -> ToolRegistry:
    return ToolRegistry(wiki_search=wiki_search, record_learning=record_learning)

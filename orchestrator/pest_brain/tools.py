"""
Pest's real openhands-sdk tools - all read-only, reusing the EXISTING
agent_brain.tools functions rather than duplicating them (same game-file/
map/wiki access Mori's help path already has, see docs/OPENHANDS_NPC_BRAIN.md).

No Bash/FileEditor tool is defined here on purpose - that's pest_evolve.py's
job (a separate, offline, profile-gated process), never the live chat path.
See docs/PEST_OPENHANDS_BRAIN.md for the full reasoning.

Shape (Action/Observation/ToolExecutor/ToolDefinition.create()) confirmed
against the real installed package - not guessed from summarized docs -
by inspecting openhands.sdk.tool.builtins.finish.FinishTool's actual source
inside python:3.12-slim (this project's own base image) while building
this integration.

Registration: register_pest_tools() is called once at orchestrator startup
(main.py) and returns the list of openhands.sdk.Tool(name=...) references
to hand to Agent(tools=[...]). Each Pest turn still gets a FRESH executor
instance (ToolDefinition.create() runs per-Conversation), which is also how
propose_play_action's per-turn proposal capture works - see its Executor.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from typing import Any, Awaitable, Callable, Self

from pydantic import Field

from openhands.sdk import Tool
from openhands.sdk.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
    register_tool,
)

from agent_brain.tools import game_files, map_world

logger = logging.getLogger("npc.pest_brain.tools")

WikiSearchFn = Callable[[str], Awaitable[list[str]]]

# Set once at orchestrator startup (main.py) to WIKI.search - kept as a
# module-level indirection rather than threaded through Tool(params=...)
# because it's a single process-wide singleton, same as agent_brain's own
# WIKI global; avoids a circular import between pest_brain and main.py.
_wiki_search_fn: WikiSearchFn | None = None


def set_wiki_search_fn(fn: WikiSearchFn) -> None:
    global _wiki_search_fn
    _wiki_search_fn = fn


def _obs(result: dict[str, Any], obs_cls: type[Observation]) -> Observation:
    """Shared adapter: agent_brain.tools functions already return
    {"ok": bool, "content": str, ...} - reuse that shape instead of
    reinventing per-tool result handling."""
    return obs_cls.from_text(text=str(result.get("content", "")),
                              is_error=not bool(result.get("ok", False)))


# ---------------------------------------------------------------------------
# search_game_files
# ---------------------------------------------------------------------------

class SearchGameFilesAction(Action):
    query: str = Field(description="Keyword or filename fragment to search for.")
    root: str = Field(
        default="All",
        description="Assets|ServerJar|UserData|Client|PluginRun|All (default All).",
    )
    limit: int = Field(default=15, description="Max hits to return.")


class SearchGameFilesObservation(Observation):
    pass


class SearchGameFilesExecutor(ToolExecutor):
    def __call__(self, action: SearchGameFilesAction, conversation=None
                 ) -> SearchGameFilesObservation:
        result = game_files.search_game_files(action.query, root=action.root,
                                               limit=action.limit)
        return _obs(result, SearchGameFilesObservation)


class SearchGameFilesTool(ToolDefinition[SearchGameFilesAction, SearchGameFilesObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:
        return [cls(
            name="search_game_files",
            action_type=SearchGameFilesAction,
            observation_type=SearchGameFilesObservation,
            description=(
                "Search file names (and Assets.zip entries) under the mounted "
                "Hytale install for something by keyword, e.g. a recipe or "
                "creature JSON. Use before read_game_file."
            ),
            executor=SearchGameFilesExecutor(),
            annotations=ToolAnnotations(
                title="search_game_files", readOnlyHint=True,
                destructiveHint=False, idempotentHint=True, openWorldHint=False,
            ),
        )]


# ---------------------------------------------------------------------------
# read_game_file
# ---------------------------------------------------------------------------

class ReadGameFileAction(Action):
    path: str = Field(
        description=(
            "Path from search_game_files' results, e.g. "
            "'Assets:Server/Item/Items/Bench/Bench_Campfire.json' or "
            "'UserData:Saves/...'."
        )
    )


class ReadGameFileObservation(Observation):
    pass


class ReadGameFileExecutor(ToolExecutor):
    def __call__(self, action: ReadGameFileAction, conversation=None
                 ) -> ReadGameFileObservation:
        result = game_files.read_game_file(action.path)
        return _obs(result, ReadGameFileObservation)


class ReadGameFileTool(ToolDefinition[ReadGameFileAction, ReadGameFileObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:
        return [cls(
            name="read_game_file",
            action_type=ReadGameFileAction,
            observation_type=ReadGameFileObservation,
            description="Read a real game file (JSON/text) by the path search_game_files found.",
            executor=ReadGameFileExecutor(),
            annotations=ToolAnnotations(
                title="read_game_file", readOnlyHint=True,
                destructiveHint=False, idempotentHint=True, openWorldHint=False,
            ),
        )]


# ---------------------------------------------------------------------------
# list_game_tree
# ---------------------------------------------------------------------------

class ListGameTreeAction(Action):
    path: str = Field(description="Directory path/alias to list one level deep.")
    limit: int = Field(default=40, description="Max entries to return.")


class ListGameTreeObservation(Observation):
    pass


class ListGameTreeExecutor(ToolExecutor):
    def __call__(self, action: ListGameTreeAction, conversation=None
                 ) -> ListGameTreeObservation:
        result = game_files.list_game_tree(action.path, limit=action.limit)
        return _obs(result, ListGameTreeObservation)


class ListGameTreeTool(ToolDefinition[ListGameTreeAction, ListGameTreeObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:
        return [cls(
            name="list_game_tree",
            action_type=ListGameTreeAction,
            observation_type=ListGameTreeObservation,
            description="List directories/files one level deep under a game path.",
            executor=ListGameTreeExecutor(),
            annotations=ToolAnnotations(
                title="list_game_tree", readOnlyHint=True,
                destructiveHint=False, idempotentHint=True, openWorldHint=False,
            ),
        )]


# ---------------------------------------------------------------------------
# read_map_markers / read_world_config
# ---------------------------------------------------------------------------

class ReadMapMarkersAction(Action):
    world: str = Field(default="default", description="World name, if known.")


class ReadMapMarkersObservation(Observation):
    pass


class ReadMapMarkersExecutor(ToolExecutor):
    def __call__(self, action: ReadMapMarkersAction, conversation=None
                 ) -> ReadMapMarkersObservation:
        result = map_world.read_map_markers(action.world)
        return _obs(result, ReadMapMarkersObservation)


class ReadMapMarkersTool(ToolDefinition[ReadMapMarkersAction, ReadMapMarkersObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:
        return [cls(
            name="read_map_markers",
            action_type=ReadMapMarkersAction,
            observation_type=ReadMapMarkersObservation,
            description="Read world map markers from a save so Pest and the player navigate together.",
            executor=ReadMapMarkersExecutor(),
            annotations=ToolAnnotations(
                title="read_map_markers", readOnlyHint=True,
                destructiveHint=False, idempotentHint=True, openWorldHint=False,
            ),
        )]


class ReadWorldConfigAction(Action):
    world: str = Field(default="default", description="World name, if known.")


class ReadWorldConfigObservation(Observation):
    pass


class ReadWorldConfigExecutor(ToolExecutor):
    def __call__(self, action: ReadWorldConfigAction, conversation=None
                 ) -> ReadWorldConfigObservation:
        result = map_world.read_world_config(action.world)
        return _obs(result, ReadWorldConfigObservation)


class ReadWorldConfigTool(ToolDefinition[ReadWorldConfigAction, ReadWorldConfigObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:
        return [cls(
            name="read_world_config",
            action_type=ReadWorldConfigAction,
            observation_type=ReadWorldConfigObservation,
            description="Read a world's config.json.",
            executor=ReadWorldConfigExecutor(),
            annotations=ToolAnnotations(
                title="read_world_config", readOnlyHint=True,
                destructiveHint=False, idempotentHint=True, openWorldHint=False,
            ),
        )]


# ---------------------------------------------------------------------------
# search_wiki
# ---------------------------------------------------------------------------

class SearchWikiAction(Action):
    query: str = Field(description="What to look up in the locally-ingested Hytale wiki.")


class SearchWikiObservation(Observation):
    pass


class SearchWikiExecutor(ToolExecutor):
    def __call__(self, action: SearchWikiAction, conversation=None
                 ) -> SearchWikiObservation:
        if _wiki_search_fn is None:
            return SearchWikiObservation.from_text(text="Wiki store not wired.", is_error=True)
        # This executor runs inside session.py's asyncio.to_thread worker
        # (see that module's docstring) - a plain OS thread with no running
        # event loop of its own, so asyncio.run() here is safe and does not
        # conflict with the orchestrator's main loop.
        try:
            snippets = asyncio.run(_wiki_search_fn(action.query))
        except Exception as e:
            logger.exception("search_wiki failed")
            return SearchWikiObservation.from_text(text=f"Wiki search error: {e}", is_error=True)
        if not snippets:
            return SearchWikiObservation.from_text(
                text=f"No wiki hits for {action.query!r}. Try game files or ask the player.")
        return SearchWikiObservation.from_text(text="\n---\n".join(snippets))


class SearchWikiTool(ToolDefinition[SearchWikiAction, SearchWikiObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:
        return [cls(
            name="search_wiki",
            action_type=SearchWikiAction,
            observation_type=SearchWikiObservation,
            description="Search locally-ingested Hytale wiki knowledge (no live network access).",
            executor=SearchWikiExecutor(),
            annotations=ToolAnnotations(
                title="search_wiki", readOnlyHint=True,
                destructiveHint=False, idempotentHint=True, openWorldHint=False,
            ),
        )]


# ---------------------------------------------------------------------------
# propose_play_action - follow/lead parity with Mori, decision-only (no
# write access to anything - this only ever produces a proposal dict the
# plugin may act on, same _play_proposal_to_wire() mapping main.py's
# existing agent_brain path already uses, see main.py's handle_pest_dialogue).
# ---------------------------------------------------------------------------

class ProposePlayActionAction(Action):
    action: str = Field(
        description="gather|craft|go_to|explore|fight|rest|mine|build|trade"
    )
    target: str = Field(default="", description="Item/place/entity id or description.")
    reason: str = Field(default="")


class ProposePlayActionObservation(Observation):
    pass


class ProposePlayActionExecutor(ToolExecutor):
    def __init__(self, session_state: dict[str, Any]):
        # session_state is a plain dict shared with session.py for THIS
        # turn only (passed in via Tool(params=...) -> create(**params),
        # confirmed real for local-in-process execution the same way
        # TerminalTool.create() threads working_dir through - see
        # openhands.sdk.tool.builtins.finish.FinishTool's source, which
        # this module's whole shape is modeled on).
        self._session_state = session_state

    def __call__(self, action: ProposePlayActionAction, conversation=None
                 ) -> ProposePlayActionObservation:
        prop = {"action": action.action, "target": action.target, "reason": action.reason}
        self._session_state["play_proposal"] = prop
        return ProposePlayActionObservation.from_text(text=f"Noted play proposal: {prop}")


class ProposePlayActionTool(ToolDefinition[ProposePlayActionAction, ProposePlayActionObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence[Self]:
        session_state = params.get("session_state")
        if session_state is None:
            raise ValueError("ProposePlayActionTool requires session_state param")
        return [cls(
            name="propose_play_action",
            action_type=ProposePlayActionAction,
            observation_type=ProposePlayActionObservation,
            description=(
                "Propose an in-game action Pest would take if acting like a player "
                "(gather/craft/go_to/explore/fight/rest/mine/build/trade) - e.g. "
                "leading the player somewhere. Logged, may be enacted by the plugin."
            ),
            executor=ProposePlayActionExecutor(session_state=session_state),
            annotations=ToolAnnotations(
                title="propose_play_action", readOnlyHint=True,
                destructiveHint=False, idempotentHint=False, openWorldHint=False,
            ),
        )]


_REGISTERED = False


def register_pest_tools() -> None:
    """Idempotent - register_tool() is a process-wide registry; call once
    at orchestrator startup (main.py)."""
    global _REGISTERED
    if _REGISTERED:
        return
    register_tool("search_game_files", SearchGameFilesTool)
    register_tool("read_game_file", ReadGameFileTool)
    register_tool("list_game_tree", ListGameTreeTool)
    register_tool("read_map_markers", ReadMapMarkersTool)
    register_tool("read_world_config", ReadWorldConfigTool)
    register_tool("search_wiki", SearchWikiTool)
    register_tool("propose_play_action", ProposePlayActionTool)
    _REGISTERED = True


def build_tool_refs(session_state: dict[str, Any]) -> list[Tool]:
    """One set of Tool(name=...) references per turn - propose_play_action
    needs this turn's own session_state dict, the rest take no params."""
    return [
        Tool(name="search_game_files"),
        Tool(name="read_game_file"),
        Tool(name="list_game_tree"),
        Tool(name="read_map_markers"),
        Tool(name="read_world_config"),
        Tool(name="search_wiki"),
        Tool(name="propose_play_action", params={"session_state": session_state}),
    ]

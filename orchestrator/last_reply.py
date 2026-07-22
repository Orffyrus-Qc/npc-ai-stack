"""
Per-(npc, player) "literal last thing said" cache - a plain in-memory
dict, updated SYNCHRONOUSLY the instant a reply is generated (no async
task, no network round trip, nothing to race).

2026-07-22 real bug found live: "he continue to talk about the past
things when I ask a new question" - the actual symptom, confirmed via the
real server log, was the model repeating the exact same reply verbatim
many times in a row. The only prior anti-repeat signal was "Things YOU
remember" (recall_recent()/recall_similar() against Qdrant episodic
memory), written via main.py's `_spawn(MEMORY.remember_episode(...))` -
a fire-and-forget asyncio task, not awaited before the turn returns. A
fast next turn could race ahead of that write landing, and even when it
lands in time, similarity/recency ranking could bury the just-said line
under other memories instead of surfacing it prominently. This sidesteps
both problems: nothing here depends on Qdrant timing or ranking at all.
"""

from typing import Dict

_LAST_REPLY: Dict[str, str] = {}


def _key(npc_id: str, player_id: str) -> str:
    return f"{npc_id}|{player_id}"


def record(npc_id: str, player_id: str, text: str) -> None:
    """Called synchronously right after a real reply is generated - see
    main.py's handle_dialogue(). Empty text (the "nothing to say this
    turn" case) is not worth remembering as "the last thing said."""
    if not player_id or not text:
        return
    _LAST_REPLY[_key(npc_id, player_id)] = text


def get(npc_id: str, player_id: str) -> str:
    """Returns "" if this (npc, player) pair has no recorded reply yet
    (first turn ever, or the process restarted - this is plain in-memory
    state, not persisted, which is fine: it only needs to survive one
    turn to the next, not across a restart)."""
    if not player_id:
        return ""
    return _LAST_REPLY.get(_key(npc_id, player_id), "")

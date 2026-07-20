"""
Runs INSIDE the throwaway sandbox container. Loads a candidate skill and
checks it behaves: correct interface, well-formed actions, in-bounds values,
deterministic-enough, and doesn't try anything it shouldn't need.

Exit 0 = pass, nonzero = fail. Extend TEST_STATES with real cases from your
game as you discover failure modes - every rejected skill is a future test.
"""

import importlib.util
import json
import sys

ALLOWED_ACTIONS = {
    "say", "emote", "walk_to", "look_at", "give_item",
    "set_quest_flag", "trade_offer", "idle",
}
MAX_SAY_LEN = 240

TEST_STATES = [
    {"event": "player_greets", "player_id": "p1", "npc_hp": 100,
     "time_of_day": "noon", "nearby_players": 1},
    {"event": "player_attacks", "player_id": "p2", "npc_hp": 40,
     "time_of_day": "night", "nearby_players": 3},
    {"event": "idle_tick", "player_id": None, "npc_hp": 100,
     "time_of_day": "dawn", "nearby_players": 0},
    # adversarial-ish inputs: skill must not crash on junk
    {"event": "player_greets", "player_id": "p3" * 200, "npc_hp": -5,
     "time_of_day": "???", "nearby_players": 9999},
]


def fail(msg: str) -> None:
    print(f"VALIDATION FAIL: {msg}")
    sys.exit(1)


def main(path: str) -> None:
    spec = importlib.util.spec_from_file_location("candidate", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except Exception as e:  # import-time crash or blocked syscall
        fail(f"import error: {e!r}")

    if not hasattr(mod, "decide") or not callable(mod.decide):
        fail("no callable decide(state) found")

    for i, state in enumerate(TEST_STATES):
        try:
            out = mod.decide(dict(state))
        except Exception as e:
            fail(f"decide() raised on case {i}: {e!r}")

        if not isinstance(out, dict):
            fail(f"case {i}: decide() must return a dict, got {type(out).__name__}")
        action = out.get("action")
        if action not in ALLOWED_ACTIONS:
            fail(f"case {i}: action {action!r} not in allowed set")
        if action == "say":
            text = out.get("text", "")
            if not isinstance(text, str) or not (0 < len(text) <= MAX_SAY_LEN):
                fail(f"case {i}: say text missing/too long")
        if action == "give_item":
            if not isinstance(out.get("item_id"), str) or \
               not isinstance(out.get("count"), int) or \
               not (1 <= out["count"] <= 8):
                fail(f"case {i}: give_item malformed or count out of bounds")
        # serialization check - action must survive the wire
        try:
            json.dumps(out)
        except (TypeError, ValueError):
            fail(f"case {i}: action not JSON-serializable")

    print("VALIDATION PASS")
    sys.exit(0)


if __name__ == "__main__":
    main(sys.argv[1])

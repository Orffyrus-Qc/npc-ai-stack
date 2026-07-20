"""
Reference skill used as a regression test for skill_harness.py itself
(see .github/workflows/skill-validation.yml). Not part of the live
candidates/approved/rejected pipeline - just a known-good decide().
"""


def decide(state: dict) -> dict:
    event = state.get("event")
    nearby = state.get("nearby_players") or 0

    if event == "player_greets":
        return {"action": "say", "text": "Well met, traveler."}
    if event == "player_attacks":
        return {"action": "emote", "text": "flinch"}
    if nearby and nearby > 0:
        return {"action": "look_at", "text": "nearest_player"}
    return {"action": "idle"}

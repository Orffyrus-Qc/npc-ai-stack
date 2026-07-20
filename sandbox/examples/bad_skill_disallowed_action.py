"""
Deliberately broken reference skill: returns an action outside
ALLOWED_ACTIONS. Used to prove skill_harness.py actually rejects bad
candidates, not just accepts everything (see .github/workflows/skill-validation.yml).
"""


def decide(state: dict) -> dict:
    return {"action": "teleport", "text": "nope"}

"""
Invoked by orchestrator/skill_runtime.py as a plain OS subprocess (deliberately
NOT in-process import - see that module's docstring for the full trust-model
reasoning) so a hung or crashing decide() can be genuinely killed on timeout
and can never take the live orchestrator process down with it.

Reads one JSON state object from stdin, prints decide(state)'s raw return
value as one JSON object to stdout. Any failure (import error, no decide(),
decide() raising, non-serializable output) is an uncaught exception -> non-
zero exit, traceback on stderr. Deliberately does NOT re-implement
skill_harness.py's output-shape rules here; skill_runtime.py re-validates
the real return value against skill_harness.validate_output() itself, once
this process has exited, so there's exactly one copy of those rules.
"""
import importlib.util
import json
import sys


def main(candidate_path: str) -> None:
    state = json.loads(sys.stdin.read())
    spec = importlib.util.spec_from_file_location("candidate", candidate_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.decide(state)
    print(json.dumps(out))


if __name__ == "__main__":
    main(sys.argv[1])

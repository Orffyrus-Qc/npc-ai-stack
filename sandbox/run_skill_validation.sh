#!/usr/bin/env bash
# Validate a proposed NPC skill inside an ephemeral, locked-down container.
#
# A "skill" here is a small Python file exposing decide(state: dict) -> dict.
# The self-improvement loop writes candidates to ./candidates/<skill>.py;
# this script runs each against the test harness in total isolation:
#   --network none      no exfiltration / no calls out
#   --read-only         no filesystem writes except /tmp scratch
#   --memory/--pids     can't bomb the host
#   timeout             can't hang forever
# Only skills that pass get moved to ./approved/ (the versioned registry dir
# the orchestrator loads from). Everything else lands in ./rejected/ with logs.
#
# Run this offline / on a cron during low-player windows - never live.

set -euo pipefail

CANDIDATES_DIR="${1:-./candidates}"
APPROVED_DIR="${2:-./approved}"
REJECTED_DIR="${3:-./rejected}"
HARNESS="$(dirname "$0")/skill_harness.py"
TIMEOUT_S=20

mkdir -p "$APPROVED_DIR" "$REJECTED_DIR"

for skill in "$CANDIDATES_DIR"/*.py; do
  [ -e "$skill" ] || { echo "no candidates."; exit 0; }
  name="$(basename "$skill")"
  echo "=== validating $name ==="

  set +e
  docker run --rm \
    --network none \
    --read-only \
    --tmpfs /tmp:size=16m \
    --memory 256m \
    --memory-swap 256m \
    --pids-limit 32 \
    --cpus 0.5 \
    --cap-drop ALL \
    --security-opt no-new-privileges \
    --user 65534:65534 \
    -v "$(realpath "$skill")":/skill/candidate.py:ro \
    -v "$(realpath "$HARNESS")":/skill/harness.py:ro \
    python:3.12-slim \
    timeout "$TIMEOUT_S" python /skill/harness.py /skill/candidate.py \
    > "/tmp/skillval_$name.log" 2>&1
  rc=$?
  set -e

  if [ $rc -eq 0 ]; then
    ts="$(date +%Y%m%d%H%M%S)"
    cp "$skill" "$APPROVED_DIR/${ts}_${name}"   # timestamped = crude versioning
    rm "$skill"
    echo "PASS -> approved/${ts}_${name}"
  else
    mv "$skill" "$REJECTED_DIR/$name"
    cp "/tmp/skillval_$name.log" "$REJECTED_DIR/$name.log"
    echo "FAIL (rc=$rc) -> rejected/ (see log)"
  fi
done

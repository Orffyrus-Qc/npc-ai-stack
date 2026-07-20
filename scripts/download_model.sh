#!/usr/bin/env bash
# Fetch the quantized model. Q4_K_M fits 8GB cards with 4x2048 ctx slots;
# use Q5_K_M on 10-12GB cards for a small quality bump.
#
# Qwen/Qwen2.5-7B-Instruct-GGUF publishes q4_k_m and q5_k_m as TWO shards each
# (qwen2.5-7b-instruct-<quant>-00001-of-00002.gguf / -00002-of-00002.gguf) -
# there is no single-file qwen2.5-7b-instruct-<quant>.gguf. llama.cpp loads a
# split model by pointing --model at the *first* shard, as long as every
# shard sits in the same directory.
set -euo pipefail
mkdir -p models
QUANT="${1:-q4_k_m}"   # or: q5_k_m
REPO="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main"

case "$QUANT" in
  q4_k_m|q5_k_m)
    PART1="qwen2.5-7b-instruct-${QUANT}-00001-of-00002.gguf"
    PART2="qwen2.5-7b-instruct-${QUANT}-00002-of-00002.gguf"
    ;;
  *)
    echo "Unsupported quant '$QUANT'. This script only knows the shard layout" >&2
    echo "for q4_k_m and q5_k_m - check the file list at" >&2
    echo "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/tree/main before adding others." >&2
    exit 1
    ;;
esac

echo "Downloading ${PART1} (~2.3GB)..."
curl -L --fail -o "models/${PART1}" "${REPO}/${PART1}"
echo "Downloading ${PART2} (~2.3GB)..."
curl -L --fail -o "models/${PART2}" "${REPO}/${PART2}"
echo "Done. Set LLM_MODEL_FILE=${PART1} (e.g. in .env) if this isn't the q4_k_m default -"
echo "llama.cpp will pick up ${PART2} automatically from the same directory."
echo "Then: docker compose up -d"

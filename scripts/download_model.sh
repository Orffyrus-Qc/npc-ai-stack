#!/usr/bin/env bash
# Fetch the quantized model. Q4_K_M fits 8GB cards with 4x2048 ctx slots;
# use Q5_K_M on 10-12GB cards for a small quality bump.
set -euo pipefail
mkdir -p models
QUANT="${1:-q4_k_m}"   # or: q5_k_m
FILE="qwen2.5-7b-instruct-${QUANT}.gguf"
URL="https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GGUF/resolve/main/${FILE}"
echo "Downloading ${FILE} (~4.5-5.5GB)..."
curl -L --fail -o "models/qwen2.5-7b-instruct-q4_k_m.gguf" "$URL"
echo "Done. Start the stack: docker compose up -d"

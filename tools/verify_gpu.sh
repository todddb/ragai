#!/usr/bin/env bash
set -euo pipefail

prompt="${1:-Write a short sentence about GPU acceleration.}"

echo "=== GPU status (before) ==="
nvidia-smi || true
echo

echo "=== Ollama test generation ==="
curl -s http://localhost:11434/api/generate \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"${OLLAMA_MODEL:-llama3}\",\"prompt\":\"${prompt}\",\"stream\":false}" \
  | sed -n '1,5p'
echo

echo "=== GPU status (after) ==="
nvidia-smi || true

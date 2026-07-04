#!/usr/bin/env bash
# Radiology AI Assistant model setup helper.
# Pulls recommended LOCAL models for radiology via Ollama, sized to the machine's
# unified memory. Everything stays on device. No cloud.
#
# Recommendations (from the model research workstream):
#   Reasoning (primary):  Qwen3-32B (64GB+), Qwen3-14B or gpt-oss:20b (32GB)
#   Medical + vision:     MedGemma 27B (64GB+), MedGemma 4B (32GB)
#   Embeddings (RAG):     nomic-embed-text
#
# Usage: ./scripts/setup-models.sh [--tier 32|64|128] [--embed-only]

set -euo pipefail

if ! command -v ollama >/dev/null 2>&1; then
  echo "Ollama not found. Install it from https://ollama.com then re-run."
  exit 1
fi

# Detect unified memory (GB) on macOS, else allow override via --tier.
mem_gb=0
if [[ "$(uname)" == "Darwin" ]]; then
  mem_gb=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
fi

TIER=""
EMBED_ONLY=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tier) TIER="$2"; shift 2 ;;
    --embed-only) EMBED_ONLY=1; shift ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

if [[ -z "$TIER" ]]; then
  if   (( mem_gb >= 120 )); then TIER=128
  elif (( mem_gb >= 60 ));  then TIER=64
  else TIER=32
  fi
fi

echo "Detected memory: ${mem_gb}GB. Using tier: ${TIER}GB."
echo "Models are pulled locally and never leave this machine."
echo

pull() { echo ">> ollama pull $1"; ollama pull "$1"; }

# Embeddings for local RAG (small, always useful).
pull "nomic-embed-text"
if (( EMBED_ONLY == 1 )); then echo "Embed-only done."; exit 0; fi

case "$TIER" in
  32)
    echo "32GB tier: fast, fits a laptop or entry Mac Studio."
    pull "qwen3:14b"        # primary reasoning that fits 32GB
    pull "gpt-oss:20b"      # tested alternative, fast on Apple Silicon
    pull "medgemma:4b"      # medical text + image understanding (small)
    DEFAULT="qwen3:14b"
    ;;
  64)
    echo "64GB tier: recommended for a radiology reading room."
    pull "qwen3:32b"        # primary reasoning
    pull "medgemma:27b"     # medical text + vision
    pull "gpt-oss:20b"      # fast secondary
    DEFAULT="qwen3:32b"
    ;;
  128|256|512)
    echo "${TIER}GB tier: frontier-class local models."
    pull "qwen3:32b"
    pull "llama3.3:70b"     # or qwen3:72b for higher quality
    pull "medgemma:27b"
    DEFAULT="llama3.3:70b"
    ;;
  *)
    echo "Unknown tier $TIER"; exit 1 ;;
esac

echo
echo "Done. Suggested default reasoning model for this tier: ${DEFAULT}"
echo "Point Radiology AI Assistant at it by setting the environment variables:"
echo "  export RADIOLOGY_AI_CHAT_MODEL=${DEFAULT}"
echo "  export RADIOLOGY_AI_EMBED_MODEL=nomic-embed-text"
echo "Then start the app with ./run.sh"

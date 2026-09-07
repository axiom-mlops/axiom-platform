#!/usr/bin/env bash
# Serve the diagnostician inside the trust boundary.
# Prefix caching is what makes the token economics in evals/results/RESULTS.md work:
# the system prompt and the guardrail vocabulary are identical on every call, so
# they are paid for once rather than on every incident.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3.5-9B}"
ADAPTER="${ADAPTER:-./adapters/aiops-rca-v1}"
PORT="${PORT:-8000}"

python3 -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --enable-lora \
  --lora-modules "aiops-rca=${ADAPTER}" \
  --max-lora-rank 32 \
  --enable-prefix-caching \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.90 \
  --guided-decoding-backend outlines \
  --port "$PORT"

# Structured output is requested per call with the RCA schema:
#   "guided_json": <contents of contracts/schemas/rca.schema.json>
# Constraining at decode time is why first-try schema validity is 100% locally.

#!/usr/bin/env bash
# Launch two GLM-5.3-Flash TP4/EP4 replicas plus the loopback nginx proxy on 8x B200.
#
# Public defaults (unchanged) create the standing topology:
#   replica A  glm53-serve      GPUs 0-3  :30000
#   replica B  glm53-serve-b    GPUs 4-7  :30100
#   proxy      glm53f-nginx               :8080
#
# Overrides (all optional; preserve documented defaults when unset):
#   NAME_A / NAME_B / NAME_PROXY          container names
#   PORT_A / PORT_B                       replica loopback ports
#   GPU_A  / GPU_B                        --base-gpu-id for each replica
#   RESTART                              restart policy (default unless-stopped)
#   LABELS                               comma-separated k=v labels to add to each container
#   NGINX_CONF / PROXY_CONF              host paths for the nginx config and the proxy
#                                         common include (default: this directory)
#   RUN_REPLICAS_VERIFY                  set to 0 to skip post-launch service
#                                         verification (default: verify)
#
# Set before running:
#   MODEL_DIR   host path to the pinned GLM-5.3-Flash snapshot (mounted read-only at /model)
#   IMAGE       pinned image, e.g. lmsysorg/sglang:glm-5.3-flash@sha256:...
#
# Post-launch verification (on by default) requires caller-supplied credentials:
#   GLM53F_OPENAI_BASE_URL  OpenAI base URL (default http://127.0.0.1:8080/v1)
#   GLM53F_HOST             bare host for the Anthropic route (default http://127.0.0.1:8080)
#   GLM53F_API_KEY          your endpoint key; never embedded in this script
#
# The engine command in each replica is the measured profile: TP4/EP4, FP8 KV,
# static NEXTN 5-1-6, TRT-LLM DSA, flashinfer_trtllm MoE, tokenizer-worker-num 4.
# The effective 16,384-token prefill chunk is the pinned image's boot default;
# no explicit chunk flag is passed.
#
# Every readiness and verification failure exits nonzero. A dispatched inference
# request is never retried on another replica: the proxy sets proxy_next_upstream
# off on all routes.
set -euo pipefail

MODEL_DIR="${MODEL_DIR:?set MODEL_DIR to the host path of the pinned model snapshot}"
IMAGE="${IMAGE:?set IMAGE to the pinned container image (digest)}"

NAME_A="${NAME_A:-glm53-serve}"
NAME_B="${NAME_B:-glm53-serve-b}"
NAME_PROXY="${NAME_PROXY:-glm53f-nginx}"
PORT_A="${PORT_A:-30000}"
PORT_B="${PORT_B:-30100}"
PROXY_PORT="${PROXY_PORT:-8080}"
GPU_A="${GPU_A:-0}"
GPU_B="${GPU_B:-4}"
RESTART="${RESTART:-unless-stopped}"
LABELS="${LABELS:-}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NGINX_CONF="${NGINX_CONF:-$SCRIPT_DIR/nginx.conf}"
PROXY_CONF="${PROXY_CONF:-$SCRIPT_DIR/proxy-common.conf}"
READY_TRIES="${READY_TRIES:-120}"

# Build label flags from a comma-separated k=v list.
label_flags=()
if [ -n "${LABELS:-}" ]; then
  IFS=',' read -ra _labels <<< "$LABELS"
  for _l in "${_labels[@]}"; do
    label_flags+=(--label "$_l")
  done
fi

# wait_ready <url> <label>: poll until curl succeeds or the deadline passes.
# A replica that never becomes ready is a hard failure.
wait_ready() {
  local url="$1" label="$2" try
  for ((try = 1; try <= READY_TRIES; try++)); do
    if curl -fsS -o /dev/null --max-time 3 "$url" 2>/dev/null; then
      echo "$label: ready"
      return 0
    fi
    sleep 5
  done
  echo "ERROR: $label did not become ready at $url after $READY_TRIES attempts" >&2
  return 1
}

# require_credentials: verification probes need caller-supplied values.
require_credentials() {
  : "${GLM53F_OPENAI_BASE_URL:?set GLM53F_OPENAI_BASE_URL (for example http://127.0.0.1:8080/v1)}"
  : "${GLM53F_HOST:?set GLM53F_HOST (for example http://127.0.0.1:8080)}"
  : "${GLM53F_API_KEY:?set GLM53F_API_KEY to your endpoint key}"
}

# probe_generation: a real generation through /v1/chat/completions requiring a
# non-empty visible reply.
probe_generation() {
  local resp
  resp=$(curl -fsS --max-time 120 \
    -H "Authorization: Bearer $GLM53F_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hello"}],"max_tokens":8,"reasoning_effort":"low"}' \
    "$GLM53F_OPENAI_BASE_URL/chat/completions")
  printf '%s' "$resp" | python3 -c '
import json
import sys

try:
    body = json.load(sys.stdin)
    choices = body.get("choices")
    content = choices[0].get("message", {}).get("content")
    valid = isinstance(content, str) and bool(content.strip())
except (AttributeError, IndexError, TypeError, ValueError):
    valid = False
raise SystemExit(0 if valid else 1)
' || {
    echo "ERROR: generation probe returned no visible content" >&2
    return 1
  }
  echo "generation probe: ok"
}

# probe_messages: the Anthropic Messages route.
probe_messages() {
  local resp
  resp=$(curl -fsS --max-time 120 \
    -H "Authorization: Bearer $GLM53F_API_KEY" \
    -H "x-api-key: $GLM53F_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"model":"glm-5.3-flash","max_tokens":8,"messages":[{"role":"user","content":"hello"}]}' \
    "$GLM53F_HOST/v1/messages")
  printf '%s' "$resp" | python3 -c '
import json
import sys

try:
    body = json.load(sys.stdin)
    valid = body.get("role") == "assistant"
except (AttributeError, TypeError, ValueError):
    valid = False
raise SystemExit(0 if valid else 1)
' || {
    echo "ERROR: Messages probe returned no assistant message" >&2
    return 1
  }
  echo "Messages probe: ok"
}

# probe_responses: the OpenAI Responses route.
probe_responses() {
  local resp
  resp=$(curl -fsS --max-time 120 \
    -H "Authorization: Bearer $GLM53F_API_KEY" \
    -H 'Content-Type: application/json' \
    -d '{"model":"glm-5.3-flash","input":"hello"}' \
    "$GLM53F_OPENAI_BASE_URL/responses")
  printf '%s' "$resp" | python3 -c '
import json
import sys

try:
    body = json.load(sys.stdin)
    valid = body.get("object") == "response" and body.get("status") == "completed"
except (AttributeError, TypeError, ValueError):
    valid = False
raise SystemExit(0 if valid else 1)
' || {
    echo "ERROR: Responses probe did not return a completed response" >&2
    return 1
  }
  echo "Responses probe: ok"
}

verify_service() {
  require_credentials
  probe_generation
  probe_messages
  probe_responses
}

launch_containers() {
  # Replica A (GPUs 0-3, port $PORT_A)
  docker run -d --name "$NAME_A" --network host --gpus all --restart "$RESTART" --shm-size 1g \
    "${label_flags[@]}" \
    -v "$MODEL_DIR":/model:ro \
    "$IMAGE" sglang serve --model-path /model --tp-size 4 --ep-size 4 --base-gpu-id "$GPU_A" \
    --dsa-prefill-backend trtllm --dsa-decode-backend trtllm --kv-cache-dtype fp8_e4m3 \
    --moe-runner-backend flashinfer_trtllm --disable-shared-experts-fusion \
    --speculative-algorithm NEXTN --speculative-num-steps 5 --speculative-eagle-topk 1 --speculative-num-draft-tokens 6 \
    --reasoning-parser glm45 --tool-call-parser glm47 --served-model-name glm-5.3-flash \
    --mem-fraction-static 0.85 --max-running-requests 16 --enable-metrics --tokenizer-worker-num 4 \
    --host 127.0.0.1 --port "$PORT_A"

  # Replica B (GPUs 4-7, port $PORT_B)
  docker run -d --name "$NAME_B" --network host --gpus all --restart "$RESTART" --shm-size 1g \
    "${label_flags[@]}" \
    -v "$MODEL_DIR":/model:ro \
    "$IMAGE" sglang serve --model-path /model --tp-size 4 --ep-size 4 --base-gpu-id "$GPU_B" \
    --dsa-prefill-backend trtllm --dsa-decode-backend trtllm --kv-cache-dtype fp8_e4m3 \
    --moe-runner-backend flashinfer_trtllm --disable-shared-experts-fusion \
    --speculative-algorithm NEXTN --speculative-num-steps 5 --speculative-eagle-topk 1 --speculative-num-draft-tokens 6 \
    --reasoning-parser glm45 --tool-call-parser glm47 --served-model-name glm-5.3-flash \
    --mem-fraction-static 0.85 --max-running-requests 16 --enable-metrics --tokenizer-worker-num 4 \
    --host 127.0.0.1 --port "$PORT_B"

  # Loopback nginx proxy (session-affine consistent hash over the two replicas)
  docker run -d --name "$NAME_PROXY" --network host --restart "$RESTART" \
    "${label_flags[@]}" \
    -v "$NGINX_CONF":/etc/nginx/nginx.conf:ro \
    -v "$PROXY_CONF":/etc/nginx/proxy-common.conf:ro \
    nginx:alpine
}

main() {
  launch_containers
  # Readiness: both replicas and the proxy. Any miss is a hard failure.
  wait_ready "http://127.0.0.1:${PORT_A}/ping" "replica on ${PORT_A}"
  wait_ready "http://127.0.0.1:${PORT_B}/ping" "replica on ${PORT_B}"
  wait_ready "http://127.0.0.1:${PROXY_PORT}/ping" "proxy on ${PROXY_PORT}"
  if [ "${RUN_REPLICAS_VERIFY:-1}" != "0" ]; then
    verify_service
  fi
  echo "all replicas and the proxy are ready"
}

# Sourceable for testing: only run when executed directly.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi

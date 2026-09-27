#!/usr/bin/env bash
# Non-GPU tests for the reproduction path: readiness failures exit nonzero,
# verification probes pass against the mock endpoint, exact routes and the
# no-retry contract are present, and credentials are caller-supplied only.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
FAILS=0

pass() { echo "  PASS: $1"; }
fail() { echo "  FAIL: $1"; FAILS=$((FAILS + 1)); }
# check NAME CMD...: PASS when CMD succeeds.
check() {
  local name="$1"; shift
  [ $# -ge 1 ] || { fail "$name (no command)"; return; }
  if "$@"; then pass "$name"; else fail "$name"; fi
}
# check_absent NAME PATTERN FILE: PASS only when grep finds no match (exit 1);
# a match or a grep error (missing or unreadable file) fails.
check_absent() {
  local name="$1" status
  grep -q -- "$2" "$3"; status=$?
  if [ "$status" -eq 1 ]; then pass "$name"
  elif [ "$status" -eq 0 ]; then fail "$name (found: $2)"
  else fail "$name (grep error $status on $3)"; fi
}

echo "== shell syntax =="
check "run-replicas.sh bash -n" bash -n "$HERE/../run-replicas.sh"
check "self bash -n" bash -n "$0"

echo "== exact routes in nginx.conf =="
for route in /v1/chat/completions /v1/completions /v1/models /v1/messages /v1/messages/count_tokens /v1/responses /ping; do
  check "exact location $route" grep -q "location = ${route} " "$HERE/../nginx.conf"
done
check "default 404" grep -q 'location / { return 404; }' "$HERE/../nginx.conf"
check "32m body cap" grep -q 'client_max_body_size 32m' "$HERE/../nginx.conf"

echo "== no-retry contract =="
check "proxy_next_upstream off" grep -q 'proxy_next_upstream off;' "$HERE/../proxy-common.conf"
check "proxy_buffering off" grep -q 'proxy_buffering off;' "$HERE/../proxy-common.conf"
# No inference route may inherit a retry policy from anywhere else.
check_absent "no retry directive in nginx.conf" 'proxy_next_upstream' "$HERE/../nginx.conf"

echo "== engine command parity =="
for flag in "--tokenizer-worker-num 4" "--tp-size 4" "--ep-size 4" "--moe-runner-backend flashinfer_trtllm" \
  "--kv-cache-dtype fp8_e4m3" "--speculative-num-steps 5" "--speculative-eagle-topk 1" \
  "--speculative-num-draft-tokens 6" "--max-running-requests 16" "--mem-fraction-static 0.85"; do
  count=$(grep -c -- "$flag" "$HERE/../run-replicas.sh")
  check "engine flag $flag present in both replicas (count $count)" [ "$count" -ge 2 ]
done
# The reference configuration relies on the image default chunked-prefill size (16,384).
check_absent "no explicit chunk flag (image boot default 16,384)" "--chunked-prefill-size" "$HERE/../run-replicas.sh"

echo "== readiness failure exits nonzero =="
export MODEL_DIR="/tmp/test-model-dir" IMAGE="test-image:latest"
if ! READY_TRIES=1 bash -c "
  source '$HERE/../run-replicas.sh' >/dev/null 2>&1
  wait_ready 'http://127.0.0.1:1/ping' 'dead port'
" >/dev/null 2>&1; then
  pass "dead replica readiness fails nonzero"
else
  fail "dead replica readiness exits 0"
fi

echo "== mock endpoint service =="
MOCK_PIDS=()
# shellcheck disable=SC2317,SC2329  # invoked through trap cleanup EXIT
cleanup() {
  local pid
  for pid in "${MOCK_PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

start_mock() {
  local mode="$1" port="$2"
  MOCK_MODE="$mode" python3 "$HERE/mock_endpoint.py" "$port" >/dev/null 2>&1 &
  MOCK_PIDS+=("$!")
  sleep 1
}

verify_mock() {
  local port="$1"
  bash -c "
    source '$HERE/../run-replicas.sh' >/dev/null 2>&1
    GLM53F_OPENAI_BASE_URL='http://127.0.0.1:${port}/v1' \
    GLM53F_HOST='http://127.0.0.1:${port}' \
    GLM53F_API_KEY='mock-key' \
    verify_service >/dev/null 2>&1
  "
}

MOCK_PORT=18091
start_mock ok "$MOCK_PORT"

if READY_TRIES=2 bash -c "
  source '$HERE/../run-replicas.sh' >/dev/null 2>&1
  wait_ready 'http://127.0.0.1:${MOCK_PORT}/ping' 'mock /ping'
" >/dev/null 2>&1; then
  pass "mock /ping readiness passes"
else
  fail "mock /ping readiness failed"
fi

if verify_mock "$MOCK_PORT"; then
  pass "generation + Messages + Responses probes pass on mock"
else
  fail "verify_service failed on mock"
fi

if bash -c "
  source '$HERE/../run-replicas.sh' >/dev/null 2>&1
  GLM53F_OPENAI_BASE_URL='http://127.0.0.1:${MOCK_PORT}/v1' \
  GLM53F_HOST='http://127.0.0.1:${MOCK_PORT}' \
  require_credentials >/dev/null 2>&1
" >/dev/null 2>&1; then
  fail "missing key passed credentials requirement"
else
  pass "missing key fails credentials requirement"
fi

echo "== unauthorized endpoint fails closed =="
start_mock unauthorized 18092
if verify_mock 18092 >/dev/null 2>&1; then
  fail "401 endpoint passed verification"
else
  pass "401 endpoint fails verification nonzero"
fi

echo "== malformed and unsuccessful responses fail closed =="
start_mock chat-empty 18093
if verify_mock 18093 >/dev/null 2>&1; then
  fail "empty Chat Completions content passed verification"
else
  pass "empty Chat Completions content fails verification"
fi

start_mock responses-failed 18094
if verify_mock 18094 >/dev/null 2>&1; then
  fail "failed Responses status passed verification"
else
  pass "failed Responses status fails verification"
fi

start_mock malformed-chat 18095
if verify_mock 18095 >/dev/null 2>&1; then
  fail "malformed Chat Completions JSON passed verification"
else
  pass "malformed Chat Completions JSON fails verification"
fi

echo "== no embedded credentials anywhere in reproduce/ =="
if grep -rnE '(Bearer [A-Za-z0-9_-]{16,}|sk-[A-Za-z0-9]{16,})' "$HERE/../" --include='*.sh' --include='*.py' --include='*.conf'; then
  fail "embedded credential pattern found"
else
  pass "no embedded credential patterns"
fi

echo
if [ "$FAILS" -eq 0 ]; then
  echo "repro script tests: all passed"
  exit 0
fi
echo "repro script tests: $FAILS failure(s)"
exit 1

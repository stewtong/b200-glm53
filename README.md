<!-- register: public technical recipe | reader: SGLang operators and GPU performance engineers | consumed: GitHub reference -->

# GLM-5.3-Flash on 8x B200

A reference SGLang deployment for GLM-5.3-Flash on one 8x NVIDIA B200 node. It runs two independent SGLang servers, each with tensor parallel size 4 and expert parallel size 4 (TP4/EP4), behind a session-affine nginx reverse proxy. The servers expose the OpenAI-compatible Chat Completions API, the OpenAI Responses API, and the Anthropic Messages API for interactive coding-agent traffic. `BENCHMARKS.md` documents the measurements used to select this configuration.

This repository provides launch scripts, proxy configuration, a benchmark client, and sanitized results. You supply the model files, B200 node, container runtime, and credentials. Host values are placeholders, and every `127.0.0.1` listener is bound to loopback.

## Run the reference deployment

Prerequisites: one 8x B200 node with the container runtime and NVIDIA GPU support, the pinned model snapshot on local disk, `curl`, and Python 3 for response verification and the benchmark client. Two loopback engine ports (30000, 30100) and one loopback proxy port (8080) are used.

| Pinned input | Value |
| --- | --- |
| Model | [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash) at revision `3f1971b7b5f7a528c9c4ef6212c8785298a8c24a`, served as `glm-5.3-flash` |
| Container | `lmsysorg/sglang:glm-5.3-flash@sha256:3a97bd50034ca60c6e6c86b8e36a73675d261f6a5eb71197796aee5175409290` |
| SGLang runtime | `0.0.0.dev1+gd6ab04bdf1`, source commit [`d6ab04bdf1`](https://github.com/sgl-project/sglang/commit/d6ab04bdf1) |
| Driver | 580.173.02 |

Launch both SGLang servers and the loopback proxy:

```bash
export MODEL_DIR="/path/to/GLM-5.3-Flash"
export IMAGE="lmsysorg/sglang:glm-5.3-flash@sha256:3a97bd50034ca60c6e6c86b8e36a73675d261f6a5eb71197796aee5175409290"
reproduce/run-replicas.sh
```

The script starts server A on GPUs 0-3 at port 30000, server B on GPUs 4-7 at port 30100, and nginx on port 8080. Each server is an independent replica. The script waits for both servers and the proxy to become ready, then runs verification probes. Any readiness or verification failure exits nonzero. Set `RUN_REPLICAS_VERIFY=0` to skip the post-launch probes.

Each server runs this command; only `--base-gpu-id` and `--port` differ:

```bash
sglang serve --model-path /model --tp-size 4 --ep-size 4 --base-gpu-id {0|4} \
  --dsa-prefill-backend trtllm --dsa-decode-backend trtllm \
  --kv-cache-dtype fp8_e4m3 --moe-runner-backend flashinfer_trtllm \
  --disable-shared-experts-fusion \
  --speculative-algorithm NEXTN --speculative-num-steps 5 \
  --speculative-eagle-topk 1 --speculative-num-draft-tokens 6 \
  --reasoning-parser glm45 --tool-call-parser glm47 \
  --served-model-name glm-5.3-flash --mem-fraction-static 0.85 \
  --max-running-requests 16 --enable-metrics --tokenizer-worker-num 4 \
  --host 127.0.0.1 --port {30000|30100}
```

The pinned image starts each replica with an FP8 E4M3 KV cache holding 7,362,048 tokens (48.57 GB), 446 KDA/Mamba state slots, a chunked prefill size of 16,384 tokens, and a maximum of 16 running requests. The chunked prefill size comes from the image default; the command does not set `--chunked-prefill-size`. Do not set a server-wide `reasoning_effort` through `--default-chat-template-kwargs`. The deployment accepts effort per request, and an omitted value uses the checkpoint template's `max` setting.

`reproduce/nginx.conf` and `reproduce/proxy-common.conf` define the loopback proxy. They use exact-match locations for seven routes, consistent hashing on `x-claude-code-session-id`, `proxy_buffering off`, a 32 MiB body limit, 3,000-second upstream timeouts, and `proxy_next_upstream off`. A dispatched inference request is therefore not retried on the other replica. nginx binds to loopback only; place an authenticated TLS-terminating ingress in front of it and keep bearer keys outside the repository. Consistent hashing is not load-aware and can send too many heavy sessions to one replica. Use a load-aware proxy if the workload requires it.

## Verify the service

The launch script runs these probes itself when credentials are supplied:

```bash
export GLM53F_OPENAI_BASE_URL="http://127.0.0.1:8080/v1"
export GLM53F_HOST="http://127.0.0.1:8080"
export GLM53F_API_KEY="<your-key>"
reproduce/run-replicas.sh   # readiness + generation, Messages, and Responses probes
```

| Check | Route | Passes when |
| --- | --- | --- |
| Liveness | `/ping` | HTTP 200 (authenticated in the reference deployment; a probe must send a key) |
| Generation | `/v1/chat/completions` | Non-empty visible reply |
| Anthropic Messages | `/v1/messages` | Assistant message returned |
| OpenAI Responses | `/v1/responses` | Completed response object |
| Authentication | any | Missing or bogus key returns 401 |
| Body limit | any | Oversized body returns 413 |

The generation probe requires visible response text. HTTP 200 alone can hide a reasoning-only completion, a truncated reply, or a stream buffered until generation ends.

## How the benchmark results selected the configuration

| Reference setting | Selected by | Benchmark record |
| --- | --- | --- |
| Two TP4/EP4 replicas | 128K topology cell (historical) | [Topology](BENCHMARKS.md#why-dual-tp4ep4-historical-topology-selection); both arms used a different MoE backend and speculative decoding policy from the reference configuration, and no matched current-stack TP4-versus-TP8 cell exists |
| `flashinfer_trtllm` MoE | 32K backend cell | [MoE backend](BENCHMARKS.md#moe-backend-selection) |
| Static NEXTN speculative decoding: 5 steps, top-k 1, 6 draft tokens | 32K synthetic cell and a later replay of captured request shapes | [Speculative decoding](BENCHMARKS.md#speculative-decoding) |
| Chunked prefill size of 16,384 tokens | 128K and 400K chunk sweep | [Chunked prefill](BENCHMARKS.md#prefill-chunk-selection) |
| Session affinity on `x-claude-code-session-id` | nginx routing requirements and the prefix-cache study | [Prefix reuse](BENCHMARKS.md#prefix-reuse-and-session-affinity) |
| Four tokenizer workers | Near-1M-token preprocessing A/B | [Tokenizer workers](BENCHMARKS.md#tokenizer-workers-and-hardening) |

## Serving benchmark results

The August 28 serving benchmark tested six combinations of input length and request concurrency, with three runs each: about 131K input tokens at concurrency 1 and 16, about 410K at concurrency 8, about 524K at concurrency 16 with 2,048 output tokens, and about 1M at concurrency 1 and 4 with 512 output tokens. All 138 requests returned HTTP 200 and the requested number of output tokens, with zero timeouts and zero HTTP 429 responses. At concurrency 1, output tokens divided by end-to-end latency, including prefill, had a median of 245.29 tok/s at about 131K input tokens and 12.50 tok/s at about 1M. Aggregate output token throughput, time per output token (TPOT), and inter-token latency (ITL) are not reported because the retained timestamps cannot calculate them.

Sanitized request-level records reproduce every reported value:

```bash
python3 reproduce/derive-results.py            # verify staged summaries
python3 reproduce/derive-results.py --self-test
```

`reproduce/benchmark.py` runs the same request contract against a live endpoint. It reads `GLM53F_OPENAI_BASE_URL` and `GLM53F_API_KEY` from the environment and records failed requests, timeouts, HTTP 429 responses, and integrity violations.

## Additional benchmark coverage

`BENCHMARKS.md` covers the historical topology, MoE backend, speculative decoding, and chunked prefill tests; context-length and concurrency tests; the prefix-cache and session-affinity study; the tokenizer-worker A/B test and hardening checks; GSM8K, identifier, and coding-trace correctness checks; a limited GLM-versus-DSV4 endpoint comparison; and all stopped, invalid, or unrun work. Campaign manifests and the machine-readable summary are in `results/`. [SGLang discussion #37153](https://github.com/sgl-project/sglang/discussions/37153) summarizes the work as of August 30, 2026. This repository contains the underlying sanitized evidence and manifests.

## API and client behavior

OpenAI-compatible use:

```bash
export GLM53F_API_KEY="<your-key>"
export GLM53F_OPENAI_BASE_URL="http://127.0.0.1:8080/v1"

curl "$GLM53F_OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $GLM53F_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hello"}],"max_tokens":512,"reasoning_effort":"low"}'
```

```python
import os
from openai import OpenAI

client = OpenAI(
    base_url=os.environ["GLM53F_OPENAI_BASE_URL"],
    api_key=os.environ["GLM53F_API_KEY"],
    timeout=180,
)
response = client.chat.completions.create(
    model="glm-5.3-flash",
    messages=[{"role": "user", "content": "hello"}],
    max_tokens=512,
    extra_body={"reasoning_effort": "low"},
)
print(response.choices[0].message.content)

# The Responses route is served natively on this profile:
resp = client.responses.create(model="glm-5.3-flash", input="hello")
print(resp.output_text)
```

Claude Code, with variables scoped to the launched process:

```bash
export GLM53F_API_KEY="<your-key>"
export GLM53F_HOST="http://127.0.0.1:8080"

env \
  ANTHROPIC_BASE_URL="$GLM53F_HOST" \
  ANTHROPIC_AUTH_TOKEN="$GLM53F_API_KEY" \
  ANTHROPIC_DEFAULT_OPUS_MODEL=glm-5.3-flash \
  ANTHROPIC_DEFAULT_SONNET_MODEL=glm-5.3-flash \
  ANTHROPIC_DEFAULT_HAIKU_MODEL=glm-5.3-flash \
  CLAUDE_CODE_MAX_CONTEXT_TOKENS=1000000 \
  CLAUDE_CODE_MAX_OUTPUT_TOKENS=32768 \
  CLAUDE_CODE_ATTRIBUTION_HEADER=0 \
  API_TIMEOUT_MS=3000000 \
  CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
  claude
```

Keep `ANTHROPIC_BASE_URL` at the bare host because Claude Code appends `/v1/messages`; adding `/v1` produces `/v1/v1/messages` and a 404. The context and output caps sum to 1,032,768, leaving 15,808 tokens below the model's combined limit. The variable names were verified with the August 28, 2026 Claude Code release and may change in later releases. `CLAUDE_CODE_ATTRIBUTION_HEADER=0` stops Claude Code from prepending a per-request attribution block, which would otherwise be the first token to diverge between turns and defeat prefix-cache reuse.

Context arithmetic: input and output share the 1,048,576-token model limit. Set the input cap below that limit so requested output tokens still fit. Claude Code sends the full conversation on each Anthropic Messages request, so long sessions must also fit the nginx body limit and the client and proxy timeouts.

Reasoning effort: set `reasoning_effort` on every OpenAI request (`low` for direct answers, `high` for harder tasks). An omitted value resolves to the checkpoint template default of `max` on this deployment and can spend much of the output budget on reasoning. Use Claude Code's default adaptive-thinking profile; explicit `thinking: disabled` can place reasoning in the visible text, and explicit `thinking: enabled` returns HTTP 400 on this runtime.

Images: the runtime accepts image input. Serialized request bytes count against the 32 MiB nginx body limit, and processed visual tokens count against the model context. Test both limits with your media sizes. Image input was not tested across every client or request shape.

Streaming and limits: keep `proxy_buffering off`; without it the first byte arrives only after full generation. Use a client timeout of at least 180 seconds for large requests. nginx returns its default error bodies for 401, 404, 413, 429, 502, and 504 responses.

## Limits of the measurements

- The tested load matrix does not establish maximum capacity. No saturation search ran, and no measured cell exceeded concurrency 16.
- Aggregate output token throughput and TPOT are not reported because the retained timestamps cannot calculate them. `CLAIM-LEDGER.md` records these missing metrics.
- No matched current-stack TP4-versus-TP8 cell, no local BF16-KV control, no accepted W11 request-cap comparison, no accepted route comparison, and no direct cache-hit attribution exist. `BENCHMARKS.md` records each so partial coverage is visible.
- Maximum memory-limited concurrency at 128K input is unmeasured because the configured 16-request limit applies first.

## Repository contents

- `BENCHMARKS.md`: the complete campaign record
- `CLAIM-LEDGER.md`: claim-to-source map with evidence status
- `reproduce/`: launch, proxy, benchmark, and derivation assets
- `results/`: sanitized evidence, campaign manifests, environment, checksums

Contributions are welcome. The model, SGLang, and any dataset keep their own licenses; nothing here redistributes them.

Keep API keys out of documentation, logs, shell history, and process arguments. TLS should terminate with a publicly trusted certificate and verification enabled. Gateway access logs carry request metadata only; a TLS-terminating ingress can still read request and response content. Treat `/ping` as process liveness and use generation probes when validating model health.

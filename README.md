<!-- register: public technical recipe | reader: SGLang operators and GPU performance engineers | consumed: GitHub reference -->

# GLM-5.3-Flash on 8x B200

Measured SGLang serving profile for GLM-5.3-Flash on one 8x NVIDIA B200 node. The configuration runs two independent TP4/EP4 replicas behind a session-affine reverse proxy and supports OpenAI-compatible Chat Completions plus the Anthropic Messages API.

This repository documents a deployment pattern and its measured behavior. It contains no hosted endpoint, credentials, infrastructure identifiers, or production automation.

The current [upstream SGLang B200 recipe](https://github.com/sgl-project/sglang/blob/9fc60a8afd64b135342a7313bf611285aeab7208/docs/src/snippets/configs/zai-org/glm-5.3-flash.jsx#L498-L541) uses a single TP8/EP8 engine with `deep_gemm` and adaptive EAGLE for its verified low-latency profile. The configuration here is a separately measured dual-replica alternative for interactive traffic and session-affine cache locality.

## Validated serving profile

| Setting | Validated value |
| --- | --- |
| Model | [`zai-org/GLM-5.3-Flash`](https://huggingface.co/zai-org/GLM-5.3-Flash), served as `glm-5.3-flash` |
| Hardware | 8x NVIDIA B200, driver 580.173.02 |
| Topology | Two independent TP4/EP4 replicas, GPUs 0-3 and 4-7 |
| Container | `lmsysorg/sglang:glm-5.3-flash@sha256:3a97bd50034ca60c6e6c86b8e36a73675d261f6a5eb71197796aee5175409290` |
| SGLang runtime | `0.0.0.dev1+gd6ab04bdf1`, source commit [`d6ab04bdf1`](https://github.com/sgl-project/sglang/commit/d6ab04bdf1) |
| DSA backends | TRT-LLM for prefill and decode |
| MoE runner | `flashinfer_trtllm` |
| KV cache | `fp8_e4m3` |
| Speculative decoding | Static NEXTN 5-1-6 |
| Context | 1,048,576 input and output tokens combined |
| Per-replica admission | 16 running requests |
| Validation dates | August 27-28, 2026 |

The measured deployment loaded a cached copy of the model without recording its Hugging Face commit. The container digest and SGLang source are exact; the checkpoint revision is an explicit reproducibility boundary. Pin a model commit before using this recipe for a new measurement.

## Engine commands

Run both commands inside the pinned container image with all eight GPUs visible. Mount a pinned model snapshot at `/model`. The commands differ only in GPU base ID and port.

Replica A:

```bash
sglang serve \
  --model-path /model \
  --tp-size 4 \
  --ep-size 4 \
  --base-gpu-id 0 \
  --dsa-prefill-backend trtllm \
  --dsa-decode-backend trtllm \
  --kv-cache-dtype fp8_e4m3 \
  --moe-runner-backend flashinfer_trtllm \
  --disable-shared-experts-fusion \
  --speculative-algorithm NEXTN \
  --speculative-num-steps 5 \
  --speculative-eagle-topk 1 \
  --speculative-num-draft-tokens 6 \
  --reasoning-parser glm45 \
  --tool-call-parser glm47 \
  --served-model-name glm-5.3-flash \
  --mem-fraction-static 0.85 \
  --max-running-requests 16 \
  --enable-metrics \
  --host 127.0.0.1 \
  --port 30000
```

Replica B:

```bash
sglang serve \
  --model-path /model \
  --tp-size 4 \
  --ep-size 4 \
  --base-gpu-id 4 \
  --dsa-prefill-backend trtllm \
  --dsa-decode-backend trtllm \
  --kv-cache-dtype fp8_e4m3 \
  --moe-runner-backend flashinfer_trtllm \
  --disable-shared-experts-fusion \
  --speculative-algorithm NEXTN \
  --speculative-num-steps 5 \
  --speculative-eagle-topk 1 \
  --speculative-num-draft-tokens 6 \
  --reasoning-parser glm45 \
  --tool-call-parser glm47 \
  --served-model-name glm-5.3-flash \
  --mem-fraction-static 0.85 \
  --max-running-requests 16 \
  --enable-metrics \
  --host 127.0.0.1 \
  --port 30100
```

Do not add a server-wide `--default-chat-template-kwargs` value for `reasoning_effort`. The measured deployment accepts effort per request. An omitted effort falls back to the checkpoint template's `max` setting.

## Session-affine proxy

The two replicas do not share radix cache state. When a client sends a stable `x-claude-code-session-id`, hashing that value keeps its requests on one replica and preserves prefix-cache locality. Requests without that header receive a request-specific key. Session-only and session-plus-agent hashing were not compared in this measurement.

The following nginx fragment binds only to loopback. Put an authenticated TLS ingress in front of it. Keep bearer keys outside the repository and do not expose this listener directly.

```nginx
map $http_x_claude_code_session_id $sticky_key {
    ""      $request_id;
    default $http_x_claude_code_session_id;
}

upstream glm53f {
    hash $sticky_key consistent;
    server 127.0.0.1:30000;
    server 127.0.0.1:30100;
    keepalive 32;
}

server {
    listen 127.0.0.1:8080;
    client_max_body_size 32m;
    client_body_buffer_size 8m;

    location = /v1/chat/completions { proxy_pass http://glm53f; include /etc/nginx/glm53f-proxy.conf; }
    location = /v1/completions      { proxy_pass http://glm53f; include /etc/nginx/glm53f-proxy.conf; }
    location = /v1/models           { proxy_pass http://glm53f; include /etc/nginx/glm53f-proxy.conf; }
    location = /v1/messages         { proxy_pass http://glm53f; include /etc/nginx/glm53f-proxy.conf; }
    location = /v1/messages/count_tokens { proxy_pass http://glm53f; include /etc/nginx/glm53f-proxy.conf; }
    location = /ping                { proxy_pass http://glm53f; include /etc/nginx/glm53f-proxy.conf; }
    location / { return 404; }
}
```

Create `/etc/nginx/glm53f-proxy.conf` with:

```nginx
proxy_buffering off;
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_read_timeout 3000s;
proxy_send_timeout 3000s;
```

`proxy_buffering off` preserves streaming behavior. The 32 MiB request cap accommodates long text sessions while bounding ingress memory and disk exposure. Payload bytes and model tokens are different limits, especially for images.

## Measured performance

The table reports per-session decode-window output tokens per second. Concurrent sessions were split evenly across the two replicas and then averaged. The 128K through 512K cells used 2,048-token outputs. The approximately 1M cell used 990,834 rendered input tokens and 512-token outputs on one replica. All requests set `reasoning_effort: low`.

| Input context | 1 concurrent | 8 concurrent | 16 concurrent |
| ---: | ---: | ---: | ---: |
| 128K | 323 tok/s | 116 tok/s | 68 tok/s |
| 400K | 313 tok/s | 78 tok/s | 33 tok/s |
| 512K | 323 tok/s | 71 tok/s | 25 tok/s |
| About 1M | 288 tok/s | Not measured | Not measured |

Single-session decode remained between 313 and 323 tok/s from 128K through 512K. That shape is consistent with a weight-bandwidth-bound batch-1 decode path, but no profiler trace was collected to establish the mechanism.

Eight staggered coding sessions with repeated prefixes produced a median 350 tok/s per session and 0.25 seconds median warm time to first token. That traffic-shape run used shorter mixed contexts and warm prefix-cache hits, so it is not comparable to the synchronized table cells.

A cold 990,834-token prompt took about 40 seconds to first token at concurrency 1. Six of six requests completed at each tested concurrency from one through four. A cached repeat took about 4.3 seconds to first token.

These measurements came from a custom internal harness. Raw traces and the harness are not published in this repository. Independent reproduction requires an equivalent harness and prompt set.

## OpenAI-compatible client

```bash
export GLM53F_API_KEY="<your-key>"
export GLM53F_OPENAI_BASE_URL="https://<your-endpoint-host>/v1"

curl "$GLM53F_OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $GLM53F_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hello"}],"max_tokens":512,"reasoning_effort":"low"}'
```

Python with the OpenAI SDK:

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
```

The proxy exposes `/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/v1/messages`, `/v1/messages/count_tokens`, and `/ping`. Keep `/ping` behind the same authenticated ingress as the inference routes. The OpenAI SDK's `responses.create()` route is not available in this profile.

## Claude Code client

Run Claude Code with variables scoped to that process:

```bash
export GLM53F_API_KEY="<your-key>"
export GLM53F_HOST="https://<your-endpoint-host>"

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

Keep `ANTHROPIC_BASE_URL` at the bare host because Claude Code appends `/v1/messages`. The context and output caps sum to 1,032,768, leaving 15,808 tokens below the model's combined 1,048,576-token limit. The variable names were verified with Claude Code on August 28, 2026 and may change in later releases.

Claude Code sends the full conversation on each Anthropic Messages request. Streaming and request-body limits therefore affect long sessions. Keep `proxy_buffering off` and set the ingress body cap deliberately.

Image input is supported. Serialized image bytes count against the 32 MiB proxy cap, while processed visual tokens count against model limits. Test both boundaries for the media sizes used by your application.

Use Claude Code's default adaptive-thinking profile. On the validated runtime, explicit `thinking: disabled` can place reasoning in visible response text, while explicit `thinking: enabled` returns HTTP 400. The normal Claude Code path sends adaptive thinking and did not trigger either behavior in the compatibility checks.

## Operational limits

- Stream long responses and use a client timeout of at least 180 seconds for the validated profile.
- Keep API keys out of documentation, logs, shell history, and process arguments.
- Terminate TLS with a publicly trusted certificate and keep certificate verification enabled.
- Log request metadata only. A TLS-terminating ingress can still read request and response content.
- Treat `/ping` as process liveness. Use generation probes separately when validating model health.

`count_tokens` reports token counts and does not perform generation admission. Tests accepted two 10 MiB requests containing 1,310,732 counted tokens, above the model's generation context. A 40 MiB request returned HTTP 413 at the proxy. Normal text generation remains bounded by the combined 1,048,576-token input and output context.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| `401` | Missing or incorrect bearer key at the authenticated ingress |
| `404` from `/v1/v1/messages` | `/v1` was added to `ANTHROPIC_BASE_URL` |
| `404` from `responses.create()` | The Responses API is outside this serving profile |
| `413` | Serialized request body exceeds the proxy cap |
| Timeout on a large request | Client or proxy timeout is shorter than prefill plus generation |
| `400` with explicit thinking | `thinking: enabled` is unsupported on the validated runtime |
| Reasoning appears in visible text | The client sent `thinking: disabled` |
| `certificate verify failed` | A local CA override or trust setting is active |

## License

Licensed under the Apache License 2.0. See [`LICENSE`](LICENSE).

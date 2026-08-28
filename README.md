# GLM-5.3-Flash on B200

Serving recipe and performance notes for running GLM-5.3-Flash on a dedicated 8x B200 node behind an OpenAI-compatible gateway. The endpoint accepts text and image inputs through OpenAI-compatible Chat Completions and the Anthropic Messages API.

|                             |                                        |
| --------------------------- | -------------------------------------- |
| Model                       | `glm-5.3-flash`                        |
| Deployment                  | Two independent TP4 replicas with session-sticky routing |
| Backend context             | 1,048,576 tokens, validated directly on both replicas |
| Current Claude Code profile | 1,000,000 input tokens and 32,768 output tokens |
| Base URL                    | `https://<your-endpoint-host>`         |
| Authentication              | `Authorization: Bearer <your key>`     |

The endpoint uses a publicly trusted TLS certificate. Keep certificate verification enabled.

Direct backend tests completed at 990,834 rendered input tokens. The gateway body cap is 32 MiB. Public-path checks counted 1,000,013 tokens in a 5,000,073-byte request, completed a cold 2,552,139-byte generation in 37.353 seconds, and accepted two 10 MiB `count_tokens` requests with 1,310,732 tokens. A 40 MiB request returned a clean 413. The model's 1,048,576-token generation context is the binding limit for normal text sessions.

## Performance

Measurements were taken with `reasoning_effort: low`. Rates are output tokens per second for each active session.

| Input context | 1 concurrent | 8 concurrent | 16 concurrent |
| ---: | ---: | ---: | ---: |
| 128K | 323 tok/s | 116 tok/s | 68 tok/s |
| 400K | 313 tok/s | 78 tok/s | 33 tok/s |
| 512K | 323 tok/s | 71 tok/s | 25 tok/s |
| About 1M | 288 tok/s | Not measured | Not measured |

Repeated-prefix coding traffic at eight concurrent sessions produced a median 350 tok/s per session and 0.25 s warm time to first token. A cold 1M prompt took about 40 s to first token; a cached repeat took about 4.3 s.

Direct OpenAI and Anthropic tests returned zero errors on both replicas across short, 32K, 128K, and 990,834-token prompts. Anthropic warm time to first token stayed within about 3% of OpenAI in every measured cell. The sample establishes functional compatibility; larger samples are required for p95 non-inferiority.

## OpenAI-compatible use

```bash
export GLM53F_API_KEY="<your-key>"
export GLM53F_OPENAI_BASE_URL="https://<your-endpoint-host>/v1"

curl "$GLM53F_OPENAI_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $GLM53F_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3-flash","messages":[{"role":"user","content":"hello"}],"max_tokens":64,"reasoning_effort":"low"}'
```

Set `reasoning_effort` on every request. Use `low` for fast, direct answers and `high` for harder tasks. An omitted value defaults to `max` and may spend much of the output budget on reasoning.

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
    max_tokens=64,
    extra_body={"reasoning_effort": "low"},
)
print(response.choices[0].message.content)
```

Supported routes are `/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/v1/messages`, and `/v1/messages/count_tokens`. The OpenAI SDK's `responses.create()` route is not available.

## Claude Code

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

Keep `ANTHROPIC_BASE_URL` at the bare host. Claude Code appends `/v1/messages`; adding `/v1` to the base URL produces `/v1/v1/messages` and a 404. These variables apply only to the launched process.

**Reverse proxy notes (Anthropic Messages route).** Claude Code is stateless, so it resends the whole conversation on every turn, and each turn streams its answer. Two nginx settings matter for that. First, `proxy_buffering off`: without it nginx holds the entire response before sending the first byte, so time to first token equals full generation time and a coding terminal feels dead. Second, a raised `client_max_body_size` (this deployment uses 32 MiB with an 8 MiB buffer): nginx's default 1 MiB limit hard-rejects long sessions around 130-200K tokens, because the whole conversation is resent each turn. A reverse proxy in front of this endpoint needs both settings.

Image input is supported. Keep the total serialized request under the 32 MiB gateway body cap; at typical serialization rates that is several million tokens, so the model's 1,048,576-token context, not the gateway, is the binding limit.

Use Claude Code's default adaptive-thinking profile. Explicit `thinking: disabled` can expose reasoning in the visible response, and explicit `thinking: enabled` currently returns 400 on this deployment.

## Limits and data handling

- Stream long responses and use a client timeout of at least 180 seconds for large requests.
- Authenticate with a bearer key; keep key values out of docs, logs, and shell history.
- Gateway access logs contain request metadata, not request or response bodies. TLS terminates at the gateway edge, which can read request and response content.
- A Claude Code `unrecognized_model` warning is informational and does not block inference.

| Symptom | Likely cause |
| --- | --- |
| `401` | Missing or incorrect bearer key |
| `404` from `/v1/v1/messages` | `/v1` was added to `ANTHROPIC_BASE_URL` |
| `413` | Serialized request body exceeds the 32 MiB gateway cap |
| Timeout on a large request | Client timeout is below 180 seconds |
| `400` with explicit thinking | `thinking: enabled` is unsupported on this deployment |
| Reasoning appears in visible text | Client sent `thinking: disabled` |
| `certificate verify failed` | A local CA override or trust setting is active |

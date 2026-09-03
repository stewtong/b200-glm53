# Deployment verification, September 2, 2026

The standing endpoint was re-verified after the September 2 tunnel migration to
a project-scoped parent. This record describes the current serving state and is
separate from every historical benchmark environment: the August 27-28
benchmark runs predate the four-tokenizer-worker adoption, the September 2
tunnel migration, and the current ingress state.

## What was verified

- Both TP4/EP4 replicas run the standing engine command with
  `--tokenizer-worker-num 4`, pinned image, and static NEXTN 5-1-6.
- A 24/24 route-and-auth matrix passed twice, more than five minutes apart:
  authenticated 200 on the OpenAI Chat Completions, Anthropic Messages, and
  OpenAI Responses routes; 401 on missing or bogus keys; exact-route matching
  intact with extra route suffixes returning 404.
- `/ping` remains the authenticated liveness route; `/health` submits a real
  one-token generation and is not a cheap probe.
- The 32 MiB ingress body cap is in place with an 8 MiB request buffer.

## Standing configuration

| Setting | Value |
| --- | --- |
| Replicas | Two independent TP4/EP4, GPUs 0-3 and 4-7 |
| MoE / DSA | `flashinfer_trtllm` MoE; TRT-LLM DSA prefill and decode |
| Speculative decoding | Static NEXTN 5-1-6, top-k 1 |
| KV cache | FP8 E4M3 |
| Prefill chunk | 16,384 tokens in the effective boot state |
| Tokenizer workers | 4 per replica (both replicas, since August 30, 2026) |
| Request ceiling | 16 running requests per replica |
| Reasoning effort | Per request; omitted effort resolves to the checkpoint default of max |
| Routes | `/v1/chat/completions`, `/v1/completions`, `/v1/models`, `/v1/messages`, `/v1/messages/count_tokens`, `/v1/responses`, `/ping` |
| Ingress | Session-affine loopback nginx; 32 MiB body cap; metadata-only access logs |

## What this record does not establish

No new benchmark, capacity measurement, or performance comparison was run for
this verification. Concurrency, throughput, and latency claims live in
`BENCHMARKS.md` under the campaigns that measured them, in their own dated
environments.

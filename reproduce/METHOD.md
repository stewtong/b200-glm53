# Methodology

This file records how the measured results in this repository were produced and
what each number does and does not mean. Every campaign has a machine-readable
manifest under `results/manifests/` with its full configuration identity, and
`CLAIM-LEDGER.md` maps every claim to its evidence class.

## Serving envelope request contract

Every audited serving-envelope request used `/v1/chat/completions` with
streaming, `temperature: 0`, `reasoning_effort: low`, `min_tokens = max_tokens`,
`ignore_eos: true`, and a fixed output length. A stable system message and one
user message of cache-busted public-domain Federalist prose formed each prompt
(`reproduce/corpus-federalist.txt`, SHA-256
`5a06783f628ed6faf07d78a8b7bf0f9fd1dc22ad13edc2bd5eeba0102af02690`). Each
request is cache-busted so repeated requests do not reuse radix cache from a
prior request, except in the cache-affinity study, which intentionally reuses
a prefix.

Response integrity requires HTTP 200, a final usage record, non-empty visible
output, no corruption signature, `finish_reason = length`, and an exact
completion token count. Requests that time out or return HTTP 429 remain in
the success denominator; a malformed response is a hard failure.

`reproduce/benchmark.py` implements this contract. It requires
`GLM53F_OPENAI_BASE_URL` and `GLM53F_API_KEY` from the caller's environment
and never embeds credentials.

## Cells and repetition

The six serving-envelope cells are:

| Cell | Input tokens (rendered) | Concurrency | Active replicas | Output tokens/request |
| --- | ---: | ---: | --- | ---: |
| H1 | 131,102-131,103 | 1 | A | 2,048 |
| H2 | 131,100-131,107 | 16 | A+B, split 8/8 | 2,048 |
| H3 | 409,630-409,634 | 8 | A+B, split 4/4 | 2,048 |
| H4 | 524,318-524,325 | 16 | A+B, split 8/8 | 2,048 |
| H5 | 1,000,032-1,000,034 | 1 | A | 512 |
| H6 | 1,000,030-1,000,034 | 4 | A+B, split 2/2 | 512 |

Each cell used three independent repetitions after one unreported warmup and an
explicit cache flush. Reported values are the median over repetitions.

## Metric definitions

- **End-to-end output rate** = output tokens divided by full request wall time,
  including prefill. Reported only for the concurrency-1 cells (H1 and H5).
- **Success** = a request that returned HTTP 200 with the requested completion
  length; failures, timeouts, and HTTP 429 are counted in the denominator.

Aggregate concurrent throughput, decode-time per-token latency (TPOT), and
inter-token latency (ITL) are excluded. The retained request records lack
absolute request-start clocks for reconstructing a true batch makespan, and the
recorded stream events lack per-output-event clocks. These metrics are
therefore not published rather than published with an unprovable method.

## September 27 campaign metrics

The September 27 records (`results/holistic-20260927/raw/`) keep a monotonic
dispatch and completion offset for every request within its repetition, so that
campaign reports three measures. Each repetition at concurrency c dispatches c
requests at once and waits for all of them; no request is replaced.

- **Burst output rate** = completion tokens of successful requests divided by
  the span from the first dispatch to the last completion in a repetition. It
  includes prefill and drain, so at long context it is dominated by prefill and
  does not measure sustained throughput under continuous load.
- **Request output rate** = one request's completion tokens divided by its
  full wall time, including prefill.
- **TTFT p50** = time to the first streamed output event, reasoning or
  visible. Grid, TP8, request-cap, and route cells report the median across
  repetitions of each repetition's median; warm-extension turns report the
  median across all turns of a kind.

Success adds a no-timeout condition to the rule above. Output length is fixed
with `ignore_eos` and `max_tokens`. `reproduce/derive-results.py` recomputes
all three measures, the per-repetition ranges, and the GSM8K paired
comparison from the rows.

## Correctness (GSM8K)

The GSM8K evaluation used the full 1,319-problem test set
(SHA-256 `3730d312f6e3440559ace48831e51066acaca737f6eabec99bccb9e4b3c39d14`) at
`temperature 0`. Scoring extracts the final numeric value after the last
`Answer` marker and compares it numerically with the reference answer in the
test set.

- Comparator condition: no `reasoning_effort` value (resolves to the checkpoint
  default, max), 2,048-token output limit.
- Low-effort condition: `reasoning_effort: low`, 1,024-token output limit.

The two conditions are reported separately and are not pooled. Both are
FP8-KV reasoning-effort conditions; neither is a precision comparison, and no
local BF16 control was run.

## Cache-affinity study

The cache study used ingress `/v1/chat/completions` requests and flushed both
replica caches before each measured turn. Each condition has three repetitions
with three sessions and four turns (36 requests per condition). TTFT-any is the
time to the first streamed output event, whether reasoning or visible content.
Within a repetition, turn 1 is the median across three sessions and turns 2-4
are the median across nine requests. The ratio is computed within each
repetition before the median is taken across repetitions.

The accepted evidence supports a latency association. Consistent hashing
preserves a stable placement key by configuration; direct per-request cache
attribution was not measured.

## Configuration-selection retune

The W14/W12 retune replayed captured coding-agent request shapes against the
deployed static NEXTN 5-1-6 control, three narrower static widths at
concurrency 1, and adaptive speculation at concurrency 1 and 8, in fresh
temporary containers: three repetitions of 24 eligible rows per point,
decode-window TPOT from streamed responses, opening and closing 5-1-6 controls
bracketing the campaign with a 5% drift bound. These repetition-level records
are published under `results/config-selection/`. The earlier synthetic
speculation-selection cell used different prompts, routes, loads, and timing;
the two campaigns are never averaged.

## Tokenizer-worker A/B

The tokenizer-worker A/B drove one four-worker replica and one one-worker
replica with the same near-990K-token `count_tokens` request (990,819 counted
tokens), sampling authenticated `/ping` during preprocessing and running
concurrent short generations, then revalidating streaming delivery and
disconnected-client recovery. It is operational hardening evidence, not a
throughput benchmark.

## Provenance and license boundaries

The corpus is public-domain Federalist text. The GSM8K test set, the model
checkpoint `zai-org/GLM-5.3-Flash`, and SGLang retain their own licenses and
are not redistributed by this repository, which is MIT licensed (`LICENSE`).
Dataset and corpus SHA-256 values are recorded alongside each campaign in
`results/manifests/`.

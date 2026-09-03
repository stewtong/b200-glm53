<!-- register: public benchmark record | reader: SGLang operators and performance engineers | consumed: configuration-decision review -->

# Benchmarks

The complete campaign record behind the serving configuration in `README.md`:
what was tested, which controls were matched, which results selected the
standing settings, and which comparisons remain unresolved. Every campaign has
a machine-readable manifest under `results/manifests/`; the compact matrix is
`results/benchmark-summary.json`. Claim-level sources and boundaries are in
`CLAIM-LEDGER.md`.

Historical environments are kept separate from the standing endpoint. The
August 26-28 benchmark runs predate the four-tokenizer-worker adoption
(August 30) and the September 2 tunnel migration. No historical setting was
rewritten to match the endpoint that exists now. One tokenizer worker ran in
every benchmark environment; the standing service runs four, and the
tokenizer-worker A/B is reported in its own section.

`BENCHMARKS.md` and an earlier public discussion summarize overlapping work:
[SGLang discussion #37153](https://github.com/sgl-project/sglang/discussions/37153)
(August 30, 2026) is a dated snapshot and community thread, not the full
evidence archive. This file and `results/` are the fuller record.

## Audited serving envelope

Six cells, three repetitions each, on the standing profile (dual TP4/EP4,
`flashinfer_trtllm`, static NEXTN 5-1-6, FP8 KV, effective 16,384-token
prefill chunk, one tokenizer worker). Requests streamed through
`/v1/chat/completions` with `temperature: 0`, `reasoning_effort: low`, fixed
output length, an explicit cache flush, and cache-busted Federalist prose.
Success means HTTP 200, intact output, and the exact requested completion
length; timeouts and HTTP 429 stay in the denominator. Evidence:
`results/serving-envelope/`.

| Input tokens (rendered) | Concurrency | Replicas | Output tokens | Success |
| ---: | ---: | --- | ---: | ---: |
| 131,102-131,103 | 1 | A | 2,048 | 3/3 |
| 131,100-131,107 | 16 | A+B, split 8/8 | 2,048 | 48/48 |
| 409,630-409,634 | 8 | A+B, split 4/4 | 2,048 | 24/24 |
| 524,318-524,325 | 16 | A+B, split 8/8 | 2,048 | 48/48 |
| 1,000,032-1,000,034 | 1 | A | 512 | 3/3 |
| 1,000,030-1,000,034 | 4 | A+B, split 2/2 | 512 | 12/12 |

All 138 requests returned HTTP 200 with the requested completion length. None
timed out or returned HTTP 429. The 524K-input, concurrency-16 row completed
every request in every repetition without an observed out-of-memory error:
a tested point, not a capacity maximum.

At concurrency 1, the end-to-end output rate divides output tokens by the full
request wall time, including prefill. The median was 245.29 tok/s at about
131K input (repetitions 244.78, 247.99, 245.29) and 12.50 tok/s at about 1M
input (repetitions 12.469, 12.502, 12.537). The 1M rate is dominated by
prefill.

Aggregate concurrent throughput, decode-window TPOT, and inter-token latency
are excluded from this envelope because the retained clocks do not support
them: the saved request records lack absolute request-start clocks, and the
stream events lack per-output-event clocks. Those metrics are absent rather
than reconstructed.

## Why dual TP4/EP4 (historical topology selection)

The topology decision came from one 128K-input cell, 2,048 output tokens,
total concurrency 16 (8 requests per replica), measured on August 26:

| Configuration | Other configuration | Success | Per-session decode | Aggregate output | TTFT median | TTFT p95 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Two TP4/EP4 replicas | FP8 KV, `deep_gemm`, adaptive NEXTN | 96/96 | about 61-63 tok/s | about 737 tok/s | 7.7 s | 31-44 s |
| One TP8/EP8 engine | FP8 KV, `deep_gemm`, adaptive NEXTN | 80/80 | about 32-34 tok/s | about 429 tok/s | 8.27 s | 58.5 s |

Both arms ran `deep_gemm` and adaptive speculation. The standing profile later
changed to `flashinfer_trtllm` and static NEXTN 5-1-6, so this result explains
the historical choice; it is not a matched measurement of the standing stack.
No matched current-stack TP4-versus-TP8 cell was ever accepted. The planned
32K topology arm did not run; only the 128K decision records exist.

## MoE backend selection

One TP4/EP4 replica at 32K input, 2,048 output tokens, concurrency 8, 48
requests, low reasoning effort, seed 42 (August 27):

| Backend | TPOT median | TTFT median / p95 | Aggregate output |
| --- | ---: | ---: | ---: |
| `deep_gemm` | 7.99 ms | 1,080 / 7,648 ms | 855.74 tok/s |
| `flashinfer_trtllm` | 7.42 ms | 978 / 7,583 ms | 917.81 tok/s |

Both backends were verified engaged from live boot logs, not merely parsed.
`flashinfer_trtllm` won the tested cell by about 7% with slightly lower TTFT.
The conclusion applies to that cell; it does not establish a universal backend
ranking.

## Speculative decoding

Two campaigns, never averaged: their prompts, routes, loads, and timing
methods differ.

Initial selection (Card C, August 27): one TP4/EP4 replica with
`flashinfer_trtllm`, 32K input, 2,048 output tokens, concurrency 8, 48
requests, low effort, seed 42.

| Setting | TPOT median | Aggregate output |
| --- | ---: | ---: |
| Speculation off | 10.44 ms | 644 tok/s |
| Adaptive NEXTN | 7.42 ms | 918 tok/s |
| Static 1-1-2 | 9.16 ms | 777 tok/s |
| Static 2-1-3 | 7.97 ms | 855 tok/s |
| Static 3-1-4 | 7.18 ms | 908 tok/s |
| Static 5-1-6 | 6.76 ms | 981 tok/s |

Static 5-1-6 won the synthetic decode-heavy cell, and turning speculation off
cost about 1.5x in decode rate plus roughly 4.4 s of TTFT. Accept-length
stdout lines were absent on that image (a capture gap, disclosed rather than
estimated).

Later retune (W14/W12, August 29): captured coding-agent request shapes
replayed in fresh temporary containers against the deployed 5-1-6 control,
three narrower static widths at concurrency 1, and adaptive speculation at
concurrency 1 and 8. Repetition-level records: `results/config-selection/`.

| Comparison | Result |
| --- | --- |
| Opening static 5-1-6, c1 control | 2.895 ms/token median TPOT, 24/24 eligible rows |
| Static 1-1-2, 2-1-3, 3-1-4 at c1 | median TPOT regressed 67.8%, 27.1%, 12.6%; no candidate advanced |
| Adaptive at c1 | 3.387 ms/token median, a 17.0% regression |
| Adaptive at c8 | 3.818 ms/token median, a 3.7% improvement, below the required 10% |
| Closing static 5-1-6 drift | 1.3% at c1 and 3.5% at c8, within the 5% validity bound |

Static NEXTN 5-1-6 remained the selected and deployed configuration.

The campaign's W6 identifier check returned 34/36 visible-exact responses with
exact replica placement. Two requests exhausted the 128-token completion
budget in reasoning and returned empty visible content, with zero identifier
substitutions. That correctness stop ended the campaign before the W11
request-cap comparison could run.

## Prefill-chunk selection

Card D (August 27) on the then-selected profile (TP4/EP4,
`flashinfer_trtllm`, static 5-1-6), 512 output tokens, concurrency 8:

| Chunk | 128K input TPOT | 400K input TPOT |
| ---: | ---: | ---: |
| 8,192 | 56.90 ms | 58.80 ms |
| 16,384 | 44.91 ms | 38.16 ms |
| 32,768 | 38.42 ms | 51.32 ms |
| 65,536 | timed out; no accepted result | timed out; no accepted result |

32,768 led at 128K and 16,384 led at 400K, so 16,384 was retained as the
measured compromise and became the effective boot state. The 65,536 chunk
timed out and appears in no performance table. The public launch command does
not pass an explicit chunk flag; it relies on the pinned image's effective
default of 16,384, which matches the measured boot state. Mixed chunking is
incompatible with the deployed speculative algorithm.

## Operational ladder (historical)

The full context and concurrency ladder ran August 27 under dual TP4/EP4,
`flashinfer_trtllm`, static 5-1-6, FP8 KV, low effort, and effective
16,384-token chunking, with a different harness and timing record from the
audited envelope. Per-session decode-window rates (tok/s):

| Input | c1 | c2 | c4 | c8 | c16 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16K | 357 | 357 | 303 | 227 | 182 |
| 32K | 345 | 357 | 286 | 213 | 149 |
| 64K | 345 | 357 | 250 | 169 | 104 |
| 128K | 323 | 357 | 222 | 116 | 68 |
| 256K | 321 | 333 | 199 | 86 | 42 |
| 400K | 313 | 316 | 130 | 78 | 33 |
| 512K | 323 | 344 | 267 | 71 | 25 |
| about 1M | 288 | 221 | 126 | - | - |

The 1M rows used 512 output tokens at concurrency 1, 2, and 4 with 6/6
completions each and zero aborts; cold 1M TTFT was 40.4 s (c1), 76.2 s (c2),
and 112.8 s (c4). Cold TTFT p50 elsewhere: about 0.49 s at 16K, 3.8 s at 128K
(5.5 s at c16), and 17-45 s across 400K-512K. Aggregate output at c16 was
2,434 tok/s at 16K down to 838 tok/s at 128K; about 504 tok/s at 1M c4.

The 512K concurrency-16 point (25 tok/s per session) fell below the
preregistered 30 tok/s decode threshold. That is a tested operational point
under the preregistered gate, not a capacity limit. A traffic-shape
demonstration with eight staggered repeated-prefix coding sessions measured a
median 350 tok/s per session with warm TTFT 0.25 s, 8/8 successes.

## Prefix reuse and session affinity

The cache study (August 28) used ingress `/v1/chat/completions` requests and
flushed both replica caches before every measured turn. Each condition held
three repetitions of three sessions x four turns: 36 requests per condition,
144/144 successful overall. TTFT-any is the time to the first streamed output
event, reasoning or visible. Within a repetition, turn 1 is the median across
three sessions and turns 2-4 the median across nine requests; the table
reports the median across repetitions, and the ratio is computed within each
repetition first. Evidence: `results/cache-affinity/`.

| Request shape | Turn 1 TTFT-any | Turns 2-4 TTFT-any | Turn 1 / later turns |
| --- | ---: | ---: | ---: |
| Every turn cold | 3.157 s | 3.386 s | 0.928x |
| Repeated prefix without `x-claude-code-session-id` | 3.114 s | 0.774 s | 3.975x |
| Repeated prefix with a stable `x-claude-code-session-id` | 3.098 s | 0.617 s | 5.024x |
| Stable affinity with a changed leading system line | 3.132 s | 3.238 s | 0.968x |

Two facts, kept separate. First, by configuration: consistent hashing on the
session header preserves a stable placement key, which is what makes repeated
prefixes land on the replica that already holds them. Second, by measurement:
repeated-prefix requests showed lower later-turn TTFT in this study, and
changing only the leading system line removed the effect. Direct per-request
cache attribution was not measured; the accepted evidence is the latency
association plus the routing semantics.

## Tokenizer workers and hardening

Operational hardening, kept separate from the performance ladder.

Tokenizer-worker A/B (August 30): a near-990K-token `count_tokens` request
(990,819 tokens) drove one replica with four tokenizer workers against one
with a single worker. During preprocessing, authenticated `/ping` p99 was
1.7 ms on the four-worker replica versus 1,666 ms on the one-worker control,
and the 1M-body tokenization was about 21% faster. Concurrent short
generations, streaming delivery, and disconnected-client recovery were
revalidated clean. Four workers were adopted on both replicas. Evidence:
`results/tokenizer-workers/`.

Bounded deployment checks (August 28-30):

- Three single-request mid-prefill cancellation checks, one each through the
  direct replica, the nginx loopback, and the public tunnel, each closing with
  more than 712K tokens still pending. Immediate same-path requests completed
  in 0.243, 0.389, and 0.906 seconds. One request per path; not a general
  proof over all disconnect shapes.
- The 32 MiB ingress body cap: two 10 MiB `count_tokens` requests counting
  1,310,732 tokens returned 200, and a 40 MiB request returned 413.
- Exact-route matching: the six inference routes are exact-match locations;
  extra suffixes return 404.
- Reboot recovery: all four containers use `restart=unless-stopped`; a reboot
  rehearsal returned authenticated `/ping` in 96 seconds and completed a real
  generation in 8 seconds.
- `/v1/responses` is served natively and verified through the ingress.

## Correctness panels

Each panel ran in its own dated environment under the audited campaign.

- GSM8K, full 1,319-problem test set, temperature 0, exact numeric extraction
  after the final `Answer` marker. Comparator condition (effort omitted,
  resolving to the checkpoint default of max; 2,048-token output limit):
  1,284/1,319 = 97.35%; longest completion 1,998 tokens; none reached the
  limit. Low-effort condition (`reasoning_effort: low`; 1,024-token limit):
  1,272/1,319 = 96.44%; longest completion 356 tokens; none reached the limit.
  Both conditions are FP8-KV reasoning-effort measurements with different
  output limits; they are not an FP8-versus-BF16 precision comparison, and no
  local BF16 control exists.
- Identifier check: exact visible replies requested for `kimi-k3`, `nova-x7`,
  `apollo-n9`, `route_query_v2`, `def handle_request`, and `glm-5.3-flash`.
  36/36 visible-exact with the deployed speculative configuration; 31/36 with
  speculation removed, with the five non-exact replies all placing the
  requested string in reasoning instead of visible content, and the served
  model name visible-exact in 1/6 spec-off observations. This is an observed
  placement difference; it does not isolate speculation from response
  stochasticity.
- Coding trace: 19 four-turn sessions attempted; one session was invalid
  because its first response spent all 1,024 completion tokens on reasoning
  with no visible content or tool call, and a clean retake replaced it. The
  retained set is 18 sessions and 72 accepted requests, all HTTP 200, every
  final prompt within 1% of target, 14/18 final task checks passed. Wrong
  debug answers remain in the denominator. A bounded task panel, not a general
  quality measurement.

## Bounded cross-model panel

A matched GLM-5.3-Flash versus DeepSeek-V4-Flash-0731 endpoint comparison ran
August 28 through both current `/v1/messages` routes from one client origin.
GLM completed 526/528 serving requests; at the ratio-eligible fixed cells GLM
led serial 512K and 1M concurrency-1 wall p50 (21.75 s versus 25.31 s, and
44.01 s versus 63.09 s) while DSV4 led fixed-decode concurrency-8 wall p50
(59.84 s versus 102.00 s at 512K, and 115.05 s versus 154.17 s at 1M). GLM
completed the tested c16 load reliably (48/48); DSV4 completed exactly 8 of 16
requests in every tested c16 repetition.

The two endpoints differ in engine, topology, speculative decoder, region,
ingress, and reasoning profile. This panel is an endpoint observation kept
separate from every single-model configuration decision above; it is not
model-quality or engine-efficiency evidence. GLM passed 20/21 interactive
tasks and DSV4 21/21; the matched trace ended 12/18 sessions per endpoint,
with GLM producing 29 valid expected tool calls to DSV4's 22 of 54, and
long-context patch tests at 22/27 versus 20/27.

## Not run, stopped, or invalid

Recorded so no reader mistakes these for coverage:

- No accepted matched current-stack dual-TP4-versus-TP8 result. The topology
  table above is historical-selection evidence only.
- No local BF16-KV correctness control. The two GSM8K scores are
  reasoning-effort conditions, not precision arms.
- No accepted W11 latency or TPOT comparison for request-cap 16 versus 48.
  The mandatory W6 identifier check stopped the campaign; the bounded W11
  scheduler observation stays out of every performance table because seed,
  order, response mix, and output-token budget confound a matched conclusion.
- No accepted engine-to-ingress route comparison. 31 of 72 route identities
  were mutated, so the attempted comparison was not controlled; a later
  route-only attempt ran zero measured requests.
- No direct per-request cache-hit attribution for the cache study.
- No maximum-capacity or saturation search; no audited-envelope cell above
  concurrency 16; no open-loop Poisson or gamma arrival process; no
  high-availability or failover test beyond the reboot rehearsal; no broad
  multimodal quality or maximum-image-size campaign.
- The 65,536 prefill chunk timed out and produced no accepted result.
- The Card A 32K topology arm did not run.

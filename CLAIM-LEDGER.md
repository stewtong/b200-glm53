# Claim ledger

Every numerical or causal claim made in this repository, with its evidence class
and where the claim comes from. Classes:

- **public-derivable**: re-derives from sanitized request-level or
  repetition-level files in `results/` via `reproduce/derive-results.py`.
- **retained-summary-only**: stated at summary level; the retained internal
  record is the source, and detailed evidence is not published. The boundary
  states why.
- **configuration-only**: a deployment configuration fact, not a measurement.
- **first-party**: a dated operational record from the deployment itself.
- **invalid / stopped / not-run**: recorded so no reader mistakes them for
  coverage. A planned cell is not evidence that the cell ran.
- **superseded / partial / unresolved** (not-run table only): a gap later
  covered by a named campaign, covered in part, or measured without a
  conclusion.

Rows for the September 27 campaign name the `block` field of
`results/holistic-20260927/raw/grid-rows.jsonl`: `1A` grid, `2D` TP8/EP8,
`2B` request cap, `1F-direct` and `1F-proxy` direct and proxied access.

Retained records are cited by their campaign identifiers (for example
`track4-glm53flash-a-20260826`); the full retained trees are internal and are
not part of this repository.

## Serving envelope

| Claim | Class | Source | Boundary |
| --- | --- | --- | --- |
| Six cells completed 138/138 requests with HTTP 200, the exact requested completion length, zero timeouts, zero HTTP 429 | public-derivable | `results/serving-envelope/raw/serving-envelope-rows.jsonl` | Counts every request in the denominator; a tested operating point, not a capacity maximum |
| Concurrency-1 full-request output rate: median 245.293 tok/s at about 131K input (repetitions 244.780, 247.993, 245.293) | public-derivable | same rows | Output tokens divided by full request wall time, including prefill |
| Concurrency-1 full-request output rate: median 12.502 tok/s at about 1M input (repetitions 12.469, 12.502, 12.537) | public-derivable | same rows | Same rate rule; the 1M c1 rate is dominated by prefill |
| 524K-input, concurrency-16 cell completed all 16 requests in every repetition with no observed out-of-memory error | public-derivable | same rows, cell H4 | Tested point; maximum capacity remains unmeasured |
| Aggregate concurrent throughput, decode TPOT, and ITL are excluded | public-derivable | same rows; `results/serving-envelope/summary.json` `excluded` | Retained records lack absolute request-start clocks and per-output-event clocks; the excluded metrics are not reconstructed |

## Cache affinity

| Claim | Class | Source | Boundary |
| --- | --- | --- | --- |
| Turn-1 TTFT-any about 3.10 to 3.16 s in every condition; later turns 0.617 s with stable affinity, 0.774 s without a session header, 3.386 s cold, 3.238 s with a changed leading system line | public-derivable | `results/cache-affinity/raw/cache-affinity-rows.jsonl` | 144/144 requests; medians per the aggregation rule in the summary |
| Consistent hashing preserves a stable placement key for a stable session header | configuration-only | nginx reference in `reproduce/nginx.conf` | Routing semantics by configuration, not a measured per-request placement claim |
| Repeated-prefix requests showed lower later-turn TTFT in the measured study | public-derivable | same rows | Latency association only |
| Direct per-request cache attribution | not-run | `results/benchmark-summary.json` `not_run_or_invalid` | The study measured latency, not cache-hit counters; route placement counters were not retained |

## Configuration selection

| Claim | Class | Source | Boundary |
| --- | --- | --- | --- |
| flashinfer_trtllm selected over deep_gemm: TPOT median 7.42 ms versus 7.99 ms, aggregate 917.81 versus 855.74 tok/s in the tested cell | retained-summary-only | `track4-glm53flash-b-20260827` | One cell (32K, c8, 48 requests, seed 42); both backends verified engaged from boot logs; no universal backend ranking |
| Static NEXTN 5-1-6 selected: static 1-1-2, 2-1-3, 3-1-4 regressed median TPOT by 67.8%, 27.1%, and 12.6% at c1; adaptive regressed 17.0% at c1 and improved 3.7% at c8, below the required 10% | repetition-level-public | `results/config-selection/raw/config-selection-repetition-level.json` | Later campaign on captured coding traffic; never averaged with the earlier Card C synthetic cell |
| Static drift between opening and closing 5-1-6 controls: 1.3% at c1, 3.5% at c8, within the 5% validity bound | repetition-level-public | same file | Validity control for the retune comparison |
| W6 identifier check returned 34/36 visible-exact responses with exact replica placement; two requests exhausted the 128-token completion budget in reasoning | repetition-level-public | same file, `w6` section | The correctness stop ended the campaign before W11 ran |
| Prefill chunk 16,384 retained as the effective boot state: 32,768 led at 128K, 16,384 led at 400K, 65,536 timed out | retained-summary-only | `track4-glm53flash-d-20260827` | 65,536 has no accepted result and appears in no performance table; the public launch command omits an explicit chunk flag and documents the effective default |
| Dual TP4/EP4 selected over TP8 at 128K: 96/96 versus 80/80 retained successes; per-session decode about 61-63 versus 32-34 tok/s; aggregate about 737 versus 429 tok/s; TTFT median 7.7 versus 8.27 s, p95 31-44 versus 58.5 s | retained-summary-only | `track4-glm53flash-a-20260826` | Historical topology selection. Both arms ran deep_gemm plus adaptive NEXTN, unlike the standing flashinfer_trtllm plus static 5-1-6 stack. No matched current-stack topology cell exists |
| Session affinity: stable `x-claude-code-session-id` header preserves prefix-cache locality across the two replicas | configuration-only | `reproduce/nginx.conf`; cache study | Hash semantics by configuration |
| Four tokenizer workers adopted on both replicas: /ping p99 during near-1M-token preprocessing 1.7 ms (4 workers) versus 1,666 ms (1 worker); 1M-body tokenization about 21% faster; streaming and disconnect delivery revalidated | public-derivable | `results/tokenizer-workers/raw/tokenizer-worker-ab.json` | One load shape (990,819-token count_tokens); operational hardening, not a throughput benchmark |

## Correctness panels

| Claim | Class | Source | Boundary |
| --- | --- | --- | --- |
| GSM8K comparator condition (effort omitted, resolves to max; 2,048-token limit): 1,284/1,319 = 97.35%; longest completion 1,998 tokens; none at the limit | retained-summary-only | `track4-glm53flash-publication-20260828` gsm8k records | FP8-KV reasoning-effort condition, not an FP8-versus-BF16 comparison; no local BF16 control exists |
| GSM8K low-effort condition (`reasoning_effort: low`; 1,024-token limit): 1,272/1,319 = 96.44%; longest completion 356 tokens; none at the limit | retained-summary-only | same records | Same prompt, temperature, and scorer; reported separately, never pooled |
| Identifier check: 36/36 visible-exact with the deployed speculation; 31/36 with speculation removed; the served model name was the non-exact case in 5/6 spec-off observations | retained-summary-only | same records, identifier evidence | Observational placement difference; does not isolate speculation from response stochasticity |
| Coding trace: 18 retained sessions, 72 accepted requests, all HTTP 200, all final prompts within 1% of target, 14/18 final task checks passed | retained-summary-only | same records, trace cells | One invalid session was replaced by a clean retake; wrong debug answers stay in the denominator; bounded task panel, not a general quality measurement |

## Operational ladder (historical)

| Claim | Class | Source | Boundary |
| --- | --- | --- | --- |
| Phase 1-3 per-session decode-window rates, cold TTFT p50, and aggregate rates at 16K-1M input, c1-c16 | retained-summary-only | `track4-glm53flash-p1-20260827`, `p2-20260827`, `p3-20260827` | Different harness and timing record from the audited envelope; kept separate from it; the 512K c16 point fell below the preregistered decode threshold, an operational point rather than a capacity limit |
| Traffic-shape demo: eight staggered sessions, median 350 tok/s/session, warm TTFT 0.25 s | retained-summary-only | `track4-glm53flash-f-20260827` | Warm-turn demonstration, not a controlled cell |

## Hardening and current state

| Claim | Class | Source | Boundary |
| --- | --- | --- | --- |
| Mid-prefill cancellation contained: immediate same-path requests completed in 0.243, 0.389, and 0.906 s after a near-991K request was closed at the direct, nginx, and public attach points with over 712K tokens pending | retained-summary-only | `track4-glm53flash-p1-cancel-20260830` | One request per path; not a general proof over all disconnect shapes |
| 32 MiB ingress body cap: two 10 MiB count_tokens requests accepted (1,310,732 counted tokens); 40 MiB returned 413 | first-party | dated deployment verification records | DoS guard; the 1,048,576-token context remains the generation limit |
| Exact-route matching: six prefix locations are exact locations; extra suffixes return 404 | first-party | dated deployment verification records | Verified through the loaded config plus negative probes |
| Reboot recovery: all four containers `restart=unless-stopped`; authenticated `/ping` in 96 s, real generation in 8 s after a reboot rehearsal | first-party | dated deployment verification records | Automatic container recovery only; no failover test |
| `/v1/responses` serves natively and is verified through the ingress | first-party | dated deployment verification records (2026-08-28 opening, verified through 2026-09-02) | Multi-turn Responses verified; the DSV4 endpoint's lack of Responses support does not apply here |
| Standing endpoint verified after the 2026-09-02 tunnel migration: 24/24 route-and-auth matrix twice, more than five minutes apart | first-party | dated deployment verification record | Current state, separate from every historical benchmark environment |

## Bounded cross-model panel

| Claim | Class | Source | Boundary |
| --- | --- | --- | --- |
| GLM-5.3-Flash versus DeepSeek-V4-Flash-0731 endpoint panel results (serial 512K/1M c1, fixed-decode c8, session oracles, trace, retention) | retained-summary-only | `track5-glm53f-dsv4-coding-ab-20260828` | Bounded public-safe summary. Different engines, topologies, speculative decoders, regions, and reasoning profiles; an endpoint observation, not a model-quality or configuration-selection claim |

## Same-day campaign (September 27)

| Claim | Class | Source | Boundary |
| --- | --- | --- | --- |
| The 40-cell grid (16K-1M input, concurrency 1-16) completed 744/744 requests with HTTP 200, integrity pass, no timeout, and the exact completion length | public-derivable | `results/holistic-20260927/raw/grid-rows.jsonl`, block `1A` | Patched configuration (K-pool chunked logits and parser overlay); the 1M concurrency-16 cell exceeds the per-server KV pool and is stress, not an operating point |
| Burst output rate, request output rate, and TTFT p50 for every grid, TP8, request-cap, and route cell, with per-repetition ranges | public-derivable | same rows; `results/holistic-20260927/summary.json` | Each repetition is one burst of c requests with no replacement; burst output rate uses first dispatch to last completion and is not sustained throughput; TTFT is the first streamed event, reasoning or visible |
| Dual TP4/EP4 burst output is 1.19-1.39x TP8/EP8 at 32K concurrency 8-16 and 1.80-2.11x at 128K c16, 512K c8-c16, and 1M c4-c8; at concurrency 1, TP8 is 1-4% slower at 128K and 1M and within repetition spread at 32K | public-derivable | same rows, blocks `1A` and `2D` | Same node and day, run sequentially 3.9 to 5.4 hours apart with different prompts; TP8 used the TP4-selected 16,384-token chunked prefill, cap 32, and KV pool 11,556,672 against 2 × 16 and 2 × 7,362,048; DP attention not tested |
| Warm extension: turns 2-4 TTFT p50 0.79 s at 128K through 5.07 s at 1M | public-derivable | `results/holistic-20260927/raw/warm-extend-rows.jsonl` | Two sessions per server, 8K appended per turn, stable session header |
| FP8 and BF16 KV each scored 1,283/1,319 on GSM8K with effort omitted; 1,267 and 1,272 at low effort (exact McNemar p = 0.405) | public-derivable | `results/holistic-20260927/raw/gsm8k-kv-rows.jsonl` | Concurrent arms on separate GPU halves after a 0.99% calibration; no accuracy difference is claimed |
| BF16 KV boots with 4,101,312 tokens against 7,362,048 for FP8 | first-party | boot logs of the two arms | Same memory fraction, flags, and TRT-LLM DSA backends; only the KV dtype differs |
| Request cap 48 against 16 at 8, 16, and 24 requests per server | public-derivable | grid rows, block `2B` | The caps differ in behavior only at 24 per server; the cap-48 server also had a smaller KV pool and was slower where no cap bound, so the rows compare complete configurations |
| Loopback proxy is within 1% of direct engine access in three matched cells | public-derivable | grid rows, blocks `1F-direct` and `1F-proxy` | Proxy overhead only; the public ingress path is untested |
| The pinned image's unchunked K-pool logits allocation raised a scheduler CUDA out-of-memory error at 1M concurrency 4 on September 26 (29.68 GiB request); the local patch chunks it, as upstream SGLang #40854 does | retained-summary-only | internal run `track4-glm53flash-holistic-run2-20260926` diagnosis and the patch equivalence record | Logit-identity and top-k check at 32K and 128K only: logits bit-identical in 1,152 of 1,152 calls, top-k differing in 466 against 501 for the unpatched kernel run twice, so top-k identity is not shown; the patch keeps the unchunked call during CUDA graph capture; when the chunked path engaged in scored cells and its overhead were not measured |
| Poisson ladder: completion rate 97.5-100% of offered at 0.1-0.4 requests/s, 89.6% at a 0.8 target | retained-summary-only | internal run `track4-glm53flash-holistic-run3`, `bench_serving` outputs | Shortened windows (236-292 s); bounded observations, not a saturation measurement |
| Anthropic Messages matrix: 17 of 20 cases passed, one tool-use case was invalid (64-token limit), two failed | retained-summary-only | same internal run | Ran with the parser overlay; the cases are listed in `BENCHMARKS.md` |
| Stopping one server mid-load: both requests on the other server completed; both streams on the stopped server kept HTTP 200 but ended with no finish reason or usage | retained-summary-only | same internal run, replica-loss rows and proxy error log | `docker stop` of the temporary cap-48 server (SIGTERM, then SIGKILL after the 10 s grace period); tokens stopped within 0.9 s of SIGTERM and the streams closed at the kill with no error event; one observation, not a failover measurement |

## Not run, stopped, or invalid

| Item | Class | Note |
| --- | --- | --- |
| Matched current-stack dual-TP4-versus-TP8 | superseded | Still not run as a matched comparison; a sequential same-day comparison with different prompts and TP4 prefill settings ran September 27. The August attempt produced no accepted topology cell |
| Local BF16-KV control | superseded | Run September 27 with side-by-side arms; the August GSM8K conditions remain reasoning-effort conditions, not precision arms |
| W11 request-cap 16 versus 48 performance | stopped | W6 correctness stop; bounded scheduler observation excluded from performance tables because of seed, order, response-mix, and output-token confounds |
| Engine-to-ingress route comparison | invalid | 31/72 route identities were mutated; not controlled. A later attempt ran zero measured requests |
| Direct cache-hit attribution | not-run | Latency association only |
| Maximum capacity or saturation search | not-run | Tested points only |
| Audited-envelope concurrency above 16 | not-run | |
| Open-loop Poisson or gamma arrivals | partial | Bounded Poisson ladder September 27; no gamma arrivals |
| High-availability or failover test | not-run | Reboot recovery only |
| Broad multimodal quality or maximum-image-size campaign | not-run | Image support is functional, coverage incomplete |
| Card D 65,536 chunk | invalid | Timed out; no accepted result |
| Card A 32K topology arm | not-run | Only the 128K decision records exist |
| September 27 prefix-cache study | stopped | One turn spent its 64-token budget on reasoning |
| September 27 KV admission boundary | unresolved | The 1,000,000-token prompt rendered 3.25% long and was excluded; 9 of 24 retrieval requests at 128K-768K reached the 256-token output limit before answering |

## Unsupported claims (never made)

- No claim of universal topology superiority, maximum throughput, saturation
  capacity, general model quality, or fault tolerance beyond the evidence above.
- No causal claim that speculation causes reasoning-only output placement.
- No cache-hit or route-placement claim from the latency-only cache study.

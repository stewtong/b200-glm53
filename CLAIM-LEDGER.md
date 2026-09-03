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

## Not run, stopped, or invalid

| Item | Class | Note |
| --- | --- | --- |
| Matched current-stack dual-TP4-versus-TP8 | not-run | The later attempt produced no accepted topology cell |
| Local BF16-KV control | not-run | Listed so the GSM8K conditions are never read as precision arms |
| W11 request-cap 16 versus 48 performance | stopped | W6 correctness stop; bounded scheduler observation excluded from performance tables because of seed, order, response-mix, and output-token confounds |
| Engine-to-ingress route comparison | invalid | 31/72 route identities were mutated; not controlled. A later attempt ran zero measured requests |
| Direct cache-hit attribution | not-run | Latency association only |
| Maximum capacity or saturation search | not-run | Tested points only |
| Audited-envelope concurrency above 16 | not-run | |
| Open-loop Poisson or gamma arrivals | not-run | Closed-loop concurrency only |
| High-availability or failover test | not-run | Reboot recovery only |
| Broad multimodal quality or maximum-image-size campaign | not-run | Image support is functional, coverage incomplete |
| Card D 65,536 chunk | invalid | Timed out; no accepted result |
| Card A 32K topology arm | not-run | Only the 128K decision records exist |

## Unsupported claims (never made)

- No claim of universal topology superiority, maximum throughput, saturation
  capacity, general model quality, or fault tolerance beyond the evidence above.
- No causal claim that speculation causes reasoning-only output placement.
- No cache-hit or route-placement claim from the latency-only cache study.

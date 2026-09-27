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
every August benchmark environment; the standing service runs four, and the
tokenizer-worker A/B is reported in its own section. The September 27 campaign
ran the four-worker reference configuration with two local source changes,
described in its section.

`BENCHMARKS.md` and an earlier public discussion summarize overlapping work:
[SGLang discussion #37153](https://github.com/sgl-project/sglang/discussions/37153)
(August 30, 2026) is a dated snapshot and community thread, not the full
evidence archive. This file and `results/` are the fuller record.

## Same-day campaign on the patched reference configuration (September 27)

On September 27, 2026, one 8x B200 node ran the reference configuration (the two TP4/EP4 servers in `README.md`) through a full context and concurrency grid. The same day, it compared that configuration against a single TP8/EP8 engine, a BF16 KV cache, a 48-request cap, and direct engine access without the proxy. Evidence is in `results/holistic-20260927/`. `reproduce/derive-results.py` recomputes, from the sanitized request rows, every cell's burst output rate, request output rate, TTFT, and repetition range, the warm-extension values, and the GSM8K statistics. Ratios and percentages in the text are arithmetic on those values. The claim ledger classes the remaining values (arrival ladder, Anthropic Messages, replica loss, KV admission, calibration, timing between arms, and the patch checks) as retained summaries or first-party records.

Every server in this campaign ran two local source changes on top of the pinned image, and every result in this section was measured with them. Neither change is in the pinned image, and `reproduce/run-replicas.sh` applies neither; the README shows how to mount both.

- **Chunked K-pool indexer logits** (`reproduce/patches/kpool-chunked-logits.diff`). The servers ran it together with `reproduce/patches/kpool-logits-verify.diff`, an opt-in check that stayed off; the resulting file has SHA-256 `58e59be00ad2bec6bc28901d72c2a53853df2bc9635a9528d23528a1764a107e`. The fix alone gives `89cdb6933ef024d1bc596c016cadd0ca989878964405a2770c9e139c476614f0`. Each diff's header states its base commit, scope, and caveats. The DSA sparse-attention indexer scores each new prompt token against the cached index keys (the K-pool) to choose which keys to attend to. In the pinned image, `_get_topk_ragged_kpool_plan` computes the FP32 scores for a whole prefill step in one `fp8_mqa_logits` call. The largest allocations observed were about 15 GiB near the end of one 1M prompt and 29.68 GiB when two 1M prompts shared a prefill step. On September 26 the second one raised a CUDA out-of-memory error in the scheduler of one server during a 1M concurrency-4 cell; the server's HTTP endpoints kept answering while generation had stopped. The local patch splits the scores by query rows under a memory budget: the smaller of 4,096 MiB, half the free plus reusable cached device memory, and 30% of total device memory. The same defect was fixed upstream in [SGLang #40854](https://github.com/sgl-project/sglang/pull/40854), merged September 26, 2026, after the pinned image was built; that PR reports a 15.72 GiB failed allocation for 1M-token requests on GB300. The local patch was written independently and is not identical to #40854. No image containing #40854 was tested here.
- **Patch checks and scope.** A logit-identity and top-k check on one TP4/EP4 server at 32K concurrency 4 and 128K concurrency 2, with a 2,048 MiB budget, compared the unpatched call against chunks of one seventh of the rows. The scores were bit-identical in 1,152 of 1,152 indexer calls. The top-k selections differed in 466 calls; running the unpatched top-k (`_topk_from_kpool_logits`) twice on the same scores differed in 501 calls, a separate count, most likely from tied scores in the repetitive corpus. These counts show identical scores; they do not show identical top-k selections or model output. A 1M concurrency-2 run with a 256 MiB budget completed 2/2. During CUDA graph capture, which covers decode-sized and speculative-verify batches, the patch keeps the unchunked call. The patch chunks only `_get_topk_ragged_kpool_plan`; two other K-pool paths stay unchunked. The scored cells did not record how often the chunked path engaged, so any long-context cell may have run it, and its overhead was not measured against an unpatched run. That check used the separate verify diff with `SGLANG_DSA_KPOOL_LOGITS_VERIFY=1`; the mode is off by default.
- **Tool-call parser overlay** (`reproduce/patches/parser-overlay.diff`, in use on the service since September 6). It changes `function_call/glm47_moe_detector.py` (SHA-256 `28d5f11e3f0d9811817dd295306d7f43428a604b63f31409a3c1b1cb9e5fa41e`) and `function_call/utils.py` (SHA-256 `8f04cda38ecd805546442ed8fd3b86d35f054119280790ed211b3bdb05a7cf21`). It ports the GLM brace and schema handling from merged [SGLang #36626](https://github.com/sgl-project/sglang/pull/36626) and the closing-tag overlap repair from unmerged [SGLang #24147](https://github.com/sgl-project/sglang/pull/24147). It changes tool-call parsing only. No request in the serving grid sends tools; the Anthropic Messages matrix below does.

Grid, TP8, request-cap, route, and warm-extension requests used the [audited serving envelope](#audited-serving-envelope) contract: streamed `/v1/chat/completions`, `temperature: 0`, `reasoning_effort: low`, a fixed output length (`ignore_eos` with `max_tokens` set to the requested length), prompts cut from the Federalist Papers text in `reproduce/corpus-federalist.txt` with a distinct seed per request so no prompt repeats, and both servers' prefix caches flushed before every repetition. A request succeeds only with HTTP 200, the response integrity check in [`reproduce/METHOD.md`](reproduce/METHOD.md), no timeout, and the exact requested completion length. Input sizes are requested prompt tokens (16K = 16,000; 1M = 1,000,000). Each repetition of a cell at concurrency c dispatches c requests at once (all within 0.34 s) and waits for all of them; no request is replaced. Each cell ran three repetitions, except one single-repetition cross-check on server B. Tables write concurrency N as cN: N requests across the node, split evenly across the two servers, except that c1 goes to server A only.

- **Burst output rate**: completion tokens over the span from the first dispatch to the last completion in a repetition. At long context the span is mostly prefill, so this is the rate for one burst of requests; it does not measure sustained throughput under continuous load.
- **Request output rate**: completion tokens over one request's full wall time, including prefill.
- **TTFT p50**: time to the first streamed output event, reasoning or visible.

Grid, TP8, request-cap, and direct-versus-proxy values are the median across repetitions of each repetition's value (for TTFT, each repetition's median). Per-repetition values are in `results/holistic-20260927/summary.json`.

### Context and concurrency grid

All 40 cells and 744 requests succeeded. Output was 2,048 tokens per request, and 512 at 1M. Rendered input was within about 0.25% of the requested size, for example 999,292-999,300 tokens for a 1,000,000-token request. No server process failed and no recovery ran.

Burst output rate, tok/s:

| Input | c1 | c2 | c4 | c8 | c16 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16K | 243.7 | 532.3 | 879.0 | 1,439.7 | 2,242.8 |
| 32K | 247.4 | 484.9 | 653.8 | 1,053.5 | 1,598.2 |
| 64K | 217.7 | 483.6 | 582.2 | 892.5 | 1,190.8 |
| 128K | 249.4 | 491.0 | 677.8 | 706.2 | 835.8 |
| 256K | 164.1 | 322.1 | 408.8 | 470.6 | 502.8 |
| 400K | 116.4 | 227.1 | 271.5 | 301.3 | 309.9 |
| 512K | 92.3 | 180.0 | 198.1 | 233.7 | 245.9 |
| 1M | 11.8 | 23.5 | 25.8 | 26.8 | 27.6 |

Request output rate, tok/s:

| Input | c1 | c2 | c4 | c8 | c16 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16K | 243.7 | 345.8 | 245.3 | 190.9 | 152.6 |
| 32K | 247.4 | 243.7 | 203.9 | 164.6 | 119.9 |
| 64K | 217.7 | 244.3 | 196.8 | 134.7 | 90.8 |
| 128K | 249.4 | 246.6 | 169.7 | 106.3 | 61.0 |
| 256K | 164.1 | 161.4 | 102.7 | 59.2 | 32.3 |
| 400K | 116.4 | 113.8 | 68.5 | 38.1 | 20.3 |
| 512K | 92.3 | 90.9 | 53.3 | 29.4 | 15.4 |
| 1M | 11.8 | 12.0 | 6.5 | 3.4 | 1.8 |

TTFT p50, seconds:

| Input | c1 | c2 | c4 | c8 | c16 |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 16K | 0.51 | 0.53 | 0.91 | 1.70 | 3.19 |
| 32K | 0.98 | 1.02 | 1.77 | 3.02 | 5.80 |
| 64K | 1.93 | 1.99 | 3.54 | 6.81 | 12.82 |
| 128K | 3.91 | 3.97 | 7.34 | 14.13 | 27.55 |
| 256K | 8.25 | 8.28 | 15.51 | 29.62 | 57.59 |
| 400K | 13.32 | 13.70 | 25.35 | 48.69 | 94.93 |
| 512K | 17.83 | 18.19 | 33.71 | 64.62 | 126.75 |
| 1M | 40.60 | 40.92 | 58.85 | 95.06 | 255.01 |

At c1 and c2 each server holds at most one request, so single-prompt variation shows. The three c1 repetitions ranged 178.6-253.0 tok/s at 32K and 175.3-259.1 tok/s at 64K, against 249.0-249.5 at 128K, so the 64K median below 128K is within repetition spread. In the 16K c2 cell, server B ran its request at 426-432 tok/s in all three repetitions, against 209-267 on server A, which raises that cell's request output rate; at 32K to 256K the two servers showed no consistent difference. Why server B was faster there was not isolated; speculative-decoding acceptance, which varies with prompt text, was not recorded per request.

The 1M c16 cell puts about 8.0M prompt tokens on each server, above its 7,362,048-token KV pool. All 48 requests completed (TTFT p50 255 s); it is a stress result, not an operating point.

Extra cells in the same grid:

| Cell | Success | Burst output (tok/s) | Request output (tok/s) | TTFT p50 (s) |
| --- | ---: | ---: | ---: | ---: |
| 128K, c1, 8,192 output | 3/3 | 318.3 | 318.3 | 3.96 |
| 128K, c8, 8,192 output | 24/24 | 1,216.2 | 157.5 | 14.17 |
| 32K, c16, 8,192 output | 48/48 | 2,270.1 | 152.1 | 4.65 |
| 1M, c4, 2,048 output | 12/12 | 97.9 | 24.9 | 76.53 |
| 128K, c1 on server B (one repetition) | 1/1 | 248.6 | 248.6 | 3.89 |

The longer 8,192-token outputs raise the burst output rate at the same input (32K c16: 1,598.2 tok/s with 2,048 output tokens against 2,270.1 with 8,192), because prefill takes a smaller share of the span.

The August 28 audited envelope measured 245.29 tok/s at about 131K input and 12.50 tok/s at about 1M, both at concurrency 1, on the pinned image without the K-pool patch; this grid measured 249.39 tok/s at 128K and 11.84 tok/s at 1M with it. The August 27 ladder recorded a cold 1M TTFT of 40.4 s; this grid measured 40.60 s. The two campaigns differ in input size (131,072 against 128,000 tokens), tokenizer workers (1 against 4), and both source changes, and the c1 repetitions here spread by more than the differences, so these are consistency checks, not a measured patch cost. The August 27 ladder's aggregate figures and per-session rates exclude prefill (decode-window rates) and do not compare with the burst output rates above.

### Warm extension of long sessions

Two sessions per server built a cold prefix, then ran three more turns that each appended about 8K tokens, with 512 output tokens per turn and a stable session header. The sessions ran one at a time across the whole node, so each turn's cached-token counter delta belongs to that turn alone. There were three repetitions and 48 turns per context; all 192 turns succeeded with exactly 512 output tokens. TTFT p50 values are across all turns of that kind in the three repetitions.

| Starting context | Turn 1 TTFT p50 (s) | Turns 2-4 TTFT p50 (s) | Turns 2-4 visible-output TTFT p50 (s) | Turns 2-4 max TTFT (s) | Turns 2-4 prompt share served from cache (min-max) |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 128K | 3.85 | 0.79 | 0.88 | 1.03 | 94.1-94.7% |
| 256K | 8.12 | 1.27 | 1.36 | 1.81 | 93.3-97.1% |
| 512K | 17.81 | 2.62 | 2.79 | 2.94 | 97.0-98.5% |
| 1M | 40.13 | 5.07 | 5.17 | 6.11 | 97.6-99.2% |

The limits registered before the run applied to turns 2-4 TTFT p50: under 3 s at 256K, under 5 s at 512K, and under 8 s at 1M. All three passed, and so did the slowest turn at each context.

### Same-day TP8/EP8 against dual TP4/EP4

After the grid, both servers were stopped and one TP8/EP8 engine ran on all eight GPUs with the same image, patches, and flags, including the 16,384-token chunked prefill selected for TP4, and `--max-running-requests 32` to match the dual total. Its KV pool was 11,556,672 tokens, 21.5% less than the dual servers' combined 2 × 7,362,048; the largest TP8 load (about 8M tokens at 1M concurrency 8) fit within it. The dual comparators come from the grid above: the same node, day, and request contract, run 3.9 to 5.4 hours earlier, with different seeds and therefore different prompts. All 237 TP8 requests succeeded. `--enable-dp-attention` was not tested.

| Input | Concurrency | Dual burst output, tok/s (range) | TP8 burst output, tok/s (range) | Dual / TP8 | Dual TTFT p50 (s) | TP8 TTFT p50 (s) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 32K | 1 | 247.4 (178.6-253.0) | 237.3 (187.9-241.4) | 1.04x | 0.98 | 1.14 |
| 32K | 8 | 1,053.5 (1,040.2-1,071.3) | 885.5 (879.5-950.3) | 1.19x | 3.02 | 4.63 |
| 32K | 16 | 1,598.2 (1,591.2-1,645.3) | 1,150.5 (802.3-1,171.8) | 1.39x | 5.80 | 11.47 |
| 128K | 1 | 249.4 (249.0-249.5) | 246.4 (242.8-246.7) | 1.01x | 3.91 | 4.29 |
| 128K | 16 | 835.8 (830.1-845.2) | 465.6 (420.4-469.9) | 1.80x | 27.55 | 58.80 |
| 512K | 8 | 233.7 (219.7-234.7) | 111.9 (111.9-112.3) | 2.09x | 64.62 | 137.85 |
| 512K | 16 | 245.9 (244.7-246.6) | 116.3 (97.9-117.9) | 2.11x | 126.75 | 271.15 |
| 1M | 1 | 11.8 (11.8-12.2) | 11.4 (11.3-11.4) | 1.04x | 40.60 | 43.56 |
| 1M | 4 | 25.8 (25.7-25.8) | 12.8 (12.7-12.8) | 2.02x | 58.85 | 157.91 |
| 1M | 8 | 26.8 (26.8-26.9) | 12.9 (12.8-12.9) | 2.09x | 95.06 | 180.25 |

Ranges are across the three repetitions. At c1 the ranges overlap at 32K; at 128K and 1M the dual server A is 1-4% faster. With more requests, the dual servers' advantage grows with input length: 1.19x and 1.39x at 32K, 1.80x at 128K concurrency 16, and 2.02-2.11x at 512K and 1M, with TTFT 37-53% of TP8's in those long-context cells. A plausible contributor, not quantified here, is prefill: the two servers run two prefill pipelines of 16,384 tokens per step, and the single engine runs one; the different prompts, sequential timing, and KV pool size are also uncontrolled. The result is consistent with the dual TP4/EP4 choice under the reference flags; a TP8 configuration tuned for its own prefill settings was not tested.

### FP8 against BF16 KV cache

A temporary BF16-KV server on GPUs 4-7 ran at the same time as the FP8 reference server A on GPUs 0-3, with the same flags and TRT-LLM DSA backends; only the KV dtype differed. Both received the full 1,319-problem GSM8K test set with exact numeric extraction after the final answer marker. Before scoring, a calibration cell with identical flags on both GPU halves showed a 0.99% difference in median decode-window TPOT (time per output token after the first streamed token).

| Condition | FP8 KV correct | BF16 KV correct | Only FP8 correct | Only BF16 correct | BF16 minus FP8 (95% CI, points) |
| --- | ---: | ---: | ---: | ---: | ---: |
| Effort omitted (checkpoint default `max`), 2,048-token limit | 1,283 (97.27%; Wilson 96.24-98.02) | 1,283 (97.27%; Wilson 96.24-98.02) | 6 | 6 | -0.51 to +0.51 |
| `reasoning_effort: low`, 1,024-token limit | 1,267 (96.06%; Wilson 94.87-96.98) | 1,272 (96.44%; Wilson 95.29-97.31) | 9 | 14 | -0.33 to +1.09 |

The paired difference is 0 points with effort omitted and +0.38 points for BF16 at low effort (exact McNemar p = 0.405); both confidence intervals include zero and bound the difference to about one point. With effort omitted, each arm had 2 answers that failed extraction and 2 completions that reached the limit. For run-to-run context, the August FP8 panel scored 1,284 and 1,272 in the same two conditions. The BF16 server booted with a 4,101,312-token KV pool against 7,362,048 for FP8, so FP8 holds 1.80x as many tokens at the same memory fraction. The [SGLang GLM-5.3-Flash cookbook](https://docs.sglang.io/cookbook/autoregressive/GLM/GLM-5.3-Flash) reports a similar result on GB300 for FP8 KV with TRT-LLM DSA against BF16 KV with TileLang DSA: GSM8K within noise and about 1.8x the KV capacity. This run measures FP8 against BF16 on B200 with the same DSA backend and paired items.

The largest request each KV type can admit is unresolved. Both arms accepted and answered 1,032,485-token prompts. The one built for the 1,000,000-token step came out 3.25% long, outside the 2% tolerance, so it is excluded; the steps labelled 1,040,000 and 1,048,320 tokens rendered at the same size, below their labels. From 128K to 768K, 9 of 24 retrieval requests reached the 256-token output limit before answering, and every request that finished within the limit returned the needle. No admission boundary is claimed, and the retrieval misses reflect the output limit.

### Request cap 16 against 48

A temporary FP8 server with `--max-running-requests 48` on GPUs 4-7 ran at the same time as reference server A (cap 16) on GPUs 0-3. The driver alternated requests of the same shape between the two arms, with distinct seeds, so the two arms received different prompts. All 576 requests succeeded.

| Input | Node concurrency | Per arm | Cap 16 burst output, tok/s (range) | Cap 16 TTFT p50 (s) | Cap 48 burst output, tok/s (range) | Cap 48 TTFT p50 (s) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 32K | 16 | 8 | 808.5 (806.1-960.9) | 4.28 | 738.8 (736.8-787.1) | 7.30 |
| 32K | 32 | 16 | 1,011.0 (925.1-1,024.3) | 8.29 | 993.5 (732.5-995.3) | 10.37 |
| 32K | 48 | 24 | 1,045.0 (1,032.6-1,057.0) | 11.97 | 1,121.3 (1,113.1-1,131.9) | 16.65 |
| 128K | 16 | 8 | 418.1 (413.1-421.6) | 27.60 | 381.7 (361.9-411.7) | 34.03 |
| 128K | 32 | 16 | 486.3 (478.1-499.6) | 51.15 | 406.8 (361.6-422.8) | 67.44 |
| 128K | 48 | 24 | 483.6 (483.2-516.9) | 51.36 | 418.3 (369.9-425.3) | 101.27 |

Burst output is per arm. Only the 24-per-arm rows exceed the 16-request cap. The cap-48 server was already slower at 8 per arm, where neither cap binds, and booted with a smaller KV pool (6,445,120 tokens); raising `--max-running-requests` also changes settings SGLang derives from it, which this run did not record, so the low-load difference is unexplained. The calibration cell that preceded this comparison used a separate temporary server with cap 16 and one 128K request per arm, and showed a 0.37% median decode-window TPOT difference between the GPU halves; it does not cover the cap-48 server or concurrent load. These rows compare two complete server configurations. The reference configuration keeps the 16-request cap because this comparison does not isolate the cap's effect.

### Direct engine against the loopback proxy

The same seeds ran back to back, directly to the servers and through a loopback copy of the nginx configuration without authentication. This measures proxy overhead only, not the public ingress. All 150 requests succeeded.

| Cell | Direct burst output (tok/s) | Proxy burst output (tok/s) | Direct TTFT p50 (s) | Proxy TTFT p50 (s) |
| --- | ---: | ---: | ---: | ---: |
| 128K, c1 | 250.5 | 251.3 | 3.87 | 3.87 |
| 128K, c16 | 836.3 | 830.9 | 27.53 | 27.42 |
| 512K, c8 | 234.3 | 233.9 | 64.62 | 64.49 |

Every proxy cell is within 1% of direct. Because these cells reused the grid's seeds, they also repeat three grid cells: 128K c16 gave 835.8 tok/s in the grid and 836.3 here.

### Poisson arrival ladder

SGLang's `sglang.bench_serving` tool at the pinned commit sent Poisson arrivals through the loopback proxy, using a coding-mix prompt set (7,950-127,953 input tokens, median about 32K). The pinned tool has no burstiness option, so no gamma-arrival run was possible. The ladder's stop rule compared completed requests per second, measured over each scored window, with the offered rate; below 90% ended the ladder. At target rates of 0.1, 0.2, and 0.4 requests/s, completions kept pace with 97.5-100% of the offered rate, with a p95 TTFT of 3.9-5.2 s. At a target of 0.8 requests/s the realized Poisson rate was 0.828, and 0.742 requests/s completed (89.6%, 0.4 points under the threshold) with a p95 TTFT of 11.1 s, so 1.2 and 1.6 requests/s did not run. The scored windows ran 236-292 s instead of the planned 300 s, and requests still in flight at the end of a window count against the completion rate, so the 0.8 requests/s stop is a bounded observation. TTFT here is `bench_serving`'s own measurement. A shared-prefix run of 4 groups × 8 sessions × 4 turns, with prompts of about 2,250 tokens and 256 output tokens, completed 128/128 with a p95 TTFT of 0.42 s.

### Anthropic Messages matrix

The 20-case Anthropic Messages matrix ran against the reference configuration, including the parser overlay. 17 of 20 cases passed; the other three:

- The tool-use case had a 64-token output limit and spent it on a thinking block, so no tool block could be returned. The case needs a larger limit before it says anything about tool calls.
- The case that sent `thinking: {"type": "enabled", "budget_tokens": 512}` returned HTTP 400, as the README documents for explicit `thinking: enabled` on this runtime. The README's recommended adaptive-thinking profile was not exercised by this case.
- A request carrying a 1×1-pixel PNG image block returned HTTP 500. A server error for a degenerate image suggests missing input validation. This run did not capture the error body or send the same image to `/v1/chat/completions`. Larger images were not tested in this matrix.

### Replica loss during streaming

For a replica-loss observation, four 32K streaming requests ran through a loopback proxy in front of reference server A and the temporary cap-48 server, two hashed to each. The temporary server was stopped with `docker stop` (SIGTERM, then SIGKILL after Docker's 10 s grace period) while its running-requests metric was positive. Its token stream stopped within 0.9 s of SIGTERM. The process did not exit within the grace period, and the stop took 10.6 s. Both requests on server A completed normally. Both streams on the stopped server sent no further events and closed when the process was killed, with no error event, finish reason, or usage. Their HTTP status stayed 200 because a streaming response sends its status at the start; nginx logged `upstream prematurely closed connection` for both. After the server restarted, two requests hashed to it succeeded. A client that checks only the HTTP status would count these truncated streams as successes; check `finish_reason` and usage. This is one observation, not a failover measurement.

### Not completed in this campaign

- **Prefix-cache study**: stopped at its first condition. One turn returned HTTP 200 with its entire 64-token budget spent on reasoning and no visible output, which fails the integrity rule. The August study in [Prefix reuse and session affinity](#prefix-reuse-and-session-affinity) remains the cache-affinity evidence.
- **Arrival ladder**: shortened windows; no gamma arrivals.
- **Anthropic Messages**: one invalid and two failed cases.
- **KV admission**: no boundary for either KV type.
- **Patch overhead**: no unpatched control ran.

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
The planned 32K topology arm did not run in August; only the 128K decision
records exist. The September 27 campaign added a same-day, sequential
current-stack comparison under the reference prefill settings:
[Same-day TP8/EP8 against dual TP4/EP4](#same-day-tp8ep8-against-dual-tp4ep4).

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
(5.5 s at c16), and 17-45 s across 400K-512K. Decode-window aggregate output at
c16 (not comparable to the September 27 burst output rate) was 2,434 tok/s at
16K down to 838 tok/s at 128K; about 504 tok/s at 1M c4. These rows ran on the
pinned image without the K-pool patch. A September 26 attempt to reproduce 1M
concurrency 4 on the pinned image ended in a scheduler out-of-memory error; the
patched September 27 grid completed it 12/12.

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
  output limits; they are not an FP8-versus-BF16 precision comparison. The
  paired BF16 control ran September 27:
  [FP8 against BF16 KV cache](#fp8-against-bf16-kv-cache).
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

- The August request-cap observation stays out of every performance
  table: seed, order, response mix, and output-token budget confound it. The
  September 27 cap comparison exceeds the cap at one load point only and
  compares complete server configurations.
- The August route comparison was not controlled (31 of 72 route identities
  mutated). The September 27 direct-versus-loopback-proxy comparison measures
  proxy overhead only; the public ingress path remains untested.
- The September 27 prefix-cache study, full-duration arrival ladder, gamma
  arrivals, three Anthropic Messages cases, and KV admission boundary did not
  complete; see [Not completed in this campaign](#not-completed-in-this-campaign).
- No direct per-request cache-hit attribution for the cache study.
- No maximum-capacity or saturation search. The September 27 Poisson ladder
  is bounded by shortened windows, and the only measured loads above
  concurrency 16 are the request-cap comparison's node loads of 32 and 48. No
  high-availability or failover test beyond the reboot rehearsal; no broad
  multimodal quality or maximum-image-size campaign.
- The 65,536 prefill chunk timed out and produced no accepted result.
- The Card A 32K topology arm did not run.

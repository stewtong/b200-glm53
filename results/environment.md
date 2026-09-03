# Environment

Software and hardware profile for the measurements in this repository, with
the historical benchmark environments kept separate from the current standing
endpoint state.

## Hardware (all campaigns)

- GPU: 8x NVIDIA B200, 183,359 MiB each, driver 580.173.02
- CPU: 160 vCPU (x86_64), host memory 1,722 GiB
- Host OS: Ubuntu 24.04.4 LTS, kernel 6.11.0-1016-nvidia
- GPU clocks and power limits: at factory defaults for the measured runs

## Software stack (all campaigns)

- Container image: `lmsysorg/sglang:glm-5.3-flash@sha256:3a97bd50034ca60c6e6c86b8e36a73675d261f6a5eb71197796aee5175409290`
- SGLang runtime: `0.0.0.dev1+gd6ab04bdf1`, source commit `d6ab04bdf1`
- Model: `zai-org/GLM-5.3-Flash` at revision `3f1971b7b5f7a528c9c4ef6212c8785298a8c24a`
- CUDA runtime: 13.0; PyTorch `2.13.0+cu130`; NCCL `(2, 29, 7)`
- Reverse proxy: nginx (alpine) in front of the two loopback engine listeners

## Historical benchmark environments (August 26-29)

Topology: two independent TP4/EP4 replicas. Replica A used GPUs 0-3 on
loopback port 30000; replica B used GPUs 4-7 on loopback port 30100. KV cache
was FP8 E4M3 with 7,362,048 tokens and 48.57 GB per replica at boot, plus 446
KDA/Mamba state slots per replica. The effective prefill chunk was 16,384
tokens in the boot state. Shared-experts fusion was disabled.

Every benchmark environment ran one tokenizer worker per replica. The
four-worker configuration did not exist until August 30, so no benchmark
number in this repository reflects it. The August 27-28 performance runs also
predate the September 2 tunnel migration.

## Standing endpoint (August 30 deployment, verified September 2, 2026)

Both replicas run the same engine command as the benchmark environments plus
`--tokenizer-worker-num 4`, adopted after the A/B in
`results/tokenizer-workers/`. The dated deployment verification is
`results/deployment-verification-2026-09-02.md`. Current-state facts in this
repository come from that record, not from the benchmark environments.

## Client versions

Client behavior in the OpenAI and Claude Code examples is observed with
specific versions and may change in later releases. The OpenAI Python SDK
example is the standard `chat.completions.create` path; Claude Code
compatibility was checked with the August 28, 2026 release as noted in the
README. Pin client versions when reproducing route-sensitive behavior.

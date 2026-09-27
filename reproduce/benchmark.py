#!/usr/bin/env python3
"""Public benchmark client for the audited serving-envelope request contract.

Reproduces the request shape behind results/serving-envelope/: streaming
/v1/chat/completions, temperature 0, low reasoning effort, a fixed output
length, and a cache-busted prompt built from the staged public-domain corpus.

The client requires caller-supplied credentials and never embeds a key:

  export GLM53F_OPENAI_BASE_URL="http://127.0.0.1:8080/v1"
  export GLM53F_API_KEY="<your-key>"
  python3 benchmark.py --cell H1 --output /tmp/h1-rows.jsonl

A failing run exits nonzero and leaves the output file untouched until every
row is computed. Timeouts, HTTP 429, and integrity failures are preserved as
rows and counted as failures.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CORPUS = REPO / "reproduce" / "corpus-federalist.txt"
CORPUS_SHA256 = "5a06783f628ed6faf07d78a8b7bf0f9fd1dc22ad13edc2bd5eeba0102af02690"

# The audited serving-envelope cells. Multi-request cells were sent through
# the two-replica proxy; H1 and H5 were single-request cells on replica A.
CELLS = {
    "H1": {"prompt_tokens": 131072, "completion_tokens": 2048, "concurrency": 1},
    "H2": {"prompt_tokens": 131072, "completion_tokens": 2048, "concurrency": 16},
    "H3": {"prompt_tokens": 409600, "completion_tokens": 2048, "concurrency": 8},
    "H4": {"prompt_tokens": 524288, "completion_tokens": 2048, "concurrency": 16},
    "H5": {"prompt_tokens": 1000000, "completion_tokens": 512, "concurrency": 1},
    "H6": {"prompt_tokens": 1000000, "completion_tokens": 512, "concurrency": 4},
}

CHARS_PER_TOKEN = 5.03  # empirical chars-per-token of the staged corpus


class BenchmarkError(Exception):
    pass


def require_inputs():
    base = os.environ.get("GLM53F_OPENAI_BASE_URL")
    key = os.environ.get("GLM53F_API_KEY")
    if not base:
        raise BenchmarkError("set GLM53F_OPENAI_BASE_URL to the endpoint base URL")
    if not key:
        raise BenchmarkError("set GLM53F_API_KEY to your endpoint key")
    return base.rstrip("/"), key


def build_prompt(target_tokens: int, nonce: str) -> str:
    """Cache-busted prompt: a unique marker line plus corpus text sized to the
    target token count. The unique marker prevents radix reuse across requests."""
    if not CORPUS.exists():
        raise BenchmarkError(f"missing corpus file {CORPUS}")
    digest = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    if digest != CORPUS_SHA256:
        raise BenchmarkError(f"corpus hash mismatch: {digest}")
    corpus = CORPUS.read_text()
    need = int(target_tokens * CHARS_PER_TOKEN)
    text = corpus
    while len(text) < need:
        text += "\n" + corpus
    return f"[benchmark nonce {nonce}]\n{text[:need]}"


def parse_sse(lines):
    """Yield (event_dict, raw_line) pairs from an SSE byte stream."""
    event = {}
    for raw in lines:
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        if line == "":
            if event:
                yield event
            event = {}
            continue
        if line.startswith("data:"):
            event.setdefault("data", []).append(line[5:].strip())
    if event:
        yield event


def stream_request(url, key, payload, timeout_s):
    """One streaming chat completion. Returns a result row."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{url}/chat/completions", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        })
    row = {
        "status": None, "error": None, "wall_s": None, "ttft_any_s": None,
        "finish_reason": None, "visible_chars": 0, "reasoning_chars": 0,
        "usage_prompt_tokens": None, "usage_completion_tokens": None,
        "integrity_ok": False, "integrity_reasons": [], "timeout": False,
        "is_429": False,
    }
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            row["status"] = resp.status
            if resp.status == 429:
                row["is_429"] = True
                row["integrity_reasons"].append("http_429")
                return row
            saw_first_event = False
            for event in parse_sse(resp):
                if not saw_first_event:
                    row["ttft_any_s"] = time.monotonic() - start
                    saw_first_event = True
                for data in event.get("data", []):
                    if data.strip() == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        row["integrity_reasons"].append("unparsable_chunk")
                        continue
                    choices = chunk.get("choices") or []
                    if choices:
                        delta = choices[0].get("delta") or {}
                        row["visible_chars"] += len(delta.get("content") or "")
                        row["reasoning_chars"] += len(delta.get("reasoning_content") or "")
                        if choices[0].get("finish_reason"):
                            row["finish_reason"] = choices[0]["finish_reason"]
                    if chunk.get("usage"):
                        row["usage_prompt_tokens"] = chunk["usage"].get("prompt_tokens")
                        row["usage_completion_tokens"] = chunk["usage"].get("completion_tokens")
    except urllib.error.HTTPError as e:
        e.close()  # release the error response body; unclosed, it warns at garbage collection
        row["status"] = e.code
        if e.code == 429:
            row["is_429"] = True
            row["integrity_reasons"].append("http_429")
        else:
            row["integrity_reasons"].append(f"http_{e.code}")
        row["error"] = f"HTTP {e.code}"
    except (TimeoutError, socket.timeout) as e:
        row["timeout"] = True
        row["error"] = f"timeout after {timeout_s}s"
    row["wall_s"] = round(time.monotonic() - start, 4)
    return row


def finalize_integrity(row, requested_completion):
    reasons = list(row["integrity_reasons"])
    if row["usage_completion_tokens"] != requested_completion:
        reasons.append("completion_token_count")
    if row["finish_reason"] != "length":
        reasons.append(f"finish_reason_{row['finish_reason']}")
    if row["visible_chars"] == 0:
        reasons.append("empty_visible_output")
    row["integrity_ok"] = not reasons
    row["integrity_reasons"] = reasons
    return row


def run_cell(base, key, cell, reps, timeout_s, out_path, flush=None):
    spec = CELLS[cell]
    rows = []
    lock = threading.Lock()

    def one(task):
        rep, idx = task
        seed = 100000 + rep * 1000 + idx
        nonce = hashlib.sha256(f"{cell}-{rep}-{idx}-{seed}".encode()).hexdigest()[:12]
        prompt = build_prompt(spec["prompt_tokens"], nonce)
        payload = {
            "model": "glm-5.3-flash",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "reasoning_effort": "low",
            "max_tokens": spec["completion_tokens"],
            "min_tokens": spec["completion_tokens"],
            "ignore_eos": True,
            "stream": True,
        }
        row = stream_request(base, key, payload, timeout_s)
        finalize_integrity(row, spec["completion_tokens"])
        row.update({
            "benchmark_id": f"{cell}-r{rep}-{idx}",
            "cell": cell, "repetition": rep, "seed": seed,
            "requested_prompt_tokens": spec["prompt_tokens"],
            "requested_completion_tokens": spec["completion_tokens"],
            "prompt_chars": len(prompt),
        })
        return row

    total_tasks = reps * spec["concurrency"]
    for rep in range(1, reps + 1):
        if flush:
            flush()
        tasks = [(rep, idx) for idx in range(spec["concurrency"])]
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=spec["concurrency"]) as ex:
            for row in ex.map(one, tasks):
                with lock:
                    rows.append(row)
                    failures = sum(1 for r in rows if not r["integrity_ok"])
                    print(f"  {row['benchmark_id']}: "
                          f"{'ok' if row['integrity_ok'] else 'FAIL ' + ','.join(row['integrity_reasons'])} "
                          f"({len(rows)}/{total_tasks} rows, {failures} failures)")
    rows.sort(key=lambda r: r["benchmark_id"])
    tmp = out_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    os.replace(tmp, out_path)
    failed = sum(1 for r in rows if not r["integrity_ok"])
    if failed:
        raise BenchmarkError(f"{failed} of {len(rows)} requests failed integrity")
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cell", required=True, choices=sorted(CELLS))
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=1800.0)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--flush-url", default=None,
                    help="optional /flush_cache endpoint called before each measured repetition")
    args = ap.parse_args()
    try:
        base, key = require_inputs()
    except BenchmarkError as e:
        print(f"benchmark: {e}", file=sys.stderr)
        return 2
    flush = None
    if args.flush_url:
        def flush():
            req = urllib.request.Request(args.flush_url, data=b"", method="POST",
                                         headers={"Authorization": f"Bearer {key}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status != 200:
                        raise BenchmarkError(
                            f"cache flush returned HTTP {resp.status}")
            except urllib.error.HTTPError as e:
                e.close()  # as above: release the error response body
                raise BenchmarkError(f"cache flush returned HTTP {e.code}")
    try:
        run_cell(base, key, args.cell, args.reps, args.timeout, args.output, flush)
    except (BenchmarkError, urllib.error.URLError) as e:
        print(f"benchmark FAILED (nonzero): {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

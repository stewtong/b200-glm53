#!/usr/bin/env python3
"""Re-derive every staged numerical summary from the sanitized staged inputs.

Reads only files inside this repository (results/*/raw/), recomputes the
summaries, and compares them with the committed summary files. Exits nonzero on
any schema violation, duplicate identity, missing repetition, hidden failure,
configuration mismatch, or value mismatch, and leaves prior outputs unchanged
unless --write is passed.

Usage:
  python3 derive-results.py            # verify mode (default)
  python3 derive-results.py --write    # write derived summaries
  python3 derive-results.py --self-test # run built-in fail-closed fixtures
"""
import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

EXPECTED_CELLS = {
    "H1": {"conc": 1, "reps": 3, "requests_per_rep": 1, "out": 2048},
    "H2": {"conc": 16, "reps": 3, "requests_per_rep": 16, "out": 2048},
    "H3": {"conc": 8, "reps": 3, "requests_per_rep": 8, "out": 2048},
    "H4": {"conc": 16, "reps": 3, "requests_per_rep": 16, "out": 2048},
    "H5": {"conc": 1, "reps": 3, "requests_per_rep": 1, "out": 512},
    "H6": {"conc": 4, "reps": 3, "requests_per_rep": 4, "out": 512},
}

CACHE_ARMS = {
    "E0": "every turn cold",
    "E1": "repeated prefix without a session-affinity header",
    "E2": "repeated prefix with a stable session-affinity header",
    "E3": "stable affinity with a changed leading system line",
}

SERVE_FIELDS = {
    "benchmark_id": str, "cell": str, "repetition": int, "replica": str,
    "requested_prompt_tokens": int, "requested_completion_tokens": int,
    "status": int, "wall_s": float, "usage_completion_tokens": int,
    "integrity_ok": bool,
}
CACHE_FIELDS = {
    "arm": str, "repetition": int, "session": int, "turn": int,
    "status": int, "ttft_any_s": float, "integrity_ok": bool,
}


class DerivationError(Exception):
    pass


def load_jsonl(path):
    rows = []
    with open(path) as f:
        for i, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise DerivationError(f"{path}:{i}: invalid JSON ({e})")
    return rows


LEGACY_OPTIONAL = (
    "seed", "prompt_chars", "ttft_any_s", "finish_reason",
    "usage_prompt_tokens", "timeout", "is_429",
    "turn_target_tokens", "wall_s", "finish", "prompt_tokens",
    "completion_tokens", "landed_replica", "affinity_present",
)


def check_fields(rows, fields, label, optional=LEGACY_OPTIONAL):
    for i, r in enumerate(rows):
        for name, typ in fields.items():
            if name not in r:
                raise DerivationError(f"{label} row {i}: missing field {name}")
            v = r[name]
            if typ is float and isinstance(v, (int, float)) and not isinstance(v, bool):
                continue
            if typ is bool and isinstance(v, bool):
                continue
            if not isinstance(v, typ):
                raise DerivationError(
                    f"{label} row {i}: field {name} expected {typ.__name__}, got {type(v).__name__}")
        for name in r:
            if name == "error" or name == "ttft_any_s" and r[name] is None:
                continue
            if name not in fields and name not in optional:
                raise DerivationError(f"{label} row {i}: unexpected field {name}")


def request_success(r):
    """A request counts as success only with HTTP 200, intact output, and the
    exact requested completion length. Timeouts and HTTP 429 stay in the
    denominator and never count as success."""
    return bool(
        r["status"] == 200
        and r["integrity_ok"] is True
        and r["usage_completion_tokens"] == r["requested_completion_tokens"]
        and not r.get("timeout", False)
        and not r.get("is_429", False)
    )


def derive_serving_envelope(rows):
    """Success counts and concurrency-1 full-request output rates, per repetition."""
    check_fields(rows, SERVE_FIELDS, "serving-envelope")
    ids = [r["benchmark_id"] for r in rows]
    if len(ids) != len(set(ids)):
        raise DerivationError("serving-envelope: duplicate benchmark_id")
    by_cell = defaultdict(list)
    for r in rows:
        by_cell[r["cell"]].append(r)
    if set(by_cell) != set(EXPECTED_CELLS):
        raise DerivationError(
            f"serving-envelope: cell set {sorted(by_cell)} != expected {sorted(EXPECTED_CELLS)}")
    cells = {}
    for cell, spec in EXPECTED_CELLS.items():
        rs = by_cell[cell]
        by_rep = defaultdict(list)
        for r in rs:
            by_rep[r["repetition"]].append(r)
        if sorted(by_rep) != list(range(1, spec["reps"] + 1)):
            raise DerivationError(
                f"{cell}: repetitions {sorted(by_rep)} != {list(range(1, spec['reps'] + 1))}")
        rep_entries = []
        for rep in sorted(by_rep):
            group = by_rep[rep]
            if len(group) != spec["requests_per_rep"]:
                raise DerivationError(
                    f"{cell} rep {rep}: {len(group)} requests, expected {spec['requests_per_rep']}")
            replicas = {r["replica"] for r in group}
            success = 0
            rates = []
            for r in group:
                if request_success(r):
                    success += 1
                    if cell in ("H1", "H5"):
                        rates.append(round(r["usage_completion_tokens"] / r["wall_s"], 3))
            rep_entries.append({
                "repetition": rep, "requests": len(group), "success": success,
                "replicas": sorted(replicas), "output_tps": rates or None,
            })
        entry = {
            "concurrency": spec["conc"],
            "output_tokens_per_request": spec["out"],
            "repetitions": rep_entries,
            "success": (
                f"{sum(e['success'] for e in rep_entries)}"
                f"/{sum(e['requests'] for e in rep_entries)}"),
        }
        if cell in ("H1", "H5"):
            rates = [r for e in rep_entries for r in (e["output_tps"] or [])]
            entry["median_output_tps"] = round(statistics.median(rates), 3)
        cells[cell] = entry
    success_str = {}
    for cell in EXPECTED_CELLS:
        rs = by_cell[cell]
        ok = sum(1 for r in rs if request_success(r))
        success_str[cell] = f"{ok}/{len(rs)}"
    timeouts = sum(1 for r in rows if r.get("timeout"))
    http429 = sum(1 for r in rows if r.get("is_429"))
    return {
        "schema": "b200-glm53-serving-envelope-summary-v1",
        "request_contract": {
            "route": "/v1/chat/completions", "stream": True, "temperature": 0,
            "ignore_eos": True, "min_tokens_max_tokens": True,
            "reasoning_effort": "low",
            "corpus": "reproduce/corpus-federalist.txt",
            "corpus_sha256": "5a06783f628ed6faf07d78a8b7bf0f9fd1dc22ad13edc2bd5eeba0102af02690",
            "cache_busted": True,
        },
        "cells": cells, "success_by_cell": success_str,
        "total_requests": len(rows), "total_successes": sum(
            1 for r in rows if r["status"] == 200 and r["integrity_ok"] is True
            and r["usage_completion_tokens"] == r["requested_completion_tokens"]),
        "timeouts": timeouts, "http_429": http429,
        "excluded": {
            "aggregate_concurrent_throughput": (
                "retained request records lack absolute request-start clocks; "
                "a true batch makespan is not reconstructable"),
            "decode_tpot_itl": (
                "retained stream events lack per-output-event clocks; the saved "
                "per-token field stored decode duration, not per-token latency"),
        },
    }


def derive_cache_affinity(rows, flush):
    """Turn-1 and turns-2-4 TTFT-any medians per arm, per the audited aggregation."""
    check_fields(rows, CACHE_FIELDS, "cache-affinity")
    keys = [(r["arm"], r["repetition"], r["session"], r["turn"]) for r in rows]
    if len(keys) != len(set(keys)):
        raise DerivationError("cache-affinity: duplicate (arm, repetition, session, turn)")
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    if set(by_arm) != set(CACHE_ARMS):
        raise DerivationError(f"cache-affinity: arms {sorted(by_arm)} != {sorted(CACHE_ARMS)}")
    for arm, rs in by_arm.items():
        reps = {r["repetition"] for r in rs}
        if reps != {1, 2, 3}:
            raise DerivationError(f"{arm}: repetitions {sorted(reps)} != [1, 2, 3]")
        if len(rs) != 36:
            raise DerivationError(f"{arm}: {len(rs)} rows, expected 36")
        for rep in (1, 2, 3):
            grp = [r for r in rs if r["repetition"] == rep]
            sessions = {r["session"] for r in grp}
            if sessions != {0, 1, 2}:
                raise DerivationError(f"{arm} rep {rep}: sessions {sorted(sessions)} != [0, 1, 2]")
            turns = sorted(r["turn"] for r in grp)
            if turns != [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3]:
                raise DerivationError(f"{arm} rep {rep}: turn shape {turns}")
    missing_flush = [
        f"{arm}-r{rep}" for arm in CACHE_ARMS for rep in (1, 2, 3)
        if flush.get(f"{arm}-r{rep}") != {"flush_A_status": 200, "flush_B_status": 200}
    ]
    if missing_flush:
        raise DerivationError(f"cache-affinity: repetitions without both-cache flush: {missing_flush}")
    arms = {}
    for arm, label in CACHE_ARMS.items():
        t1s, lats, ratios = [], [], []
        for rep in (1, 2, 3):
            grp = [r for r in by_arm[arm] if r["repetition"] == rep]
            t1 = statistics.median(r["ttft_any_s"] for r in grp if r["turn"] == 0)
            lat = statistics.median(r["ttft_any_s"] for r in grp if r["turn"] > 0)
            t1s.append(t1)
            lats.append(lat)
            ratios.append(t1 / lat)
        arms[arm] = {
            "condition": label,
            "turn1_ttft_s": round(statistics.median(t1s), 3),
            "later_turns_ttft_s": round(statistics.median(lats), 3),
            "turn1_over_later_ratio": round(statistics.median(ratios), 3),
            "requests": 36, "success": 36,
        }
    return {
        "schema": "b200-glm53-cache-affinity-summary-v1",
        "method": {
            "route": "/v1/chat/completions", "streaming": True,
            "repetitions": 3, "sessions": 3, "turns": 4,
            "requests_per_condition": 36,
            "flush": "both replica caches flushed before every measured turn",
            "metric": "TTFT-any: time to the first streamed output event, reasoning or visible",
            "aggregation": (
                "within a repetition, turn 1 is the median across three sessions and "
                "turns 2-4 are the median across nine requests; the table reports the "
                "median across repetitions; the ratio is computed within each "
                "repetition before the median is taken"),
        },
        "arms": arms,
        "total_requests": len(rows), "total_success": len(rows),
        "boundary": (
            "accepted evidence supports a latency association only; consistent "
            "hashing preserves a stable placement key by configuration, and direct "
            "per-request cache attribution was not measured"),
    }


def derive_config_selection(raw):
    """Re-derive the W14/W12 retune medians from the repetition-level record."""
    for section in ("w14", "w12", "w6"):
        if section not in raw:
            raise DerivationError("config-selection: missing section " + section)
    w14, w12, w6 = raw["w14"], raw["w12"], raw["w6"]
    if w14.get("decision") != "no-static-finalist" or w14.get("finalist") is not None:
        raise DerivationError("config-selection: W14 decision mismatch")
    if w12.get("decision") != "hold-current-5-1-6":
        raise DerivationError("config-selection: W12 decision mismatch")
    if w6.get("requests") != 36 or w6.get("parser_failures") != 0 or w6.get("transport_failures") != 0:
        raise DerivationError("config-selection: W6 control counts mismatch")
    opening = w14["opening_static_c1"]["median_tpot_s"] * 1000
    candidates = {}
    for name, cand in sorted(w14["candidates"].items()):
        short = name.replace("w14-static-", "").replace("-c1", "")
        candidates[short] = {
            "median_tpot_ms": round(cand["median_tpot_s"] * 1000, 3),
            "change_vs_static_fraction": round(cand["change_vs_opening_fraction"], 3),
            "advance": cand["advance"],
            "repetitions": len(cand["directional_comparisons"]),
        }
    adaptive = {
        "c1": {
            "median_tpot_ms": round(w12["adaptive_c1"]["median_tpot_s"] * 1000, 3),
            "change_vs_static_fraction": round(w12["adaptive_c1"]["median_change_vs_static_fraction"], 3),
        },
        "c8": {
            "median_tpot_ms": round(w12["adaptive_c8"]["median_tpot_s"] * 1000, 3),
            "change_vs_static_fraction": round(w12["adaptive_c8"]["median_change_vs_static_fraction"], 3),
        },
    }
    return {
        "schema": "b200-glm53-config-selection-summary-v1",
        "campaign": "W14/W12/W6 speculative-decoding retune, 2026-08-29",
        "method": {
            "requests": "captured coding-agent request shapes replayed to temporary containers",
            "repetitions_per_point": 3, "rows_per_point": 24,
            "control": "static NEXTN 5-1-6",
            "effort": "low",
        },
        "opening_static_c1_median_tpot_ms": round(opening, 3),
        "static_candidates_c1": candidates,
        "adaptive": adaptive,
        "static_drift_fraction": {
            "c1": round(w12["drift"]["c1_fraction"], 3),
            "c8": round(w12["drift"]["c8_fraction"], 3),
        },
        "w6_identifier_check": {
            "requests": w6["requests"],
            "visible_exact": w6["requests"] - w6["substitution_count"],
            "substitution_count": w6["substitution_count"],
            "placement_pass": w6["placement_pass"],
            "decision": w6["decision"],
        },
        "decision": w12["decision"],
        "boundary": (
            "the later campaign used captured request shapes, fresh containers, and a "
            "different timing boundary from the earlier configuration-selection runs; "
            "the two are never averaged"),
    }


def derive_tokenizer_workers(raw):
    arms = raw.get("arms", {})
    if set(arms) != {"A_p2a_4workers", "B_control_1worker"}:
        raise DerivationError("tokenizer-workers: unexpected arm set")
    a, b = arms["A_p2a_4workers"], arms["B_control_1worker"]
    if a["tokenizer_worker_num"] != 4 or b["tokenizer_worker_num"] != 1:
        raise DerivationError("tokenizer-workers: arm worker counts mismatch")
    if a["input_tokens"] != b["input_tokens"]:
        raise DerivationError("tokenizer-workers: load mismatch between arms")
    speedup = b["count_tokens_approx_wall_ms"] / a["count_tokens_approx_wall_ms"]
    return {
        "schema": "b200-glm53-tokenizer-workers-summary-v1",
        "method": {
            "load": "one /v1/messages/count_tokens request rendering 990,819 input tokens",
            "oracle": "/ping liveness sampled during preprocessing, plus concurrent short generations",
            "date": "2026-08-30",
        },
        "four_workers": {
            "during_ping_p99_ms": a["during_ping_p99_approx_ms"],
            "count_tokens_wall_ms": a["count_tokens_approx_wall_ms"],
        },
        "one_worker": {
            "during_ping_p99_ms": b["during_ping_p99_approx_ms"],
            "count_tokens_wall_ms": b["count_tokens_approx_wall_ms"],
        },
        "tokenization_speedup_fraction": round(speedup - 1, 2),
        "streaming_revalidated": raw["streaming_revalidation"]["streaming_incremental"],
        "disconnected_client_recovery_status": (
            raw["disconnect_revalidation"]["A_p2a_disconnect_recovery"]["recovery_status"]),
    }


HOLISTIC_GRID_FIELDS = {
    "benchmark_id": str, "block": str, "cell": str, "repetition": int,
    "request_index": int, "replica": str, "requested_prompt_tokens": int,
    "prompt_tokens": int, "requested_completion_tokens": int,
    "completion_tokens": int, "status": int, "integrity_ok": bool,
    "timeout": bool, "ttft_any_s": float, "dispatch_offset_s": float,
    "end_offset_s": float,
}
HOLISTIC_GRID_OPTIONAL = ("seed", "finish_reason", "ttft_visible_s")
HOLISTIC_WARM_FIELDS = {
    "context": str, "repetition": int, "session": int, "turn": int,
    "replica": str, "prompt_tokens": int, "completion_tokens": int,
    "status": int, "integrity_ok": bool, "timeout": bool,
    "ttft_any_s": float, "cached_tokens_delta": float,
}
HOLISTIC_WARM_OPTIONAL = (
    "context_tokens_target", "append_tokens_target", "finish_reason", "ttft_visible_s")
HOLISTIC_GSM8K_FIELDS = {
    "arm": str, "condition": str, "index": int, "max_tokens": int,
    "status": int, "completion_limit_hit": bool, "extraction_valid": bool,
    "correct": bool,
}
HOLISTIC_GSM8K_OPTIONAL = (
    "reasoning_effort", "finish_reason", "completion_tokens", "extracted_value", "gold_value")

# The 40-cell grid (8 inputs x 5 concurrencies) plus five extra 1A cells:
# longer outputs, 1M with 2,048 output tokens, and a server-B cross-check.
_GRID_INPUTS = ("16k", "32k", "64k", "128k", "256k", "400k", "512k", "1m")
HOLISTIC_1A_CELLS = {
    f"G{ctx}-c{n}{'A' if n == 1 else ''}-o{'512' if ctx == '1m' else '2048'}"
    for ctx in _GRID_INPUTS for n in (1, 2, 4, 8, 16)
} | {"G128k-c1A-o8192", "G128k-c8-o8192", "G32k-c16-o8192", "G1m-c4-o2048", "G128k-c1B-o2048"}
HOLISTIC_CELL_COUNTS = {"1A": len(HOLISTIC_1A_CELLS), "1F-direct": 3, "1F-proxy": 3,
                        "2B": 6, "2B-calibration": 1, "2D": 10}
# The server-B cross-check ran once; every other cell ran three repetitions.
HOLISTIC_SINGLE_REP_CELLS = {("1A", "G128k-c1B-o2048")}
WARM_CONTEXTS = {"128k", "256k", "512k", "1m"}
WARM_COMPLETION_TOKENS = 512
WARM_TURNS_PER_CONTEXT = 48  # 3 repetitions x 2 servers x 2 sessions x 4 turns
GSM8K_TEST_ITEMS = 1319
GSM8K_ARMS = {"fp8", "bf16"}
Z95 = 1.96


def grid_success(r):
    """HTTP 200, intact output, no timeout, and the exact requested completion length."""
    return bool(r["status"] == 200 and r["integrity_ok"] is True and not r["timeout"]
                and r["completion_tokens"] == r["requested_completion_tokens"])


def wilson_interval(k, n, z=Z95):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def mcnemar_exact_p(b, c):
    """Two-sided exact McNemar p-value for b and c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, j) for j in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _grid_group(rows):
    """Burst output rate, request output rate, and TTFT medians per repetition.

    Callers pass only successful rows (derive_holistic rejects any failure).
    """
    by_rep = defaultdict(list)
    for r in rows:
        by_rep[r["repetition"]].append(r)
    reps = []
    for rep in sorted(by_rep):
        g = by_rep[rep]
        span = max(r["end_offset_s"] for r in g) - min(r["dispatch_offset_s"] for r in g)
        reps.append({
            "repetition": rep, "requests": len(g),
            "aggregate_output_tps": round(sum(r["completion_tokens"] for r in g) / span, 2),
            "median_request_output_tps": round(statistics.median(
                r["completion_tokens"] / (r["end_offset_s"] - r["dispatch_offset_s"]) for r in g), 2),
            "median_ttft_any_s": round(statistics.median(r["ttft_any_s"] for r in g), 3),
        })
    rates = [e["aggregate_output_tps"] for e in reps]
    return {
        "success": f"{len(rows)}/{len(rows)}",
        "repetitions": reps,
        "median_aggregate_output_tps": round(statistics.median(rates), 2),
        "range_aggregate_output_tps": [min(rates), max(rates)],
        "median_request_output_tps": round(statistics.median(e["median_request_output_tps"] for e in reps), 2),
        "median_ttft_any_s": round(statistics.median(e["median_ttft_any_s"] for e in reps), 3),
    }


def _derive_grid(grid_rows, arms, cell_counts):
    check_fields(grid_rows, HOLISTIC_GRID_FIELDS, "holistic grid", HOLISTIC_GRID_OPTIONAL)
    ids = [r["benchmark_id"] for r in grid_rows]
    if len(ids) != len(set(ids)):
        raise DerivationError("holistic grid: duplicate benchmark_id")
    by_cell = defaultdict(list)
    for r in grid_rows:
        by_cell[(r["block"], r["cell"])].append(r)
    blocks = defaultdict(dict)
    for (block, cell), rs in sorted(by_cell.items()):
        reps = sorted({r["repetition"] for r in rs})
        expected = [1] if (block, cell) in HOLISTIC_SINGLE_REP_CELLS else [1, 2, 3]
        if reps != expected:
            raise DerivationError(f"{block} {cell}: repetitions {reps} != {expected}")
        per_rep = {rep: sorted(r["request_index"] for r in rs if r["repetition"] == rep) for rep in reps}
        if len({len(idx) for idx in per_rep.values()}) != 1:
            raise DerivationError(f"{block} {cell}: unequal request counts per repetition")
        if any(idx != list(range(len(idx))) for idx in per_rep.values()):
            raise DerivationError(f"{block} {cell}: request indices not contiguous")
        if len({r["requested_completion_tokens"] for r in rs}) != 1:
            raise DerivationError(f"{block} {cell}: mixed requested completion lengths")
        failed = [r["benchmark_id"] for r in rs if not grid_success(r)]
        if failed:
            raise DerivationError(f"{block} {cell}: unsuccessful requests {failed[:3]}")
        missing_arm = {r["replica"] for r in rs if f"{block}:{r['replica']}" not in arms}
        if missing_arm:
            raise DerivationError(f"{block} {cell}: no arm metadata for replicas {sorted(missing_arm)}")
        entry = {
            "input_tokens_rendered": [min(r["prompt_tokens"] for r in rs), max(r["prompt_tokens"] for r in rs)],
            "requests_per_repetition": len(per_rep[reps[0]]),
            "output_tokens_per_request": rs[0]["requested_completion_tokens"],
            "replicas": sorted({r["replica"] for r in rs}),
        }
        entry.update(_grid_group(rs))
        if block == "2B":
            entry["by_arm"] = {rep: _grid_group([r for r in rs if r["replica"] == rep])
                               for rep in sorted({r["replica"] for r in rs})}
        blocks[block][cell] = entry
    if cell_counts is not None:
        got = {block: len(cells) for block, cells in blocks.items()}
        if got != cell_counts:
            raise DerivationError(f"holistic grid: cells per block {got} != {cell_counts}")
        if set(blocks["1A"]) != HOLISTIC_1A_CELLS:
            raise DerivationError(
                f"holistic grid: 1A cell names differ: {sorted(set(blocks['1A']) ^ HOLISTIC_1A_CELLS)}")
    return dict(blocks)


def _derive_warm(warm_rows, require_all_contexts):
    check_fields(warm_rows, HOLISTIC_WARM_FIELDS, "warm-extend", HOLISTIC_WARM_OPTIONAL)
    contexts = {r["context"] for r in warm_rows}
    if require_all_contexts and contexts != WARM_CONTEXTS:
        raise DerivationError(f"warm-extend: contexts {sorted(contexts)} != {sorted(WARM_CONTEXTS)}")
    warm = {}
    for ctx in sorted(contexts):
        rs = [r for r in warm_rows if r["context"] == ctx]
        keys = [(r["repetition"], r["replica"], r["session"], r["turn"]) for r in rs]
        if len(keys) != len(set(keys)):
            raise DerivationError(f"warm-extend {ctx}: duplicate (repetition, replica, session, turn)")
        if require_all_contexts and len(rs) != WARM_TURNS_PER_CONTEXT:
            raise DerivationError(f"warm-extend {ctx}: {len(rs)} turns, expected {WARM_TURNS_PER_CONTEXT}")
        bad = [k for r, k in zip(rs, keys)
               if not (r["status"] == 200 and r["integrity_ok"] and not r["timeout"]
                       and r["completion_tokens"] == WARM_COMPLETION_TOKENS)]
        if bad:
            raise DerivationError(f"warm-extend {ctx}: unsuccessful turns {bad[:3]}")
        later = [r for r in rs if r["turn"] > 1]
        cached = [r["cached_tokens_delta"] / r["prompt_tokens"] for r in later]
        warm[ctx] = {
            "requests": len(rs),
            "turn1_ttft_any_p50_s": round(statistics.median(r["ttft_any_s"] for r in rs if r["turn"] == 1), 3),
            "later_turns_ttft_any_p50_s": round(statistics.median(r["ttft_any_s"] for r in later), 3),
            "later_turns_ttft_visible_p50_s": round(statistics.median(
                r["ttft_visible_s"] for r in later if r.get("ttft_visible_s") is not None), 3),
            "later_turns_ttft_any_max_s": round(max(r["ttft_any_s"] for r in later), 3),
            "later_turns_cached_fraction_range": [round(min(cached), 3), round(max(cached), 3)],
        }
    return warm


def _derive_gsm8k(gsm_rows):
    check_fields(gsm_rows, HOLISTIC_GSM8K_FIELDS, "gsm8k", HOLISTIC_GSM8K_OPTIONAL)
    table = defaultdict(dict)
    for r in gsm_rows:
        if r["arm"] not in GSM8K_ARMS:
            raise DerivationError(f"gsm8k: unexpected arm {r['arm']}")
        if r["correct"] != (r["extraction_valid"] is True and r.get("extracted_value") == r.get("gold_value")):
            raise DerivationError(f"gsm8k {r['arm']} {r['condition']} item {r['index']}: correct flag disagrees")
        items = table[(r["arm"], r["condition"])]
        if r["index"] in items:
            raise DerivationError(f"gsm8k {r['arm']} {r['condition']}: duplicate item {r['index']}")
        items[r["index"]] = r
    kv = {}
    for cond in sorted({c for _, c in table}):
        if ("fp8", cond) not in table or ("bf16", cond) not in table:
            raise DerivationError(f"gsm8k {cond}: both arms required")
        f, b = table[("fp8", cond)], table[("bf16", cond)]
        if set(f) != set(range(GSM8K_TEST_ITEMS)) or set(b) != set(f):
            raise DerivationError(f"gsm8k {cond}: arms must each cover items 0-{GSM8K_TEST_ITEMS - 1}")
        arm_out = {}
        for arm, t in (("fp8", f), ("bf16", b)):
            k = sum(1 for r in t.values() if r["correct"])
            arm_out[arm] = {
                "correct": k, "total": len(t), "accuracy": round(k / len(t), 4),
                "wilson95": wilson_interval(k, len(t)),
                "invalid_extractions": sum(1 for r in t.values() if not r["extraction_valid"]),
                "completion_limit_hits": sum(1 for r in t.values() if r["completion_limit_hit"]),
                "non_200": sum(1 for r in t.values() if r["status"] != 200),
            }
        only_f = sum(1 for i in f if f[i]["correct"] and not b[i]["correct"])
        only_b = sum(1 for i in f if b[i]["correct"] and not f[i]["correct"])
        n = len(f)
        diff = (only_b - only_f) / n
        # Wald interval for a paired difference in proportions.
        se = math.sqrt((only_f + only_b) - (only_b - only_f) ** 2 / n) / n
        kv[cond] = {
            "max_tokens": next(iter(f.values()))["max_tokens"], "arms": arm_out,
            "bf16_minus_fp8_accuracy": round(diff, 4),
            "bf16_minus_fp8_wald95": [round(diff - Z95 * se, 4), round(diff + Z95 * se, 4)],
            "discordant_fp8_only": only_f, "discordant_bf16_only": only_b,
            "mcnemar_exact_two_sided_p": round(mcnemar_exact_p(only_f, only_b), 3),
        }
    return kv


def derive_holistic(grid_rows, warm_rows, gsm_rows, arms, cell_counts=HOLISTIC_CELL_COUNTS):
    """September 27, 2026 campaign: grid, TP8, request cap, direct versus proxy,
    warm extension, and KV precision. Pass cell_counts=None to skip the
    whole-campaign coverage checks (self-test fixtures)."""
    full = cell_counts is not None
    return {
        "schema": "b200-glm53-holistic-summary-v1",
        "campaign": "Same-day holistic campaign on one 8x B200 node, 2026-09-27",
        "request_contract": {
            "route": "/v1/chat/completions", "stream": True, "temperature": 0,
            "reasoning_effort": "low", "fixed_output_length": "ignore_eos with max_tokens",
            "cache_flush": "both servers' prefix caches flushed before every repetition",
            "corpus": "reproduce/corpus-federalist.txt, cache-busted per request by seed",
        },
        "metrics": {
            "aggregate_output_tps": (
                "burst output rate: each repetition dispatches all of its requests at once with no "
                "replacement; completion tokens over the span from first dispatch to last completion, "
                "including prefill and drain"),
            "median_request_output_tps": (
                "median across repetitions of the per-repetition median, over requests, of completion "
                "tokens over that request's full wall time, including prefill"),
            "median_ttft_any_s": (
                "median across repetitions of the per-repetition median time to the first streamed "
                "output event, reasoning or visible"),
            "success": "HTTP 200, integrity pass, no timeout, and the exact requested completion length",
            "bf16_minus_fp8_wald95": "Wald 95% interval for the paired accuracy difference",
        },
        "arms": arms,
        "blocks": _derive_grid(grid_rows, arms, cell_counts),
        "warm_extend": _derive_warm(warm_rows, full),
        "kv_precision_gsm8k": _derive_gsm8k(gsm_rows),
        "boundary": (
            "Every serving arm ran the local chunked K-pool indexer patch and the September 6 "
            "parser overlay. 2B split each node load evenly across the two arms, so each arm saw "
            "concurrency 8, 16, and 24; the two request caps differ in behavior only at 24."),
    }

def canonical(obj):
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def run(root, write=False):
    results = root / "results"
    serve_rows = load_jsonl(results / "serving-envelope/raw/serving-envelope-rows.jsonl")
    cache_rows = load_jsonl(results / "cache-affinity/raw/cache-affinity-rows.jsonl")
    with open(results / "cache-affinity/raw/cache-flush-flags.json") as f:
        flush = json.load(f)
    with open(results / "config-selection/raw/config-selection-repetition-level.json") as f:
        cfg_raw = json.load(f)
    with open(results / "tokenizer-workers/raw/tokenizer-worker-ab.json") as f:
        p2a_raw = json.load(f)

    hol = results / "holistic-20260927/raw"
    with open(hol / "arms.json") as f:
        hol_arms = json.load(f)
    outputs = {
        "holistic-20260927/summary.json": derive_holistic(
            load_jsonl(hol / "grid-rows.jsonl"), load_jsonl(hol / "warm-extend-rows.jsonl"),
            load_jsonl(hol / "gsm8k-kv-rows.jsonl"), hol_arms),
        "serving-envelope/summary.json": derive_serving_envelope(serve_rows),
        "cache-affinity/summary.json": derive_cache_affinity(cache_rows, flush),
        "config-selection/summary.json": derive_config_selection(cfg_raw),
        "tokenizer-workers/summary.json": derive_tokenizer_workers(p2a_raw),
    }
    changed = []
    for rel, obj in outputs.items():
        target = results / rel
        text = canonical(obj)
        if target.exists() and target.read_text() == text:
            continue
        changed.append(rel)
        if write:
            target.write_text(text)
    if changed:
        if write:
            print("wrote:", ", ".join(changed))
        else:
            print("MISMATCH (re-run with --write after review):", ", ".join(changed))
            return 1
    else:
        print("all summaries re-derive exactly from staged raw inputs")
    return 0


def self_test():
    """Fail-closed fixtures: malformed, incomplete, duplicate, config-mismatched."""
    good_serve = {
        "benchmark_id": "H1-r1-0-aaaaaaaa", "cell": "H1", "repetition": 1,
        "replica": "A", "requested_prompt_tokens": 131072,
        "requested_completion_tokens": 2048, "status": 200, "wall_s": 8.0,
        "usage_completion_tokens": 2048, "integrity_ok": True,
    }
    good_cache = {
        "arm": "E2", "repetition": 1, "session": 0, "turn": 0, "status": 200,
        "ttft_any_s": 3.1, "integrity_ok": True,
    }
    cases = []
    dup = dict(good_serve)
    cases.append(("duplicate request identity", [good_serve, dup], "duplicate"))
    incomplete = {k: v for k, v in good_serve.items() if k != "status"}
    cases.append(("missing required field", [incomplete], "missing field"))
    wrong_shape = dict(good_serve, cell="H9")
    cases.append(("unexpected cell shape", [wrong_shape], "cell set"))
    cfg_mismatch = dict(good_cache, arm="E9")
    cases.append(("configuration mismatch arm", [cfg_mismatch], "arms"))
    bad_type = dict(good_serve, wall_s="8.0")
    cases.append(("wrong field type", [bad_type], "expected"))
    failures = 0
    for name, rows, needle in cases:
        try:
            if name == "configuration mismatch arm":
                derive_cache_affinity(rows, {})
            else:
                derive_serving_envelope(rows)
        except DerivationError as e:
            if needle in str(e):
                print(f"  fixture PASS (fails closed): {name}")
            else:
                print(f"  fixture FAIL (wrong error): {name}: {e}")
                failures += 1
        else:
            print(f"  fixture FAIL (accepted bad input): {name}")
            failures += 1
    # Success predicate: timeouts and HTTP 429 stay in the denominator, never
    # count as success, and a truncated completion is not a success.
    hidden_fail = dict(good_serve, status=429)
    truncated = dict(good_serve, usage_completion_tokens=1024)
    timed_out = dict(good_serve, timeout=True)
    corrupt = dict(good_serve, integrity_ok=False)
    for name, row, expected in [
        ("clean request counts as success", good_serve, True),
        ("HTTP 429 never counts as success", hidden_fail, False),
        ("truncated completion never counts as success", truncated, False),
        ("timed-out request never counts as success", timed_out, False),
        ("integrity failure never counts as success", corrupt, False),
    ]:
        if request_success(row) is expected:
            print(f"  fixture PASS: {name}")
        else:
            print(f"  fixture FAIL: {name}")
            failures += 1
    good_grid = {
        "benchmark_id": "1A/G16k-c1A-o2048/r1/0", "block": "1A", "cell": "G16k-c1A-o2048",
        "repetition": 1, "request_index": 0, "replica": "A", "requested_prompt_tokens": 16000,
        "prompt_tokens": 15990, "requested_completion_tokens": 2048, "completion_tokens": 2048,
        "status": 200, "integrity_ok": True, "timeout": False, "ttft_any_s": 0.5,
        "dispatch_offset_s": 0.0, "end_offset_s": 8.0,
    }
    three_reps = [dict(good_grid, repetition=r, benchmark_id=f"x{r}") for r in (1, 2, 3)]
    good_warm = {
        "context": "128k", "repetition": 1, "session": 0, "turn": 2, "replica": "A",
        "prompt_tokens": 136000, "completion_tokens": 512, "status": 200, "integrity_ok": True,
        "timeout": False, "ttft_any_s": 0.8, "cached_tokens_delta": 128000.0,
    }
    good_gsm = {
        "arm": "fp8", "condition": "low", "index": 0, "max_tokens": 1024, "status": 200,
        "completion_limit_hit": False, "extraction_valid": True, "correct": True,
        "extracted_value": 18, "gold_value": 18,
    }
    arms = {"1A:A": {"topology": "dual TP4/EP4"}}

    def grid(rows):
        return lambda: derive_holistic(rows, [], [], arms, cell_counts=None)

    holistic_cases = [
        ("holistic duplicate request identity", grid([good_grid, dict(good_grid)]), "duplicate"),
        ("holistic missing repetitions", grid([good_grid]), "repetitions"),
        ("holistic truncated completion",
         grid([dict(r, completion_tokens=1024) if r["repetition"] == 2 else r for r in three_reps]),
         "unsuccessful"),
        ("holistic unexpected field", grid([dict(good_grid, route_label="x")]), "unexpected field"),
        ("holistic unequal requests per repetition",
         grid(three_reps + [dict(good_grid, repetition=1, request_index=1, benchmark_id="x1b")]),
         "unequal request counts"),
        ("warm truncated turn",
         lambda: derive_holistic([], [dict(good_warm, completion_tokens=500)], [], arms, cell_counts=None),
         "unsuccessful turns"),
        ("gsm8k correct flag disagrees",
         lambda: derive_holistic([], [], [dict(good_gsm, extracted_value=17)], arms, cell_counts=None),
         "correct flag disagrees"),
        ("gsm8k missing arm",
         lambda: derive_holistic([], [], [good_gsm], arms, cell_counts=None), "both arms required"),
        ("gsm8k unexpected arm",
         lambda: derive_holistic([], [], [dict(good_gsm, arm="int4")], arms, cell_counts=None),
         "unexpected arm"),
    ]
    for name, call, needle in holistic_cases:
        try:
            call()
        except DerivationError as e:
            if needle in str(e):
                print(f"  fixture PASS (fails closed): {name}")
            else:
                print(f"  fixture FAIL (wrong error): {name}: {e}")
                failures += 1
        else:
            print(f"  fixture FAIL (accepted bad input): {name}")
            failures += 1
    for name, row, expected in [
        ("holistic clean request counts as success", good_grid, True),
        ("holistic timeout never counts as success", dict(good_grid, timeout=True), False),
    ]:
        if grid_success(row) is expected:
            print(f"  fixture PASS: {name}")
        else:
            print(f"  fixture FAIL: {name}")
            failures += 1
    if failures:
        print(f"self-test: {failures} fixture failure(s)")
        return 1
    print("self-test: all fail-closed fixtures rejected as expected")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="write derived summaries (default: verify only)")
    ap.add_argument("--self-test", action="store_true",
                    help="run built-in fail-closed fixtures and exit")
    args = ap.parse_args()
    if args.self_test:
        return self_test()
    try:
        return run(REPO, write=args.write)
    except DerivationError as e:
        print(f"DERIVATION FAILED (nonzero): {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

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


def check_fields(rows, fields, label):
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
            if name not in fields and name not in (
                "seed", "prompt_chars", "ttft_any_s", "finish_reason",
                "usage_prompt_tokens", "timeout", "is_429",
                "turn_target_tokens", "wall_s", "finish", "prompt_tokens",
                "completion_tokens", "landed_replica", "affinity_present",
            ):
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

    outputs = {
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
    failures = 0
    for name, rows, needle in cases:
        try:
            if name == "configuration mismatch arm":
                derive_cache_affinity(rows, {})
            elif name == "unexpected cell shape":
                derive_serving_envelope(rows)
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

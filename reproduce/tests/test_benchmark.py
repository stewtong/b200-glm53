#!/usr/bin/env python3
"""Mock tests for reproduce/benchmark.py: generation, streaming, and failure
modes. Every test runs against the local mock endpoint; no GPU, no network
beyond loopback, no credentials.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO / "reproduce"))
sys.path.insert(0, str(HERE))

import benchmark  # noqa: E402


class MockServer:
    def __init__(self, mode="ok"):
        os.environ["MOCK_MODE"] = mode
        import importlib
        import mock_endpoint
        importlib.reload(mock_endpoint)
        from http.server import ThreadingHTTPServer
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), mock_endpoint.Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("mock server thread did not stop")


class BenchmarkMockTests(unittest.TestCase):
    def setUp(self):
        self.server = MockServer("ok")
        self.base = f"http://127.0.0.1:{self.server.port}/v1"
        self.key = "mock-key"

    def tearDown(self):
        self.server.stop()

    def run_cell(self, mode=None, **kw):
        if mode:
            self.server.stop()
            self.server = MockServer(mode)
            self.base = f"http://127.0.0.1:{self.server.port}/v1"
        args = dict(base=self.base, key=self.key, cell="H6", reps=1,
                    timeout_s=30.0, out_path=Path(tempfile.mkdtemp()) / "rows.jsonl")
        args.update(kw)
        return benchmark.run_cell(**args)

    def test_generation_success(self):
        rows = self.run_cell()
        self.assertEqual(len(rows), 4)
        for r in rows:
            self.assertTrue(r["integrity_ok"], r)
            self.assertEqual(r["status"], 200)
            self.assertEqual(r["usage_completion_tokens"], 512)
            self.assertEqual(r["finish_reason"], "length")
            self.assertGreater(r["visible_chars"], 0)
            self.assertGreater(r["ttft_any_s"], 0)

    def test_streaming_multiple_events(self):
        rows = self.run_cell()
        # Three content chunks per mock response: visible content accumulated.
        self.assertGreaterEqual(rows[0]["visible_chars"], len("mock output ") * 3)

    def test_http_429_preserved_and_fails(self):
        with self.assertRaises(benchmark.BenchmarkError) as ctx:
            self.run_cell(mode="http429")
        self.assertIn("failed integrity", str(ctx.exception))

    def test_reasoning_only_empty_visible_fails(self):
        with self.assertRaises(benchmark.BenchmarkError):
            self.run_cell(mode="reasoning-only")

    def test_finish_reason_mismatch_fails(self):
        with self.assertRaises(benchmark.BenchmarkError):
            self.run_cell(mode="finish-stop")

    def test_timeout_preserved(self):
        # A short timeout against the silent mock: the row is preserved and
        # counted as a failure.
        with self.assertRaises(benchmark.BenchmarkError):
            self.run_cell(mode="silent", timeout_s=1.0)

    def test_missing_credentials_exit_code(self):
        env = dict(os.environ)
        env["GLM53F_OPENAI_BASE_URL"] = "http://127.0.0.1:1/v1"
        env.pop("GLM53F_API_KEY", None)
        proc = subprocess.run(
            [sys.executable, str(REPO / "reproduce" / "benchmark.py"),
             "--cell", "H6", "--output", "/tmp/unused.jsonl"],
            env=env, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("GLM53F_API_KEY", proc.stderr)

    def test_caller_supplied_credentials_used(self):
        # The Authorization header must carry the caller's key, never a literal.
        seen = {}

        class Probe(benchmark.urllib.request.Request):
            pass

        orig = benchmark.urllib.request.Request

        def capture(url, **kw):
            req = orig(url, **kw)
            seen["auth"] = req.headers.get("Authorization")
            return req

        benchmark.urllib.request.Request = capture
        try:
            self.run_cell()
        finally:
            benchmark.urllib.request.Request = orig
        self.assertEqual(seen["auth"], f"Bearer {self.key}")

    def test_h1_cardinality_and_flush_before_each_repetition(self):
        events = []

        def flush():
            events.append("flush")

        def request(*args, **kwargs):
            events.append("request")
            return {
                "status": 200,
                "error": None,
                "wall_s": 1.0,
                "ttft_any_s": 0.1,
                "finish_reason": "length",
                "visible_chars": 1,
                "reasoning_chars": 0,
                "usage_prompt_tokens": 131072,
                "usage_completion_tokens": 2048,
                "integrity_ok": False,
                "integrity_reasons": [],
                "timeout": False,
                "is_429": False,
            }

        out_path = Path(tempfile.mkdtemp()) / "rows.jsonl"
        with mock.patch.object(benchmark, "build_prompt", return_value="prompt"), \
                mock.patch.object(benchmark, "stream_request", side_effect=request):
            rows = benchmark.run_cell(
                self.base, self.key, "H1", 3, 30.0, out_path, flush)

        self.assertEqual(benchmark.CELLS["H1"]["concurrency"], 1)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            events,
            ["flush", "request", "flush", "request", "flush", "request"],
        )

    def test_flush_failure_leaves_output_unchanged(self):
        out_path = Path(tempfile.mkdtemp()) / "rows.jsonl"
        out_path.write_text("preserve\n")

        def fail_flush():
            raise benchmark.BenchmarkError("flush failed")

        with mock.patch.object(benchmark, "build_prompt", return_value="prompt"), \
                mock.patch.object(benchmark, "stream_request") as request:
            with self.assertRaises(benchmark.BenchmarkError):
                benchmark.run_cell(
                    self.base, self.key, "H1", 1, 30.0, out_path, fail_flush)

        request.assert_not_called()
        self.assertEqual(out_path.read_text(), "preserve\n")


class DerivationFailClosedTests(unittest.TestCase):
    def test_self_test_passes(self):
        proc = subprocess.run(
            [sys.executable, str(REPO / "reproduce" / "derive-results.py"), "--self-test"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_derivation_verifies(self):
        proc = subprocess.run(
            [sys.executable, str(REPO / "reproduce" / "derive-results.py")],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()

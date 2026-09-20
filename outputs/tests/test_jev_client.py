from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

import resource

from ctxfilter_jev.jev_client import JevClient  # noqa: E402
from ctxfilter_jev.support import ClassifyError, Deadline  # noqa: E402

PYTHON = "/opt/homebrew/opt/python@3.14/bin/python3.14"
HUNG = Path(__file__).resolve().parent / "hung_stdin.py"
STICKY = Path(__file__).resolve().parent / "sticky_child.py"
MOCK = Path(__file__).resolve().parent / "mock_jev.py"


class JevClientTests(unittest.TestCase):
    def test_write_times_out_when_child_does_not_read(self) -> None:
        env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "USER", "TMPDIR")}
        client = JevClient([PYTHON, "-u", str(HUNG)], env, Deadline(800))
        try:
            with self.assertRaises(ClassifyError) as raised:
                for _ in range(8):
                    client.send(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "ping",
                            "params": {"pad": "x" * 20000},
                        }
                    )
            self.assertEqual(raised.exception.code, "jev_timeout")
        finally:
            client.close()

    def test_close_kills_process_group_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "sticky.pid"
            env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "USER", "TMPDIR")}
            env["STICKY_PID_FILE"] = str(pid_file)
            client = JevClient([PYTHON, "-u", str(STICKY)], env, Deadline(5000))
            try:
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline and not pid_file.is_file():
                    time.sleep(0.05)
                self.assertTrue(pid_file.is_file())
                child_pid = int(pid_file.read_text(encoding="utf-8"))
            finally:
                client.close()
            gone = False
            for _ in range(20):
                try:
                    os.kill(child_pid, 0)
                    time.sleep(0.05)
                except OSError:
                    gone = True
                    break
            self.assertTrue(gone, "descendant still alive after client.close")

    def test_idle_wait_does_not_busy_spin(self) -> None:
        env = {k: v for k, v in os.environ.items() if k in ("HOME", "PATH", "USER", "TMPDIR")}
        env["CTXFILTER_JEV_MOCK_DELAY_MS"] = "400"
        before = resource.getrusage(resource.RUSAGE_SELF)
        client = JevClient([PYTHON, "-u", str(MOCK)], env, Deadline(5000))
        try:
            client.initialize()
        finally:
            client.close()
        after = resource.getrusage(resource.RUSAGE_SELF)
        cpu = (after.ru_utime + after.ru_stime) - (before.ru_utime + before.ru_stime)
        self.assertLess(cpu, 0.25, f"cpu {cpu} suggests stdin write-readiness busy spin")

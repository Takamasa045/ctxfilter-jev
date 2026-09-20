from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "bin" / "ctxfilter-jev"
WORK = ROOT.parent / "work"
PYTHON = "/opt/homebrew/opt/python@3.14/bin/python3.14"
MOCK = Path(__file__).resolve().parent / "mock_jev.py"


def run_cli(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [PYTHON, "-u", str(LAUNCHER), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


class CliProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(WORK / "fixtures"))
        from build_fixtures import build

        cls.fx = build()

    def test_cli_deterministic(self) -> None:
        proc = run_cli(["classify", "--path", self.fx["literal"], "--deterministic"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertTrue(body["ok"])
        self.assertFalse(body["jev"]["called"])

    def test_cli_forward_denied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            allow = Path(tmp) / "allow.json"
            allow.write_text(json.dumps({"version": 1, "policy": "default_deny", "entries": []}), encoding="utf-8")
            proc = run_cli(["classify", "--path", self.fx["jsonl"], "--allowlist", str(allow)])
        self.assertNotEqual(proc.returncode, 0)
        body = json.loads(proc.stdout)
        self.assertEqual(body["error"]["code"], "forward_denied")

    def test_cli_fingerprint_does_not_authorize(self) -> None:
        proc = run_cli(["fingerprint", "--path", self.fx["jsonl"]])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertTrue(body["ok"])
        self.assertEqual(body["tool"], "fingerprint")
        self.assertIn("does not authorize", body["note"])
        digest = hashlib.sha256(Path(self.fx["jsonl"]).read_bytes()).hexdigest()
        size = Path(self.fx["jsonl"]).stat().st_size
        self.assertEqual(body["fingerprint"]["sha256"], digest)
        self.assertEqual(body["fingerprint"]["size"], size)
        self.assertTrue(body["fingerprint"]["hash_complete"])
        self.assertEqual(body["candidate"]["sha256"], digest)
        self.assertEqual(body["candidate"]["size"], size)
        self.assertIn("resolved_path", body["candidate"])

    def test_cli_allowlist_add_metadata(self) -> None:
        digest = hashlib.sha256(Path(self.fx["jsonl"]).read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            allow = Path(tmp) / "allow.json"
            proc = run_cli(
                [
                    "allowlist-add",
                    "--path",
                    self.fx["jsonl"],
                    "--allowlist",
                    str(allow),
                    "--id",
                    "tmp-hash-test",
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            body = json.loads(proc.stdout)
        self.assertTrue(body["ok"])
        self.assertEqual(body["tool"], "allowlist-add")
        self.assertTrue(body["added"])
        self.assertEqual(body["entry"]["id"], "tmp-hash-test")
        self.assertEqual(body["entry"]["sha256"], digest)
        self.assertEqual(body["entry"]["size"], Path(self.fx["jsonl"]).stat().st_size)

    def test_cli_mock_transport(self) -> None:
        proc = run_cli(
            ["classify", "--path", self.fx["jsonl"], "--allowlist", self.fx["allowlist"]],
            {"CTXFILTER_JEV_TEST_TRANSPORT": str(MOCK)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        body = json.loads(proc.stdout)
        self.assertTrue(body["jev"]["mock"])
        self.assertTrue(body["jev"]["called"])
        self.assertNotIn("IGNORE ALL", proc.stderr)

    def test_cli_four_line_continuation(self) -> None:
        first = run_cli(["classify", "--path", self.fx["four"], "--deterministic", "--max-records", "2"])
        self.assertEqual(first.returncode, 0, first.stderr)
        body = json.loads(first.stdout)
        self.assertEqual([item["id"] for item in body["items"]], ["a", "b"])
        second = run_cli(
            [
                "classify",
                "--path",
                self.fx["four"],
                "--deterministic",
                "--max-records",
                "2",
                "--from-byte",
                str(body["cursor"]["next_byte"]),
                "--identity-json",
                json.dumps(body["identity"]),
            ]
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        body2 = json.loads(second.stdout)
        self.assertEqual([item["id"] for item in body2["items"]], ["c", "d"])

    def test_cli_fingerprint_fifo_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fifo = Path(tmp) / "pipe.fifo"
            os.mkfifo(fifo)
            proc = run_cli(["fingerprint", "--path", str(fifo)])
        self.assertNotEqual(proc.returncode, 0)
        body = json.loads(proc.stdout)
        self.assertIn(body["error"]["code"], {"not_a_regular_file", "invalid_path", "io_error"})

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "bin" / "ctxfilter-jev"
WORK = ROOT.parent / "work"
PYTHON = "/opt/homebrew/opt/python@3.14/bin/python3.14"
MOCK = Path(__file__).resolve().parent / "mock_jev.py"


def encode_message(payload: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return body.encode("utf-8") + b"\n"


def read_message(proc: subprocess.Popen[bytes]) -> dict:
    assert proc.stdout is not None
    line = proc.stdout.readline()
    if line == b"":
        raise EOFError("MCP server closed stdout")
    if len(line) > 1_000_000:
        raise RuntimeError("MCP line too large")
    return json.loads(line.decode("utf-8"))


class McpProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(WORK / "fixtures"))
        from build_fixtures import build

        cls.fx = build()

    def _start(self, extra_env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if extra_env:
            env.update(extra_env)
        return subprocess.Popen(
            [PYTHON, "-u", str(LAUNCHER), "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def test_initialize_list_call_ping_and_parse_error(self) -> None:
        proc = self._start()
        try:
            assert proc.stdin is not None
            proc.stdin.write(
                encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "ctxfilter-jev-test", "version": "0"},
                        },
                    }
                )
            )
            proc.stdin.flush()
            init = read_message(proc)
            self.assertEqual(init["id"], 1)
            self.assertEqual(init["result"]["serverInfo"]["name"], "ctxfilter-jev")

            proc.stdin.write(encode_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            proc.stdin.flush()

            proc.stdin.write(encode_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))
            proc.stdin.flush()
            listed = read_message(proc)
            tools = listed["result"]["tools"]
            self.assertEqual([tool["name"] for tool in tools], ["classify_records"])
            ann = tools[0]["annotations"]
            self.assertTrue(ann["openWorldHint"])
            self.assertFalse(ann["idempotentHint"])
            self.assertIn("external", tools[0]["description"].lower())
            self.assertIn("readOnlyHint does not mean", tools[0]["description"])

            proc.stdin.write(
                encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "classify_records",
                            "arguments": {"path": self.fx["literal"], "deterministic": True},
                        },
                    }
                )
            )
            proc.stdin.flush()
            called = read_message(proc)
            body = json.loads(called["result"]["content"][0]["text"])
            self.assertTrue(body["ok"])
            self.assertFalse(body["jev"]["called"])
            self.assertFalse(called["result"]["isError"])

            proc.stdin.write(b"{not-json\n")
            proc.stdin.flush()
            parse_err = read_message(proc)
            self.assertEqual(parse_err["error"]["code"], -32700)

            proc.stdin.write(encode_message({"jsonrpc": "2.0", "id": 4, "method": "ping"}))
            proc.stdin.flush()
            ping = read_message(proc)
            self.assertEqual(ping["id"], 4)
            self.assertEqual(ping["result"], {})
        finally:
            proc.kill()
            proc.wait(timeout=3)

    def test_mcp_denies_unallowlisted_forward(self) -> None:
        proc = self._start({"CTXFILTER_JEV_ALLOWLIST": str(Path(self.fx["allowlist"]))})
        try:
            assert proc.stdin is not None
            proc.stdin.write(
                encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
                    }
                )
            )
            proc.stdin.flush()
            read_message(proc)
            proc.stdin.write(encode_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            proc.stdin.flush()
            with open(self.fx["jsonl"], "rb") as handle:
                # private-looking path not on allowlist
                pass
            other = Path(self.fx["jsonl"]).parent / "no-match-allow.txt"
            other.write_text("secret-looking local text\n", encoding="utf-8")
            proc.stdin.write(
                encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "classify_records", "arguments": {"path": str(other)}},
                    }
                )
            )
            proc.stdin.flush()
            called = read_message(proc)
            body = json.loads(called["result"]["content"][0]["text"])
            self.assertTrue(called["result"]["isError"])
            self.assertEqual(body["error"]["code"], "forward_denied")
        finally:
            proc.kill()
            proc.wait(timeout=3)

    def test_mcp_four_line_continuation_and_bool_rejected(self) -> None:
        proc = self._start()
        try:
            assert proc.stdin is not None
            proc.stdin.write(
                encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
                    }
                )
            )
            proc.stdin.flush()
            read_message(proc)
            proc.stdin.write(encode_message({"jsonrpc": "2.0", "method": "notifications/initialized"}))
            proc.stdin.flush()
            proc.stdin.write(
                encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "classify_records",
                            "arguments": {"path": self.fx["four"], "deterministic": True, "max_records": 2},
                        },
                    }
                )
            )
            proc.stdin.flush()
            first = json.loads(read_message(proc)["result"]["content"][0]["text"])
            self.assertTrue(first["ok"])
            self.assertEqual([item["id"] for item in first["items"]], ["a", "b"])
            self.assertFalse(first["scan_complete"])
            proc.stdin.write(
                encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": "classify_records",
                            "arguments": {
                                "path": self.fx["four"],
                                "deterministic": True,
                                "max_records": 2,
                                "from_byte": first["cursor"]["next_byte"],
                                "identity": first["identity"],
                            },
                        },
                    }
                )
            )
            proc.stdin.flush()
            second = json.loads(read_message(proc)["result"]["content"][0]["text"])
            self.assertTrue(second["ok"], second)
            self.assertEqual([item["id"] for item in second["items"]], ["c", "d"])
            proc.stdin.write(
                encode_message(
                    {
                        "jsonrpc": "2.0",
                        "id": 4,
                        "method": "tools/call",
                        "params": {
                            "name": "classify_records",
                            "arguments": {"path": self.fx["four"], "deterministic": True, "max_records": True},
                        },
                    }
                )
            )
            proc.stdin.flush()
            bad_msg = read_message(proc)
            bad = json.loads(bad_msg["result"]["content"][0]["text"])
            self.assertTrue(bad_msg["result"]["isError"])
            self.assertFalse(bad.get("ok"))
            proc.stdin.write(encode_message({"jsonrpc": "2.0", "id": 5, "method": "ping"}))
            proc.stdin.flush()
            ping = read_message(proc)
            self.assertEqual(ping["id"], 5)
        finally:
            proc.kill()
            proc.wait(timeout=3)

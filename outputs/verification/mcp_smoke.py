"""Local stdio MCP smoke: initialize, list, ping, deterministic classify. No Jev."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

PYTHON = "/opt/homebrew/opt/python@3.14/bin/python3.14"
LAUNCHER = Path("/Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/bin/ctxfilter-jev")
FIXTURE = Path("/Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/work/fixtures/synthetic-literal.jsonl")


def write(proc: subprocess.Popen[bytes], obj: dict) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())
    proc.stdin.flush()


def send(proc: subprocess.Popen[bytes], obj: dict) -> dict:
    write(proc, obj)
    assert proc.stdout is not None
    line = proc.stdout.readline()
    return json.loads(line.decode())


def main() -> int:
    proc = subprocess.Popen(
        [PYTHON, "-u", str(LAUNCHER), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    try:
        init = send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}},
            },
        )
        write(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        listed = send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        ping = send(proc, {"jsonrpc": "2.0", "id": 3, "method": "ping"})
        called = send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "classify_records",
                    "arguments": {"path": str(FIXTURE), "deterministic": True},
                },
            },
        )
        names = [tool["name"] for tool in listed["result"]["tools"]]
        body = json.loads(called["result"]["content"][0]["text"])
        print(
            json.dumps(
                {
                    "ok": init["result"]["serverInfo"]["name"] == "ctxfilter-jev"
                    and names == ["classify_records"]
                    and ping["result"] == {}
                    and body.get("ok") is True
                    and body.get("jev", {}).get("called") is False,
                    "server": init["result"]["serverInfo"]["name"],
                    "tools": names,
                    "ping": ping["result"],
                    "classify_ok": body.get("ok"),
                    "jev_called": body.get("jev", {}).get("called"),
                    "openWorldHint": listed["result"]["tools"][0]["annotations"]["openWorldHint"],
                }
            )
        )
        return 0 if body.get("ok") else 1
    finally:
        proc.kill()
        proc.wait(timeout=3)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""MOCK_JEV_TEST_ONLY: local stdio MCP stand-in. No network, no real inference."""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

MODE = os.environ.get("CTXFILTER_JEV_MOCK_MODE", "ok")
DELAY_MS = int(os.environ.get("CTXFILTER_JEV_MOCK_DELAY_MS") or "0")


def _read() -> dict[str, Any] | None:
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    return json.loads(line.decode("utf-8"))


def _write(msg: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _choice(qid: str, low: bool = False) -> dict[str, Any]:
    mapping = {
        "r1": "relevant",
        "r2": "irrelevant",
        "r3": "needs_context",
        "r4": "irrelevant",
        "r5": "relevant",
        "r6": "irrelevant",
    }
    choice = mapping.get(qid, "needs_context")
    conf = 0.2 if low else 0.91
    probs = {"relevant": 0.03, "irrelevant": 0.03, "needs_context": 0.03, "none": 0.01}
    probs[choice] = conf
    rest = 1.0 - sum(probs.values())
    probs[choice] += rest
    action = "abstain" if low else "act"
    return {
        "choice": choice,
        "confidence": conf,
        "probabilities": probs,
        "action": action,
    }


def _handle_call(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if MODE == "timeout":
        time.sleep(60)
    if MODE == "error":
        return {
            "isError": True,
            "content": [{"type": "text", "text": json.dumps({"error": {"kind": "mock_error"}})}],
        }
    if name == "jev_models":
        payload = {"active_model": "mock-jev-test-only", "models": []}
        return {"content": [{"type": "text", "text": json.dumps(payload)}], "structuredContent": payload}
    questions = arguments.get("questions") or []
    ids = [q.get("id") for q in questions if isinstance(q, dict)]
    if MODE == "malformed":
        payload = {"answers": {}, "none_options": {}, "model": "mock-jev-test-only", "usage": {}, "latency_ms": 1}
        return {"content": [{"type": "text", "text": json.dumps(payload)}], "structuredContent": payload}
    low = MODE == "low_confidence"
    answers = {qid: _choice(str(qid), low=low) for qid in ids}
    payload = {
        "answers": answers,
        "none_options": {qid: "none" for qid in ids},
        "model": "mock-jev-test-only",
        "usage": {"input_tokens": 12, "output_tokens": 4},
        "latency_ms": 3,
        "label": "MOCK_JEV_TEST_ONLY",
    }
    return {"content": [{"type": "text", "text": json.dumps(payload)}], "structuredContent": payload}


def main() -> int:
    if DELAY_MS > 0:
        time.sleep(DELAY_MS / 1000.0)
    sys.stderr.write("MOCK_JEV_TEST_ONLY ready\n")
    sys.stderr.flush()
    while True:
        msg = _read()
        if msg is None:
            return 0
        method = msg.get("method")
        msg_id = msg.get("id")
        if method == "initialize":
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "mock-jev-test-only", "version": "test"},
                    },
                }
            )
        elif method in ("notifications/initialized",) or (isinstance(method, str) and method.startswith("notifications/")):
            continue
        elif method == "tools/list":
            _write(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "tools": [
                            {
                                "name": "jev_ask",
                                "description": "MOCK_JEV_TEST_ONLY",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"state": {}, "questions": {}},
                                    "required": ["state", "questions"],
                                },
                            }
                        ]
                    },
                }
            )
        elif method == "tools/call":
            result = _handle_call(msg.get("params") or {})
            _write({"jsonrpc": "2.0", "id": msg_id, "result": result})
        elif method == "ping":
            _write({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        elif msg_id is not None:
            _write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}})


if __name__ == "__main__":
    raise SystemExit(main())

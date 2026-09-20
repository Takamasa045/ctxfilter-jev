"""Stdio MCP server for the opt-in classify tool. NDJSON JSON-RPC."""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, BinaryIO

from ctxfilter_jev import __version__
from ctxfilter_jev.classify import classify_records, dumps_classify, result_ok
from ctxfilter_jev.constants import (
    DEFAULT_ALLOWLIST,
    DEFAULT_EXCERPT_CHARS,
    DEFAULT_GOAL,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_RECORDS,
    DEFAULT_MAX_RUNTIME_MS,
    DEFAULT_MAX_SCAN_BYTES,
    HARD_MAX_OUTPUT_BYTES,
    MCP_HARD_DISCARD_BYTES,
    MCP_MAX_MESSAGE_BYTES,
    PROTOCOL_VERSION,
)
from ctxfilter_jev.support import is_strict_int

SERVER_NAME = "ctxfilter-jev"


def _read_message(stdin: BinaryIO) -> dict[str, Any] | None:
    while True:
        buf = bytearray()
        oversized = False
        discarded = 0
        while True:
            byte = stdin.read(1)
            if byte == b"":
                if not buf and not oversized:
                    return None
                return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
            if byte == b"\n":
                break
            if oversized:
                discarded += 1
                if discarded > MCP_HARD_DISCARD_BYTES:
                    return {"_close": True, "jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Message too large"}}
                continue
            buf += byte
            if len(buf) > MCP_MAX_MESSAGE_BYTES:
                oversized = True
                buf.clear()
        if oversized:
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Message too large"}}
        if buf.endswith(b"\r"):
            del buf[-1]
        if not buf:
            continue
        try:
            parsed = json.loads(buf.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        if not isinstance(parsed, dict):
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
        return parsed


def _write_message(stdout: BinaryIO, message: dict[str, Any]) -> None:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    stdout.write(body.encode("utf-8") + b"\n")
    stdout.flush()


def _tool_schemas() -> list[dict[str, Any]]:
    return [
        {
            "name": "classify_records",
            "description": (
                "Opt-in relevance classification of allowlisted local JSONL records against a fixed goal. "
                "Forwards bounded excerpts, the caller goal, and restricted criteria to the existing Jev MCP "
                "(external inference). Allowlisting a file does not authorize confidential goals or extra rules. "
                "readOnlyHint does not mean the call is local-only. Default deny for files not on the "
                "workspace hash allowlist. The caller cannot self-authorize private files. "
                "Missing, partial, changed, low-confidence, review, needs_context, or error evidence is deferred, "
                "never a negative. max_output_bytes bounds the tool content JSON only; the MCP JSON-RPC envelope is extra and counted separately. "
                "Deterministic mode extracts literal_label only and is not a semantic proof. "
                "Ordinary literal search should use ctxfilter without Jev."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Local JSONL path. Must be allowlisted unless deterministic."},
                    "goal": {"type": "string", "maxLength": 400},
                    "criteria": {
                        "type": "object",
                        "description": "Restricted judgment criteria. Options must stay relevant/irrelevant/needs_context. Extra rules cannot replace mandatory untrusted-evidence rules.",
                    },
                    "deterministic": {
                        "type": "boolean",
                        "description": "If true, extract literal_label only and never call Jev. Not a semantic proof.",
                    },
                    "from_byte": {"type": "integer", "minimum": 0, "description": "Record-boundary cursor only."},
                    "identity": {
                        "type": "object",
                        "description": "size, mtime_ns, inode, and dev are decimal strings. Required when from_byte > 0.",
                    },
                    "max_records": {"type": "integer", "minimum": 1, "maximum": 16},
                    "max_output_bytes": {"type": "integer", "minimum": 256, "maximum": 16384},
                    "max_runtime_ms": {"type": "integer", "minimum": 50, "maximum": 45000},
                    "max_scan_bytes": {"type": "integer", "minimum": 64},
                },
                "required": ["path"],
            },
            "annotations": {
                "readOnlyHint": True,
                "destructiveHint": False,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        }
    ]


def _strict_int_arg(arguments: dict[str, Any], name: str, default: int) -> int:
    if name not in arguments or arguments[name] is None:
        return default
    value = arguments[name]
    if not is_strict_int(value):
        raise ValueError(name)
    return value


def _handle_tool(name: str, arguments: Any) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        arguments = {}
    if name != "classify_records":
        return {"ok": False, "error": {"code": "unknown_tool", "message": "unknown tool"}}
    path = arguments.get("path")
    if not isinstance(path, str):
        return {"ok": False, "error": {"code": "invalid_params", "message": "path is invalid"}}
    det = arguments.get("deterministic", False)
    if det not in (True, False):
        return {"ok": False, "error": {"code": "invalid_params", "message": "deterministic is invalid"}}
    max_output = _strict_int_arg(arguments, "max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES)
    if max_output < 256 or max_output > HARD_MAX_OUTPUT_BYTES:
        return {"ok": False, "error": {"code": "invalid_params", "message": "max_output_bytes is invalid"}}
    return classify_records(
        path,
        goal=arguments.get("goal") or DEFAULT_GOAL,
        criteria=arguments.get("criteria"),
        deterministic=bool(det),
        from_byte=_strict_int_arg(arguments, "from_byte", 0),
        identity=arguments.get("identity"),
        max_records=_strict_int_arg(arguments, "max_records", DEFAULT_MAX_RECORDS),
        max_output_bytes=max_output,
        max_runtime_ms=_strict_int_arg(arguments, "max_runtime_ms", DEFAULT_MAX_RUNTIME_MS),
        max_scan_bytes=_strict_int_arg(arguments, "max_scan_bytes", DEFAULT_MAX_SCAN_BYTES),
        excerpt_chars=DEFAULT_EXCERPT_CHARS,
        allowlist_path=os.environ.get("CTXFILTER_JEV_ALLOWLIST") or DEFAULT_ALLOWLIST,
        test_transport=os.environ.get("CTXFILTER_JEV_TEST_TRANSPORT"),
    )


def _rpc_id(message: dict[str, Any]) -> Any:
    msg_id = message.get("id")
    if msg_id is None or is_strict_int(msg_id) or (isinstance(msg_id, str) and msg_id):
        if isinstance(msg_id, float) and not math.isfinite(msg_id):
            return None
        return msg_id
    return None


def handle_rpc(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("_close"):
        return message
    if message.get("error") and "method" not in message:
        return message
    method = message.get("method")
    msg_id = _rpc_id(message) if "id" in message else None
    if not isinstance(method, str):
        if "id" not in message:
            return None
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32600, "message": "Invalid Request"}}
    if method.startswith("notifications/") or method == "notifications/initialized":
        return None
    if method == "initialize":
        params = message.get("params")
        if params is not None and not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "Invalid params"}}
        client_version = ""
        if isinstance(params, dict):
            client_version = str(params.get("protocolVersion") or "")
        version = client_version if client_version.startswith("2024-") or client_version.startswith("2025-") else PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": _tool_schemas()}}
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict):
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "Invalid params"}}
        name = params.get("name")
        if not isinstance(name, str):
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "Invalid params"}}
        arguments = params.get("arguments") if "arguments" in params else {}
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32602, "message": "Invalid params"}}
        try:
            result = _handle_tool(name, arguments)
            requested = DEFAULT_MAX_OUTPUT_BYTES
            if isinstance(arguments, dict) and arguments.get("max_output_bytes") is not None:
                requested = int(arguments["max_output_bytes"])
            max_output = int((result.get("limits") or {}).get("max_output_bytes") or requested)
            payload = dumps_classify(result, max_output)
        except (ValueError, TypeError, UnicodeError, OSError):
            payload = dumps_classify(
                {"ok": False, "error": {"code": "invalid_params", "message": "arguments are invalid"}},
                DEFAULT_MAX_OUTPUT_BYTES,
            )
        is_error = not result_ok(payload)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": payload}],
                "isError": is_error,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
    if "id" not in message:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Method not found"}}


def serve(stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> int:
    in_buf = stdin or sys.stdin.buffer
    out_buf = stdout or sys.stdout.buffer
    try:
        while True:
            message = _read_message(in_buf)
            if message is None:
                return 0
            try:
                reply = handle_rpc(message)
            except (TypeError, ValueError, UnicodeError, OSError):
                msg_id = message.get("id") if isinstance(message, dict) else None
                reply = {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32603, "message": "Internal error"}}
            if isinstance(reply, dict) and reply.get("_close"):
                _write_message(out_buf, {k: v for k, v in reply.items() if k != "_close"})
                return 1
            if reply is not None:
                _write_message(out_buf, reply)
    except BrokenPipeError:
        return 0
    except OSError:
        return 1


def main() -> int:
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())

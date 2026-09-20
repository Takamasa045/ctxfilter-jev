"""Bounded stdio MCP client for the existing Jev server. Payloads stay local."""

from __future__ import annotations

import fcntl
import json
import os
import selectors
import signal
import subprocess
from typing import Any, Callable

from ctxfilter_jev.constants import (
    MAX_JEV_REQUEST_BYTES,
    MAX_JEV_RESPONSE_BYTES,
    MAX_RPC_ID_CHARS,
    PROTOCOL_VERSION,
)
from ctxfilter_jev.support import ClassifyError, Deadline, is_strict_int


def _valid_rpc_id(msg_id: Any) -> bool:
    if msg_id is None:
        return True
    if is_strict_int(msg_id):
        return abs(msg_id) <= 2**53
    if isinstance(msg_id, str):
        return 0 < len(msg_id) <= MAX_RPC_ID_CHARS
    return False


class JevClient:
    def __init__(self, argv: list[str], env: dict[str, str], deadline: Deadline):
        self.deadline = deadline
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            start_new_session=True,
        )
        self.selector = selectors.DefaultSelector()
        assert self.proc.stdout is not None
        assert self.proc.stdin is not None
        self._set_nonblock(self.proc.stdout.fileno())
        self._set_nonblock(self.proc.stdin.fileno())
        self.selector.register(self.proc.stdout, selectors.EVENT_READ)
        self._stdin_watch = False
        self.buffer = b""
        self.seq = 0
        self.server_info: dict[str, Any] = {}
        self._pgid = None
        try:
            if self.proc.pid:
                self._pgid = os.getpgid(self.proc.pid)
        except (ProcessLookupError, OSError):
            self._pgid = self.proc.pid

    @staticmethod
    def _set_nonblock(fd: int) -> None:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def _watch_stdin(self, enable: bool) -> None:
        if enable and not self._stdin_watch:
            self.selector.register(self.proc.stdin, selectors.EVENT_WRITE)
            self._stdin_watch = True
        elif not enable and self._stdin_watch:
            try:
                self.selector.unregister(self.proc.stdin)
            except Exception:
                pass
            self._stdin_watch = False

    def _drain_stdout(self) -> None:
        assert self.proc.stdout is not None
        try:
            chunk = os.read(self.proc.stdout.fileno(), 65536)
        except BlockingIOError:
            return
        if not chunk:
            raise ClassifyError("jev_transport", "Jev MCP disconnected")
        self.buffer += chunk
        if len(self.buffer) > MAX_JEV_RESPONSE_BYTES:
            self.buffer = b""
            raise ClassifyError("jev_response_bound", "Jev MCP response exceeded byte bound")

    def _write_all(self, data: bytes) -> None:
        if self.proc.stdin is None:
            raise ClassifyError("jev_transport", "Jev stdin is closed")
        if len(data) > MAX_JEV_REQUEST_BYTES:
            raise ClassifyError("jev_request_bound", "Jev MCP request exceeded byte bound")
        fd = self.proc.stdin.fileno()
        sent = 0
        view = memoryview(data)
        self._watch_stdin(True)
        try:
            while sent < len(data):
                left = self.deadline.remaining_s()
                if left <= 0:
                    raise ClassifyError("jev_timeout", "Jev MCP write deadline exceeded")
                events = self.selector.select(left)
                writable = False
                for key, mask in events:
                    if key.fileobj is self.proc.stdout and mask & selectors.EVENT_READ:
                        self._drain_stdout()
                    if key.fileobj is self.proc.stdin and mask & selectors.EVENT_WRITE:
                        writable = True
                if not writable:
                    if self.deadline.remaining_s() <= 0:
                        raise ClassifyError("jev_timeout", "Jev MCP write deadline exceeded")
                    continue
                try:
                    written = os.write(fd, view[sent:])
                except BlockingIOError:
                    continue
                if written == 0:
                    raise ClassifyError("jev_transport", "Jev stdin closed during write")
                sent += written
        finally:
            self._watch_stdin(False)

    def send(self, obj: dict[str, Any]) -> None:
        if "id" in obj and not _valid_rpc_id(obj.get("id")):
            raise ClassifyError("jev_malformed", "RPC id is invalid")
        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
        self._write_all(line.encode("utf-8"))

    def _read_msg(self) -> dict[str, Any]:
        while b"\n" not in self.buffer:
            left = self.deadline.remaining_s()
            if left <= 0:
                raise ClassifyError("jev_timeout", "Jev MCP deadline exceeded")
            events = self.selector.select(left)
            readable = False
            for key, mask in events:
                if key.fileobj is self.proc.stdout and mask & selectors.EVENT_READ:
                    readable = True
            if not readable:
                if self.deadline.remaining_s() <= 0:
                    raise ClassifyError("jev_timeout", "Jev MCP deadline exceeded")
                continue
            assert self.proc.stdout is not None
            try:
                chunk = os.read(self.proc.stdout.fileno(), 65536)
            except BlockingIOError:
                continue
            if not chunk:
                raise ClassifyError("jev_transport", "Jev MCP disconnected")
            self.buffer += chunk
            if len(self.buffer) > MAX_JEV_RESPONSE_BYTES:
                self.buffer = b""
                raise ClassifyError("jev_response_bound", "Jev MCP response exceeded byte bound")
        line, self.buffer = self.buffer.split(b"\n", 1)
        if line.endswith(b"\r"):
            line = line[:-1]
        if len(line) > MAX_JEV_RESPONSE_BYTES:
            raise ClassifyError("jev_response_bound", "Jev MCP line exceeded byte bound")
        try:
            msg = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ClassifyError("jev_malformed", "Jev MCP returned invalid JSON")
        if not isinstance(msg, dict):
            raise ClassifyError("jev_malformed", "Jev MCP returned a non-object")
        return msg

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.seq += 1
        request_id = self.seq
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        while True:
            msg = self._read_msg()
            if msg.get("id") == request_id:
                if "error" in msg:
                    raise ClassifyError("jev_rpc", "Jev MCP returned an RPC error")
                result = msg.get("result")
                if not isinstance(result, dict):
                    raise ClassifyError("jev_malformed", "Jev MCP result is invalid")
                return result
            if "method" in msg and "id" in msg:
                if not _valid_rpc_id(msg.get("id")):
                    continue
                try:
                    self.send(
                        {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {"code": -32601, "message": "unsupported"},
                        }
                    )
                except ClassifyError:
                    continue

    def initialize(self) -> dict[str, Any]:
        result = self.call(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ctxfilter-jev", "version": "0.1.0"},
            },
        )
        self.server_info = result
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def close(self) -> None:
        try:
            self.selector.close()
        except Exception:
            pass
        pgid = self._pgid
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            self.proc.wait(timeout=0.4)
        except Exception:
            pass
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
        try:
            self.proc.wait(timeout=1)
        except Exception:
            pass
        for stream in (self.proc.stdin, self.proc.stdout):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    def __enter__(self) -> "JevClient":
        try:
            self.initialize()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *args: object) -> None:
        self.close()


def parse_tool_result(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("isError"):
        raise ClassifyError("jev_error", "Jev tool returned an error")
    content = result.get("structuredContent")
    if content is None:
        texts = []
        for block in result.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    texts.append(text)
        if len(texts) != 1:
            raise ClassifyError("jev_malformed", "Jev tool result is not structured")
        try:
            content = json.loads(texts[0])
        except json.JSONDecodeError:
            raise ClassifyError("jev_malformed", "Jev tool text is not JSON")
    if not isinstance(content, dict):
        raise ClassifyError("jev_malformed", "Jev tool payload is invalid")
    return content


def real_jev_ask(
    launcher: dict[str, Any],
    payload: dict[str, Any],
    deadline: Deadline,
) -> dict[str, Any]:
    with JevClient(list(launcher["argv"]), dict(launcher["env"]), deadline) as client:
        listed = client.call("tools/list", {})
        tools = listed.get("tools")
        if not isinstance(tools, list) or not any(
            isinstance(tool, dict) and tool.get("name") == "jev_ask" for tool in tools
        ):
            raise ClassifyError("jev_schema", "jev_ask is not available")
        result = client.call("tools/call", {"name": "jev_ask", "arguments": payload})
        parsed = parse_tool_result(result)
        parsed["_server"] = {
            "name": (client.server_info.get("serverInfo") or {}).get("name"),
            "version": (client.server_info.get("serverInfo") or {}).get("version"),
            "protocolVersion": client.server_info.get("protocolVersion"),
        }
        return parsed


JevCall = Callable[[dict[str, Any], dict[str, Any], Deadline], dict[str, Any]]

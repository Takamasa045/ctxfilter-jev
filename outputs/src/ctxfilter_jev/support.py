"""Shared helpers. Error text must not include source bodies or Jev payloads."""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path
from typing import Any

from ctxfilter_jev.constants import (
    MAX_ABS_PATH_CHARS,
    MAX_ERROR_DETAIL,
    MAX_PATH_DISPLAY,
    VERSION,
)


class ClassifyError(Exception):
    def __init__(self, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


class Deadline:
    def __init__(self, runtime_ms: int):
        self.end = time.monotonic() + (runtime_ms / 1000.0)

    def expired(self) -> bool:
        return time.monotonic() >= self.end

    def remaining_s(self) -> float:
        return max(0.0, self.end - time.monotonic())


def clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def is_strict_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def finite01(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        return None
    return number


def path_display(path: object, limit: int = MAX_PATH_DISPLAY) -> str:
    if not isinstance(path, str):
        return "<invalid-path>"
    if len(path) <= limit:
        return path
    keep = max(8, (limit - 3) // 2)
    return path[:keep] + "..." + path[-keep:]


def error_envelope(
    tool: str,
    code: str,
    message: str,
    path: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    msg = message if len(message) <= MAX_ERROR_DETAIL else message[: MAX_ERROR_DETAIL - 3] + "..."
    out: dict[str, Any] = {
        "ok": False,
        "v": VERSION,
        "tool": tool,
        "status": "error",
        "error": {"code": code, "message": msg},
        "jev_called": False,
    }
    if path:
        out["path_display"] = path_display(path)
    if extra:
        jev = extra.get("jev")
        if isinstance(jev, dict):
            out["jev"] = {
                "called": bool(jev.get("called")),
                "mock": bool(jev.get("mock")),
                "package": jev.get("package"),
                "model": jev.get("model"),
                "usage": jev.get("usage"),
                "latency_ms": jev.get("latency_ms"),
            }
            out["jev_called"] = bool(jev.get("called"))
        counts = extra.get("call_counts")
        if isinstance(counts, dict):
            out["call_counts"] = counts
    return out


def dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def encoded_size(text: str) -> int:
    return len(text.encode("utf-8"))


def count_payload(obj: Any) -> dict[str, int]:
    raw = dumps(obj) if isinstance(obj, dict) else json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return {"bytes": encoded_size(raw), "chars": len(raw)}


def has_symlink_component(user_path: str) -> bool:
    if not isinstance(user_path, str) or not user_path or "\x00" in user_path:
        raise ClassifyError("invalid_path", "path is invalid")
    if len(user_path) > MAX_ABS_PATH_CHARS:
        raise ClassifyError("path_too_long", "path exceeds length limit")
    path = Path(user_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        if path.is_symlink():
            return True
        parent = path.parent
        if parent != parent.parent and parent.is_symlink():
            if len(parent.parts) > 2:
                return True
    except OSError:
        raise ClassifyError("invalid_path", "path could not be resolved")
    return False


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def strip_secret_env(src: dict[str, str] | None = None) -> dict[str, str]:
    keep = ("HOME", "PATH", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
    env: dict[str, str] = {}
    source = src if src is not None else os.environ
    for key in keep:
        value = source.get(key)
        if value:
            env[key] = value
    env["npm_config_offline"] = "true"
    env["npm_config_ignore_scripts"] = "true"
    env["NPM_CONFIG_OFFLINE"] = "true"
    return env

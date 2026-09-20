"""Confirm existing Jev MCP configuration and execute the inspected cache bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import tomllib

from ctxfilter_jev.constants import (
    CODEX_CONFIG,
    EXPECTED_JEV_ARG,
    EXPECTED_NPX_NAME,
    EXPECTED_PACKAGE_NAME,
    EXPECTED_PACKAGE_VERSION,
    JEV_CACHE_DIST,
    JEV_CACHE_PKG,
    KEY_FILE,
    TESTS_ROOT,
    PYTHON,
)
from ctxfilter_jev.support import ClassifyError, is_under, strip_secret_env


def _read_jev_server(config_path: Path) -> dict[str, Any]:
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        raise ClassifyError("provenance", "existing Codex config could not be read")
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError:
        raise ClassifyError("provenance", "existing Codex config is not valid TOML")
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        raise ClassifyError("provenance", "existing Jev MCP is not configured")
    jev = servers.get("jev")
    if not isinstance(jev, dict):
        raise ClassifyError("provenance", "existing Jev MCP is not configured")
    if jev.get("enabled") is False:
        raise ClassifyError("provenance", "existing Jev MCP is disabled")
    command = jev.get("command")
    args = jev.get("args")
    if not isinstance(command, str) or not command:
        raise ClassifyError("provenance", "existing Jev command is missing")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ClassifyError("provenance", "existing Jev args are invalid")
    return {"command": command, "args": list(args)}


def _confirm_package() -> dict[str, str]:
    pkg_path = JEV_CACHE_PKG
    dist = JEV_CACHE_DIST
    if not pkg_path.is_file() or not dist.is_file():
        raise ClassifyError("provenance", "cached jev-mcp package is missing")
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ClassifyError("provenance", "cached jev-mcp package metadata is unreadable")
    if pkg.get("name") != EXPECTED_PACKAGE_NAME or pkg.get("version") != EXPECTED_PACKAGE_VERSION:
        raise ClassifyError("provenance", "cached jev-mcp package identity mismatch")
    digest = hashlib.sha256(dist.read_bytes()).hexdigest()
    return {
        "package_name": EXPECTED_PACKAGE_NAME,
        "package_version": EXPECTED_PACKAGE_VERSION,
        "package_path": str(pkg_path.parent),
        "dist_path": str(dist),
        "dist_sha256": digest,
    }


def _confirm_key_file() -> dict[str, Any]:
    if not KEY_FILE.is_file():
        raise ClassifyError("provenance", "existing Jev key file is missing")
    mode = KEY_FILE.stat().st_mode & 0o777
    if KEY_FILE.stat().st_size <= 0:
        raise ClassifyError("provenance", "existing Jev key file is empty")
    return {"key_file_present": True, "key_file_mode": oct(mode)}


def confirm_jev(config_path: Path | None = None) -> dict[str, Any]:
    cfg = Path(config_path) if config_path else CODEX_CONFIG
    server = _read_jev_server(cfg)
    configured = [server["command"], *server["args"]]
    if Path(configured[0]).name != EXPECTED_NPX_NAME or EXPECTED_JEV_ARG not in configured:
        raise ClassifyError("provenance", "Jev launcher is not the inspected npx jev-mcp@0.4.0")
    if not Path(configured[0]).is_file():
        raise ClassifyError("provenance", "configured Jev command path is missing")
    node = Path(configured[0]).parent / "node"
    if not node.is_file():
        raise ClassifyError("provenance", "node next to configured npx is missing")
    package = _confirm_package()
    key = _confirm_key_file()
    env = strip_secret_env()
    pinned = [str(node), package["dist_path"]]
    return {
        "ok": True,
        "mock": False,
        "argv": pinned,
        "configured_argv": configured,
        "env": env,
        **package,
        **key,
        "config_path": str(cfg),
        "pin": "cached_dist",
    }


def resolve_launcher(
    *,
    test_transport: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    if test_transport:
        script = Path(test_transport)
        if not is_under(script, TESTS_ROOT):
            raise ClassifyError("provenance", "test transport is not an owned test script")
        if not script.is_file():
            raise ClassifyError("provenance", "test transport script is missing")
        return {
            "ok": True,
            "mock": True,
            "argv": [PYTHON, "-u", str(script.resolve())],
            "env": strip_secret_env(),
            "package_name": "mock-jev-test-only",
            "package_version": "test",
            "package_path": str(script.resolve()),
            "dist_path": str(script.resolve()),
            "key_file_present": False,
            "key_file_mode": None,
            "config_path": None,
        }
    return confirm_jev(config_path)

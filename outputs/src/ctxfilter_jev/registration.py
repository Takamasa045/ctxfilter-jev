"""Owned-block install/rollback helpers. Tests use temp fixtures; default paths are global."""

from __future__ import annotations

import fcntl
import hashlib
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

CFG_BEGIN = "# BEGIN CTXFILTER-JEV MCP\n"
CFG_END = "# END CTXFILTER-JEV MCP\n"
AG_BEGIN = "<!-- BEGIN CTXFILTER-JEV -->\n"
AG_END = "<!-- END CTXFILTER-JEV -->\n"

OWNED_CFG_BLOCK = """# BEGIN CTXFILTER-JEV MCP
[mcp_servers.ctxfilter_jev]
command = "/opt/homebrew/opt/python@3.14/bin/python3.14"
args = [
  "/Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/outputs/bin/ctxfilter-jev",
  "mcp",
]
# END CTXFILTER-JEV MCP
"""

OWNED_AG_BLOCK = """<!-- BEGIN CTXFILTER-JEV -->
ctxfilter-jev is an opt-in MCP tool. It classifies allowlisted local JSONL records against a fixed goal by forwarding bounded excerpts to the existing Jev MCP. Files not on the workspace hash allowlist are denied. The caller cannot self-authorize private data. readOnlyHint does not mean the call stays local. Missing, partial, changed, low-confidence, or error evidence is deferred, never treated as a negative. Allowlisting a file does not authorize confidential goals or extra rules. Deterministic mode extracts literal_label only and is not a semantic proof. Ordinary literal search should use ctxfilter without Jev. Do not send secrets. Local subprocess smoke is not Codex-native MCP; native use needs a reloaded session after an approved install.
<!-- END CTXFILTER-JEV -->
"""

DEFAULT_CONFIG = Path("/Users/takamasa/.codex/config.toml")
DEFAULT_AGENTS = Path("/Users/takamasa/.codex/AGENTS.md")
DEFAULT_BACKUP = Path("/Users/takamasa/Documents/Codex/2026-09-20/ctxfilter-jev-integration/work/backup")


class RegistrationError(Exception):
    def __init__(self, code: str, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra or {}


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _refuse_symlink(path: Path) -> None:
    if path.exists() and path.is_symlink():
        raise RegistrationError("symlink_denied", "refusing symlink path")
    if path.parent.exists() and path.parent.is_symlink():
        raise RegistrationError("symlink_denied", "refusing symlink parent")


def _marker_pair(text: str, begin: str, end: str) -> tuple[str, int, int] | None:
    begins = text.count(begin)
    ends = text.count(end)
    if begins == 0 and ends == 0:
        return None
    if begins != 1 or ends != 1:
        raise RegistrationError("marker_conflict", "owned marker pair is missing, duplicate, or malformed")
    start = text.index(begin)
    stop = text.index(end) + len(end)
    if stop <= start:
        raise RegistrationError("marker_conflict", "owned marker pair is malformed")
    between = text[start:stop]
    if between.count(begin) != 1 or between.count(end) != 1:
        raise RegistrationError("marker_conflict", "owned marker pair is malformed")
    return between, start, stop


def _has_unowned_table(text: str, table: str, begin: str, end: str) -> bool:
    pair = None
    try:
        pair = _marker_pair(text, begin, end)
    except RegistrationError:
        raise
    body = text
    if pair:
        _block, start, stop = pair
        body = text[:start] + text[stop:]
    return table in body


def _insert_or_replace(text: str, begin: str, end: str, owned: str) -> str:
    pair = _marker_pair(text, begin, end)
    if pair is None:
        if not text.endswith("\n"):
            text += "\n"
        return text + "\n" + owned
    block, start, stop = pair
    if block != owned:
        raise RegistrationError("marker_conflict", "existing owned block was modified; refusing to mutate")
    return text[:start] + owned + text[stop:]


def _remove_owned(text: str, begin: str, end: str, owned: str) -> str:
    pair = _marker_pair(text, begin, end)
    if pair is None:
        return text
    block, start, stop = pair
    if block != owned:
        raise RegistrationError("marker_conflict", "existing owned block was modified; refusing rollback delete")
    return text[:start] + text[stop:]


def private_backup(src: Path, backup_dir: Path) -> Path:
    _refuse_symlink(src)
    if backup_dir.exists() and backup_dir.is_symlink():
        raise RegistrationError("symlink_denied", "backup directory is a symlink")
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dest = backup_dir / f"{stamp}-{src.name}"
    data = src.read_bytes()
    fd = os.open(str(dest), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise
    return dest


def atomic_write(path: Path, content: str, preimage: str) -> None:
    """Replace path if it still matches preimage.

    A cooperating flock is used by apply_pair. A writer that does not take
    that lock can still race after the last preimage recheck and before
    os.replace; that race is unavoidable without kernel compare-and-swap
    on the text file.
    """
    _refuse_symlink(path)
    current = path.read_text(encoding="utf-8")
    if sha(current) != preimage:
        raise RegistrationError("conflict", "file changed since read; refusing write")
    tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        latest = path.read_text(encoding="utf-8")
        if sha(latest) != preimage:
            raise RegistrationError("conflict", "file changed before replace; refusing write")
        os.replace(str(tmp), str(path))
        dirfd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _lock_file(backup_dir: Path) -> int:
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    lock_path = backup_dir / "ctxfilter-jev-registration.lock"
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    return lock_fd


def plan_install(config_text: str, agents_text: str) -> tuple[str, str]:
    if _has_unowned_table(config_text, "[mcp_servers.ctxfilter_jev]", CFG_BEGIN, CFG_END):
        raise RegistrationError("unowned_table", "unowned [mcp_servers.ctxfilter_jev] already exists")
    new_cfg = _insert_or_replace(config_text, CFG_BEGIN, CFG_END, OWNED_CFG_BLOCK)
    new_agents = _insert_or_replace(agents_text, AG_BEGIN, AG_END, OWNED_AG_BLOCK)
    try:
        tomllib.loads(new_cfg)
    except tomllib.TOMLDecodeError as exc:
        raise RegistrationError("invalid_toml", "resulting config would not parse") from exc
    return new_cfg, new_agents


def plan_rollback(config_text: str, agents_text: str) -> tuple[str, str]:
    new_cfg = _remove_owned(config_text, CFG_BEGIN, CFG_END, OWNED_CFG_BLOCK)
    new_agents = _remove_owned(agents_text, AG_BEGIN, AG_END, OWNED_AG_BLOCK)
    try:
        tomllib.loads(new_cfg)
    except tomllib.TOMLDecodeError as exc:
        raise RegistrationError("invalid_toml", "resulting config would not parse") from exc
    return new_cfg, new_agents


def apply_pair(
    config_path: Path,
    agents_path: Path,
    new_cfg: str,
    new_agents: str,
    cfg_pre: str,
    agents_pre: str,
    backup_dir: Path,
    after_config: Callable[[], None] | None = None,
) -> dict[str, Any]:
    lock_fd = _lock_file(backup_dir)
    try:
        cfg_backup = private_backup(config_path, backup_dir)
        agents_backup = private_backup(agents_path, backup_dir)
        wrote_config = False
        try:
            atomic_write(config_path, new_cfg, cfg_pre)
            wrote_config = True
            if after_config is not None:
                after_config()
            atomic_write(agents_path, new_agents, agents_pre)
        except Exception as exc:
            if wrote_config:
                current = config_path.read_text(encoding="utf-8")
                extra = {
                    "config_backup": str(cfg_backup),
                    "agents_backup": str(agents_backup),
                    "config_left_intact": current != new_cfg,
                }
                if current == new_cfg:
                    original = cfg_backup.read_text(encoding="utf-8")
                    try:
                        atomic_write(config_path, original, sha(new_cfg))
                    except Exception:
                        extra["config_restore_failed"] = True
                        raise RegistrationError(
                            "partial_failure",
                            "config wrote; agents failed; config restore failed",
                            extra=extra,
                        ) from exc
                    extra["config_restored"] = True
                    extra["config_left_intact"] = False
                    raise RegistrationError(
                        "partial_failure",
                        "config wrote; agents failed; config restored from backup",
                        extra=extra,
                    ) from exc
                raise RegistrationError(
                    "partial_failure",
                    "config wrote; agents failed; config left intact because it changed after write",
                    extra=extra,
                ) from exc
            raise
        return {"config_backup": str(cfg_backup), "agents_backup": str(agents_backup)}
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(lock_fd)


def run_install(
    *,
    config_path: Path = DEFAULT_CONFIG,
    agents_path: Path = DEFAULT_AGENTS,
    backup_dir: Path = DEFAULT_BACKUP,
    apply: bool = False,
) -> dict[str, Any]:
    cfg = config_path.read_text(encoding="utf-8")
    agents = agents_path.read_text(encoding="utf-8")
    new_cfg, new_agents = plan_install(cfg, agents)
    report = {
        "ok": True,
        "apply": apply,
        "config_would_change": new_cfg != cfg,
        "agents_would_change": new_agents != agents,
        "config_has_jev": "[mcp_servers.jev]" in new_cfg,
        "config_has_ctxfilter": "[mcp_servers.ctxfilter]" in new_cfg,
        "config_has_ctxfilter_jev": "[mcp_servers.ctxfilter_jev]" in new_cfg,
        "config_sha_before": sha(cfg),
        "config_sha_after": sha(new_cfg),
        "agents_sha_before": sha(agents),
        "agents_sha_after": sha(new_agents),
    }
    if not apply:
        report["dry_run"] = True
        return report
    extra = apply_pair(config_path, agents_path, new_cfg, new_agents, sha(cfg), sha(agents), backup_dir)
    report.update(extra)
    report["applied"] = True
    return report


def run_rollback(
    *,
    config_path: Path = DEFAULT_CONFIG,
    agents_path: Path = DEFAULT_AGENTS,
    backup_dir: Path = DEFAULT_BACKUP,
    apply: bool = False,
) -> dict[str, Any]:
    cfg = config_path.read_text(encoding="utf-8")
    agents = agents_path.read_text(encoding="utf-8")
    if CFG_BEGIN not in cfg and AG_BEGIN not in agents:
        return {"ok": True, "note": "no ctxfilter-jev markers found"}
    new_cfg, new_agents = plan_rollback(cfg, agents)
    report = {
        "ok": True,
        "apply": apply,
        "config_would_change": new_cfg != cfg,
        "agents_would_change": new_agents != agents,
        "config_has_jev_after": "[mcp_servers.jev]" in new_cfg,
        "config_has_ctxfilter_after": "[mcp_servers.ctxfilter]" in new_cfg,
        "config_has_ctxfilter_jev_after": "[mcp_servers.ctxfilter_jev]" in new_cfg,
        "config_sha_before": sha(cfg),
        "config_sha_after": sha(new_cfg),
        "agents_sha_before": sha(agents),
        "agents_sha_after": sha(new_agents),
    }
    if not apply:
        report["dry_run"] = True
        return report
    extra = apply_pair(config_path, agents_path, new_cfg, new_agents, sha(cfg), sha(agents), backup_dir)
    report.update(extra)
    report["applied"] = True
    return report

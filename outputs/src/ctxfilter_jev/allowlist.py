"""Default-deny hash/path allowlist. MCP callers cannot add entries."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ctxfilter.core import ToolError, identity_from_handle, open_regular, resolve_regular_file

from ctxfilter_jev.constants import DEFAULT_ALLOWLIST, HARD_MAX_SCAN_BYTES, MAX_ABS_PATH_CHARS
from ctxfilter_jev.support import ClassifyError, Deadline, has_symlink_component, path_display


def load_allowlist(path: Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_ALLOWLIST
    if not target.is_file():
        return {"version": 1, "policy": "default_deny", "entries": [], "path": str(target)}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ClassifyError("allowlist_invalid", "allowlist could not be read")
    if not isinstance(raw, dict) or raw.get("policy") not in (None, "default_deny"):
        raise ClassifyError("allowlist_invalid", "allowlist policy must be default_deny")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ClassifyError("allowlist_invalid", "allowlist entries are invalid")
    return {"version": 1, "policy": "default_deny", "entries": entries, "path": str(target)}


def fingerprint(path: Path | str, max_bytes: int, deadline: Deadline | None = None) -> dict[str, Any]:
    path_str = str(path)
    if has_symlink_component(path_str):
        raise ClassifyError("symlink_denied", "symlinks are not followed")
    try:
        return _fingerprint(path_str, max_bytes, deadline)
    except ToolError as exc:
        raise ClassifyError(exc.code, exc.message) from exc


def _fingerprint(path_str: str, max_bytes: int, deadline: Deadline | None) -> dict[str, Any]:
    resolved = resolve_regular_file(path_str)
    handle, st = open_regular(resolved)
    try:
        ident = identity_from_handle(handle, st, resolved)
        handle.seek(0)
        digest = hashlib.sha256()
        total = 0
        limit = min(int(max_bytes), HARD_MAX_SCAN_BYTES)
        clock = deadline or Deadline(2000)
        while total < limit:
            if clock.expired():
                raise ClassifyError("timeout", "fingerprint deadline exceeded")
            chunk = handle.read(min(65536, limit - total))
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        complete = total == ident.size
        if not complete:
            raise ClassifyError("incomplete_hash", "file exceeds hash bound; no allowlist entry")
        return {
            "resolved_path": str(resolved),
            "size": ident.size,
            "sha256": digest.hexdigest(),
            "hash_complete": True,
            "bytes_hashed": total,
            "sample_sha256": ident.sample_sha256,
        }
    finally:
        handle.close()


def os_norm(value: object) -> str:
    if not isinstance(value, str) or not value:
        return ""
    return str(Path(value))


def check_forward_allowed(
    resolved: Path,
    fp: dict[str, Any],
    allowlist_path: Path | None,
) -> dict[str, Any]:
    if not fp.get("hash_complete"):
        raise ClassifyError("incomplete_hash", "file exceeds hash bound; forwarding denied")
    allow = load_allowlist(allowlist_path)
    path_hit = False
    hash_hit = False
    for entry in allow.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        entry_path = os_norm(entry.get("resolved_path"))
        suffix = entry.get("path_suffix")
        same_path = entry_path == str(resolved) or (
            isinstance(suffix, str) and str(resolved).endswith(suffix.replace("\\", "/"))
        )
        if same_path:
            path_hit = True
            if entry.get("sha256") == fp.get("sha256") and int(entry.get("size") or -1) == fp.get("size"):
                return {"allowed": True, "entry_id": entry.get("id"), "allowlist": allow["path"]}
            hash_hit = entry.get("sha256") != fp.get("sha256")
    if path_hit and hash_hit:
        raise ClassifyError("allowlist_hash_mismatch", "allowlisted path content changed; forwarding denied")
    raise ClassifyError(
        "forward_denied",
        f"file is not on the hash allowlist: {path_display(str(resolved))}",
    )


def candidate_entry(fp: dict[str, Any], reason: str) -> dict[str, Any]:
    if not fp.get("hash_complete"):
        raise ClassifyError("incomplete_hash", "incomplete fingerprint cannot be allowlisted")
    resolved = fp["resolved_path"]
    return {
        "id": "pending-approval",
        "resolved_path": resolved,
        "path_suffix": resolved,
        "sha256": fp["sha256"],
        "size": fp["size"],
        "reason": reason,
    }


def add_entry(allowlist_path: Path, entry: dict[str, Any]) -> dict[str, Any]:
    allow = load_allowlist(allowlist_path)
    entries = list(allow.get("entries") or [])
    for existing in entries:
        if existing.get("sha256") == entry.get("sha256") and existing.get("resolved_path") == entry.get(
            "resolved_path"
        ):
            return {"added": False, "allowlist": str(allowlist_path), "entry": existing}
    if not isinstance(entry.get("resolved_path"), str) or len(entry["resolved_path"]) > MAX_ABS_PATH_CHARS:
        raise ClassifyError("invalid_path", "allowlist path is invalid")
    if not entry.get("sha256") or not isinstance(entry.get("size"), int):
        raise ClassifyError("incomplete_hash", "incomplete fingerprint cannot be allowlisted")
    entries.append(entry)
    payload = {"version": 1, "policy": "default_deny", "entries": entries}
    allowlist_path.parent.mkdir(parents=True, exist_ok=True)
    allowlist_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return {"added": True, "allowlist": str(allowlist_path), "entry": entry}

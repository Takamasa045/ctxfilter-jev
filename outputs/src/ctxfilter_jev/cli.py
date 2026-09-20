"""CLI. User values are never passed to a shell."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from ctxfilter_jev import __version__
from ctxfilter_jev.allowlist import add_entry, candidate_entry, fingerprint
from ctxfilter_jev.classify import classify_records, dumps_classify, result_ok
from ctxfilter_jev.constants import (
    DEFAULT_ALLOWLIST,
    DEFAULT_EXCERPT_CHARS,
    DEFAULT_GOAL,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_RECORDS,
    DEFAULT_MAX_RUNTIME_MS,
    DEFAULT_MAX_SCAN_BYTES,
    HARD_MAX_SCAN_BYTES,
)
from ctxfilter_jev.support import ClassifyError, error_envelope


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctxfilter-jev",
        description="Opt-in ctxfilter + existing Jev relevance classification for allowlisted files.",
    )
    parser.add_argument("--version", action="version", version=f"ctxfilter-jev {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    classify = sub.add_parser("classify", help="Classify bounded JSONL records against a fixed goal.")
    classify.add_argument("--path", required=True)
    classify.add_argument("--goal", default=DEFAULT_GOAL)
    classify.add_argument("--criteria-json", default=None)
    classify.add_argument("--deterministic", action="store_true")
    classify.add_argument("--from-byte", type=int, default=0)
    classify.add_argument("--identity-json", default=None)
    classify.add_argument("--max-records", type=int, default=DEFAULT_MAX_RECORDS)
    classify.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    classify.add_argument("--max-runtime-ms", type=int, default=DEFAULT_MAX_RUNTIME_MS)
    classify.add_argument("--max-scan-bytes", type=int, default=DEFAULT_MAX_SCAN_BYTES)
    classify.add_argument("--excerpt-chars", type=int, default=DEFAULT_EXCERPT_CHARS)
    classify.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))

    fp = sub.add_parser("fingerprint", help="Print path/hash candidate for later explicit approval.")
    fp.add_argument("--path", required=True)
    fp.add_argument("--reason", default="explicit nonsecret approval candidate")

    add = sub.add_parser(
        "allowlist-add",
        help="Write one fingerprint into a workspace allowlist. Not exposed as an MCP tool.",
    )
    add.add_argument("--path", required=True)
    add.add_argument("--allowlist", default=str(DEFAULT_ALLOWLIST))
    add.add_argument("--reason", default="explicit nonsecret approval")
    add.add_argument("--id", default="pending-approval")

    sub.add_parser("mcp", help="Run the stdio MCP server (newline-delimited JSON).")
    return parser


def _load_json(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"invalid": True}


def _test_transport() -> str | None:
    return os.environ.get("CTXFILTER_JEV_TEST_TRANSPORT") or None


def dispatch(args: argparse.Namespace) -> tuple[int, str]:
    if args.command == "fingerprint":
        try:
            fp = fingerprint(Path(args.path), HARD_MAX_SCAN_BYTES)
            out = {
                "ok": True,
                "tool": "fingerprint",
                "fingerprint": fp,
                "candidate": candidate_entry(fp, args.reason),
                "note": "This does not authorize forwarding. Add the candidate to the workspace allowlist separately.",
            }
            return 0, dumps_classify(out, DEFAULT_MAX_OUTPUT_BYTES)
        except (OSError, ClassifyError) as exc:
            code = getattr(exc, "code", "invalid_path")
            payload = dumps_classify(
                error_envelope("fingerprint", code, "fingerprint failed", args.path),
                DEFAULT_MAX_OUTPUT_BYTES,
            )
            return 1, payload
    if args.command == "allowlist-add":
        try:
            fp = fingerprint(Path(args.path), HARD_MAX_SCAN_BYTES)
            entry = candidate_entry(fp, args.reason)
            entry["id"] = args.id
            result = add_entry(Path(args.allowlist), entry)
            result["ok"] = True
            result["tool"] = "allowlist-add"
            return 0, dumps_classify(result, DEFAULT_MAX_OUTPUT_BYTES)
        except (OSError, ClassifyError) as exc:
            code = getattr(exc, "code", "allowlist_invalid")
            payload = dumps_classify(
                error_envelope("allowlist-add", code, "allowlist update failed", args.path),
                DEFAULT_MAX_OUTPUT_BYTES,
            )
            return 1, payload
    result = classify_records(
        args.path,
        goal=args.goal,
        criteria=_load_json(args.criteria_json),
        deterministic=args.deterministic,
        from_byte=args.from_byte,
        identity=_load_json(args.identity_json),
        max_records=args.max_records,
        max_output_bytes=args.max_output_bytes,
        max_runtime_ms=args.max_runtime_ms,
        max_scan_bytes=args.max_scan_bytes,
        excerpt_chars=args.excerpt_chars,
        allowlist_path=args.allowlist,
        test_transport=_test_transport(),
    )
    payload = dumps_classify(result, args.max_output_bytes)
    return (0 if result_ok(payload) else 1), payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "mcp":
        from ctxfilter_jev.mcp_server import serve

        return serve()
    exit_code, payload = dispatch(args)
    sys.stdout.write(payload)
    if not payload.endswith("\n"):
        sys.stdout.write("\n")
    return exit_code

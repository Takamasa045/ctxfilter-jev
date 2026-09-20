"""Relevance classification of bounded JSONL records against one fixed goal."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ctxfilter.core import identities_match, identity_from_handle, open_regular, parse_identity, resolve_regular_file

from ctxfilter_jev.allowlist import check_forward_allowed, fingerprint
from ctxfilter_jev.constants import (
    ACT_ABOVE,
    DEFAULT_ALLOWLIST,
    DEFAULT_EXCERPT_CHARS,
    DISPLAY_EXCERPT_CHARS,
    DEFAULT_GOAL,
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MAX_RECORDS,
    DEFAULT_MAX_RUNTIME_MS,
    DEFAULT_MAX_SCAN_BYTES,
    FINAL_CHOICES,
    HARD_EXCERPT_CHARS,
    HARD_MAX_OUTPUT_BYTES,
    HARD_MAX_RECORDS,
    HARD_MAX_RUNTIME_MS,
    HARD_MAX_SCAN_BYTES,
    MANDATORY_RULES,
    MAX_EXTRA_RULES,
    MAX_GOAL_CHARS,
    MAX_RULE_CHARS,
    MAX_STATE_CHARS,
    OPTION_KEYS,
    OPTIONS,
    OUTPUT_ENVELOPE_RESERVE,
    REVIEW_ABOVE,
    UNTRUSTED_NOTICE,
    VERSION,
)
from ctxfilter_jev.extract import Record, excerpt_of, extract_records
from ctxfilter_jev.jev_client import real_jev_ask
from ctxfilter_jev.provenance import resolve_launcher
from ctxfilter_jev.support import (
    ClassifyError,
    Deadline,
    clamp,
    dumps,
    encoded_size,
    error_envelope,
    finite01,
    has_symlink_component,
    is_strict_int,
    path_display,
)
import time


def _criteria(raw: Any) -> dict[str, Any]:
    allowed_keys = {"task", "options", "act_above", "review_above", "literal_field", "question_rules"}
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ClassifyError("invalid_criteria", "criteria must be an object")
    extra = set(raw) - allowed_keys
    if extra:
        raise ClassifyError("invalid_criteria", "criteria contains unsupported keys")
    options = raw.get("options") or OPTIONS
    if not isinstance(options, dict) or tuple(options.keys()) != OPTION_KEYS:
        raise ClassifyError("invalid_criteria", "criteria options must be the fixed relevant/irrelevant/needs_context set")
    act_above = finite01(raw.get("act_above", ACT_ABOVE))
    review_above = finite01(raw.get("review_above", REVIEW_ABOVE))
    if act_above is None or review_above is None or not 0 <= review_above <= act_above <= 1:
        raise ClassifyError("invalid_criteria", "confidence thresholds are invalid")
    extra_rules: list[str] = []
    caller_rules = raw.get("question_rules")
    if caller_rules is not None:
        if not isinstance(caller_rules, list) or len(caller_rules) > MAX_EXTRA_RULES:
            raise ClassifyError("invalid_criteria", "question_rules exceed the extra-rule bound")
        for rule in caller_rules:
            if not isinstance(rule, str) or not rule or len(rule) > MAX_RULE_CHARS:
                raise ClassifyError("invalid_criteria", "question_rules contain an invalid rule")
            extra_rules.append(rule)
    return {
        "task": "relevance_classification",
        "options": {key: OPTIONS[key] for key in OPTION_KEYS},
        "act_above": act_above,
        "review_above": review_above,
        "literal_field": "literal_label",
        "question_rules": list(MANDATORY_RULES) + extra_rules,
    }


def _current_identity(path: Path) -> Any:
    resolved = resolve_regular_file(str(path))
    handle, st = open_regular(resolved)
    try:
        return identity_from_handle(handle, st, resolved)
    finally:
        handle.close()


def _item_from_record(record: Record, excerpt_chars: int) -> dict[str, Any]:
    excerpt, truncated = excerpt_of(record, excerpt_chars)
    return {
        "id": record.id,
        "byte_offset": record.byte_offset,
        "end_byte": record.end_byte,
        "excerpt": excerpt,
        "excerpt_truncated": truncated,
        "decision": "deferred",
        "probability": None,
        "confidence": None,
        "action": None,
        "source": "fallback",
        "fallback_reason": "insufficient_context" if truncated else None,
        "further_fetch": {"byte_offset": record.byte_offset, "end_byte": record.end_byte} if truncated else None,
    }


def _apply_literal(item: dict[str, Any], record: Record) -> None:
    if item.get("excerpt_truncated"):
        item["decision"] = "deferred"
        item["source"] = "fallback"
        item["fallback_reason"] = "insufficient_context"
        return
    label = record.literal_label
    if label in OPTION_KEYS:
        item["decision"] = label
        item["probability"] = 1.0
        item["confidence"] = 1.0
        item["action"] = "act"
        item["source"] = "literal"
        item["fallback_reason"] = None
        return
    item["decision"] = "deferred"
    item["source"] = "fallback"
    item["fallback_reason"] = "missing_literal"


def _budget_item_cap(max_output_bytes: int, max_records: int, excerpt_chars: int) -> int:
    per_item = max(280, 180 + excerpt_chars)
    cap = (max_output_bytes - OUTPUT_ENVELOPE_RESERVE) // per_item
    if cap < 1:
        raise ClassifyError("output_budget", "max_output_bytes cannot hold telemetry and one item")
    return min(max_records, cap)


def _state_and_questions(
    goal: str,
    records: list[Record],
    items: list[dict[str, Any]],
    criteria: dict[str, Any],
    excerpt_chars: int,
) -> dict[str, Any]:
    payload_records = []
    questions = []
    for rec, item in zip(records, items):
        excerpt, truncated = excerpt_of(rec, excerpt_chars)
        if truncated or not rec.raw_ok:
            continue
        payload_records.append({"id": rec.id, "byte_offset": rec.byte_offset, "excerpt": excerpt})
        questions.append(
            {
                "id": rec.id,
                "type": "classify",
                "add_none": True,
                "question": {
                    "task": "Classify relevance of one record to the goal.",
                    "goal": goal,
                    "record_id": rec.id,
                    "rules": criteria["question_rules"],
                },
                "options": criteria["options"],
            }
        )
    payload = {
        "state": {
            "goal": goal,
            "untrusted_notice": UNTRUSTED_NOTICE,
            "records": payload_records,
        },
        "questions": questions,
        "act_above": criteria["act_above"],
        "review_above": criteria["review_above"],
    }
    encoded = json.dumps(payload, ensure_ascii=False)
    if len(encoded) > MAX_STATE_CHARS or len(questions) == 0:
        raise ClassifyError("state_bound", "Jev state cannot contain these records")
    return payload


def _defer_item(item: dict[str, Any], reason: str, source: str = "fallback") -> None:
    item["decision"] = "deferred"
    item["source"] = source
    item["fallback_reason"] = reason


def _map_answer(
    item: dict[str, Any],
    answer: Any,
    none_option: str | None,
    criteria: dict[str, Any],
) -> None:
    if item.get("excerpt_truncated"):
        _defer_item(item, "insufficient_context")
        return
    if not isinstance(answer, dict):
        _defer_item(item, "malformed")
        return
    choice = answer.get("choice")
    action = answer.get("action")
    conf = finite01(answer.get("confidence"))
    probs = answer.get("probabilities")
    if not isinstance(choice, str) or action not in ("act", "review", "abstain") or conf is None:
        _defer_item(item, "malformed")
        return
    if not isinstance(probs, dict):
        _defer_item(item, "malformed")
        return
    parsed_probs: dict[str, float] = {}
    for key in (*OPTION_KEYS, *( [none_option] if none_option else [])):
        value = finite01(probs.get(key))
        if value is None:
            _defer_item(item, "malformed")
            return
        parsed_probs[key] = value
    total = sum(parsed_probs.values())
    if abs(total - 1.0) > 0.05:
        _defer_item(item, "malformed")
        return
    item["confidence"] = conf
    item["action"] = action
    item["probability"] = parsed_probs.get(choice)
    if none_option and choice == none_option:
        _defer_item(item, "none_selected", source="jev")
        return
    if choice not in OPTION_KEYS:
        _defer_item(item, "malformed")
        return
    if action == "abstain" or conf < criteria["review_above"]:
        _defer_item(item, "low_confidence", source="jev")
        return
    if action == "review" or conf < criteria["act_above"]:
        _defer_item(item, "review", source="jev")
        return
    if choice == "needs_context":
        item["decision"] = "needs_context"
        item["source"] = "jev"
        item["fallback_reason"] = None
        return
    if choice not in FINAL_CHOICES:
        _defer_item(item, "malformed")
        return
    item["decision"] = choice
    item["source"] = "jev"
    item["fallback_reason"] = None


def _jev_min(jev: Any) -> dict[str, Any]:
    if not isinstance(jev, dict):
        return {"called": False}
    out: dict[str, Any] = {"called": bool(jev.get("called"))}
    usage = jev.get("usage")
    if isinstance(usage, dict):
        slim_usage = {}
        if "input_tokens" in usage:
            slim_usage["input_tokens"] = usage["input_tokens"]
        if "output_tokens" in usage:
            slim_usage["output_tokens"] = usage["output_tokens"]
        if slim_usage:
            out["usage"] = slim_usage
    return out


def _minimal_budget_text(limit: int, tool: str, jev: Any) -> str:
    called = bool(isinstance(jev, dict) and jev.get("called"))
    candidates = [
        {
            "ok": False,
            "tool": tool,
            "error": {"code": "output_budget"},
            "jev_called": called,
            "jev": _jev_min(jev),
        },
        {
            "ok": False,
            "error": {"code": "output_budget"},
            "jev_called": called,
            "jev": _jev_min(jev),
        },
        {"ok": False, "error": {"code": "output_budget"}, "jev_called": called},
    ]
    chosen = dumps(candidates[-1])
    for item in candidates:
        text = dumps(item)
        if encoded_size(text) <= limit:
            return text
        chosen = text
    return chosen


def _fit_dump(obj: dict[str, Any], limit: int) -> str:
    text = dumps(obj)
    if encoded_size(text) <= limit:
        return text
    slim = {key: value for key, value in obj.items() if value is not None and key not in {"path_display", "identity", "cursor", "items", "limits", "forwarding_note"}}
    text = dumps(slim)
    if encoded_size(text) <= limit:
        return text
    return _minimal_budget_text(limit, str(obj.get("tool") or "classify_records"), obj.get("jev"))


def compact_codex_result(obj: dict[str, Any]) -> dict[str, Any]:
    """Codex-facing stream: one identity, no redundant nulls, short display excerpts.

    Display truncation is not semantic truncation. Jev still sees the bounded
    full excerpt on the internal item before this compact step.
    """
    if obj.get("tool") and obj.get("tool") != "classify_records":
        return {key: value for key, value in obj.items() if value is not None}
    if not obj.get("ok"):
        out = {key: value for key, value in obj.items() if value is not None}
        if "item_count" not in out:
            out["item_count"] = len(out.get("items") or [])
        return out
    ident = obj.get("identity") or {}
    identity = {
        key: ident[key]
        for key in ("path", "size", "mtime_ns", "inode", "dev", "sample_sha256", "sha256")
        if key in ident
    }
    items = []
    for item in obj.get("items") or []:
        row: dict[str, Any] = {
            "id": item.get("id"),
            "byte_offset": item.get("byte_offset"),
            "decision": item.get("decision"),
            "source": item.get("source"),
        }
        excerpt = item.get("excerpt") or ""
        if excerpt:
            row["excerpt"] = excerpt[:DISPLAY_EXCERPT_CHARS]
        for key in ("probability", "confidence", "action"):
            if item.get(key) is not None:
                row[key] = item[key]
        if item.get("fallback_reason"):
            row["fallback_reason"] = item["fallback_reason"]
        if item.get("excerpt_truncated"):
            row["excerpt_truncated"] = True
            row["end_byte"] = item.get("end_byte")
        items.append(row)
    out = {
        "ok": True,
        "tool": obj.get("tool", "classify_records"),
        "status": obj.get("status"),
        "identity": identity,
        "scan_complete": obj.get("scan_complete"),
        "items": items,
        "item_count": len(items),
        "call_counts": obj.get("call_counts"),
        "elapsed_ms": obj.get("elapsed_ms"),
    }
    if obj.get("defer_reason"):
        out["defer_reason"] = obj["defer_reason"]
    if obj.get("further_fetch_needed"):
        out["further_fetch_needed"] = True
        out["next_byte"] = obj.get("next_byte")
        if obj.get("cursor"):
            out["cursor"] = {
                "next_byte": obj["cursor"].get("next_byte"),
                "identity": identity,
            }
    jev = obj.get("jev")
    if isinstance(jev, dict):
        slim = {"called": bool(jev.get("called"))}
        for key in ("mock", "package", "model", "usage", "latency_ms"):
            if jev.get(key) is not None:
                slim[key] = jev[key]
        out["jev"] = slim
    return out


def dumps_classify(obj: dict[str, Any], max_output_bytes: int) -> str:
    limit = clamp(max_output_bytes, 256, HARD_MAX_OUTPUT_BYTES)
    if obj.get("tool") and obj.get("tool") != "classify_records":
        payload = {key: value for key, value in obj.items() if value is not None}
        return _fit_dump(payload, limit)
    compact = compact_codex_result(obj)
    text = dumps(compact)
    if encoded_size(text) <= limit:
        return text
    shrunk = json.loads(text)
    shrunk["output_truncated"] = True
    shrunk["status"] = "deferred"
    shrunk["defer_reason"] = shrunk.get("defer_reason") or "output_budget"
    shrunk["further_fetch_needed"] = True
    shrunk["scan_complete"] = False
    items = list(shrunk.get("items") or [])
    while items and encoded_size(dumps({**shrunk, "items": items, "item_count": len(items)})) > limit:
        dropped = items.pop()
        shrunk["next_byte"] = dropped.get("byte_offset", shrunk.get("next_byte"))
        shrunk["cursor"] = {"next_byte": shrunk["next_byte"], "identity": shrunk.get("identity")}
    shrunk["items"] = items
    shrunk["item_count"] = len(items)
    fitted = dumps(shrunk)
    if items and encoded_size(fitted) <= limit:
        return fitted
    return _fit_dump(shrunk, limit)


def _jev_blob(meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "called": meta["called"],
        "mock": meta["mock"],
        "package": meta["package"],
        "model": meta["model"],
        "usage": meta["usage"],
        "latency_ms": meta["latency_ms"],
    }


def classify_records(
    path: str,
    *,
    goal: str | None = None,
    criteria: dict[str, Any] | None = None,
    deterministic: bool = False,
    from_byte: int = 0,
    identity: Any = None,
    max_records: int = DEFAULT_MAX_RECORDS,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    max_runtime_ms: int = DEFAULT_MAX_RUNTIME_MS,
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    allowlist_path: str | Path | None = None,
    test_transport: str | None = None,
    jev_call: Any = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    tool = "classify_records"
    jev_meta: dict[str, Any] = {
        "called": False,
        "mock": False,
        "package": None,
        "model": None,
        "usage": None,
        "latency_ms": None,
        "internal_calls": 0,
    }
    ctx_calls = 0
    try:
        if not isinstance(path, str):
            raise ClassifyError("invalid_path", "path is invalid")
        if has_symlink_component(path):
            raise ClassifyError("symlink_denied", "symlinks are not followed")
        if not isinstance(goal, str) or not goal.strip():
            goal_text = DEFAULT_GOAL
        else:
            goal_text = goal.strip()
        if len(goal_text) > MAX_GOAL_CHARS:
            raise ClassifyError("invalid_goal", "goal exceeds length limit")
        parsed_criteria = _criteria(criteria)
        if not is_strict_int(max_records):
            raise ClassifyError("invalid_params", "max_records is invalid")
        if not is_strict_int(max_output_bytes):
            raise ClassifyError("invalid_params", "max_output_bytes is invalid")
        if not is_strict_int(max_runtime_ms):
            raise ClassifyError("invalid_params", "max_runtime_ms is invalid")
        if not is_strict_int(max_scan_bytes):
            raise ClassifyError("invalid_params", "max_scan_bytes is invalid")
        if not is_strict_int(excerpt_chars):
            raise ClassifyError("invalid_params", "excerpt_chars is invalid")
        if not is_strict_int(from_byte):
            raise ClassifyError("invalid_cursor", "from_byte is not a record boundary")
        max_records = clamp(max_records, 1, HARD_MAX_RECORDS)
        max_output_bytes = clamp(max_output_bytes, 256, HARD_MAX_OUTPUT_BYTES)
        max_runtime_ms = clamp(max_runtime_ms, 50, HARD_MAX_RUNTIME_MS)
        max_scan_bytes = clamp(max_scan_bytes, 64, HARD_MAX_SCAN_BYTES)
        excerpt_chars = clamp(excerpt_chars, 40, HARD_EXCERPT_CHARS)
        deadline = Deadline(max_runtime_ms)
        cap = _budget_item_cap(max_output_bytes, max_records, excerpt_chars)
        resolved = resolve_regular_file(path)
        before = _current_identity(resolved)
        if from_byte > 0 and identity is None:
            raise ClassifyError("invalid_cursor", "continuation requires identity")
        if identity is not None:
            expected = identity if hasattr(identity, "as_dict") else parse_identity(identity)
            if not identities_match(expected, before):
                raise ClassifyError("stale_identity", "file identity changed; preview again")
        fp = fingerprint(resolved, HARD_MAX_SCAN_BYTES, deadline)
        allow_id = None
        launcher = None
        if not deterministic:
            allowed = check_forward_allowed(resolved, fp, Path(allowlist_path) if allowlist_path else DEFAULT_ALLOWLIST)
            allow_id = allowed.get("entry_id")
            if jev_call is None:
                launcher = resolve_launcher(
                    test_transport=test_transport,
                    config_path=Path(config_path) if config_path else None,
                )
            else:
                launcher = {
                    "ok": True,
                    "mock": True,
                    "argv": ["mock-jev-test-only"],
                    "env": {},
                    "package_name": "mock-jev-test-only",
                    "package_version": "test",
                }
            jev_meta["mock"] = bool(launcher.get("mock"))
        extracted = extract_records(
            str(resolved),
            from_byte=from_byte,
            identity=before if from_byte else None,
            max_records=cap,
            max_scan_bytes=max_scan_bytes,
            deadline=deadline,
            file_size=before.size,
        )
        ctx_calls = int(extracted.get("ctx_calls") or 0)
        if not extracted.get("ok"):
            err = extracted.get("result") or error_envelope(tool, "extract_failed", "ctxfilter extract failed", path)
            err["jev_called"] = False
            return err
        after_extract = _current_identity(resolved)
        if not identities_match(before, after_extract) or fingerprint(resolved, HARD_MAX_SCAN_BYTES, Deadline(2000))["sha256"] != fp["sha256"]:
            raise ClassifyError("changed_during_read", "file changed during read")
        records: list[Record] = list(extracted["records"])
        items = [_item_from_record(rec, excerpt_chars) for rec in records]
        pending: list[tuple[dict[str, Any], Record]] = []
        if deterministic:
            for item, rec in zip(items, records):
                if rec.raw_ok:
                    _apply_literal(item, rec)
                else:
                    _defer_item(item, "invalid_record")
        else:
            for item, rec in zip(items, records):
                if not rec.raw_ok:
                    _defer_item(item, "invalid_record")
                elif item.get("excerpt_truncated"):
                    _defer_item(item, "insufficient_context")
                else:
                    pending.append((item, rec))
        fallback_all = None
        if pending and not deterministic:
            if deadline.expired():
                for item, _rec in pending:
                    _defer_item(item, "timeout")
            else:
                try:
                    payload = _state_and_questions(
                        goal_text,
                        [rec for _item, rec in pending],
                        [item for item, _rec in pending],
                        parsed_criteria,
                        excerpt_chars,
                    )
                    call = jev_call or real_jev_ask
                    jev_meta["internal_calls"] = 1
                    jev_meta["called"] = True
                    jev_meta["package"] = f"{launcher['package_name']}@{launcher['package_version']}"
                    response = call(launcher, payload, deadline)
                    jev_meta["model"] = response.get("model")
                    jev_meta["usage"] = response.get("usage")
                    jev_meta["latency_ms"] = response.get("latency_ms")
                    after_jev = _current_identity(resolved)
                    if not identities_match(before, after_jev) or fingerprint(resolved, HARD_MAX_SCAN_BYTES, Deadline(2000))["sha256"] != fp["sha256"]:
                        raise ClassifyError(
                            "changed_during_read",
                            "file changed during Jev call",
                            extra={"jev": _jev_blob(jev_meta), "call_counts": {"ctxfilter_internal": ctx_calls, "jev_internal": jev_meta["internal_calls"], "codex_facing": 1}},
                        )
                    answers = response.get("answers")
                    none_options = response.get("none_options") if isinstance(response.get("none_options"), dict) else {}
                    if not isinstance(answers, dict):
                        raise ClassifyError("jev_malformed", "Jev answers are missing")
                    for item, rec in pending:
                        if rec.id not in answers:
                            _defer_item(item, "malformed")
                            continue
                        _map_answer(item, answers[rec.id], none_options.get(rec.id), parsed_criteria)
                except ClassifyError as exc:
                    fallback_all = exc.code
                    if exc.code == "changed_during_read":
                        raise
                    for item, _rec in pending:
                        _defer_item(
                            item,
                            {
                                "jev_timeout": "timeout",
                                "jev_malformed": "malformed",
                                "jev_error": "jev_error",
                                "jev_rpc": "jev_error",
                                "jev_transport": "jev_error",
                                "jev_response_bound": "jev_error",
                                "jev_request_bound": "jev_error",
                                "jev_schema": "jev_error",
                                "state_bound": "state_bound",
                            }.get(exc.code, "jev_error"),
                        )
        after = _current_identity(resolved)
        if not identities_match(before, after):
            raise ClassifyError(
                "changed_during_read",
                "file changed during read",
                extra={"jev": _jev_blob(jev_meta), "call_counts": {"ctxfilter_internal": ctx_calls, "jev_internal": jev_meta["internal_calls"], "codex_facing": 1}},
            )
        ident_out = before.as_dict()
        ident_out["sha256"] = fp["sha256"]
        ident_after = after.as_dict()
        ident_after["sha256"] = fingerprint(resolved, HARD_MAX_SCAN_BYTES, Deadline(2000))["sha256"]
        further = bool(extracted.get("further_fetch_needed"))
        scan_complete = bool(extracted.get("scan_complete")) and not further
        jev_uncertain = any(
            item["decision"] in ("deferred", "needs_context") or item.get("action") in ("review", "abstain")
            for item in items
        ) and not deterministic
        status = "complete"
        defer_reason = None
        if not scan_complete:
            status = "deferred"
            defer_reason = "incomplete_scan"
        elif any(item.get("excerpt_truncated") for item in items):
            status = "deferred"
            defer_reason = "insufficient_context"
        elif fallback_all:
            status = "deferred"
            defer_reason = fallback_all
        elif jev_uncertain:
            status = "deferred"
            defer_reason = "item_uncertain"
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": True,
            "v": VERSION,
            "tool": tool,
            "status": status,
            "defer_reason": defer_reason,
            "path_display": path_display(str(resolved)),
            "identity": ident_out,
            "identity_after": ident_after,
            "goal": goal_text,
            "deterministic": bool(deterministic),
            "allowlist_entry": allow_id,
            "forwarding_note": (
                "Allowlisting a file does not authorize confidential goals or extra rules. "
                "Deterministic mode extracts literal_label only and is not a semantic Jev proof. "
                "Ordinary literal search should use ctxfilter without Jev."
            ),
            "scan_complete": scan_complete,
            "further_fetch_needed": further or not scan_complete,
            "next_byte": extracted.get("next_byte"),
            "cursor": None
            if scan_complete
            else {"next_byte": extracted.get("next_byte"), "identity": ident_out},
            "items": items,
            "item_count": len(items),
            "call_counts": {
                "ctxfilter_internal": ctx_calls,
                "jev_internal": jev_meta["internal_calls"],
                "codex_facing": 1,
            },
            "jev": _jev_blob(jev_meta),
            "elapsed_ms": elapsed_ms,
            "bytes_scanned": extracted.get("bytes_scanned"),
            "limits": {
                "max_records": max_records,
                "max_output_bytes": max_output_bytes,
                "max_runtime_ms": max_runtime_ms,
                "max_scan_bytes": max_scan_bytes,
                "excerpt_chars": excerpt_chars,
            },
        }
    except ClassifyError as exc:
        extra = dict(exc.extra)
        extra.setdefault("jev", _jev_blob(jev_meta))
        extra.setdefault(
            "call_counts",
            {"ctxfilter_internal": ctx_calls, "jev_internal": jev_meta["internal_calls"], "codex_facing": 1},
        )
        return error_envelope(tool, exc.code, exc.message, path if isinstance(path, str) else None, extra=extra)
    except Exception:
        return error_envelope(tool, "invalid_input", "input is invalid", path if isinstance(path, str) else None)


def result_ok(payload: str) -> bool:
    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, UnicodeError, TypeError):
        return False
    return bool(isinstance(parsed, dict) and parsed.get("ok"))
